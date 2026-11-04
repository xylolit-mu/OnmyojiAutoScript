"""Result recognition must not capture or store result images."""
import ast
from pathlib import Path
import unittest
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[2] / 'tasks/DraftDuel/runtime/battle.py'

class CaptureChecks(unittest.TestCase):
    def test_recognition_without_capture(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_read_statistic_result')
        ns = {'logger': Mock()}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), 'exec'), ns)
        for win, lose, expected in ((True,False,'win'), (False,True,'loss'), (False,False,None), (True,True,None)):
            owner = Mock()
            owner._draft_small_game_result = None
            owner.appear.side_effect = [win, lose]
            self.assertEqual(ns['_read_statistic_result'](owner), expected)
            self.assertEqual(owner._draft_small_game_result, expected)
            owner.screenshot.assert_not_called()
            owner._capture_fight_statistic.assert_not_called()
            owner._save_draft_checkpoint.assert_not_called()

    def test_capture_removed(self):
        s = SOURCE.read_text(encoding='utf-8')
        for name in ('_capture_fight_statistic', 'FIGHT_RESULT_DIR', '_draft_result_screenshots', 'imencode', 'write_text'):
            self.assertNotIn(name, s)

if __name__ == '__main__':
    unittest.main()
