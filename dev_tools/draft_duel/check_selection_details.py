"""Details capture and member entry checks without device-service dependencies."""
import ast
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[2]


def load_class(filename, namespace):
    path = ROOT / 'tasks/DraftDuel/runtime' / filename
    namespace['__file__'] = str(path)
    tree = ast.parse(path.read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[cls.name]


class DetailsChecks(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.clock = SimpleNamespace(monotonic=Mock(return_value=0),
                                     sleep=Mock(), time_ns=Mock(side_effect=range(100, 200)))
        cls = load_class('selection.py', dict(Path=Path, re=re, time=self.clock, logger=Mock()))
        self.owner = cls()
        self.owner.SHIKIGAMI_DETAILS_DIR = Path(self.folder.name)
        self.owner.config = SimpleNamespace(config_name='TEST')
        self.owner.device = SimpleNamespace(image=object(), stuck_record_clear=Mock(), image_save=Mock())
        self.owner.screenshot = Mock()
        self.owner._is_draft_selection_page = Mock(return_value=True)
        self.owner.O_SELECT_NUMBER = SimpleNamespace(ocr=Mock(return_value='请选择第1名式神'))
        self.owner.I_SHIKIGAMI_DETAILS = object()
        self.owner.appear_then_click = Mock(return_value=True)

    def test_all_icons_open_without_saving_screenshots(self):
        owner = self.owner
        for number in range(1, 6):
            owner.O_SELECT_NUMBER.ocr.return_value = f'请选择第{number}名式神'
            owner.appear = Mock(side_effect=[True, True, True, False])
            self.assertTrue(owner._capture_selection_details(number))
            self.assertEqual(owner.appear.call_count, 4)
            self.assertTrue(owner._capture_selection_details(number))
            self.assertEqual(owner.appear.call_count, 4)
        owner.device.image_save.assert_not_called()
        self.assertEqual(owner.appear_then_click.call_count, 15)

    def test_timeout_does_not_save_incomplete_details(self):
        self.owner.appear = Mock(return_value=True)
        self.clock.monotonic.side_effect = [0, 11]
        self.assertFalse(self.owner._capture_selection_details(1))
        self.owner.device.image_save.assert_not_called()

    def test_changed_stage_or_pick_does_not_capture(self):
        self.owner._is_draft_selection_page.return_value = False
        self.assertFalse(self.owner._capture_selection_details(1))
        self.owner._is_draft_selection_page.return_value = True
        self.owner.O_SELECT_NUMBER.ocr.return_value = '请选择第2名式神'
        self.assertFalse(self.owner._capture_selection_details(1))
        self.owner.device.image_save.assert_not_called()

    def test_detail_recognition_does_not_access_disk(self):
        self.owner.appear = Mock(return_value=False)
        self.owner.device.image_save.side_effect = [OSError('disk failure'), None]
        self.assertTrue(self.owner._capture_selection_details(1))
        self.owner.device.image_save.assert_not_called()


class MemberEntryChecks(unittest.TestCase):
    def test_member_waits_without_navigating_to_lobby(self):
        modes = SimpleNamespace(MEMBER='member', CAPTAIN='captain')
        cls = load_class('invitation.py', dict(DraftMatchMode=modes))
        owner = Mock()
        owner.config.draft_duel.draft_duel_config.match_mode = modes.MEMBER
        owner._goto_draft_lobby.return_value = False
        owner._wait_member_start.return_value = True
        self.assertTrue(cls._enter_matchmaking(owner))
        owner._goto_draft_lobby.assert_not_called()
        owner.prepare_battle_cooperation.assert_called_once_with('DraftDuel')
        owner._wait_member_start.assert_called_once()
        owner.appear_then_click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
