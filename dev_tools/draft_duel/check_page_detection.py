"""协同对弈页面主标志遮挡时的备用识别检查，不连接设备。"""
import unittest
from itertools import combinations
from unittest.mock import Mock

from tasks.DraftDuel.script_task import ScriptTask


class PageDetectionTests(unittest.TestCase):
    def owner(self, *visible):
        owner = object.__new__(ScriptTask)
        owner.appear = Mock(side_effect=lambda rule: any(rule is item for item in visible))
        owner.screenshot = Mock()
        return owner

    def test_original_selection_marker_has_priority(self):
        owner = self.owner(ScriptTask.I_POST_DRAFT_SELECT)
        self.assertTrue(owner._is_draft_selection_page())
        owner.appear.assert_called_once_with(owner.I_POST_DRAFT_SELECT)

    def test_any_two_selection_buttons_are_enough(self):
        buttons = (ScriptTask.I_SELECT_1, ScriptTask.I_SELECT_2, ScriptTask.I_SELECT_3)
        for pair in combinations(buttons, 2):
            with self.subTest(pair=pair):
                self.assertTrue(self.owner(*pair)._is_draft_selection_page())

    def test_single_selection_button_is_not_enough(self):
        for rule in (ScriptTask.I_SELECT_1, ScriptTask.I_SELECT_2, ScriptTask.I_SELECT_3):
            self.assertFalse(self.owner(rule)._is_draft_selection_page())

    def test_original_rank_marker_has_priority(self):
        owner = self.owner(ScriptTask.I_POST_DRAFT_RANK)
        self.assertTrue(owner._is_draft_rank_page())
        owner.appear.assert_called_once_with(owner.I_POST_DRAFT_RANK)

    def test_both_auto_states_and_both_roles(self):
        for auto in (ScriptTask.I_AUTO_DOWN, ScriptTask.I_AUTO_RIGHT):
            for button in (ScriptTask.I_LOBBY_BATTLE, ScriptTask.I_LOBBY_TEAMMATE_WAIT):
                with self.subTest(auto=auto, button=button):
                    self.assertTrue(self.owner(auto, button)._is_draft_rank_page())

    def test_auto_or_battle_button_alone_is_not_rank(self):
        for rule in (ScriptTask.I_AUTO_DOWN, ScriptTask.I_AUTO_RIGHT,
                     ScriptTask.I_LOBBY_BATTLE, ScriptTask.I_LOBBY_TEAMMATE_WAIT):
            self.assertFalse(self.owner(rule)._is_draft_rank_page())

    def test_deployment_and_team_lobby_are_not_rank(self):
        for guard in (ScriptTask.I_POST_DRAFT_FIGHT, ScriptTask.I_WAIT_FIGHT_LOBBY):
            owner = self.owner(guard, ScriptTask.I_AUTO_RIGHT, ScriptTask.I_LOBBY_BATTLE)
            self.assertFalse(owner._is_draft_rank_page())

    def test_phase_detection_uses_selection_backup(self):
        owner = self.owner(ScriptTask.I_SELECT_1, ScriptTask.I_SELECT_3)
        self.assertEqual(owner._detect_draft_phase(), 'select')

    def test_phase_detection_uses_rank_backup(self):
        owner = self.owner(ScriptTask.I_AUTO_DOWN, ScriptTask.I_LOBBY_TEAMMATE_WAIT)
        self.assertEqual(owner._detect_draft_phase(), 'rank')

    def test_deployment_still_wins_phase_detection(self):
        owner = self.owner(ScriptTask.I_POST_DRAFT_FIGHT, ScriptTask.I_AUTO_RIGHT,
                           ScriptTask.I_LOBBY_BATTLE)
        self.assertEqual(owner._detect_draft_phase(), 'fight')


if __name__ == '__main__':
    unittest.main()
