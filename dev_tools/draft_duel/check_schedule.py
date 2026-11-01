"""开放时间边界和双脚本跳过不唤醒检查；只使用临时数据库。"""
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.cooperation import Cooperation, prepare_task_cooperation
from module.draft_duel_schedule import CHINA_TIME, skip_draft_duel_outside_hours
from module.exception import TaskEnd
from tasks.DraftDuel.config import DraftDuel
from tasks.DraftDuel.script_task import ScriptTask


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.a = Cooperation('A', Path(folder.name))
        self.b = Cooperation('B', Path(folder.name))

    def config(self, name, peer='', mode='队长'):
        item = DraftDuel(draft_duel_config={'match_mode': mode},
                         invite_config={'cooperation_script': peer})
        return SimpleNamespace(config_name=name, model=SimpleNamespace(draft_duel=item),
                               cooperation_runtime=self.a if name == 'A' else self.b,
                               task_delay=Mock(), task_call=Mock())

    def clock(self, hour, minute=0, second=0):
        return patch('module.draft_duel_schedule.draft_duel_now',
                     return_value=datetime(2026, 11, 1, hour, minute, second, tzinfo=CHINA_TIME))

    def test_boundaries_and_next_opening(self):
        for hour, minute, second, skipped, day in (
                (7, 59, 59, True, 1), (8, 0, 0, False, 1),
                (22, 59, 59, False, 1), (23, 0, 0, True, 2), (0, 0, 0, True, 1)):
            config = self.config('A', mode='单人')
            with self.clock(hour, minute, second):
                self.assertEqual(skip_draft_duel_outside_hours(config), skipped)
            if skipped:
                expected = datetime(2026, 11, day, 8, tzinfo=CHINA_TIME).astimezone().replace(tzinfo=None)
                config.task_delay.assert_called_once_with(task='DraftDuel', target=expected, server=False)
                self.assertEqual(config.task_runtime_outcome['status'], 'time_window_skipped')
            else:
                config.task_delay.assert_not_called()

    def test_live_peer_notified_without_ping_pong_or_wake(self):
        self.a.publish('DraftDuel')
        self.b.publish('DraftDuel')
        a, b = self.config('A', 'B'), self.config('B', 'A', '队员')
        for _ in range(3):
            for config in (a, b):
                with self.clock(23), self.assertRaises(TaskEnd):
                    prepare_task_cooperation(config, 'DraftDuel')
            self.assertEqual(self.a.requested(), [])
            self.assertEqual(self.b.requested(), [])
            with self.clock(23):
                self.assertIsNone(self.a.wake_next(a))
                self.assertIsNone(self.b.wake_next(b))
        self.assertIn('跳过', self.b.peer_task_completion('DraftDuel', 'A', 0))
        a.task_call.assert_not_called()
        b.task_call.assert_not_called()

    def test_offline_and_stale_peer_neither_notified_nor_woken(self):
        for stale in (False, True):
            if stale:
                self.b.publish('')
                with self.b.connect() as db:
                    db.execute('UPDATE runners SET heartbeat=heartbeat-31 WHERE name=?', ('B',))
            with self.clock(23), self.assertRaises(TaskEnd):
                prepare_task_cooperation(self.config('A', 'B'), 'DraftDuel')
            self.assertIsNone(self.b.peer_task_completion('DraftDuel', 'A', 0))
            self.assertEqual(self.b.requested(), [])

    def test_old_incoming_wake_cleared_without_task_call(self):
        config = self.config('B', 'A', '队员')
        with self.a.connect() as db:
            db.execute('INSERT INTO wakes VALUES (?, ?, ?, ?)', ('B', 'DraftDuel', 'A', 0))
        with self.clock(23):
            self.assertIsNone(self.b.wake_next(config))
        config.task_call.assert_not_called()
        config.task_delay.assert_called_once()
        self.assertEqual(self.a.requested(), [])
        self.assertEqual(self.b.requested(), [])

    def test_direct_entry_skips_before_state_or_device_work(self):
        owner = object.__new__(ScriptTask)
        owner.config = self.config('A', mode='单人')
        owner.device = Mock()
        owner._initialize_duel_state = Mock()
        with self.clock(7), self.assertRaises(TaskEnd):
            owner.run()
        owner._initialize_duel_state.assert_not_called()
        self.assertEqual(owner.device.mock_calls, [])

    def test_open_hours_preserves_cooperation_preparation(self):
        config = self.config('A', 'B')
        with self.clock(8), patch.object(self.a, 'prepare') as prepare:
            prepare_task_cooperation(config, 'DraftDuel')
        prepare.assert_called_once_with(config, 'DraftDuel', 'B')
        config.task_delay.assert_not_called()
