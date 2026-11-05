"""MetaDemon loadout categories, settings payload and ticket-runner mapping."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.config.config_model import ConfigModel
from tasks.MetaDemon.config import BossType, ExplorationTarget, MetaDemon, MetaDemonMode, loadout_group
from tasks.MetaDemon.boss_battle import BossBattle
from tasks.MetaDemon.ticket_farming import ticket_farming_config, ExplorationTicketTask, AwakeTicketTask
from tasks.EvoZone.config import EvoZone
from tasks.Exploration.config import AutoRotate, Exploration, UpType


class MetaDemonSettingsChecks(unittest.TestCase):
    def test_exploration_label_migrates(self):
        conf = MetaDemon(meta_demon_config={'mode': ['鬼王战斗', '门票刷取：探索绘卷']})
        self.assertEqual(conf.meta_demon_config.mode, [MetaDemonMode.EXPLORATION, MetaDemonMode.BOSS_BATTLE])
        self.assertEqual(MetaDemonMode.EXPLORATION.value, '门票刷取：探索')

    def test_categories_and_independent_presets(self):
        self.assertEqual([loadout_group(BossType(i)) for i in range(1, 7)], ['low_star']*4+['high_star']*2)
        conf = MetaDemon(preset_config={'low_star_enable': True, 'low_star_group': 2, 'low_star_team': 3,
            'ticket_farming_enable': True, 'ticket_farming_group': 4, 'ticket_farming_team': 2})
        low = conf.battle_config('low_star')
        ticket = conf.battle_config('ticket_farming')
        self.assertEqual((low.preset_group, low.preset_team), (2,3))
        self.assertEqual((ticket.preset_group, ticket.preset_team), (4,2))
        self.assertFalse(conf.battle_config('high_star').preset_enable)

    def test_legacy_souls_and_low_preset_migrate(self):
        conf = MetaDemon(switch_soul={'enable': True, 'one_star': '2,3', 'two_stars': '3,4',
            'five_stars': '活动,高星', 'enable_one_star_preset': True})
        low = conf.switch_soul_config.switch_for('low_star')
        high = conf.switch_soul_config.switch_for('high_star')
        self.assertTrue(low.enable)
        self.assertEqual(low.switch_group_team, '2,3')
        self.assertTrue(high.enable_switch_by_name)
        self.assertEqual((high.group_name, high.team_name), ('活动','高星'))
        self.assertTrue(conf.low_star_battle_conf.preset_enable)
        self.assertNotIn('switch_soul', conf.model_dump())

    def test_common_settings_map_to_ticket_runners(self):
        expected = {ExplorationTarget.ALL: UpType.ALL, ExplorationTarget.COIN: UpType.COIN,
                    ExplorationTarget.EXP: UpType.EXP, ExplorationTarget.DARUMA: UpType.DARUMAA}
        for target, up_type in expected.items():
            meta = MetaDemon(general_config={'exploration_rotate': True,
                'awake_buff_enable': True, 'exploration_target': target},
                switch_soul_config={'ticket_farming_by_name': True, 'ticket_farming_group_team_name': '刷票,探索'},
                preset_config={'ticket_farming_enable': True, 'ticket_farming_group': 3, 'ticket_farming_team': 2})
            awake = ticket_farming_config(meta, MetaDemonMode.AWAKE)
            exploration = ticket_farming_config(meta, MetaDemonMode.EXPLORATION)
            self.assertEqual(exploration.exploration_config.auto_rotate, AutoRotate.yes)
            self.assertEqual(exploration.exploration_config.up_type, up_type)
            self.assertFalse(exploration.scrolls.scrolls_enable)
            self.assertTrue(awake.evo_zone_config.soul_buff_enable)
            for task in (awake, exploration):
                self.assertTrue(task.switch_soul_config.enable_switch_by_name)
                self.assertEqual(task.switch_soul_config.group_name, '刷票')
                self.assertTrue(task.general_battle_config.preset_enable)
                self.assertEqual(task.general_battle_config.preset_group, 3)
            self.assertIsNot(awake.switch_soul_config, exploration.switch_soul_config)

    def test_ticket_rotation_reuses_exploration_and_disables_scrolls(self):
        from tasks.Exploration.script_task import ScriptTask as ExplorationTask
        self.assertIs(ExplorationTicketTask.switch_rotate, ExplorationTask.switch_rotate)
        self.assertIs(ExplorationTicketTask.run_on_exp_settings, ExplorationTask.run_on_exp_settings)
        for enabled in (False, True):
            meta = MetaDemon(general_config={'exploration_rotate': enabled, 'scrolls_enable': True})
            awake = ticket_farming_config(meta, MetaDemonMode.AWAKE)
            exploration = ticket_farming_config(meta, MetaDemonMode.EXPLORATION)
            self.assertEqual(exploration.exploration_config.auto_rotate,
                             AutoRotate.yes if enabled else AutoRotate.no)
            self.assertFalse(exploration.scrolls.scrolls_enable)
            self.assertNotIn('scrolls_enable', meta.general_config.model_dump())
        # Even a stale enabled scrolls setting cannot perform ticket OCR or scheduling.
        owner = object.__new__(ExplorationTicketTask)
        owner.O_REALM_RAID_NUMBER1_META = Mock()
        owner.O_REALM_RAID_NUMBER_META = Mock()
        owner.set_next_run = Mock()
        from tasks.GameUi import task_pages as pages
        exploration.scrolls.scrolls_enable = True
        for page in (pages.page_mainline, pages.page_gameplay, pages.page_exploration, pages.page_exp_entrance):
            owner.activate_realm_raid(exploration.scrolls, exploration.exploration_config, page)
        owner.O_REALM_RAID_NUMBER1_META.ocr_digit_counter.assert_not_called()
        owner.O_REALM_RAID_NUMBER_META.ocr_digit_counter.assert_not_called()
        owner.set_next_run.assert_not_called()

    def test_settings_payload_has_flat_groups_and_defaults(self):
        payload = ConfigModel.script_task(SimpleNamespace(meta_demon=MetaDemon()), 'MetaDemon')
        self.assertEqual(set(payload), {'scheduler','meta_demon_config','general_config','switch_soul_config','low_star_battle_conf','high_star_battle_conf','ticket_farming_battle_conf','quick_preset_config','settings_layout_config','assistance_config'})
        for group in payload.values():
            self.assertIsInstance(group, list)
            for field in group:
                self.assertIn('default', field)
        common = {field['name']: field for field in payload['general_config']}
        self.assertEqual(common['exploration_target']['enumEnum'], ['全部','金币','经验','达摩'])

    def test_low_and_high_share_loadout_within_category(self):
        owner = object.__new__(BossBattle)
        owner.conf = MetaDemon(
            switch_soul_config={'low_star_enable': True, 'low_star_group_team': '2,3'},
            general_config={'high_star_quick_loadout_enable': True},
            preset_config={'high_star_enable': True, 'high_star_group_name': '默认分组', 'high_star_preset_name': '超鬼王'})
        owner.goto_page = Mock()
        owner.run_switch_soul = Mock()
        owner.run_quick_loadout = Mock(return_value=True)
        for star in range(1,7):
            owner.cur_boss_type = BossType(star)
            self.assertTrue(owner.check_and_switch_soul())
        owner.run_switch_soul.assert_called_once_with('2,3')
        owner.run_quick_loadout.assert_called_once()
        config = owner.run_quick_loadout.call_args.args[0]
        self.assertEqual((config.group_name, config.preset_name), ('默认分组','超鬼王'))
        self.assertIs(owner.run_quick_loadout.call_args.kwargs['frame'], owner.F_QUICK_LOADOUT_FRAME)
        owner._battle_shared_state = {'meta_demon_low_star': object()}
        owner.cur_boss_type = BossType.ONE_STAR
        self.assertTrue(owner.check_and_switch_soul())
        self.assertNotIn('meta_demon_low_star', owner._battle_shared_state)

    def test_high_star_default_uses_records_and_battle_preset(self):
        owner = object.__new__(BossBattle)
        owner.conf = MetaDemon(
            switch_soul_config={'high_star_enable': True, 'high_star_group_team': '3,2'},
            high_star_battle_conf={'preset_enable': True, 'preset_group': 4, 'preset_team': 3})
        owner.cur_boss_type = BossType.FIVE_STARS
        owner.goto_page = Mock()
        owner.run_switch_soul = Mock()
        owner.run_quick_loadout = Mock()
        self.assertTrue(owner.check_and_switch_soul())
        owner.run_switch_soul.assert_called_once_with('3,2')
        self.assertEqual(owner.goto_page.call_count, 2)
        owner.run_quick_loadout.assert_not_called()
        self.assertTrue(owner.conf.battle_config('high_star').preset_enable)

    def test_quick_loadout_only_overrides_high_star(self):
        conf = MetaDemon(general_config={'high_star_quick_loadout_enable': True},
            low_star_battle_conf={'preset_enable': True},
            high_star_battle_conf={'preset_enable': True, 'green_enable': True},
            ticket_farming_battle_conf={'preset_enable': True, 'green_enable': True})
        self.assertTrue(conf.battle_config('low_star').preset_enable)
        self.assertFalse(conf.battle_config('high_star').preset_enable)
        self.assertTrue(conf.battle_config('high_star').green_enable)
        self.assertTrue(conf.high_star_battle_conf.preset_enable)
        awake = ticket_farming_config(conf, MetaDemonMode.AWAKE)
        exploration = ticket_farming_config(conf, MetaDemonMode.EXPLORATION)
        for task in (awake, exploration):
            self.assertTrue(task.general_battle_config.preset_enable)
            self.assertTrue(task.general_battle_config.green_enable)

    def test_custom_high_presets_once_per_new_boss(self):
        owner = object.__new__(BossBattle)
        owner.conf = MetaDemon(general_config={'high_star_quick_loadout_enable': True},
            quick_preset_config={'high_star_custom_preset_enable': True,
                'high_star_custom_preset': '大天狗:("高星","天狗");ALL:("通用","其他")'})
        owner.cur_boss_type = BossType.FIVE_STARS
        owner._boss_generation = 1
        owner._read_quick_loadout_stage_name = Mock(return_value='大天狗')
        owner.run_quick_loadout = Mock(return_value=True)
        owner.goto_page = Mock()
        self.assertTrue(owner.check_and_switch_soul())
        self.assertTrue(owner.check_and_switch_soul())
        owner.run_quick_loadout.assert_called_once()
        target = owner.run_quick_loadout.call_args.args[0]
        self.assertEqual((target.group_name, target.preset_name), ('高星', '天狗'))
        owner._boss_generation = 2
        owner._read_quick_loadout_stage_name.return_value = '酒吞童子'
        self.assertTrue(owner.check_and_switch_soul())
        self.assertEqual(owner.run_quick_loadout.call_count, 2)
        target = owner.run_quick_loadout.call_args.args[0]
        self.assertEqual((target.group_name, target.preset_name), ('通用', '其他'))
        owner.goto_page.assert_not_called()

    def test_numeric_custom_target_rejected_without_deploy(self):
        owner = object.__new__(BossBattle)
        owner.conf = MetaDemon(general_config={'high_star_quick_loadout_enable': True},
            quick_preset_config={'high_star_custom_preset_enable': True})
        owner.cur_boss_type = BossType.SIX_STARS
        owner.run_quick_loadout = Mock()
        self.assertFalse(owner.check_and_switch_soul())
        owner.run_quick_loadout.assert_not_called()

    def test_custom_high_presets_ignored_when_quick_disabled(self):
        owner = object.__new__(BossBattle)
        owner.conf = MetaDemon(quick_preset_config={'high_star_custom_preset_enable': True},
            switch_soul_config={'high_star_enable': True, 'high_star_group_team': '2,3'})
        owner.cur_boss_type = BossType.FIVE_STARS
        owner.goto_page = Mock()
        owner.run_switch_soul = Mock()
        owner.run_quick_loadout = Mock()
        self.assertTrue(owner.check_and_switch_soul())
        owner.run_switch_soul.assert_called_once_with('2,3')
        owner.run_quick_loadout.assert_not_called()

    def test_ticket_entry_uses_dedicated_config_without_copying_runtime(self):
        from module.config.config import Config
        from tasks.MetaDemon.ticket_farming import run_ticket_farming, TicketsFull
        from tasks.EvoZone.config import Layer, UserStatus as AwakeStatus
        from tasks.Exploration.config import UserStatus as ExplorationStatus
        for mode, runner_class in ((MetaDemonMode.AWAKE, AwakeTicketTask),
                                   (MetaDemonMode.EXPLORATION, ExplorationTicketTask)):
            with self.subTest(mode=mode):
                original = object.__new__(Config)
                original.config_name = 'TICKET_CHECK'
                original.task = 'MetaDemon'
                original.model = ConfigModel()
                original.model.meta_demon.general_config.exploration_rotate = True
                battle = original.model.meta_demon.ticket_farming_battle_conf
                battle.preset_enable, battle.preset_group, battle.preset_team = True, 3, 2
                original.model.exploration.scrolls.scrolls_enable = True
                original.model.exploration.general_battle_config.preset_group = 7
                original.model.evo_zone.general_battle_config.preset_group = 6
                before = original.model.model_dump()
                device = object()
                runner = object.__new__(runner_class)
                runner.config = original
                runner.device = device
                runner.run_alone = Mock(side_effect=TicketsFull)
                runner.exec_exp_page = Mock(side_effect=TicketsFull)
                runner.pre_process = Mock()
                runner.install_ticket_reward_hook = Mock()

                def build(config, actual_device):
                    self.assertIs(actual_device, device)
                    self.assertIs(config, original)
                    return runner

                with patch('tasks.MetaDemon.ticket_farming.tickets_full_before_farming', return_value=False), \
                     patch('tasks.MetaDemon.ticket_farming.AwakeTicketTask', side_effect=build), \
                     patch('tasks.MetaDemon.ticket_farming.ExplorationTicketTask', side_effect=build):
                    self.assertTrue(run_ticket_farming(SimpleNamespace(config=original, device=device), mode))
                runner.install_ticket_reward_hook.assert_called_once()
                self.assertTrue(runner._ticket_farming_complete)
                self.assertIs(runner._config.general_battle_config, battle)
                self.assertEqual((runner._config.general_battle_config.preset_group,
                                  runner._config.general_battle_config.preset_team), (3, 2))
                if mode == MetaDemonMode.AWAKE:
                    self.assertEqual(runner._config.evo_zone_config.layer, Layer.SEVEN)
                    self.assertEqual(runner._config.evo_zone_config.user_status, AwakeStatus.ALONE)
                    runner.run_alone.assert_called_once()
                    runner.exec_exp_page.assert_not_called()
                else:
                    self.assertEqual(runner._config.exploration_config.user_status, ExplorationStatus.ALONE)
                    self.assertEqual(runner._config.exploration_config.auto_rotate, AutoRotate.yes)
                    self.assertFalse(runner._config.scrolls.scrolls_enable)
                    runner.pre_process.assert_called_once()
                    runner.exec_exp_page.assert_called_once()
                    runner.run_alone.assert_not_called()
                self.assertEqual(original.model.model_dump(), before)

    def test_inherited_ticket_flows_do_not_require_independent_task_settings(self):
        from datetime import datetime, timedelta
        from tasks.EvoZone.config import Layer
        meta = MetaDemon(switch_soul_config={'ticket_farming_enable': True,
            'ticket_farming_group_team': '2,3'}, ticket_farming_battle_conf={
                'preset_enable': True, 'preset_group': 3, 'preset_team': 2})
        # Deliberately omit evo_zone/exploration: reading either is a regression.
        runtime = SimpleNamespace(meta_demon=meta)
        awake = object.__new__(AwakeTicketTask)
        awake.config = runtime
        awake.goto_page = Mock()
        awake.evozone_enter = Mock()
        awake.check_layer = Mock()
        awake.check_lock = Mock()
        awake.screenshot = Mock()
        awake.appear = Mock(side_effect=[True, False, True])
        awake.appear_then_click = Mock(return_value=True)
        awake.current_count, awake.limit_count = 0, 1
        awake.start_time, awake.limit_time = datetime.now(), timedelta(minutes=1)
        awake.run_general_battle = Mock(side_effect=lambda **kw: setattr(awake, 'current_count', 1))
        awake.run_alone()
        awake.check_layer.assert_called_once_with(Layer.SEVEN)
        self.assertIs(awake.run_general_battle.call_args.kwargs['config'], meta.ticket_farming_battle_conf)
        self.assertEqual(awake._config.switch_soul_config.switch_group_team, '2,3')
        exploration = object.__new__(ExplorationTicketTask)
        exploration.config = runtime
        exploration.C_SAFE_RANDOM_CLICK_AREA_EXP = Mock()
        exploration.goto_page = Mock()
        exploration.run_switch_soul = Mock()
        exploration.run_general_battle = Mock()
        exploration._match_end = Mock()
        exploration.pre_process()
        exploration.run_switch_soul.assert_called_once_with('2,3')
        exploration.run_on_battle()
        self.assertIs(exploration.run_general_battle.call_args.args[0], meta.ticket_farming_battle_conf)

    def test_normal_awake_config_keeps_original_task_settings(self):
        from tasks.EvoZone.script_task import ScriptTask as NormalAwakeTask
        config = EvoZone()
        task = object.__new__(NormalAwakeTask)
        task.config = SimpleNamespace(evo_zone=config)
        self.assertIs(task._config, config)

    def test_ticket_counter_uses_existing_resource_and_checks_capacity(self):
        from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
        from tasks.MetaDemon.ticket_farming import metademon_tickets_full
        image = object()
        task = SimpleNamespace(device=SimpleNamespace(image=image),
            appear=Mock(side_effect=lambda target: target is GeneralBattleAssets.I_REWARD))
        for counter, full in (((1000, 0, 1000), True), ((1001, -1, 1000), True),
                              ((999, 1, 1000), False), ((0, 0, 0), False),
                              ((-1, 1001, 1000), False)):
            with self.subTest(counter=counter), patch.object(
                    GeneralBattleAssets.O_METADEMON_PASS, 'ocr_digit_counter', return_value=counter) as ocr:
                self.assertEqual(metademon_tickets_full(task), full)
                ocr.assert_called_once_with(image)

    def test_full_tickets_interrupt_both_reward_flows_and_cleanup_skips_ocr(self):
        from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
        from tasks.MetaDemon.ticket_farming import TicketsFull
        for runner_class in (AwakeTicketTask, ExplorationTicketTask):
            runner = object.__new__(runner_class)
            runner.device = SimpleNamespace(image=object())
            runner.appear = Mock(return_value=True)
            with self.subTest(task=runner_class.__name__), patch.object(
                    GeneralBattleAssets.O_METADEMON_PASS, 'ocr_digit_counter', return_value=(1000, 0, 1000)) as ocr:
                with self.assertRaises(TicketsFull):
                    runner._handle_reward(None, None)
                self.assertEqual(ocr.call_count, 1)
                runner._ticket_farming_complete = True
                runner.check_ticket_reward()
                self.assertEqual(ocr.call_count, 1)

    def test_ticket_ocr_is_skipped_without_red_daruma_reward_image(self):
        from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
        for runner_class in (AwakeTicketTask, ExplorationTicketTask):
            runner = object.__new__(runner_class)
            runner.device = SimpleNamespace(image=object())
            runner.appear = Mock(return_value=False)
            with self.subTest(task=runner_class.__name__), patch.object(
                    GeneralBattleAssets.O_METADEMON_PASS, 'ocr_digit_counter') as ocr:
                runner.check_ticket_reward()
                runner.appear.assert_called_once_with(GeneralBattleAssets.I_REWARD)
                ocr.assert_not_called()

    def test_exploration_exhausted_targets_keep_exit_request_through_stale_frame(self):
        from tasks.Exploration.script_task import ScriptTask as NormalExplorationTask
        from tasks.Exploration.config import UserStatus
        from tasks.GameUi import task_pages as pages
        for task_class in (NormalExplorationTask, ExplorationTicketTask):
            with self.subTest(task=task_class.__name__):
                task = object.__new__(task_class)
                task.pre_page = None
                task.user_status = UserStatus.ALONE
                task.need_exit = False
                task.fire_monster_type = ''
                task.collect_reward = Mock(return_value=False)
                task.switch_rotate = Mock(return_value=False)
                task.get_fire_button = Mock(return_value=None)
                task.swipe = Mock(return_value=True)
                task.arrive_end = Mock(return_value=True)
                task.appear_then_click = Mock(return_value=True)
                task.screenshot = Mock()
                task.get_current_page = Mock(return_value=pages.page_exp_exit)
                task.run_on_exp_main()
                self.assertTrue(task.need_exit)
                task.get_fire_button.reset_mock()
                task.get_fire_button.return_value = object()
                task.run_on_exp_main()  # delayed scene frame after clicking back
                task.get_fire_button.assert_not_called()
                self.assertTrue(task.need_exit)
                task.run_on_exp_exit()
                self.assertIs(task.appear_then_click.call_args.args[0], task.I_E_EXIT_CONFIRM)

    def test_fire_does_not_cancel_requested_exit_but_accidental_popup_still_cancels(self):
        from tasks.Exploration.script_task import ScriptTask as NormalExplorationTask
        from tasks.GameUi import task_pages as pages
        task = object.__new__(NormalExplorationTask)
        task.need_exit = True
        task.screenshot = Mock()
        task.get_current_page = Mock(return_value=pages.page_exp_exit)
        self.assertTrue(task.fire(object()))
        self.assertTrue(task.need_exit)
        task.need_exit = False
        task.appear_then_click = Mock(return_value=True)
        task.run_on_exp_exit()
        self.assertIs(task.appear_then_click.call_args.args[0], task.I_E_EXIT_CANCEL)

    def _summon_owner(self, frames, star=BossType.TWO_STARS):
        from copy import deepcopy
        owner = object.__new__(BossBattle)
        owner.conf = MetaDemon(meta_demon_config={'fire_sequence': '2 1'})
        owner.State = SimpleNamespace(done=False)
        owner.cur_boss_type = None
        owner.current_count = 7
        owner._boss_generation = 0
        names = ('I_MD_FIRE', 'I_MD_GET_BOSS', 'I_MD_TICKET_EMPTY', 'I_MD_SWITCH_TICKET',
                 'I_MD_CLOSE_POPUP', 'I_MD_CHECK_SELECTED', 'I_MD_ONE_STAR', 'I_MD_TWO_STARS',
                 'I_MD_THREE_STARS', 'I_MD_FOUR_STARS', 'I_MD_FIVE_STARS', 'I_MD_SIX_STARS')
        for name in names:
            setattr(owner, name, deepcopy(getattr(owner, name)))
        target_ticket = {BossType.TWO_STARS: owner.I_MD_TWO_STARS,
                         BossType.SIX_STARS: owner.I_MD_SIX_STARS}[star]
        state = {'index': -1, 'time': 0.0}
        clicks = []

        def screenshot():
            state['index'] += 1
            state['time'] += 1

        def appear(rule, **kwargs):
            frame = frames[min(state['index'], len(frames)-1)]
            if rule is owner.I_MD_FIRE:
                return frame.get('fire', False)
            if rule is owner.I_MD_GET_BOSS:
                return frame.get('panel', False)
            if rule is owner.I_MD_SWITCH_TICKET:
                return not frame.get('panel', False) and not frame.get('fire', False)
            if rule is target_ticket:
                return frame.get('panel', False)
            if rule is owner.I_MD_CHECK_SELECTED and frame.get('panel'):
                x = target_ticket.roi_front[0] if frame.get('selected') else 360
                rule.roi_front = (x, 280, 50, 22)
                return True
            return False

        def click(rule, **kwargs):
            if not appear(rule):
                return False
            clicks.append((state['index'], rule))
            return True

        owner.screenshot = Mock(side_effect=screenshot)
        owner.appear = Mock(side_effect=appear)
        owner.appear_then_click = Mock(side_effect=click)
        return owner, state, clicks

    def test_summon_sequence_override_only_uses_six_without_changing_config(self):
        owner, state, clicks = self._summon_owner([
            {'panel': True, 'selected': True}, {'fire': True}], star=BossType.SIX_STARS)
        original = owner.conf.meta_demon_config.fire_sequence
        with patch('tasks.MetaDemon.boss_battle.time.monotonic', side_effect=lambda: state['time']):
            self.assertTrue(owner.check_and_switch_ticket(sequence=(BossType.SIX_STARS,)))
        self.assertEqual(owner.cur_boss_type, BossType.SIX_STARS)
        self.assertEqual(owner.conf.meta_demon_config.fire_sequence, original)

    def test_summon_recovers_closed_panel_and_confirms_two_star(self):
        owner, state, clicks = self._summon_owner([
            {'panel': True}, {}, {}, {'panel': True}, {'panel': True, 'selected': True},
            {}, {'fire': True}])
        with patch('tasks.MetaDemon.boss_battle.time.monotonic', side_effect=lambda: state['time']):
            self.assertTrue(owner.check_and_switch_ticket())
        self.assertTrue(any(rule is owner.I_MD_SWITCH_TICKET for _, rule in clicks))
        self.assertEqual([index for index, rule in clicks if rule is owner.I_MD_GET_BOSS], [4])
        self.assertEqual(owner.cur_boss_type, BossType.TWO_STARS)
        self.assertEqual(owner._boss_generation, 1)
        self.assertEqual(owner.current_count, 0)

    def test_summon_retries_unresponsive_confirmation_without_reselecting(self):
        owner, state, clicks = self._summon_owner([
            {'panel': True, 'selected': True}, {'panel': True, 'selected': True}, {'fire': True}])
        with patch('tasks.MetaDemon.boss_battle.time.monotonic', side_effect=lambda: state['time']):
            self.assertTrue(owner.check_and_switch_ticket())
        self.assertEqual(sum(rule is owner.I_MD_GET_BOSS for _, rule in clicks), 2)
        self.assertFalse(any(rule is owner.I_MD_TWO_STARS for _, rule in clicks))

    def test_summon_rejects_wrong_selection_and_bounds_missing_panel_wait(self):
        for frames in ([{'panel': True}], [{}]):
            with self.subTest(frames=frames):
                owner, state, clicks = self._summon_owner(frames)
                with patch('tasks.MetaDemon.boss_battle.time.monotonic', side_effect=lambda: state['time']):
                    self.assertFalse(owner.check_and_switch_ticket())
                self.assertFalse(any(rule is owner.I_MD_GET_BOSS for _, rule in clicks))
                self.assertIsNone(owner.cur_boss_type)
                self.assertEqual(owner._boss_generation, 0)
                self.assertLessEqual(owner.screenshot.call_count, 25)

    def test_full_ticket_precheck_skips_both_farming_runners(self):
        from tasks.MetaDemon.assets import MetaDemonAssets
        from tasks.MetaDemon.ticket_farming import run_ticket_farming
        from tasks.GameUi import task_pages as pages
        for mode in (MetaDemonMode.AWAKE, MetaDemonMode.EXPLORATION):
            owner = SimpleNamespace(goto_page=Mock(), screenshot=Mock(), appear=Mock(return_value=True),
                                    device=SimpleNamespace(image=object()))
            with self.subTest(mode=mode), patch.object(MetaDemonAssets.O_METADEMON_PASS_EXP,
                    'ocr_digit_counter', return_value=(50, 0, 50)) as ocr, \
                 patch('tasks.MetaDemon.ticket_farming.AwakeTicketTask') as awake, \
                 patch('tasks.MetaDemon.ticket_farming.ExplorationTicketTask') as exploration:
                self.assertTrue(run_ticket_farming(owner, mode))
                owner.goto_page.assert_called_once_with(pages.page_exploration)
                ocr.assert_called_once_with(owner.device.image)
                awake.assert_not_called()
                exploration.assert_not_called()

    def test_ticket_precheck_retries_invalid_counter_and_rejects_wrong_page(self):
        from tasks.MetaDemon.assets import MetaDemonAssets
        from tasks.MetaDemon.ticket_farming import tickets_full_before_farming
        owner = SimpleNamespace(goto_page=Mock(), screenshot=Mock(), appear=Mock(return_value=True),
                                device=SimpleNamespace(image=object()))
        with patch.object(MetaDemonAssets.O_METADEMON_PASS_EXP, 'ocr_digit_counter',
                          side_effect=[(0,0,0), (-1,51,50), (49,1,50)]) as ocr:
            self.assertFalse(tickets_full_before_farming(owner))
            self.assertEqual(ocr.call_count, 3)
        owner.appear.return_value = False
        with patch.object(MetaDemonAssets.O_METADEMON_PASS_EXP, 'ocr_digit_counter') as ocr:
            self.assertFalse(tickets_full_before_farming(owner))
            ocr.assert_not_called()

    def test_full_tickets_still_continue_to_battle_or_assistance_mode(self):
        from tasks.MetaDemon.script_task import ScriptTask
        from tasks.MetaDemon.assets import MetaDemonAssets
        for next_mode in (MetaDemonMode.BOSS_BATTLE, MetaDemonMode.BOSS_ASSIST):
            owner = object.__new__(ScriptTask)
            owner.config = SimpleNamespace(meta_demon=MetaDemon(meta_demon_config={
                'mode': [MetaDemonMode.EXPLORATION, next_mode]}))
            owner.device = SimpleNamespace(image=object())
            owner.goto_page, owner.screenshot = Mock(), Mock()
            owner.appear = Mock(return_value=True)
            with patch.object(MetaDemonAssets.O_METADEMON_PASS_EXP, 'ocr_digit_counter', return_value=(50,0,50)), \
                 patch('tasks.MetaDemon.boss_battle.BossBattle.run', return_value='battle') as battle, \
                 patch('tasks.MetaDemon.assistance.run_assistance', return_value='assistance') as assistance:
                self.assertEqual(owner.run(), 'battle' if next_mode == MetaDemonMode.BOSS_BATTLE else 'assistance')
                self.assertEqual(battle.call_count, int(next_mode == MetaDemonMode.BOSS_BATTLE))
                self.assertEqual(assistance.call_count, int(next_mode == MetaDemonMode.BOSS_ASSIST))

    def test_disabled_souls_do_not_navigate(self):
        owner = object.__new__(BossBattle)
        owner.conf = MetaDemon()
        owner.cur_boss_type = BossType.ONE_STAR
        owner.goto_page = Mock()
        self.assertTrue(owner.check_and_switch_soul())
        owner.goto_page.assert_not_called()


if __name__ == '__main__':
    unittest.main()
