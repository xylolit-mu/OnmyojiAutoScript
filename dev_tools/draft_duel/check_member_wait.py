"""队员接受邀请、等待开战及 600 秒结束规则；不操作游戏。"""

from types import SimpleNamespace
import unittest
from datetime import datetime as ScheduleDateTime, timezone as ScheduleTimezone, timedelta as ScheduleDelta
from unittest.mock import Mock, patch

from module.exception import TaskEnd
from tasks.DraftDuel.config import DraftDuel
from tasks.DraftDuel.script_task import ScriptTask
from tasks.GameUi.page import page_town


class MemberWaitTests(unittest.TestCase):
    def setUp(self):
        clock = patch('module.draft_duel_schedule.draft_duel_now', return_value=ScheduleDateTime(2026, 11, 1, 12, tzinfo=ScheduleTimezone(ScheduleDelta(hours=8))))
        clock.start()
        self.addCleanup(clock.stop)

    def owner(self):
        owner = object.__new__(ScriptTask)
        owner.config = SimpleNamespace(
            draft_duel=DraftDuel(draft_duel_config={'match_mode': '队员'}),
            notifier=Mock(),
        )
        owner.device = Mock()
        owner.screenshot = Mock()
        owner.appear = Mock(return_value=False)
        owner.appear_accept = Mock(return_value=False)
        owner._member_match_started = Mock(return_value=False)
        owner.check_then_accept = Mock(return_value=True)
        owner.goto_page = Mock()
        owner.set_next_run = Mock()
        return owner

    def test_accepts_via_common_invitation_then_waits_for_captain(self):
        owner = self.owner()
        owner.config.draft_duel.invite_config.default_invite = False
        owner._member_match_started.side_effect = [False, False, True]
        owner.appear.side_effect = [False, True]
        owner.appear_accept.side_effect = [True, False]
        with patch('tasks.DraftDuel.runtime.invitation.time.monotonic', side_effect=[0, 1, 2]), \
                patch('tasks.DraftDuel.runtime.invitation.time.sleep'):
            self.assertTrue(owner._wait_member_start())
        owner.check_then_accept.assert_called_once_with(timeout=5, default_invite=False)
        self.assertEqual(owner.screenshot.call_count, 3)
        owner.device.click.assert_not_called()
        owner.goto_page.assert_not_called()
        owner.config.notifier.push.assert_not_called()

    def test_wait_timeout_starts_on_first_wait_marker_and_is_not_reset(self):
        owner = self.owner()
        owner.appear.return_value = True
        owner._finish_member_wait_timeout = Mock(side_effect=TaskEnd)
        # Wait marker first appears at 400: deadline is 1000, not 600 or 1599.
        with patch('tasks.DraftDuel.runtime.invitation.time.monotonic', side_effect=[0, 400, 999, 1000]), \
                patch('tasks.DraftDuel.runtime.invitation.time.sleep'):
            with self.assertRaises(TaskEnd):
                owner._wait_member_start()
        self.assertEqual(owner.screenshot.call_count, 3)
        self.assertEqual(owner.device.stuck_record_clear.call_count, 3)
        owner._finish_member_wait_timeout.assert_called_once()

    def test_missing_wait_marker_is_not_automatically_a_battle_start(self):
        owner = self.owner()
        owner.appear.side_effect = [True, False, False]
        owner._finish_member_wait_timeout = Mock(side_effect=TaskEnd)
        with patch('tasks.DraftDuel.runtime.invitation.time.monotonic', side_effect=[0, 10, 609, 610]), \
                patch('tasks.DraftDuel.runtime.invitation.time.sleep'):
            with self.assertRaises(TaskEnd):
                owner._wait_member_start()
        owner._finish_member_wait_timeout.assert_called_once()
        self.assertEqual(owner.screenshot.call_count, 3)

    def test_no_invitation_also_has_a_600_second_bound(self):
        owner = self.owner()
        owner._finish_member_wait_timeout = Mock(side_effect=TaskEnd)
        with patch('tasks.DraftDuel.runtime.invitation.time.monotonic', side_effect=[0, 600]), \
                patch('tasks.DraftDuel.runtime.invitation.time.sleep'):
            with self.assertRaises(TaskEnd):
                owner._wait_member_start()
        owner._finish_member_wait_timeout.assert_called_once()
        owner.check_then_accept.assert_not_called()

    def test_already_started_does_not_wait_or_return_to_town(self):
        owner = self.owner()
        owner._member_match_started.return_value = True
        with patch('tasks.DraftDuel.runtime.invitation.time.monotonic', return_value=0):
            self.assertTrue(owner._wait_member_start())
        owner.goto_page.assert_not_called()
        owner.check_then_accept.assert_not_called()

    def test_lobby_roster_alone_does_not_count_as_captain_start(self):
        owner = self.owner()
        # 旧“我的阵容”资源已被用户替换，保留无关大厅标志的回归场景。
        owner.I_POST_DRAFT_ROSTER = object()
        owner.appear.side_effect = lambda rule: rule is owner.I_POST_DRAFT_ROSTER
        self.assertFalse(ScriptTask._member_match_started(owner))

    def test_start_detection_uses_selection_buttons_and_prioritizes_wait_state(self):
        owner = self.owner()
        owner.appear.side_effect = lambda rule: rule is owner.I_SELECT_2
        self.assertTrue(ScriptTask._member_match_started(owner))
        owner.appear.side_effect = None
        owner.appear.return_value = True
        self.assertFalse(ScriptTask._member_match_started(owner))

    def test_member_room_confirmation_requires_wait_or_start_marker(self):
        owner = self.owner()
        self.assertFalse(owner.is_in_room(False))
        owner.appear.side_effect = lambda rule: rule is owner.I_LOBBY_TEAMMATE_WAIT
        self.assertTrue(owner.is_in_room(False))
        owner.appear.side_effect = None
        owner.appear.return_value = False
        owner._member_match_started.return_value = True
        self.assertTrue(owner.is_in_room(False))

    def test_timeout_returns_to_town_completes_and_announces_reason(self):
        owner = self.owner()
        with self.assertRaisesRegex(TaskEnd, '等待开始过长'):
            owner._finish_member_wait_timeout()
        owner.goto_page.assert_called_once_with(page_town)
        owner.set_next_run.assert_called_once_with(task='DraftDuel', finish=True, success=True)
        args = owner.config.notifier.push.call_args.kwargs
        self.assertEqual(args['title'], '协同对弈')
        self.assertIn('等待开始过长', args['content'])
        self.assertIn('600', args['content'])

    def test_run_does_not_override_completed_timeout_with_failure_delay(self):
        owner = self.owner()
        owner._enter_matchmaking = Mock(side_effect=owner._finish_member_wait_timeout)
        with self.assertRaisesRegex(TaskEnd, '等待开始过长'):
            owner.run()
        owner.set_next_run.assert_called_once_with(task='DraftDuel', finish=True, success=True)


if __name__ == '__main__':
    unittest.main()
