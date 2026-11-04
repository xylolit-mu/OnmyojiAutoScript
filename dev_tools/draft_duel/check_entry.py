"""验证协斗邀请骨架、全局页面与保留的评分接口，不连接设备。"""

import json
from pathlib import Path
import unittest
from unittest.mock import patch
from datetime import datetime as ScheduleDateTime, timezone as ScheduleTimezone, timedelta as ScheduleDelta
from unittest.mock import Mock

from module.exception import TaskEnd
from module.config.config_manual import ConfigManual
from module.config.config_menu import ConfigMenu
from module.config.config_model import ConfigModel
from tasks.DraftDuel.assets import DraftDuelAssets
from tasks.DraftDuel.config import DraftDuel, DraftDuelConfig
from tasks.DraftDuel.script_task import ScriptTask
from tasks.DraftDuel.strategy import choose_pick, plan_duel_lineup, plan_lineup
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.page import PageRegistry, page_draft_duel, page_main, page_town
from tasks.GlobalGame.assets import GlobalGameAssets

ROOT = Path(__file__).resolve().parents[2]
TASK = ROOT / 'tasks/DraftDuel'


class DraftDuelMigrationTests(unittest.TestCase):
    def setUp(self):
        clock = patch('module.draft_duel_schedule.draft_duel_now', return_value=ScheduleDateTime(2026, 11, 1, 12, tzinfo=ScheduleTimezone(ScheduleDelta(hours=8))))
        clock.start()
        self.addCleanup(clock.stop)

    def test_task_registration_and_safe_defaults(self):
        self.assertIn('DraftDuel', ConfigMenu().menu['Weekly Task'])
        self.assertIn('DraftDuel', ConfigManual.SCHEDULER_PRIORITY)
        self.assertEqual(ConfigModel.type('draft_duel'), 'DraftDuel')
        self.assertFalse(DraftDuel().scheduler.enable)
        defaults = json.loads((ROOT / 'config/template.json').read_text(encoding='utf-8'))
        self.assertEqual(defaults['draft_duel'], DraftDuel().model_dump(mode='json'))

    def test_existing_config_gets_standard_groups(self):
        # An older config does not contain draft_duel; Pydantic supplies defaults.
        model = ConfigModel(config_name='migration-test', running_task='')
        args = model.script_task('DraftDuel')
        self.assertEqual(list(args), ['scheduler', 'draft_duel_config', 'invite_config'])
        fields = {item['name']: item for item in args['draft_duel_config']}
        self.assertEqual(set(fields), set(DraftDuelConfig.model_fields))
        self.assertEqual(fields['match_mode']['enumEnum'], ['单人', '队员', '队长'])
        invite = {item['name']: item for item in args['invite_config']}
        self.assertEqual(set(invite), {'friend_list', 'find_mode', 'wait_time', 'default_invite', 'cooperation_script'})
        self.assertEqual(invite['friend_list']['type'], 'multi_line')
        self.assertNotIn('invite_friend_list', fields)
        self.assertNotIn('teammate_timeout_seconds', fields)

    def test_chinese_task_and_group_labels(self):
        labels = json.loads((ROOT / 'assets/i18n/zh-CN.json').read_text(encoding='utf-8'))
        self.assertEqual(labels['DraftDuel'], '协同对弈')
        self.assertEqual(labels['draft_duel_config'], '协同对弈配置')
        for field in DraftDuelConfig.model_fields:
            self.assertIn(field, labels)

    def test_global_page_uses_new_marker(self):
        self.assertIn(page_draft_duel, PageRegistry.all({'global'}))
        self.assertIs(page_draft_duel.recognizer.target, GameUiAssets.I_DRAFT_DUEL)
        self.assertTrue(GameUiAssets.I_DRAFT_DUEL.file.endswith('page_checks_draft_duel.png'))

    def test_original_navigation_edges_and_yellow_back(self):
        from tasks.GameUi.draft_duel_navigation import handle_draft_duel_exit
        self.assertTrue(any(t.destination is page_town for t in page_main.transitions))
        into = next(t for t in page_town.transitions if t.destination is page_draft_duel)
        self.assertIs(into.action, GameUiAssets.I_TOWN_GOTO_DRAFT_DUEL)
        back = next(t for t in page_draft_duel.transitions if t.destination is page_town)
        self.assertIs(back.action, handle_draft_duel_exit)

    def test_task_entry_delegates_to_public_navigation(self):
        owner = Mock()
        owner._in_draft_lobby.return_value = True
        self.assertTrue(ScriptTask._goto_draft_lobby(owner))
        owner.goto_page.assert_called_once_with(page_draft_duel)
        owner.device.click.assert_not_called()

    def test_lobby_check_has_no_old_button_fallback(self):
        owner = Mock()
        owner.appear.return_value = False
        self.assertFalse(ScriptTask._in_draft_lobby(owner))
        owner.appear.assert_called_once_with(GameUiAssets.I_DRAFT_DUEL)

    def test_pick_and_five_choose_three(self):
        offers = ['座敷童子', '莹草', '椒图']
        self.assertIn(choose_pick(offers).name, offers)
        pool = ['座敷童子', '辉夜姬', '莹草', '季', '鬼童丸']
        result = plan_lineup(pool, team_size=3, max_fire=1)
        self.assertEqual(len(set(result)), 3)
        self.assertTrue(set(result) <= set(pool))

    def test_joint_lineup_returns_each_accounts_three(self):
        own = ['座敷童子', '莹草', '季', '椒图', '鬼童丸']
        mate = ['辉夜姬', '追月神', '鬼王酒吞童子', '山兔', '荒川之主']
        left, right = plan_duel_lineup(own, mate)
        self.assertEqual(len(left), 3)
        self.assertEqual(len(right), 3)
        self.assertTrue(set(left) <= set(own))
        self.assertTrue(set(right) <= set(mate))

    def test_matchmaking_handoff_defers_without_leaving_page(self):
        owner = Mock()
        owner._detect_draft_phase.return_value = 'lobby'
        owner._enter_matchmaking.return_value = True
        with self.assertRaises(TaskEnd):
            ScriptTask.run(owner)
        owner.set_next_run.assert_called_once_with(task='DraftDuel', success=False, finish=False)
        owner.device.assert_not_called()
        self.assertEqual(owner.device.mock_calls, [])
        owner.goto_page.assert_not_called()
        owner._invite_teammate.assert_not_called()
        owner.check_then_accept.assert_not_called()

    def test_only_invitation_fields_remain_in_oasx(self):
        settings = DraftDuel(draft_duel_config={
            'match_mode':'队长', 'start_timeout_seconds':120,
            'pick_timeout_seconds':30, 'completion_count':5,
            'battle_timeout_seconds':3600,
        })
        self.assertEqual(settings.draft_duel_config.model_dump(),
                         {'match_mode':'队长', 'match_wait_seconds': 60, 'round_count': 1})
        for item in vars(DraftDuelAssets).values():
            file = getattr(item, 'file', None)
            if isinstance(file, str) and file:
                self.assertTrue((ROOT / file).is_file(), file)


if __name__ == '__main__':
    unittest.main()
