"""Integration checks for live inference and offline backprop boundaries."""
import json
from itertools import combinations
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

from tasks.DraftDuel.strategy import runtime_policy as runtime
from tasks.DraftDuel.strategy import lineup
from dev_tools.draft_duel import fit_latent16_real as real

OWN = ['莹草', '鬼童丸', '茨木童子', '椒图', '辉夜姬']
MATE = ['座敷童子', '季', '百目鬼', '白藏主', '山兔']


class RuntimeLatentChecks(unittest.TestCase):
    def test_role_head_is_used_with_exact_own_team(self):
        owner = SimpleNamespace()
        model, _ = runtime.models(owner)
        self.assertEqual(model.head, 'role')
        with patch.object(lineup, 'choose_pick', side_effect=AssertionError('legacy called')):
            pick = runtime.latent_choose_pick(owner, OWN[2:], OWN[:2])
        self.assertEqual(pick.name, model.choose(OWN[2:], OWN[:2]).name)

    def test_captain_plans_two_three_card_sets_without_cross_side_scoring(self):
        owner = SimpleNamespace()
        _, field = runtime.models(owner)
        original = field.score_set
        def score(cards):
            self.assertEqual(len(cards), 3)
            return original(cards)
        with patch.object(field, 'score_set', side_effect=score):
            a, b = runtime.latent_plan_duel_lineup(owner, OWN, MATE, locked=OWN[:1])
        expected = max((c for c in combinations(sorted(OWN), 3) if OWN[0] in c), key=original)
        self.assertIn(OWN[0], a)
        counts = lineup.composition_counts(a+b)
        self.assertGreaterEqual(counts['fire'], 2)
        self.assertGreaterEqual(counts['damage'], 2)
        self.assertGreaterEqual(counts['protect'], 1)
        self.assertGreaterEqual(counts['flex'], 1)

    def test_joint_model_score_cannot_push_out_protection_or_control(self):
        owner = SimpleNamespace()
        _, field = runtime.models(owner)
        def score(cards):
            self.assertEqual(len(cards), 3)
            return sum(100000 for name in cards if name in ('鬼童丸','茨木童子','季'))
        with patch.object(field, 'score_set', side_effect=score):
            a, b = runtime.latent_plan_duel_lineup(owner, OWN, MATE)
        counts = lineup.composition_counts(a+b)
        self.assertGreaterEqual(counts['protect'], 1)
        self.assertGreaterEqual(counts['flex'], 1)
        self.assertEqual(counts['damage'], 2)

    def test_joint_constraints_relax_when_pool_has_no_support(self):
        owner = SimpleNamespace()
        pool = ['鬼童丸','茨木童子','季']
        a, b = runtime.latent_plan_duel_lineup(owner, pool, pool, locked=pool[:1])
        self.assertEqual(set(a), set(pool))
        self.assertEqual(set(b), set(pool))

    def test_snapshot_survives_restart_without_reloading_disk_models(self):
        owner = SimpleNamespace()
        before = runtime.latent_choose_pick(owner, OWN[:3])
        restored = SimpleNamespace(_draft_policy_snapshot=json.loads(json.dumps(owner._draft_policy_snapshot)))
        with patch.object(Path, 'read_text', side_effect=AssertionError('disk reload')):
            after = runtime.latent_choose_pick(restored, OWN[:3])
        self.assertEqual(before, after)
        self.assertEqual(len(owner._draft_policy_snapshot['ids']['draft']), 64)

    def test_missing_models_fall_back_and_stay_frozen_for_round(self):
        owner = SimpleNamespace()
        with TemporaryDirectory() as directory, patch.object(runtime, 'DRAFT_PATH', Path(directory)/'draft.json'), patch.object(runtime, 'FIELD_PATH', Path(directory)/'field.json'):
            self.assertEqual(runtime.latent_choose_pick(owner, OWN[:3]).name, lineup.choose_pick(OWN[:3]).name)
        self.assertEqual(owner._draft_policy_snapshot['ids'], {'draft': 'legacy', 'field': 'legacy'})

    def test_corrupt_model_does_not_block_round(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'bad.json'
            path.write_text('{"format":"invalid"}')
            with patch.object(runtime, 'DRAFT_PATH', path):
                owner = SimpleNamespace()
                self.assertEqual(runtime.latent_choose_pick(owner, OWN[:3]).name, lineup.choose_pick(OWN[:3]).name)

    def test_training_sides_keep_actual_rosters_and_one_round_group(self):
        battle = dict(result='win', ours=OWN, mate=MATE, fielded=OWN[:3]+MATE[:3],
                      own_fielded=OWN[:3], mate_fielded=MATE[:3], round_id='round-a', event_id='game-a',
                      draft_rounds=[dict(offers=OWN[:3], pick=OWN[0], team=[])],
                      peer_draft_rounds=[dict(offers=MATE[:3], pick=MATE[0], team=[])])
        draft, _ = real.draft_samples([battle], set(OWN+MATE))
        field, _ = real.field_samples([battle], set(OWN+MATE))
        self.assertEqual(len(draft), 2)
        self.assertEqual(len(field), 2)
        self.assertEqual(field[1]['field'], MATE[:3])
        self.assertEqual({r['event_id'] for r in draft+field}, {'round-a'})
        battle['unresolved_slots'] = [1]
        self.assertFalse(real.field_samples([battle], set(OWN+MATE))[0])
        self.assertFalse(real.draft_samples([battle], set(OWN+MATE))[0])

    def test_split_never_leaks_a_round_or_accepts_single_round(self):
        samples = [dict(event_id=event) for event in ['a','a','a','b','b','c']]
        train, test = real._split(samples, .8, 1)
        self.assertTrue(test)
        self.assertFalse({samples[i]['event_id'] for i in train} & {samples[i]['event_id'] for i in test})
        with self.assertRaises(ValueError):
            real._split(samples[:3], .8, 1)

    def test_real_checkpoint_restores_transcript_and_policy_snapshot(self):
        from dev_tools.draft_duel.check_recovery import RecoveryChecks
        with TemporaryDirectory() as directory:
            helper = RecoveryChecks()
            path = Path(directory)/'state.npz'
            owner = helper.owner(path)
            owner.draft_selected = OWN[:1]
            owner._draft_transcript = {1: dict(round=1, offers=OWN[:3], pick=OWN[0], team=[])}
            runtime.models(owner)
            owner._save_draft_checkpoint('select')
            restored = helper.owner(path)
            self.assertTrue(restored._restore_draft_checkpoint())
            self.assertEqual(restored._draft_transcript, owner._draft_transcript)
            self.assertEqual(restored._draft_policy_snapshot, owner._draft_policy_snapshot)
            self.assertEqual(runtime.latent_choose_pick(restored, OWN[1:4], OWN[:1]),
                             runtime.latent_choose_pick(owner, OWN[1:4], OWN[:1]))

    def test_real_gate_does_not_train_or_export_small_dataset(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'stats.json'
            records = [dict(result='win', ours=OWN, own_fielded=OWN[:3],
                            round_id='same-round', event_id=str(i)) for i in range(10)]
            path.write_text(json.dumps(dict(battles=records)))
            with patch.object(real, 'fine_tune_field', side_effect=AssertionError('training ran')):
                result = real.run(battles=path, output=Path(directory)/'output',
                                  min_battles=2, verbose=False)
            self.assertFalse(result['updated'])
            self.assertFalse((Path(directory)/'output').exists())

    def test_real_backprop_updates_copy_without_writing_live_policy(self):
        owner = SimpleNamespace()
        draft, field = runtime.models(owner)
        vectors = draft.vectors.copy()
        sample = dict(offers=OWN[:3], team=[], pick=OWN[0], index=0, result='win', event_id='a')
        model, _ = real.fine_tune_draft(draft, [sample], epochs=1, lr=.001, anchor=.05,
                                       batch_size=1, seed=0, verbose=False)
        self.assertFalse(np.array_equal(model.Z, vectors))
        np.testing.assert_array_equal(draft.vectors, vectors)
        model, _ = real.fine_tune_field(field, [dict(team=OWN, field=OWN[:3], result='win')],
                                       epochs=1, lr=.001, anchor=.05, batch_size=1, seed=0, verbose=False)
        self.assertFalse(np.array_equal(model.U, field.vectors))


if __name__ == '__main__':
    unittest.main()
