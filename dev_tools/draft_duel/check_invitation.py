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

    def test_only_captain_uses_continue_invite_without_random_click(self):
        for mode in (DraftMatchMode.CAPTAIN,):
            with self.subTest(mode=mode):
                owner = object.__new__(ScriptTask)
                owner.config = SimpleNamespace(draft_duel=SimpleNamespace(
                    draft_duel_config=SimpleNamespace(match_mode=mode)))
                owner.appear = Mock(side_effect=lambda rule: rule is owner.I_INVITE_CONTINUE)
                owner.appear_then_click = Mock(return_value=True)
                owner.click = Mock()
                owner._click_draft_settlement_continue()
                owner.appear_then_click.assert_called_once_with(owner.I_INVITE_CONTINUE, interval=30)
                owner.click.assert_not_called()

    def test_member_continue_button_is_not_overlay_and_does_not_click(self):
        owner = object.__new__(ScriptTask)
        owner.config = SimpleNamespace(draft_duel=SimpleNamespace(
            draft_duel_config=SimpleNamespace(match_mode=DraftMatchMode.MEMBER)))
        owner.appear = Mock(side_effect=lambda rule: rule is owner.I_INVITE_CONTINUE)
        owner.appear_then_click = Mock()
        owner.click = Mock()
        self.assertFalse(owner._dismiss_draft_lobby_overlay())
        owner.appear_then_click.assert_not_called()
        owner.click.assert_not_called()

    def test_member_with_persistent_continue_button_waits_and_accepts_invite(self):
        owner = object.__new__(ScriptTask)
        owner.config = SimpleNamespace(draft_duel=DraftDuel(draft_duel_config={'match_mode':'队员'}))
        owner.device = Mock()
        owner.screenshot = Mock()
        owner.click = Mock()
        owner.appear_then_click = Mock()
        owner.appear = Mock(side_effect=lambda rule: rule is owner.I_INVITE_CONTINUE)
        frames = [0]
        def started():
            frames[0] += 1
            return frames[0] > 20
        owner._member_match_started = Mock(side_effect=started)
        owner.appear_accept = Mock(side_effect=lambda: frames[0] == 20)
        owner.check_then_accept = Mock()
        with patch('tasks.DraftDuel.runtime.invitation.time.sleep'):
            self.assertTrue(owner._clear_draft_lobby_overlays())
            self.assertTrue(owner._wait_member_start())
        owner.check_then_accept.assert_called_once()
        owner.click.assert_not_called()
        owner.appear_then_click.assert_not_called()

    def test_captain_handles_continue_in_invite_flow_not_overlay_cleanup(self):
        owner = object.__new__(ScriptTask)
        owner.config = SimpleNamespace(draft_duel=DraftDuel(draft_duel_config={'match_mode':'队长'}))
        owner.device = Mock()
        owner.screenshot = Mock()
        owner._clear_draft_lobby_overlays = Mock(return_value=True)
        owner.appear = Mock(side_effect=lambda rule: rule is owner.I_INVITE_CONTINUE)
        owner.appear_then_click = Mock(return_value=True)
        owner.invite_friends = Mock()
        owner.click = Mock()
        self.assertTrue(owner._invite_teammate())
        owner.appear_then_click.assert_called_once_with(owner.I_INVITE_CONTINUE, interval=30)
        owner.invite_friends.assert_not_called()
        owner.click.assert_not_called()

    def test_continue_invite_obeys_thirty_second_cooldown(self):
        owner = object.__new__(ScriptTask)
        owner.config = SimpleNamespace(draft_duel=SimpleNamespace(
            draft_duel_config=SimpleNamespace(match_mode=DraftMatchMode.CAPTAIN)))
        owner.appear = Mock(return_value=True)
        owner.appear_then_click = Mock(return_value=True)
        owner.click = Mock()
        with patch('tasks.DraftDuel.runtime.battle.time.time', return_value=100):
            owner._click_draft_settlement_continue()
        with patch('tasks.DraftDuel.runtime.battle.time.time', return_value=129):
            owner._click_draft_settlement_continue()
        self.assertEqual(owner.appear_then_click.call_count, 1)
        with patch('tasks.DraftDuel.runtime.battle.time.time', return_value=130):
            owner._click_draft_settlement_continue()
        self.assertEqual(owner.appear_then_click.call_count, 2)
        owner.click.assert_not_called()

    def test_continue_disappeared_retries_original_invite_after_cooldown(self):
        owner = object.__new__(ScriptTask)
        owner.config = SimpleNamespace(draft_duel=DraftDuel(draft_duel_config={'match_mode':'队长'}))
        owner.device = Mock()
        owner.screenshot = Mock()
        owner._dismiss_draft_lobby_overlay = Mock(return_value=False)
        checks = [0]
        wall = [129]
        def in_room(_):
            checks[0] += 1
            wall[0] = 129 if checks[0] == 1 else 130
            return checks[0] >= 3
        owner.is_in_room = Mock(side_effect=in_room)
        owner._invite_teammate = Mock(return_value=True)
        owner._draft_last_invite_at = 100
        owner._draft_invite_retry_seconds = 30
        with patch('tasks.DraftDuel.runtime.invitation.time.time', side_effect=lambda: wall[0]), patch('tasks.DraftDuel.runtime.invitation.time.sleep'):
            self.assertTrue(owner._wait_for_teammate())
        owner._invite_teammate.assert_called_once()

    def test_recent_continue_invite_waits_for_join_before_opening_friend_list(self):
        owner = self.owner(DraftMatchMode.CAPTAIN)
        owner._draft_last_invite_at = 100
        owner._draft_invite_retry_seconds = 30
        with patch('tasks.DraftDuel.runtime.invitation.time.time', return_value=110):
            self.assertTrue(ScriptTask._enter_matchmaking(owner))
        owner._invite_teammate.assert_not_called()
        owner._wait_for_teammate.assert_called_once()

    def test_continue_invite_throttle_does_not_fall_through_to_random_click(self):
        owner = object.__new__(ScriptTask)
        owner.config = SimpleNamespace(draft_duel=SimpleNamespace(
            draft_duel_config=SimpleNamespace(match_mode=DraftMatchMode.CAPTAIN)))
        owner.appear = Mock(return_value=True)
        owner.appear_then_click = Mock(return_value=False)
        owner.click = Mock()
        owner._click_draft_settlement_continue()
        owner.click.assert_not_called()

    def test_solo_keeps_random_settlement_click(self):
        owner = object.__new__(ScriptTask)
        owner.config = SimpleNamespace(draft_duel=SimpleNamespace(
            draft_duel_config=SimpleNamespace(match_mode=DraftMatchMode.SOLO)))
        owner.appear = Mock(return_value=True)
        owner.appear_then_click = Mock()
        owner.click = Mock()
        owner._click_draft_settlement_continue()
        owner.appear_then_click.assert_not_called()
        owner.click.assert_called_once()

    def test_final_banner_and_reward_are_dismissed_before_lobby_is_accepted(self):
        from tasks.GlobalGame.assets import GlobalGameAssets
        from tasks.GameUi.assets import GameUiAssets
        for overlay in (DraftDuelAssets.I_FIGHT_RESULT, GlobalGameAssets.I_UI_REWARD):
            owner = object.__new__(ScriptTask)
            owner.device = Mock()
            owner.goto_page = Mock()
            owner.click = Mock()
            owner._observe_duel_lobby = Mock()
            page = [0]
            frames = [[overlay, GameUiAssets.I_DRAFT_DUEL], [GameUiAssets.I_DRAFT_DUEL],
                      [GameUiAssets.I_DRAFT_DUEL]]
            owner.appear = Mock(side_effect=lambda rule: rule in frames[page[0]])
            owner.screenshot = Mock(side_effect=lambda: page.__setitem__(0, page[0]+1))
            with patch('tasks.DraftDuel.runtime.invitation.time.sleep'):
                self.assertTrue(owner._goto_draft_lobby())
            owner.click.assert_called_once()
            self.assertEqual(owner.screenshot.call_count, 2)
            owner._observe_duel_lobby.assert_called_once()

    def test_member_wait_closes_late_banner_before_waiting_for_captain(self):
        owner = object.__new__(ScriptTask)
        owner.device = Mock()
        owner.click = Mock()
        owner.config = SimpleNamespace(draft_duel=DraftDuel(draft_duel_config={'match_mode': '队员'}))
        owner._member_match_started = Mock(side_effect=[False, True])
        owner.appear_accept = Mock(return_value=False)
        page = [-1]
        frames = [[DraftDuelAssets.I_FIGHT_RESULT], [DraftDuelAssets.I_LOBBY_TEAMMATE_WAIT], []]
        owner.screenshot = Mock(side_effect=lambda: page.__setitem__(0, page[0]+1))
        owner.appear = Mock(side_effect=lambda rule: rule in frames[page[0]])
        with patch('tasks.DraftDuel.runtime.invitation.time.sleep'):
            self.assertTrue(owner._wait_member_start())
        owner.click.assert_called_once()
        self.assertEqual(owner.screenshot.call_count, 3)

    def invite_owner(self):
        owner = object.__new__(ScriptTask)
        owner.device = Mock()
        owner.config = SimpleNamespace(draft_duel=DraftDuel())
        owner.screenshot = Mock()
        owner._clear_draft_lobby_overlays = Mock(return_value=True)
        owner._dismiss_draft_lobby_overlay = Mock(return_value=False)
        owner.appear_then_click = Mock(return_value=True)
        owner.invite_friends = Mock(return_value=True)
        owner.is_in_room = Mock(return_value=False)
        return owner

    def test_invite_retries_panel_open_and_sends_only_after_panel_appears(self):
        owner = self.invite_owner()
        owner.appear = Mock(side_effect=lambda rule: (
            owner.appear_then_click.call_count >= 2 if rule is owner.I_INVITE_ENSURE
            else rule is owner.I_EMPTY_TEAMMATE))
        with patch('tasks.DraftDuel.runtime.invitation.time.monotonic', side_effect=range(100)), \
                patch('tasks.DraftDuel.runtime.invitation.time.sleep'):
            self.assertTrue(owner._invite_teammate())
        self.assertEqual(owner.appear_then_click.call_count, 2)
        self.assertEqual(owner._clear_draft_lobby_overlays.call_count, 2)
        owner.invite_friends.assert_called_once_with(owner.config.draft_duel.invite_config, open_invite=False)

    def test_invite_stops_after_three_failed_panel_attempts(self):
        owner = self.invite_owner()
        owner.appear = Mock(side_effect=lambda rule: rule is owner.I_EMPTY_TEAMMATE)
        with patch('tasks.DraftDuel.runtime.invitation.time.monotonic', side_effect=range(100)), \
                patch('tasks.DraftDuel.runtime.invitation.time.sleep'):
            self.assertFalse(owner._invite_teammate())
        self.assertEqual(owner.appear_then_click.call_count, 3)
        self.assertEqual(owner._clear_draft_lobby_overlays.call_count, 3)
        owner.invite_friends.assert_not_called()

    def test_clear_overlays_restarts_confirmation_if_another_page_appears(self):
        owner = object.__new__(ScriptTask)
        owner.device = Mock()
        owner.screenshot = Mock()
        owner._dismiss_draft_lobby_overlay = Mock(side_effect=[True, False, True, False, False])
        with patch('tasks.DraftDuel.runtime.invitation.time.sleep'):
            self.assertTrue(owner._clear_draft_lobby_overlays())
        self.assertEqual(owner.screenshot.call_count, 4)

    def test_invite_never_clicks_entry_when_overlay_cleanup_fails(self):
        owner = self.invite_owner()
        owner._clear_draft_lobby_overlays.return_value = False
        self.assertFalse(owner._invite_teammate())
        self.assertEqual(owner._clear_draft_lobby_overlays.call_count, 3)
        owner.appear_then_click.assert_not_called()
        owner.invite_friends.assert_not_called()

    def owner(self, mode):
        owner = Mock()
        owner.config.draft_duel = DraftDuel(draft_duel_config={'match_mode': mode})
        owner.I_EMPTY_TEAMMATE = DraftDuelAssets.I_EMPTY_TEAMMATE
        owner.I_LOBBY_BATTLE = DraftDuelAssets.I_LOBBY_BATTLE
        owner.I_INVITE_ENSURE = GeneralInviteAssets.I_INVITE_ENSURE
        owner._goto_draft_lobby.return_value = True
        owner._clear_draft_lobby_overlays.return_value = True
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
                owner._clear_draft_lobby_overlays = Mock(return_value=True)
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
        owner._goto_draft_lobby.return_value = False
        self.assertTrue(ScriptTask._enter_matchmaking(owner))
        owner._goto_draft_lobby.assert_not_called()
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
