# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time

import random
from datetime import datetime
from module.atom.image import RuleImage

from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.Component.QuickLoadout.quick_loadout import QuickLoadout
from tasks.GameUi.game_ui import GameUi
from tasks.MetaDemon.config import MetaDemon, BossType, loadout_group
from tasks.MetaDemon.assets import MetaDemonAssets
from tasks.MetaDemon.synthesis import parse_synthesis_material_level
import tasks.GameUi.task_pages as ipages

from module.logger import logger
from module.exception import TaskEnd


class BossBattle(GeneralBattle, SwitchSoul, QuickLoadout, GameUi, MetaDemonAssets):
    """超鬼王"""

    class State:
        done: bool = False
        switch_soul_done: bool = False
        synthesis_done: bool = False

    conf: MetaDemon = None
    cur_boss_type: BossType = None
    total_count: int = 0

    def _meta_demon_battle_key(self) -> str:
        """返回当前鬼王类型对应的通用战斗分组键。"""

        if self.cur_boss_type is None:
            return "meta_demon_default"
        return f"meta_demon_{loadout_group(self.cur_boss_type)}"

    @property
    def enable_powerful_fire(self):
        powerful_list = self.conf.meta_demon_config.powerful_list_v
        return self.cur_boss_type is not None and self.cur_boss_type in powerful_list

    def before_run(self):
        self.navigator.add_unknown_closer(self.I_MD_GET_YESTERDAY_REWARD)
        self.State = BossBattle.State()
        self._active_loadout_group = None
        self._boss_generation = 0
        self.cur_boss_type = None
        self.total_count = 0
        self.conf = self.config.meta_demon
        self.limit_time = self.conf.meta_demon_config.limit_time_v
        self.limit_count = self.conf.meta_demon_config.limit_count
        page_shikigami_records = self.navigator.resolve_page(ipages.page_shikigami_records)
        page_meta_demon_boss = self.navigator.resolve_page(ipages.page_meta_demon_boss)
        page_main = self.navigator.resolve_page(ipages.page_main)
        if page_shikigami_records is None or page_meta_demon_boss is None or page_main is None:
            raise RuntimeError("MetaDemon 页面 session 初始化失败")

        # 仅修改当前任务 session 内的跳转图，不污染全局页面定义
        page_shikigami_records.clear_transitions()
        page_shikigami_records.connect(
            page_meta_demon_boss,
            self.I_UI_BACK_YELLOW,
            key="page_shikigami_records->page_meta_demon_boss",
        )
        page_meta_demon_boss.connect(
            page_shikigami_records,
            MetaDemonAssets.I_MD_SHIKIGAMI,
            key="page_meta_demon_boss->page_shikigami_records",
        )
        page_main.remove_transition(key="page_main->page_shikigami_records")

    def run(self):
        self.before_run()
        self.goto_page(ipages.page_meta_demon_boss)
        while True:
            self.update_global_state()
            if self.State.done:
                break
            self.screenshot()
            current_page = self.get_current_page()
            match current_page:
                case ipages.page_meta_demon:
                    self.goto_page(ipages.page_meta_demon_boss)
                case ipages.page_meta_demon_boss:
                    if self.check_and_prepare_battle():
                        self.start_battle()
                case ipages.page_battle:
                    self.battle_wait(False)
                case ipages.page_reward:
                    self.click(ipages.reward_random_click(), interval=0.6)
                case ipages.page_battle_result:
                    self.click(ipages.random_click(), interval=0.6)
                case _:
                    time.sleep(random.uniform(0.7, 1.4))
        self.set_next_run(task="MetaDemon", success=True)
        self.finish_task()

    def start_battle(self):
        logger.hr('Click fire, start battle')
        while True:
            self.screenshot()
            if self.is_in_battle(False):
                self.total_count += 1
                self.run_general_battle(
                    self.conf.battle_config(loadout_group(self.cur_boss_type)),
                    battle_key=self._meta_demon_battle_key(),
                )
                break
            if self.appear(self.I_MD_DRINK_TEA, interval=1.2):  # 出现喝茶弹窗
                if self.conf.meta_demon_config.auto_tea:
                    self.click(self.I_MD_DRINK_TEA)  # 喝茶
                    continue
                # 不喝茶则结束任务安排下一次运行
                self.ui_click_until_disappear(self.I_MD_CLOSE_POPUP, interval=0.6)
                self.set_next_run('MetaDemon', target=datetime.now() + self.conf.scheduler.wait_interval)
                self.finish_task()
            if self.appear_then_click(self.I_MD_FIRE, interval=0.8):
                continue

    def battle_wait(self, random_click_swipt_enable: bool) -> bool:
        self.device.stuck_record_add('BATTLE_STATUS_S')
        self.device.click_record_clear()
        logger.info(f"Start battle process on {self.cur_boss_type.name if self.cur_boss_type else 'None'}")
        win = False
        while True:
            self.screenshot()
            if self.appear(self.I_MD_SUMMON_BOSS, interval=0.8) or \
                    self.appear(self.I_MD_SWITCH_TICKET, interval=0.8) or \
                    self.appear(self.I_MD_FIRE, interval=0.8):
                break
            if self.appear(self.I_WIN, interval=0.8):
                win = True
                self.click(ipages.random_click())
                continue
            if self.appear(self.I_FALSE, interval=0.8):
                win = False
                self.click(ipages.random_click())
                continue
            if self.appear(self.I_MD_SETTLING, interval=3.5):
                logger.info('wait result')
                time.sleep(random.uniform(3, 5))
                continue
        total_run_time = datetime.now() - self.start_time
        logger.info(f'battle win: {win}')
        logger.info(f'battle count: {self.total_count}/{self.limit_count}')
        logger.info(f'time count: {total_run_time.total_seconds():.1f}s/{self.limit_time.total_seconds()}s')
        return win

    def update_global_state(self):
        if datetime.now() - self.start_time >= self.limit_time or \
                self.total_count >= self.limit_count:
            self.State.done = True

    def check_and_prepare_battle(self) -> bool:
        """检查并做战斗前的准备工作
        :return: True可以战斗, False不能战斗
        """
        logger.hr('battle prepare', 2)
        if not self.check_fatigue():
            return False
        self.synthesis_boss_ticket()
        return self.check_and_switch_ticket() and self.check_and_switch_soul() and self.check_and_switch_powerful()

    def synthesis_boss_ticket(self):
        """合成鬼王"""
        if self.State.synthesis_done:
            return
        logger.hr('synthesis boss ticket')
        self.State.synthesis_done = True
        if len(self.conf.meta_demon_config.synthesis_list_v) <= 0:
            return
        type_ticket_dict: dict[BossType, tuple[RuleImage, int]] = {
            BossType.ONE_STAR: (self.I_MD_SYNTHESIS_ONE_STAR, 3),
            BossType.TWO_STARS: (self.I_MD_SYNTHESIS_TWO_STAR, 3),
            BossType.THREE_STARS: (self.I_MD_SYNTHESIS_THREE_STAR, 3),
            BossType.FOUR_STARS: (self.I_MD_SYNTHESIS_FOUR_STAR, 3),
            BossType.FIVE_STARS: (self.I_MD_SYNTHESIS_FIVE_STAR, 2),
        }
        synthesis_list = self.conf.meta_demon_config.synthesis_list_v
        self.ui_click(self.I_MD_SYNTHESIS, self.I_MD_START_SYNTHESIS, interval=0.6)
        for boss_type in synthesis_list:
            self.do_synthesis(type_ticket_dict[boss_type][0], type_ticket_dict[boss_type][1], boss_type)
        self.ui_click(self.I_MD_CLOSE_POPUP, self.I_MD_SHIKIGAMI, interval=0.6)  # 关闭弹窗退回到鬼王界面

    def do_synthesis(self, target_boss_ticket: RuleImage, need_ticket: int, material_level: BossType):
        """开始合成鬼王
        :param target_boss_ticket: 目标鬼王级别门票
        :param need_ticket: 合成当前级别鬼王需要的门票数量
        :param material_level: 配置要求消耗的材料星级，必须与标题 OCR 换算结果一致
        """
        if not self.appear(target_boss_ticket):
            logger.info(f'{target_boss_ticket} not recognized, skip synthesis')
            return
        click_cnt, max_cnt = 0, random.randint(2, 3)
        verification_failures = 0
        while True:
            self.screenshot()
            if click_cnt >= max_cnt:  # 兜底,多次点击开始合成都没有出现获得奖励则退出
                break
            if self.appear(self.I_MD_SYNTHESIS_NEED_MONEY, interval=0.8):  # 出现花钱购买合成券
                self.ui_click(self.I_MD_CLOSE_POPUP, self.I_MD_START_SYNTHESIS, interval=0.6)  # 关闭购买弹窗
                break
            if self.appear(self.I_UI_REWARD, interval=0.8):  # 出现获得奖励说明合成成功
                self.click(ipages.random_click(), interval=0.6)
                click_cnt = 0  # 清空点击标志
                continue
            if self.appear(self.I_MD_SYNTHESIS_EMPTY, interval=0.8):  # 有空位
                empty_list = self.I_MD_SYNTHESIS_EMPTY.match_all(self.device.image, frame_id=self.device.image_frame_id)
                if len(empty_list) < need_ticket:  # 空位数量小于需要的门票数量->门票不够则退出
                    logger.info(f'{target_boss_ticket} ticket not enough, skip synthesis')
                    break
            if self.appear_then_click(target_boss_ticket, interval=2):  # 点击对应级别鬼王门票
                # 选中后重新截图，不能拿点击之前的标题验证。
                time.sleep(0.3)
                self.screenshot()
                title = self.O_SYNTHESIS_LEVEL.ocr(self.device.image)
                detected_level = parse_synthesis_material_level(title)
                if detected_level != int(material_level):
                    verification_failures += 1
                    logger.warning(
                        f'合成星级验证失败: 标题={title!r}, 材料星级={detected_level}, '
                        f'要求={int(material_level)}, 重试={verification_failures}/3'
                    )
                    if verification_failures >= 3:
                        logger.warning('连续三次无法确认目标合成星级，跳过本星级')
                        break
                    continue
                verification_failures = 0
                if self.appear_then_click(self.I_MD_START_SYNTHESIS, interval=2.5):  # 点击开始合成
                    click_cnt += 1
                continue
        logger.info(f'{target_boss_ticket} synthesis done')

    def check_fatigue(self):
        logger.hr('Check fatigue')
        current, remain, total = self.O_MD_FATIGUE.ocr_digit_counter(self.device.image)
        # 不喝茶且当前疲劳度已满
        if not self.conf.meta_demon_config.auto_tea and (current > total or remain < 0):
            self.set_next_run('MetaDemon', target=datetime.now() + self.conf.scheduler.wait_interval)
            self.finish_task()
        return True

    def check_and_switch_ticket(self):
        """切换鬼王门票并更新当前鬼王级别"""
        logger.hr('Summon boss')
        # 打开切换门票弹窗
        while True:
            self.screenshot()
            if self.appear(self.I_MD_FIRE, interval=1.2):  # 可以直接挑战鬼王则退出
                return True
            if self.appear(self.I_MD_GET_BOSS, interval=0.6):  # 打开弹窗了
                break
            if self.appear(self.I_MD_TICKET_EMPTY, interval=0.6):  # 任何门票都没有了
                self.State.done = True
                logger.info('Not have any boss ticket, exit')
                return False
            if self.appear_then_click(self.I_MD_SWITCH_TICKET, interval=0.6):
                continue
        type_ticket_dict: dict[BossType, RuleImage] = {
            BossType.ONE_STAR: self.I_MD_ONE_STAR,
            BossType.TWO_STARS: self.I_MD_TWO_STARS,
            BossType.THREE_STARS: self.I_MD_THREE_STARS,
            BossType.FOUR_STARS: self.I_MD_FOUR_STARS,
            BossType.FIVE_STARS: self.I_MD_FIVE_STARS,
            BossType.SIX_STARS: self.I_MD_SIX_STARS,
        }
        # 获取所有存在的鬼王门票
        can_fire_list = [boss_type for boss_type, ticket_rule_image in type_ticket_dict.items() if
                         self.appear(ticket_rule_image)]
        if len(can_fire_list) <= 0:
            self.State.done = True
            logger.info('No remain boss ticket, exit')
            self.ui_click_until_disappear(self.I_MD_CLOSE_POPUP, interval=0.6)
            return False
        # 根据存在的鬼王门票和配置的鬼王序列过滤出需要召唤的鬼王门票
        need_fire_list = [boss_type for boss_type in self.conf.meta_demon_config.fire_sequence_v if
                          boss_type in can_fire_list]
        if len(need_fire_list) <= 0:
            self.State.done = True
            logger.info('There is no boss that needs to be attacked, exit')
            self.ui_click_until_disappear(self.I_MD_CLOSE_POPUP, interval=0.6)
            return False
        if self.cur_boss_type is None or loadout_group(self.cur_boss_type) != loadout_group(need_fire_list[0]):
            self.current_count = 0
        self.cur_boss_type = need_fire_list[0]  # 更新当前鬼王级别
        target_ticket_rule_image = type_ticket_dict[self.cur_boss_type]
        selected = False
        logger.info(f'Summon {self.cur_boss_type.name.lower()} boss')
        while True:
            self.screenshot()
            if self.appear(self.I_MD_FIRE):  # 可以挑战鬼王则退出
                self._boss_generation = getattr(self, "_boss_generation", 0) + 1
                return True
            if selected and self.appear(self.I_MD_CHECK_SELECTED, interval=0.6):
                selected_x, selected_y = self.I_MD_CHECK_SELECTED.coord()
                x, y, w, h = target_ticket_rule_image.roi_front
                if x <= selected_x <= x + w:  # 确定选中目标鬼王门票, 召唤鬼王
                    self.ui_click_until_disappear(self.I_MD_GET_BOSS, interval=0.6)
                    continue
            if self.appear_then_click(target_ticket_rule_image, interval=0.6):  # 选择对应鬼王门票
                selected = True
                continue
        return False

    def check_and_switch_powerful(self) -> bool:
        """检查并切换是否开启强力"""
        logger.hr('process power fire')
        if not self.appear(self.I_MD_FIRE):
            self.goto_page(ipages.page_meta_demon_boss)
            self.screenshot()
            if not self.appear(self.I_MD_FIRE):  # 处于boss界面但是没有挑战按钮
                return False
        powerful_list = self.conf.meta_demon_config.powerful_list_v
        # 当前boss类型未知或不在强力列表中则使用普通追击
        if self.cur_boss_type is None or self.cur_boss_type not in powerful_list:
            self.ui_click(self.I_MD_ENABLE_POWERFUL_FIRE, self.I_MD_DISABLE_POWERFUL_FIRE, interval=0.6)
            return True
        # 已知类型且在强力列表中则开启强力追击
        self.ui_click(self.I_MD_DISABLE_POWERFUL_FIRE, self.I_MD_ENABLE_POWERFUL_FIRE, interval=0.6)
        return True

    def check_and_switch_soul(self) -> bool:
        """按鬼王分类应用御魂；显式开启快速上阵时通过 frame 装魂并上阵。"""
        if self.cur_boss_type is None:
            enabled = any(self.conf.switch_soul_config.switch_for(group).enable or
                          self.conf.switch_soul_config.switch_for(group).enable_switch_by_name or
                          self.conf.battle_config(group).preset_enable or
                          self.conf.general_config.high_star_quick_loadout_enable
                          for group in ('low_star', 'high_star'))
            return not enabled
        category = loadout_group(self.cur_boss_type)
        custom = self.conf.quick_preset_config
        custom_enabled = (category == 'high_star' and
                          self.conf.general_config.high_star_quick_loadout_enable and
                          custom.high_star_custom_preset_enable)
        loadout_key = (category, getattr(self, '_boss_generation', 0)) if custom_enabled else category
        if getattr(self, '_active_loadout_group', None) == loadout_key:
            return True
        if category == 'high_star' and self.conf.general_config.high_star_quick_loadout_enable:
            from tasks.Component.QuickLoadout.config import QuickLoadoutConfig
            soul = self.conf.switch_soul_config.switch_for(category)
            quick = QuickLoadoutConfig(enable=True, group_name=soul.group_name, preset_name=soul.team_name)
            if custom_enabled:
                # Invalid numeric targets must never silently fall back to another preset.
                try:
                    presets = self._parse_custom_presets(custom.high_star_custom_preset)
                except ValueError as exc:
                    logger.error(f'高星快速预设格式错误，仅支持名称: {exc}')
                    return False
                name_ocr = getattr(self, 'O_BOSS_NAME', None)
                if name_ocr is None:
                    logger.error('缺少超鬼王首领名称 OCR 资源 O_BOSS_NAME')
                    return False
                boss_name = self._read_quick_loadout_stage_name(name_ocr)
                if not self._normalize_name(boss_name):
                    logger.warning('未识别到首领名称，暂不上阵')
                    return False
                _, target, _ = self._match_custom_preset(boss_name, presets)
                if target is None:
                    logger.error(f'首领 {boss_name} 无匹配预设，请配置对应名称或 ALL')
                    return False
                quick.group_name, quick.preset_name = target
            if not self.run_quick_loadout(quick, frame=self.F_QUICK_LOADOUT_FRAME):
                return False
        else:
            soul = self.conf.switch_soul_config.switch_for(category)
            if soul.enable or soul.enable_switch_by_name:
                self.goto_page(ipages.page_shikigami_records)
                if soul.enable_switch_by_name:
                    self.run_switch_soul_by_name(soul.group_name, soul.team_name)
                else:
                    self.run_switch_soul(soul.switch_group_team)
                self.goto_page(ipages.page_meta_demon_boss)
        # 其他分类可能已改变阵容，切回时必须重新应用该分类的战斗预设。
        shared = getattr(self, '_battle_shared_state', None)
        if shared is not None:
            shared.pop(f'meta_demon_{category}', None)
        self._active_loadout_group = loadout_key
        return True

    def finish_task(self):
        self.finish_task_navigation()
        raise TaskEnd
