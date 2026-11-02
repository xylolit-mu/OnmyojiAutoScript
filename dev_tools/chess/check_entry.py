"""Offline checks for mandatory opening field selection."""
import unittest
from unittest.mock import Mock, patch
from types import MethodType
from module.exception import GameStuckError
from tasks.GameUi.chess_battle import ChessBattleNavigationMixin as Mixin
from tasks.Chess.assets import ChessAssets
from tasks.Chess.script_task import ChessLegacyScriptTask

class SixRealmsTests(unittest.TestCase):
    def task(self, frames):
        task=Mock()
        task.CHESS_EXIT_TIMEOUT=60
        task.CHESS_EXIT_SCREENSHOT_INTERVAL=0.35
        task.appear.side_effect=frames
        return task

    @patch('tasks.GameUi.chess_battle.time.sleep')
    def test_absent_does_nothing(self, sleep):
        task=self.task([False])
        self.assertFalse(Mixin.select_chess_sixrealms_if_visible(task))
        task.click.assert_not_called()

    @patch('tasks.GameUi.chess_battle.time.sleep')
    @patch('tasks.GameUi.chess_battle.random.choice', return_value=ChessAssets.C_SIXREALMS_2)
    @patch('tasks.GameUi.chess_battle.random.randint', return_value=2)
    def test_two_clicks_same_field_and_wait_until_gone(self, count, choice, sleep):
        task=self.task([True,True,True,True,False])
        self.assertTrue(Mixin.select_chess_sixrealms_if_visible(task))
        self.assertEqual(task.click.call_count,2)
        self.assertTrue(all(c.args==(ChessAssets.C_SIXREALMS_2,) for c in task.click.call_args_list))
        self.assertEqual(task.screenshot.call_count,3)

    @patch('tasks.GameUi.chess_battle.time.sleep')
    @patch('tasks.GameUi.chess_battle.random.randint', return_value=2)
    def test_disappears_after_first_click_no_second_click(self, count, sleep):
        task=self.task([True,True,False])
        self.assertTrue(Mixin.select_chess_sixrealms_if_visible(task))
        self.assertEqual(task.click.call_count,1)

    @patch('tasks.GameUi.chess_battle.time.sleep')
    @patch('tasks.GameUi.chess_battle.random.randint', return_value=1)
    @patch('tasks.GameUi.chess_battle.time.monotonic', side_effect=[0,0,1,61])
    def test_timeout_no_extra_clicks(self, clock, count, sleep):
        task=self.task([True,True,True])
        with self.assertRaises(GameStuckError):Mixin.select_chess_sixrealms_if_visible(task)
        self.assertEqual(task.click.call_count,1)

    @patch('tasks.GameUi.chess_battle.time.sleep')
    def test_exit_waits_for_selection(self, sleep):
        task=self.task([])
        task.select_chess_sixrealms_if_visible.side_effect=[True,False]
        task.appear_then_click.return_value=False
        task.chess_result_page_visible.return_value=True
        self.assertTrue(Mixin.Chess_Exit(task,return_to_lobby=False))
        events=[c[0] for c in task.method_calls]
        self.assertLess(events.index('select_chess_sixrealms_if_visible'),events.index('appear_then_click'))
        self.assertEqual(task.select_chess_sixrealms_if_visible.call_count,2)

    def test_startup_selection_before_exit_even_without_game_markers(self):
        task=Mock();task.appear.return_value=False
        task.chess_result_flow_visible.return_value=False
        task.select_chess_sixrealms_if_visible.return_value=True
        task.Chess_Exit.return_value=True
        self.assertTrue(ChessLegacyScriptTask._recover_interrupted_chess_game(task))
        task._is_in_chess_game.assert_not_called()
        task.Chess_Exit.assert_called_once_with()

    def test_matchmaking_selection_precedes_in_game_confirmation(self):
        task=Mock();task._matchmaking_timeout_seconds=60
        task.select_chess_sixrealms_if_visible.side_effect=[True,False]
        task._is_in_chess_game.return_value=True
        ChessLegacyScriptTask._wait_until_in_chess_game(task,120)
        self.assertEqual(task.select_chess_sixrealms_if_visible.call_count,2)
        task._is_in_chess_game.assert_called_once_with()

if __name__=='__main__':unittest.main()
