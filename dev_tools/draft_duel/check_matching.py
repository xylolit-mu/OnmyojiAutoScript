"""本局头像缓存、取消确认、三次上限与离队的离线检查。"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.atom.frame import FrameResult
from module.exception import TaskEnd
from tasks.DraftDuel.config import DraftDuel
from tasks.DraftDuel.script_task import ScriptTask
from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
from tasks.GameUi.assets import GameUiAssets


class RankMatchingTests(unittest.TestCase):
    def owner(self, mode='队长'):
        owner = object.__new__(ScriptTask)
        owner.config = SimpleNamespace(draft_duel=DraftDuel(
            draft_duel_config={'match_mode': mode, 'match_wait_seconds': 2}), notifier=Mock())
        owner.device = Mock()
        owner.draft_heads = ()
        owner.draft_selected = ['莹草', '鬼童丸', '茨木童子', '椒图', '辉夜姬']
        owner.set_next_run = Mock()
        owner._observe_victory_count = Mock()
        return owner

    def test_first_rank_exchanges_roster_then_plans_before_start(self):
        owner = self.owner()
        events = []
        owner._observe_duel_lobby = Mock()
        owner._save_draft_checkpoint = Mock()
        owner._capture_draft_heads = Mock(side_effect=lambda: events.append('heads') or True)
        owner._sync_draft_state = Mock(side_effect=lambda **kw: events.append('exchange') or True)
        owner._deployment_plan = Mock(side_effect=lambda: events.append('plan') or ('莹草', '椒图', '辉夜姬'))
        page = {'index': -1}
        frames = [[owner.I_POST_DRAFT_RANK, owner.I_LOBBY_BATTLE], [owner.I_POST_DRAFT_FIGHT]]
        owner.screenshot = Mock(side_effect=lambda: page.update(index=page['index'] + 1))
        owner.appear = Mock(side_effect=lambda rule: rule in frames[page['index']])
        owner.appear_then_click = Mock(side_effect=lambda *a, **kw: events.append('start') or True)
        with patch('tasks.DraftDuel.runtime.matchmaking.time.sleep'):
            self.assertTrue(owner._match_battle())
        self.assertEqual(events, ['exchange', 'plan', 'start'])
        owner._capture_draft_heads.assert_not_called()
        self.assertEqual(owner.draft_planned_lineup, ('莹草', '椒图', '辉夜姬'))

    def test_after_first_battle_auto_is_confirmed_before_matching(self):
        owner = self.owner()
        owner.draft_finished_small_games = 1
        owner.draft_small_game = 1
        owner._capture_draft_heads = Mock(return_value=True)
        owner._observe_duel_lobby = Mock()
        owner._save_draft_checkpoint = Mock()
        page = {'index': -1}
        frames = [
            [owner.I_POST_DRAFT_RANK, owner.I_AUTO_DOWN, owner.I_LOBBY_BATTLE],
            [owner.I_POST_DRAFT_RANK, owner.I_AUTO_RIGHT, owner.I_LOBBY_BATTLE],
            [owner.I_POST_DRAFT_FIGHT],
        ]
        owner.screenshot = Mock(side_effect=lambda: page.update(index=page['index'] + 1))
        owner.appear = Mock(side_effect=lambda rule: rule in frames[page['index']])
        clicks = []
        owner.appear_then_click = Mock(side_effect=lambda rule, **kw: clicks.append((page['index'], rule)))
        with patch('tasks.DraftDuel.runtime.matchmaking.time.sleep'):
            self.assertTrue(owner._match_battle())
        self.assertEqual(clicks, [(0, owner.I_AUTO_DOWN), (1, owner.I_LOBBY_BATTLE)])
        self.assertEqual(owner.draft_small_game, 2)

    def test_head_copies_are_cached_only_for_this_game(self):
        owner = self.owner()
        heads = [np.ones((4, 4, 3), dtype=np.uint8) for _ in range(5)]
        owner.runframe = Mock(return_value=FrameResult(True, heads))
        self.assertTrue(owner._capture_draft_heads())
        heads[0][:] = 0
        self.assertTrue(owner.draft_heads[0].all())
        self.assertEqual([name for name, _ in owner.draft_named_heads], owner.draft_selected)
        self.assertIs(owner.draft_named_heads[0][1], owner.draft_heads[0])
        self.assertTrue(owner._capture_draft_heads())
        owner.runframe.assert_called_once_with(owner.F_SHIKIGAMI_HEAD)

    def test_incomplete_frame_is_not_saved(self):
        owner = self.owner()
        owner.runframe = Mock(return_value=FrameResult(False))
        self.assertFalse(owner._capture_draft_heads())
        self.assertEqual(owner.draft_heads, ())

    def test_missing_names_cannot_bind_heads(self):
        owner = self.owner()
        owner.draft_selected.pop()
        owner.runframe = Mock()
        self.assertFalse(owner._capture_draft_heads())
        owner.runframe.assert_not_called()

    def matching(self, mode, frames):
        owner = self.owner(mode)
        current = [-1]
        owner.screenshot = Mock(side_effect=lambda: current.__setitem__(0, current[0] + 1))
        owner.appear = Mock(side_effect=lambda rule: rule in frames[current[0]])
        owner.appear_then_click = Mock(side_effect=lambda rule, **kw: rule in frames[current[0]])
        owner._capture_draft_heads = Mock(return_value=True)
        owner._finish_match_timeout = Mock(side_effect=TaskEnd('three attempts'))
        owner._leave_draft_team = Mock(side_effect=TaskEnd('leader left'))
        tick = iter(range(1000))
        with patch('tasks.DraftDuel.runtime.matchmaking.time.sleep'), patch(
                'tasks.DraftDuel.runtime.matchmaking.time.monotonic', side_effect=lambda: next(tick)):
            try:
                result = owner._match_battle()
            except TaskEnd:
                result = 'finished'
        return owner, result

    def test_three_attempts_cancel_before_retry(self):
        a = ScriptTask
        rank = [a.I_POST_DRAFT_RANK, a.I_LOBBY_BATTLE]
        wait = [a.I_FIGHT_WAIT, a.I_FIGHT_CANCEL_WAIT_LEADER]
        owner, result = self.matching('队长', [rank, wait, wait, wait, rank] * 3 + [rank])
        self.assertEqual(result, 'finished')
        buttons = [c.args[0] for c in owner.appear_then_click.call_args_list]
        self.assertEqual(buttons.count(a.I_LOBBY_BATTLE), 3)
        # 取消动画期间允许受 interval 节流的重试，但不能额外发起匹配。
        self.assertGreaterEqual(buttons.count(a.I_FIGHT_CANCEL_WAIT_LEADER), 3)
        owner._finish_match_timeout.assert_called_once()

    def test_battle_button_retries_every_three_seconds_for_thirty_seconds(self):
        a = ScriptTask
        rank = [a.I_POST_DRAFT_RANK, a.I_LOBBY_BATTLE]
        owner, result = self.matching('队长', [rank] * 32)
        self.assertFalse(result)
        clicks = [c for c in owner.appear_then_click.call_args_list if c.args[0] is a.I_LOBBY_BATTLE]
        self.assertEqual(len(clicks), 10)
        self.assertTrue(all(c.kwargs['interval'] == 3 for c in clicks))
        owner._finish_match_timeout.assert_not_called()

    def test_button_retry_is_not_a_new_matching_attempt(self):
        a = ScriptTask
        rank = [a.I_POST_DRAFT_RANK, a.I_LOBBY_BATTLE]
        owner, result = self.matching('队长', [rank] * 8 + [
            [a.I_FIGHT_WAIT], [a.I_POST_DRAFT_FIGHT]])
        self.assertTrue(result)
        self.assertEqual(owner.draft_small_game, 1)
        self.assertEqual(owner.appear_then_click.call_count, 3)

    def test_member_does_not_click_match_or_cancel(self):
        a = ScriptTask
        owner, result = self.matching('队员', [
            [a.I_POST_DRAFT_RANK], [a.I_FIGHT_WAIT], [GeneralBattleAssets.I_PREPARE_DARK]])
        self.assertTrue(result)
        owner.appear_then_click.assert_not_called()

    def test_member_leaves_when_leader_left_even_with_wait_or_exit_menu(self):
        a = ScriptTask
        owner, result = self.matching('队员', [[
            a.I_LEADER_EXIT, a.I_FIGHT_WAIT, a.I_EXIT_CANCEL]])
        self.assertEqual(result, 'finished')
        owner._leave_draft_team.assert_called_once_with('队长已离队')
        owner.appear_then_click.assert_not_called()

    def test_leader_does_not_use_member_leave_detection(self):
        owner, result = self.matching('队长', [[
            ScriptTask.I_LEADER_EXIT, GeneralBattleAssets.I_BATTLE_INFO]])
        self.assertTrue(result)
        owner._leave_draft_team.assert_not_called()

    def test_accidental_exit_cancel_then_battle(self):
        owner, result = self.matching('队长', [
            [ScriptTask.I_EXIT_CANCEL], [GeneralBattleAssets.I_BATTLE_INFO]])
        self.assertTrue(result)
        owner.appear_then_click.assert_called_once_with(owner.I_EXIT_CANCEL, interval=1)

    def test_exhaustion_chooses_leave_and_completes(self):
        owner = self.owner()
        frame = [-1]
        states = [[], [owner.I_EXIT, owner.I_EXIT_MOMENT, owner.I_EXIT_CANCEL],
                  [GameUiAssets.I_CHECK_TOWN]]
        owner.screenshot = Mock(side_effect=lambda: frame.__setitem__(0, frame[0] + 1))
        owner.appear = Mock(side_effect=lambda rule: rule in states[frame[0]])
        owner.appear_then_click = Mock(side_effect=lambda rule, **kw: rule in states[frame[0]])
        with patch('tasks.DraftDuel.runtime.matchmaking.time.sleep'):
            with self.assertRaises(TaskEnd):
                owner._finish_match_timeout()
        clicked = [c.args[0] for c in owner.appear_then_click.call_args_list]
        self.assertNotIn(owner.I_EXIT_CANCEL, clicked)
        self.assertNotIn(owner.I_EXIT_MOMENT, clicked)
        owner.set_next_run.assert_called_once_with(task='DraftDuel', finish=True, success=True)


if __name__ == '__main__':
    unittest.main()
