"""协斗进入匹配阶段的离线检查，不操作游戏。"""

import unittest
from datetime import datetime as ScheduleDateTime, timezone as ScheduleTimezone, timedelta as ScheduleDelta
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from module.exception import TaskEnd
from tasks.DraftDuel.assets import DraftDuelAssets
from tasks.DraftDuel.config import DraftDuel, DraftMatchMode
from tasks.DraftDuel.script_task import ScriptTask
from tasks.Component.GeneralInvite.assets import GeneralInviteAssets
from tasks.GameUi.page import page_town


class MatchmakingTests(unittest.TestCase):
    def setUp(self):
        clock = patch('module.draft_duel_schedule.draft_duel_now', return_value=ScheduleDateTime(2026, 11, 1, 12, tzinfo=ScheduleTimezone(ScheduleDelta(hours=8))))
        clock.start()
        self.addCleanup(clock.stop)

    def owner(self, mode):
        owner = Mock()
        owner.config.draft_duel = DraftDuel(draft_duel_config={'match_mode': mode})
        owner.I_EMPTY_TEAMMATE = DraftDuelAssets.I_EMPTY_TEAMMATE
        owner.I_LOBBY_BATTLE = DraftDuelAssets.I_LOBBY_BATTLE
        owner.I_INVITE_ENSURE = GeneralInviteAssets.I_INVITE_ENSURE
        owner._goto_draft_lobby.return_value = True
        owner._in_draft_lobby.return_value = True
        owner.appear.return_value = True
        owner.appear_accept.return_value = False
        owner.is_in_room.return_value = True
        owner._invite_teammate.return_value = True
        owner._wait_for_teammate.return_value = True
        owner._wait_member_start.return_value = True
        owner.wait_until_appear.return_value = True
        owner.appear_then_click.return_value = True
        return owner

    def test_solo_starts_without_invitation(self):
        owner = self.owner(DraftMatchMode.SOLO)
        self.assertTrue(ScriptTask._enter_matchmaking(owner))
        owner._goto_draft_lobby.assert_called_once()
        owner._invite_teammate.assert_not_called()
        owner.prepare_battle_cooperation.assert_not_called()
        owner.appear_then_click.assert_called_once_with(owner.I_LOBBY_BATTLE, interval=1)

    def test_captain_invites_and_waits_before_starting(self):
        owner = self.owner(DraftMatchMode.CAPTAIN)
        self.assertTrue(ScriptTask._enter_matchmaking(owner))
        owner.prepare_battle_cooperation.assert_called_once_with('DraftDuel')
        owner._invite_teammate.assert_called_once()
        owner._wait_for_teammate.assert_called_once()
        calls = owner.mock_calls
        self.assertLess(calls.index(call._invite_teammate()), calls.index(call._wait_for_teammate()))
        self.assertLess(calls.index(call._wait_for_teammate()),
                        calls.index(call.appear_then_click(owner.I_LOBBY_BATTLE, interval=1)))

    def test_captain_does_not_start_on_failed_invitation_or_wait_timeout(self):
        for invite_ok, joined in ((False, True), (True, False)):
            with self.subTest(invite_ok=invite_ok, joined=joined):
                owner = self.owner(DraftMatchMode.CAPTAIN)
                owner._invite_teammate.return_value = invite_ok
                owner._wait_for_teammate.return_value = joined
                self.assertFalse(ScriptTask._enter_matchmaking(owner))
                owner.appear_then_click.assert_not_called()
                if invite_ok:
                    owner._finish_invite_wait_timeout.assert_called_once()
                else:
                    owner._finish_invite_wait_timeout.assert_not_called()

    def test_captain_timeout_exits_announces_and_completes_without_failure_override(self):
        for seconds in (3, 90):
            with self.subTest(seconds=seconds):
                owner = object.__new__(ScriptTask)
                owner._detect_draft_phase = Mock(return_value='lobby')
                owner.config = SimpleNamespace(
                    draft_duel=DraftDuel(
                        draft_duel_config={'match_mode': '队长'},
                        invite_config={'wait_time': f'00:{seconds // 60:02d}:{seconds % 60:02d}'},
                    ),
                    notifier=Mock(),
                )
                owner.device = Mock()
                owner.screenshot = Mock()
                owner._goto_draft_lobby = Mock(return_value=True)
                owner.prepare_battle_cooperation = Mock()
                owner.appear = Mock(return_value=True)
                owner._invite_teammate = Mock(return_value=True)
                owner.is_in_room = Mock(return_value=False)
                owner.appear_then_click = Mock()
                owner.goto_page = Mock()
                owner.set_next_run = Mock()
                with patch('tasks.DraftDuel.runtime.invitation.time.monotonic', side_effect=[0, seconds]), \
                        patch('tasks.DraftDuel.runtime.invitation.time.sleep'):
                    with self.assertRaisesRegex(TaskEnd, '等待邀请过久'):
                        owner.run()
                owner._invite_teammate.assert_called_once()
                owner.appear_then_click.assert_not_called()
                owner.goto_page.assert_called_once_with(page_town)
                owner.set_next_run.assert_called_once_with(task='DraftDuel', finish=True, success=True)
                owner.config.notifier.push.assert_called_once()
                content = owner.config.notifier.push.call_args.kwargs['content']
                self.assertIn('等待邀请过久', content)
                self.assertIn(f'{seconds}秒内队友未接受邀请', content)

    def test_already_joined_captain_does_not_open_another_invitation(self):
        owner = self.owner(DraftMatchMode.CAPTAIN)
        owner.appear.return_value = False
        self.assertTrue(ScriptTask._enter_matchmaking(owner))
        owner._invite_teammate.assert_not_called()
        owner.appear_then_click.assert_called_once_with(owner.I_LOBBY_BATTLE, interval=1)

    def test_teammate_leaving_before_button_click_blocks_start(self):
        owner = self.owner(DraftMatchMode.CAPTAIN)
        owner.is_in_room.return_value = False
        self.assertFalse(ScriptTask._enter_matchmaking(owner))
        owner.appear_then_click.assert_not_called()

    def test_member_does_not_match_with_a_stranger(self):
        owner = self.owner(DraftMatchMode.MEMBER)
        self.assertTrue(ScriptTask._enter_matchmaking(owner))
        owner._wait_member_start.assert_called_once()
        owner._invite_teammate.assert_not_called()
        owner.appear_then_click.assert_not_called()

    def test_failed_navigation_or_missing_button_does_not_click(self):
        for failed in ('_goto_draft_lobby', 'wait_until_appear', '_in_draft_lobby'):
            with self.subTest(failed=failed):
                owner = self.owner(DraftMatchMode.SOLO)
                getattr(owner, failed).return_value = False
                self.assertFalse(ScriptTask._enter_matchmaking(owner))
                owner.appear_then_click.assert_not_called()

    def test_rejected_battle_click_is_not_reported_as_started(self):
        owner = self.owner(DraftMatchMode.SOLO)
        owner.appear_then_click.return_value = False
        self.assertFalse(ScriptTask._enter_matchmaking(owner))

    def test_wait_refreshes_until_teammate_joins(self):
        owner = self.owner(DraftMatchMode.CAPTAIN)
        owner.is_in_room.side_effect = [False, True]
        with patch('tasks.DraftDuel.runtime.invitation.time.monotonic', side_effect=[0, 1]), \
                patch('tasks.DraftDuel.runtime.invitation.time.sleep'):
            self.assertTrue(ScriptTask._wait_for_teammate(owner))
        self.assertEqual(owner.screenshot.call_count, 2)
        self.assertEqual(owner.device.stuck_record_clear.call_count, 2)
        owner.appear_then_click.assert_not_called()

    def test_wait_uses_standard_invitation_timeout(self):
        owner = self.owner(DraftMatchMode.CAPTAIN)
        owner.config.draft_duel = DraftDuel(invite_config={'wait_time': '00:00:03'})
        owner.is_in_room.return_value = False
        with patch('tasks.DraftDuel.runtime.invitation.time.monotonic', side_effect=[10, 13]), \
                patch('tasks.DraftDuel.runtime.invitation.time.sleep') as sleep:
            self.assertFalse(ScriptTask._wait_for_teammate(owner))
        sleep.assert_not_called()

    def test_invitation_panel_hiding_empty_slot_is_not_a_joined_teammate(self):
        owner = self.owner(DraftMatchMode.CAPTAIN)
        owner.appear.side_effect = lambda rule: rule is owner.I_INVITE_ENSURE
        self.assertFalse(ScriptTask.is_in_room(owner, False))


if __name__ == '__main__':
    unittest.main()
