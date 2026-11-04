"""Collection-only phase: original strategy and auditable round win rates."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from tasks.DraftDuel.strategy import battle_stats as stats, lineup, runtime_policy as runtime

OWN = ['莹草','鬼童丸','茨木童子','椒图','辉夜姬']
MATE = ['座敷童子','季','百目鬼','白藏主','山兔']


class RoundRecordChecks(unittest.TestCase):
    def test_round_links_both_sides_to_their_own_choices_and_late_results(self):
        own = [dict(round=1, offers=[None, OWN[0], OWN[1]], pick=OWN[0],
                    pick_slot=2, team=[], raw_offers=['未知名', OWN[0], OWN[1]])]
        peer = [dict(round=1, offers=MATE[:3], pick=MATE[0], team=[])]
        with TemporaryDirectory() as directory, patch.object(stats, 'STATS_PATH', Path(directory)/'stats.json'):
            stats.record('unknown', OWN, mate=MATE, event_id='0', instance='A', round_id='r',
                         draft_rounds=own, peer_draft_rounds=peer)
            entry = stats.record_round('r', 'A', None, OWN, mate=MATE,
                                       completed=True, total_games=1)
            self.assertEqual(entry['draft_rounds'], own)
            self.assertEqual(entry['peer_draft_rounds'][0]['pick'], MATE[0])
            self.assertEqual(entry['peer_draft_rounds'][0]['pick_slot'], 1)
            stats.resolve_result('0', 'win', 1)
            updated = stats.load()['rounds'][0]
            self.assertEqual(updated['draft_rounds'], own)
            self.assertEqual(updated['win_rate'], 1)

    def test_finalization_can_save_choices_without_small_game_record(self):
        rows = [dict(round=1, offers=OWN[:3], pick=OWN[0], team=[])]
        with TemporaryDirectory() as directory, patch.object(stats, 'STATS_PATH', Path(directory)/'stats.json'):
            entry = stats.record_round('r', 'A', None, OWN, completed=False, draft_rounds=rows)
            self.assertEqual(entry['draft_rounds'][0]['offers'], OWN[:3])
            self.assertEqual(entry['draft_rounds'][0]['pick_slot'], 1)

    def test_confirmed_choices_reject_pending_and_wrong_roster_prefix(self):
        rows = [dict(round=1, offers=OWN[:3], pick=OWN[0], team=[]),
                dict(round=2, offers=OWN[:3], pick=OWN[1], team=[OWN[4]]),
                dict(round=5, offers=OWN[2:], pick=OWN[4], team=OWN[:4])]
        self.assertEqual([r['round'] for r in stats.confirmed_draft_rounds(rows, OWN[:4])], [1])
        legacy = dict(round=1, offers=[OWN[0], OWN[1]], pick=OWN[0], team=[])
        self.assertNotIn('pick_slot', stats.serialize_draft_rounds([legacy])[0])

    def test_live_calls_only_legacy_without_loading_or_updating_matrix(self):
        owner = SimpleNamespace()
        with patch.object(runtime, 'models', side_effect=AssertionError('latent loaded')):
            self.assertEqual(runtime.choose_pick(owner, OWN[:3]), lineup.choose_pick(OWN[:3]))
            self.assertEqual(runtime.plan_lineup(owner, OWN, field_weights={}),
                             lineup.plan_lineup(OWN, team_size=3, field_weights={}))
            self.assertEqual(runtime.plan_duel_lineup(owner, OWN, MATE, field_weights={}),
                             lineup.plan_duel_lineup(OWN, MATE, field_weights={}))

    def write_games(self):
        for i, result in enumerate(('win','loss','win')):
            stats.record(result, OWN, OWN[:3]+MATE[:3], MATE, event_id=str(i),
                         instance='A', round_id='r', own_fielded=OWN[:3], mate_fielded=MATE[:3],
                         screenshot=f'{i}.jpg')

    def test_round_counts_group_lineup_and_refresh_without_double_count(self):
        with TemporaryDirectory() as directory, patch.object(stats, 'STATS_PATH', Path(directory)/'stats.json'):
            self.write_games()
            result = stats.record_round('r', 'A', None, OWN, completed=True, total_games=3)
            self.assertAlmostEqual(result['win_rate'], 2/3)
            self.assertEqual(result['wins'], 2)
            self.assertEqual(result['losses'], 1)
            self.assertEqual(result['lineups'][0]['games'], 3)
            self.assertTrue(result['lineups'][0]['verified'])
            self.assertEqual(len(result['lineups'][0]['screenshots']), 3)
            stats.record_round('r', 'A', None, OWN, completed=True, total_games=3)
            self.assertEqual(len(stats.load()['rounds']), 1)

    def test_unknown_is_not_loss_and_late_result_refreshes_summary(self):
        with TemporaryDirectory() as directory, patch.object(stats, 'STATS_PATH', Path(directory)/'stats.json'):
            self.write_games()
            stats.record('unknown', OWN, event_id='3', instance='A', round_id='r')
            result = stats.record_round('r', 'A', 2, OWN, completed=True, total_games=4)
            self.assertIsNone(result['win_rate'])
            self.assertEqual(result['unknown_games'], 1)
            self.assertEqual(result['losses'], 1)
            stats.resolve_result('3', 'loss', None)
            result = stats.load()['rounds'][0]
            self.assertEqual(result['win_rate'], .5)
            self.assertEqual(result['unknown_games'], 0)

    def test_only_verified_final_counter_can_supply_complete_round_rate(self):
        with TemporaryDirectory() as directory, patch.object(stats, 'STATS_PATH', Path(directory)/'stats.json'):
            stats.record('unknown', OWN, event_id='0', instance='A', round_id='r')
            result = stats.record_round('r', 'A', 2, OWN, completed=True,
                                        total_games=4, final_count_verified=True)
            self.assertEqual(result['win_rate'], .5)
            self.assertEqual(result['win_rate_source'], 'final_counter')
            self.assertEqual(result['unknown_games'], 4)


if __name__ == '__main__':
    unittest.main()
