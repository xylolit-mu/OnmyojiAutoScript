"""战斗信息标志与小局结算衔接的离线检查。"""
import unittest
from datetime import datetime as ScheduleDateTime, timezone as ScheduleTimezone, timedelta as ScheduleDelta
from collections import deque
from contextlib import nullcontext
from unittest.mock import Mock, patch
from module.exception import TaskEnd, GameTooManyClickError
from module.device.device import Device
from tasks.DraftDuel.script_task import ScriptTask
from tasks.Component.GeneralBattle.assets import GeneralBattleAssets


class BattleSettlementTests(unittest.TestCase):
    def setUp(self):
        clock = patch('module.draft_duel_schedule.draft_duel_now', return_value=ScheduleDateTime(2026, 11, 1, 12, tzinfo=ScheduleTimezone(ScheduleDelta(hours=8))))
        clock.start()
        self.addCleanup(clock.stop)

    def play(self, states, advance=2, device=None, battle_seen=False):
        owner = object.__new__(ScriptTask)
        owner.device = device if device is not None else Mock()
        owner._draft_battle_seen = battle_seen
        owner.click = Mock(side_effect=lambda _: Device.handle_control_check(owner.device, 'SAFE_RANDOM_CLICK')) if device else Mock()
        owner._observe_duel_lobby = Mock()
        owner._sync_draft_state = Mock(return_value=True)
        owner._observe_victory_count = Mock()
        frame = [-1]
        owner.screenshot = Mock(side_effect=lambda: frame.__setitem__(0, frame[0] + 1))
        owner.appear = Mock(side_effect=lambda rule: rule in states[frame[0]])
        clock = (patch('tasks.DraftDuel.runtime.battle.time.monotonic',
                       side_effect=lambda: max(0, frame[0]) * advance) if advance else nullcontext())
        with patch('tasks.DraftDuel.runtime.battle.time.sleep'), clock:
            phase = owner._battle_and_settlement()
        return owner, phase

    def guarded_device(self):
        device = Mock()
        device.click_record = deque(maxlen=15)
        device.click_record_clear.side_effect = device.click_record.clear
        device.click_record_add.side_effect = lambda button: device.click_record.append(str(button))
        device.click_record_check.side_effect = lambda: Device.click_record_check(device)
        return device

    def test_normal_clicks_do_not_accumulate_across_eight_games(self):
        device = self.guarded_device()
        for _ in range(8):
            Device.handle_control_check(device, 'LOBBY_BATTLE')
            owner, phase = self.play([
                [GeneralBattleAssets.I_BATTLE_INFO], [], [],
                [ScriptTask.I_FIGHT_RESULT], [ScriptTask.I_FIGHT_RESULT],
                [ScriptTask.I_POST_DRAFT_RANK]], device=device, battle_seen=True)
            self.assertEqual(phase, 'rank')
        self.assertEqual(device.click_record_clear.call_count, 16)

    def test_repeated_clicks_on_stuck_result_still_trigger_guard(self):
        device = self.guarded_device()
        with self.assertRaises(GameTooManyClickError):
            self.play([[GeneralBattleAssets.I_BATTLE_INFO], [], []] +
                      [[ScriptTask.I_FIGHT_RESULT]] * 11, device=device)

    def test_active_battle_extends_every_300_seconds(self):
        battle = GeneralBattleAssets.I_BATTLE_INFO
        with patch('tasks.DraftDuel.runtime.battle.logger.info') as log:
            owner, phase = self.play([[battle]] * 8 + [[], [], [ScriptTask.I_POST_DRAFT_RANK]],
                                     advance=301)
        self.assertEqual(phase, 'rank')
        self.assertEqual(owner.draft_finished_small_games, 1)
        extensions = [c for c in log.call_args_list if 'extend another 300s' in str(c.args[0])]
        self.assertEqual(len(extensions), 7)
        owner.click.assert_not_called()

    def test_marker_must_appear_and_disappear_then_click_three_pages(self):
        a, battle = ScriptTask, GeneralBattleAssets.I_BATTLE_INFO
        owner, phase = self.play([[], [battle], [], [battle], [], [],
                                  [a.I_DRAFT_SHARE], [a.I_FIGHT_STATISTIC],
                                  [a.I_FIGHT_RESULT], [a.I_POST_DRAFT_RANK]])
        self.assertEqual(phase, 'rank')
        self.assertEqual(owner.draft_finished_small_games, 1)
        self.assertEqual(owner.click.call_count, 3)
        self.assertEqual(owner.screenshot.call_count, 10)
        owner._observe_duel_lobby.assert_not_called()

    def test_unknown_page_does_not_trigger_random_click(self):
        owner, phase = self.play([[GeneralBattleAssets.I_BATTLE_INFO], [], [], [],
                                  [ScriptTask.I_WAIT_FIGHT_LOBBY]])
        self.assertEqual(phase, 'lobby')
        owner.click.assert_not_called()
        owner._observe_duel_lobby.assert_called_once()

    def test_marker_returns_during_settlement_wait_without_duplicate_end(self):
        battle = GeneralBattleAssets.I_BATTLE_INFO
        owner, phase = self.play([[battle], [], [], [], [battle], [], [], [],
                                  [ScriptTask.I_POST_DRAFT_RANK]], advance=1)
        self.assertEqual(phase, 'rank')
        self.assertEqual(owner.draft_finished_small_games, 1)
        owner.click.assert_not_called()

    def test_absence_before_battle_is_not_completion(self):
        owner = object.__new__(ScriptTask)
        owner.device = Mock()
        owner.screenshot = Mock()
        owner.appear = Mock(return_value=False)
        with patch('tasks.DraftDuel.runtime.battle.time.monotonic', side_effect=[0, 1, 1801]), \
             patch('tasks.DraftDuel.runtime.battle.time.sleep'):
            self.assertIsNone(owner._battle_and_settlement())
        self.assertFalse(hasattr(owner, 'draft_finished_small_games'))

    def test_next_small_game_reuses_roster_without_reinviting(self):
        owner = Mock()
        owner._detect_draft_phase.return_value = 'lobby'
        owner.draft_selected = list('abcde')
        owner._enter_matchmaking.return_value = True
        owner._select_shikigami.return_value = True
        owner._match_battle.return_value = True
        owner._deploy_shikigami.return_value = True
        owner._battle_and_settlement.side_effect = ['rank', None]
        with self.assertRaises(TaskEnd):
            ScriptTask.run(owner)
        owner._enter_matchmaking.assert_called_once()
        owner._select_shikigami.assert_called_once()
        self.assertEqual(owner._match_battle.call_count, 2)
        self.assertEqual(owner._deploy_shikigami.call_count, 2)


if __name__ == '__main__':
    unittest.main()
