"""棋局结算未知页面红色返回恢复；仅模拟截图和点击。"""
import unittest
from unittest.mock import Mock, patch

from tasks.Chess.assets import ChessAssets
from tasks.Chess.script_task import ScriptTask
from tasks.GlobalGame.assets import GlobalGameAssets


class ResultRecoveryTests(unittest.TestCase):
    def play(self, frames, restart=False):
        owner = object.__new__(ScriptTask)
        owner.device = Mock()
        owner.click = Mock(return_value=True)
        owner.appear_then_click = Mock(return_value=True)
        owner._wait_chess_action_state = Mock(return_value=True)
        page = [-1]
        owner.screenshot = Mock(side_effect=lambda: page.__setitem__(0, page[0]+1))
        owner.appear = Mock(side_effect=lambda rule: rule in frames[page[0]])
        with patch('tasks.GameUi.chess_battle.time.sleep'), \
                patch('tasks.GameUi.chess_battle.time.monotonic', side_effect=lambda: max(0, page[0])):
            result = (owner._restart_chess_game_from_result() if restart
                      else owner.return_to_chess_lobby())
        return owner, result

    def test_unknown_page_back_red_then_lobby(self):
        owner, result = self.play([[ChessAssets.I_BACK_RED], [ScriptTask.I_CHECK_CHESS]])
        self.assertTrue(result)
        owner.click.assert_called_once_with(ChessAssets.I_BACK_RED, interval=0.8)
        self.assertEqual(owner.screenshot.call_count, 2)

    def test_unknown_page_after_share_recovers_then_handles_rank(self):
        owner, result = self.play([[ChessAssets.I_SHARE], [ChessAssets.I_BACK_RED],
                                  [ScriptTask.I_CHESS_RANK_GOTO_LOBBY], [ScriptTask.I_CHECK_CHESS]])
        self.assertTrue(result)
        self.assertIn(ChessAssets.I_BACK_RED, [call.args[0] for call in owner.click.call_args_list])
        owner.appear_then_click.assert_called_once_with(ScriptTask.I_CHESS_RANK_GOTO_LOBBY, interval=1.5)

    def test_known_rank_page_takes_priority_over_red_back(self):
        owner, result = self.play([[ScriptTask.I_CHESS_RANK_GOTO_LOBBY, ChessAssets.I_BACK_RED],
                                  [ScriptTask.I_CHECK_CHESS]])
        self.assertTrue(result)
        owner.click.assert_not_called()
        owner.appear_then_click.assert_called_once()

    def test_restart_flow_rechecks_after_red_back(self):
        owner, result = self.play([[ChessAssets.I_BACK_RED], [ChessAssets.I_RESTART_AGAIN]], restart=True)
        self.assertTrue(result)
        self.assertEqual(owner.click.call_args_list[0].args[0], ChessAssets.I_BACK_RED)
        self.assertEqual(owner.click.call_args_list[1].args[0], ChessAssets.I_RESTART_AGAIN)

    def test_unknown_page_without_red_back_is_not_clicked(self):
        owner = object.__new__(ScriptTask)
        owner.appear = Mock(return_value=False)
        owner.click = Mock()
        self.assertFalse(owner._close_unknown_chess_result_page())
        owner.click.assert_not_called()
