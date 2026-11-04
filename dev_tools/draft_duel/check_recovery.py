"""恢复记录、防抖与最后一名上阵记录；全部使用临时目录和模拟画面。"""
from pathlib import Path
import os
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from datetime import datetime as ScheduleDateTime, timezone as ScheduleTimezone, timedelta as ScheduleDelta
from unittest.mock import Mock, patch

import numpy as np
from module.exception import TaskEnd
from tasks.DraftDuel.config import DraftDuel, DraftMatchMode
from tasks.DraftDuel.script_task import ScriptTask


class RecoveryChecks(unittest.TestCase):
    def setUp(self):
        clock = patch('module.draft_duel_schedule.draft_duel_now', return_value=ScheduleDateTime(2026, 11, 1, 12, tzinfo=ScheduleTimezone(ScheduleDelta(hours=8))))
        clock.start()
        self.addCleanup(clock.stop)

    def test_release_templates_removes_checkpoint_images_but_keeps_roster(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'state.npz'
            owner = self.owner(path)
            owner.draft_selected = ['莹草', '季', '辉夜姬', '椒图', '鬼童丸']
            owner.draft_hand_templates = (('莹草', np.ones((41, 56, 3), dtype=np.uint8)),)
            owner.draft_fielded = ('莹草',)
            owner.draft_deployment_rounds = [{'round': 1, 'slot': 1, 'name': '莹草'}]
            owner.draft_planned_lineup = ('莹草', '季', '辉夜姬')
            owner._save_draft_checkpoint('battle')
            owner._release_deployment_templates()
            with np.load(path, allow_pickle=False) as saved:
                self.assertEqual(saved.files, ['metadata'])
            restored = self.owner(path)
            self.assertTrue(restored._restore_draft_checkpoint())
            self.assertEqual(restored.draft_hand_templates, ())
            self.assertEqual(restored.draft_selected, owner.draft_selected)
            self.assertEqual(restored.draft_fielded, ('莹草',))
            self.assertEqual(restored.draft_planned_lineup, ())
            self.assertEqual(restored.draft_deployment_rounds, owner.draft_deployment_rounds)

    def test_checkpoint_retries_temporary_replacement_denial(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'state.npz'
            owner = self.owner(path)
            replace = os.replace
            attempts = []
            def intermittent(source, destination):
                attempts.append(source)
                if len(attempts) < 3:
                    raise PermissionError('file busy')
                replace(source, destination)
            with patch('tasks.DraftDuel.runtime.recovery.os.replace', side_effect=intermittent), \
                    patch('tasks.DraftDuel.runtime.recovery.time.sleep'):
                owner._save_draft_checkpoint('fight')
            self.assertEqual(len(attempts), 3)
            self.assertTrue(self.owner(path)._restore_draft_checkpoint())
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_blocked_checkpoint_keeps_old_file_and_retries_same_state_later(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'state.npz'
            owner = self.owner(path)
            owner._save_draft_checkpoint('rank')
            previous = path.read_bytes()
            signature = owner._draft_checkpoint_signature
            with patch('tasks.DraftDuel.runtime.recovery.os.replace',
                       side_effect=PermissionError('file busy')) as replace, \
                    patch('tasks.DraftDuel.runtime.recovery.time.sleep'):
                owner._save_draft_checkpoint('fight')
            self.assertEqual(replace.call_count, 5)
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(owner._draft_checkpoint_signature, signature)
            self.assertEqual(list(Path(folder).iterdir()), [path])
            owner._save_draft_checkpoint('fight')
            restored = self.owner(path)
            self.assertTrue(restored._restore_draft_checkpoint())
            self.assertEqual(restored._draft_resume_phase, 'fight')
            self.assertFalse(owner._draft_checkpoint_write_blocked)

    def owner(self, path=None):
        owner = object.__new__(ScriptTask)
        owner.config = SimpleNamespace(config_name='offline-check', draft_duel=DraftDuel(
            draft_duel_config={'match_mode': '队长'}))
        owner.device = Mock()
        owner._initialize_duel_state()
        if path is not None:
            owner._draft_checkpoint_path = lambda: path
        else:
            owner._save_draft_checkpoint = Mock()
        return owner

    def test_checkpoint_restores_names_portraits_and_pending_confirmation(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'state.npz'
            a = self.owner(path)
            a.draft_selected = ['莹草', '季', '辉夜姬', '椒图', '鬼童丸']
            a.draft_heads = tuple(np.full((20, 20, 3), i, dtype=np.uint8) for i in range(5))
            a.draft_hand_templates = (('辉夜姬', np.full((41, 56, 3), 127, dtype=np.uint8)),)
            a.draft_fielded = ('莹草',)
            a.draft_completed_rounds = 2
            a._draft_field_weights = {'莹草': 8.0}
            a._draft_deployment_state = {'next_epoch': 1, 'slots': [0, 1, 2],
                                        'completed': 1,
                                        'deferred': [{'index': 0, 'name': '莹草', 'stable': 0}],
                                        'pending': {'index': 1, 'clicked': True, 'redrag_attempted': True}}
            a._save_draft_checkpoint('fight')
            b = self.owner(path)
            self.assertTrue(b._restore_draft_checkpoint())
            self.assertEqual(b.draft_hand_templates[0][0], '辉夜姬')
            np.testing.assert_array_equal(b.draft_hand_templates[0][1], a.draft_hand_templates[0][1])
            self.assertEqual(b._stats_round_id, a._stats_round_id)
            self.assertEqual(b.draft_fielded, ('莹草',))
            self.assertEqual(b.draft_completed_rounds, 2)
            self.assertTrue(b._draft_deployment_state['pending']['clicked'])
            self.assertTrue(b._draft_deployment_state['pending']['redrag_attempted'])
            self.assertEqual(b._draft_deployment_state['completed'], 1)
            self.assertEqual(b._draft_deployment_state['deferred'][0]['index'], 0)
            self.assertEqual(b._draft_field_weights, a._draft_field_weights)
            for index, (name, image) in enumerate(b.draft_named_heads):
                self.assertEqual(name, a.draft_selected[index])
                np.testing.assert_array_equal(image, a.draft_heads[index])

    def test_changed_mode_or_corrupt_checkpoint_is_not_restored(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'state.npz'
            a = self.owner(path)
            a._save_draft_checkpoint('select')
            b = self.owner(path)
            b.config.draft_duel.draft_duel_config.match_mode = DraftMatchMode.MEMBER
            self.assertFalse(b._restore_draft_checkpoint())
            path.write_bytes(b'broken')
            self.assertFalse(a._restore_draft_checkpoint())
            a._clear_draft_checkpoint()
            self.assertFalse(path.exists())

    def test_restart_ignores_checkpoint_and_starts_from_lobby(self):
        owner = self.owner()
        def restore():
            owner.draft_selected = list('abcde')
        owner._restore_draft_checkpoint = Mock(side_effect=restore)
        owner._detect_draft_phase = Mock(return_value='rank')
        owner._enter_matchmaking = Mock(return_value=False)
        owner._select_shikigami = Mock()
        owner._match_battle = Mock(return_value=False)
        owner.set_next_run = Mock()
        with self.assertRaises(TaskEnd):
            owner.run()
        owner._restore_draft_checkpoint.assert_not_called()
        owner._detect_draft_phase.assert_not_called()
        owner._enter_matchmaking.assert_called_once()
        owner._select_shikigami.assert_not_called()
        owner._match_battle.assert_not_called()
        self.assertEqual(owner.draft_selected, [])

    def test_countdown_spike_and_small_increase_do_not_advance(self):
        owner = self.owner()
        owner._draft_deploy_time = None
        owner._draft_deploy_epoch = 0
        owner.O_FIGHT_WAIT_TIME = Mock()
        owner.O_FIGHT_WAIT_TIME.ocr.side_effect = [18, 78, 18, 19, 18, 30, 29, 28]
        epochs = []
        for _ in range(8):
            owner._update_deployment_clock()
            epochs.append(owner._draft_deploy_epoch)
        self.assertEqual(epochs, [0, 0, 0, 0, 0, 0, 1, 1])

    def test_restored_three_confirmed_picks_are_not_deployed_again(self):
        owner = self.owner()
        owner.draft_fielded = ('a', 'b', 'c')
        owner.screenshot = Mock()
        self.assertTrue(owner._deploy_shikigami())
        self.assertEqual(owner.draft_fielded, ('a', 'b', 'c'))
        owner.screenshot.assert_not_called()
        owner.device.press_and_drag.assert_not_called()

    def test_resume_can_confirm_reset_missed_during_pause(self):
        owner = self.owner()
        owner._draft_deploy_time = 29
        owner._draft_deploy_clock_wall = 100
        owner._draft_deploy_epoch = 0
        owner.O_FIGHT_WAIT_TIME = Mock()
        owner.O_FIGHT_WAIT_TIME.ocr.side_effect = [28, 27]
        with patch('tasks.DraftDuel.runtime.deployment.time.time', return_value=130):
            owner._update_deployment_clock()
            self.assertEqual(owner._draft_deploy_epoch, 0)
            owner._update_deployment_clock()
            self.assertEqual(owner._draft_deploy_epoch, 1)

    def test_battle_entry_keeps_verified_preview_without_guessing(self):
        for stable in (1, 2):
            owner = self.owner()
            owner.draft_fielded = ('a', 'b')
            owner.draft_deployment_rounds = [
                {'round': 1, 'slot': 1, 'name': 'a'}, {'round': 2, 'slot': 2, 'name': 'b'}]
            pending = {'index': 2, 'clicked': True, 'name': 'wrong-target',
                       'preview': 'c', 'preview_stable': stable}
            owner._finish_deployment_on_battle(pending, (0, 1, 2))
            self.assertEqual(owner.draft_fielded, ('a', 'b', 'c') if stable == 2 else ('a', 'b'))
            self.assertEqual(owner.draft_unresolved_slots, [] if stable == 2 else [3])


if __name__ == '__main__':
    unittest.main()
