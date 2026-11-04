"""Offline checks for task selection and fixed priorities."""
import unittest
import ast
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from datetime import datetime
from pydantic import ValidationError
from tasks.MetaDemon.config import MetaDemonConfig, MetaDemonMode as Mode
from tasks.ActivityShikigami.config import GeneralConfig

class SelectionChecks(unittest.TestCase):
    def test_meta_default_and_legacy(self):
        self.assertEqual(MetaDemonConfig().mode, [Mode.BOSS_BATTLE])
        self.assertEqual(MetaDemonConfig(mode=Mode.AWAKE.value).mode, [Mode.AWAKE])
        self.assertEqual(MetaDemonConfig(mode=[]).mode, [])

    def test_meta_ticket_exclusivity_and_priority(self):
        self.assertEqual(MetaDemonConfig(mode=[Mode.BOSS_BATTLE, Mode.EXPLORATION]).mode,
                         [Mode.EXPLORATION, Mode.BOSS_BATTLE])
        with self.assertRaises(ValidationError):
            MetaDemonConfig(mode=[Mode.AWAKE, Mode.EXPLORATION])

    def test_activity_order_and_legacy_exclusivity(self):
        conf = GeneralConfig(task_sequence=['首领', '爬塔', '伪神'],
                             climb_task_sequence=['百体', '体力', '简单', '困难'],
                             pass_limit='3,4', ap_limit=2, ap100_limit=1,
                             boss_limit=1, fakegod_limit=1)
        self.assertEqual(conf.task_sequence_v, ['伪神', '爬塔', '首领'])
        self.assertEqual(conf.climb_sequence_v, ['pass_hard', 'pass_easy', 'ap', 'ap100'])
        conf = GeneralConfig(task_sequence=['伪神', '大富翁', '探索', '爬塔', '首领'],
                             climb_task_sequence=[], throw_limit=1, fakegod_limit=1, boss_limit=1)
        self.assertEqual(conf.task_sequence_v, ['探索', '首领'])

class DispatchChecks(unittest.TestCase):
    def test_farming_executes_before_selected_boss(self):
        events = []
        class Boss:
            def run(self):
                events.append('boss')
        path = Path(__file__).resolve().parents[1] / 'tasks/MetaDemon/script_task.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        ns = dict(BossBattle=Boss, MetaDemonMode=Mode, datetime=datetime, logger=Mock())
        exec(compile(ast.Module(body=[cls], type_ignores=[]), str(path), 'exec'), ns)
        runner = ns['ScriptTask']()
        runner.config = SimpleNamespace(meta_demon=SimpleNamespace(meta_demon_config=
            MetaDemonConfig(mode=[Mode.BOSS_BATTLE, Mode.AWAKE])))
        fake = SimpleNamespace(run_ticket_farming=lambda owner, mode: events.append(mode) or True)
        with patch.dict(sys.modules, {'tasks.MetaDemon.ticket_farming': fake}):
            runner.run()
        self.assertEqual(events, [Mode.AWAKE, 'boss'])

if __name__ == '__main__':
    unittest.main()
