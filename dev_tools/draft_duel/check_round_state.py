"""大厅大轮与匹配小局边界，不操作设备。"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
from module.atom.frame import FrameResult
from module.exception import TaskEnd
from tasks.DraftDuel.config import DraftDuel
from tasks.DraftDuel.script_task import ScriptTask


class RoundStateTests(unittest.TestCase):
    def owner(self):
        owner = object.__new__(ScriptTask)
        owner.appear = Mock(return_value=False)
        owner.config = SimpleNamespace(draft_duel=DraftDuel(draft_duel_config={'round_count': 2}))
        owner._initialize_duel_state()
        return owner

    def test_only_lobby_starts_new_big_round(self):
        owner = self.owner()
        owner.appear.side_effect = lambda r: r is owner.I_WAIT_FIGHT_LOBBY
        owner._observe_duel_lobby()
        self.assertEqual(owner.draft_big_round, 1)
        owner.draft_selected = ['莹草']
        owner._draft_session = 'same-big-round'
        owner._observe_duel_lobby()
        self.assertEqual(owner.draft_selected, ['莹草'])
        # 大厅标志短暂缺失，再出现不误建新大轮。
        owner.appear.side_effect = lambda r: False
        owner._observe_duel_lobby()
        owner.appear.side_effect = lambda r: r is owner.I_WAIT_FIGHT_LOBBY
        owner._observe_duel_lobby()
        self.assertEqual(owner.draft_big_round, 1)
        for marker in (owner.I_POST_DRAFT_RANK, owner.I_POST_DRAFT_FIGHT):
            owner.appear.side_effect = lambda r: r is marker
            owner._observe_duel_lobby()
            self.assertEqual(owner._draft_session, 'same-big-round')
        owner.appear.side_effect = lambda r: r is owner.I_WAIT_FIGHT_LOBBY
        owner._observe_duel_lobby()
        self.assertEqual(owner.draft_big_round, 2)
        self.assertEqual(owner.draft_selected, [])
        self.assertEqual(owner._draft_session, '')

    def test_matching_retries_are_one_small_game(self):
        owner = self.owner()
        owner._begin_small_game()
        owner._begin_small_game()
        self.assertEqual(owner.draft_small_game, 1)
        owner._draft_matching_bout_active = False
        owner._begin_small_game()
        self.assertEqual(owner.draft_small_game, 2)

    def test_auto_games_keep_confirmed_lineup_and_unresolved_slots_until_new_round(self):
        owner = self.owner()
        owner._begin_small_game()
        owner.draft_fielded = ('莹草', '辉夜姬')
        owner.draft_deployment_rounds = [{'round': 1, 'slot': 1, 'name': '莹草'},
                                       {'round': 2, 'slot': 2, 'name': '辉夜姬'}]
        owner.draft_unresolved_slots = [3]
        owner._draft_matching_bout_active = False
        owner._begin_small_game()
        self.assertEqual(owner.draft_small_game, 2)
        self.assertEqual(owner.draft_fielded, ('莹草', '辉夜姬'))
        self.assertEqual(len(owner.draft_deployment_rounds), 2)
        self.assertEqual(owner.draft_unresolved_slots, [3])
        owner._initialize_duel_state()
        self.assertEqual(owner.draft_fielded, ())
        self.assertEqual(owner.draft_deployment_rounds, [])

    def test_portraits_are_captured_once_per_big_round(self):
        owner = self.owner()
        owner.appear.side_effect = lambda r: r is owner.I_WAIT_FIGHT_LOBBY
        owner._observe_duel_lobby()
        owner.draft_selected = list('abcde')
        owner.runframe = Mock(return_value=FrameResult(True, [np.ones((4, 4, 3))] * 5))
        self.assertTrue(owner._capture_draft_heads())
        saved = owner.draft_named_heads
        for _ in range(3):
            owner._draft_matching_bout_active = False
            owner._begin_small_game()
            self.assertTrue(owner._capture_draft_heads())
            self.assertIs(owner.draft_named_heads, saved)
        owner.runframe.assert_called_once()
        owner.appear.side_effect = lambda r: r is owner.I_POST_DRAFT_RANK
        owner._observe_duel_lobby()
        owner.appear.side_effect = lambda r: r is owner.I_WAIT_FIGHT_LOBBY
        owner._observe_duel_lobby()
        self.assertEqual(owner.draft_named_heads, ())
        self.assertEqual(owner.draft_selected, [])

    def test_exit_target_counts_big_rounds_not_small_games(self):
        owner = self.owner()
        owner.appear.side_effect = lambda r: r is owner.I_WAIT_FIGHT_LOBBY
        owner._observe_duel_lobby()
        self.assertFalse(hasattr(owner, 'draft_completed_rounds'))
        for number in (1, 2):
            owner.appear.side_effect = lambda r: r is owner.I_POST_DRAFT_RANK
            owner._observe_duel_lobby()
            owner.draft_small_game = 5
            owner._draft_round_had_battle = True
            owner.appear.side_effect = lambda r: r is owner.I_WAIT_FIGHT_LOBBY
            if number == 2:
                owner._leave_draft_team = Mock(side_effect=TaskEnd)
                with self.assertRaises(TaskEnd):
                    owner._observe_duel_lobby()
                owner._leave_draft_team.assert_called_once_with('已完成2大轮协斗')
            else:
                owner._observe_duel_lobby()
            self.assertEqual(owner.draft_completed_rounds, number)


if __name__ == '__main__':
    unittest.main()
