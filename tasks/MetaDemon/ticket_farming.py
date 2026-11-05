"""超鬼王门票刷取，复用觉醒和探索的战斗流程。"""
from datetime import datetime
from cached_property import cached_property

from module.exception import TaskEnd
from module.logger import logger
from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
from tasks.EvoZone.config import EvoZone, EvoZoneConfig, Layer, UserStatus as AwakeUserStatus
from tasks.EvoZone.script_task import ScriptTask as AwakeTask
from tasks.Exploration.config import Exploration, ExplorationConfig, AutoRotate, UpType, UserStatus as ExplorationUserStatus
from tasks.Exploration.script_task import ScriptTask as ExplorationTask
from tasks.GameUi.default_pages import page_reward
import tasks.GameUi.task_pages as pages
from tasks.MetaDemon.config import MetaDemonMode, ExplorationTarget
from tasks.MetaDemon.assets import MetaDemonAssets
from tasks.GameUi.assets import GameUiAssets


class TicketsFull(Exception):
    """奖励页门票已满，立即展开调用栈以阻止下一场战斗。"""


def metademon_tickets_full(task) -> bool:
    """读取当前奖励截图的门票/上限；无有效上限时不判满。"""
    # I_REWARD is the red-daruma settlement image; I_UI_REWARD is the
    # separate "获得奖励" banner and does not identify this battle reward screen.
    if not task.appear(GeneralBattleAssets.I_REWARD):
        return False
    current, remaining, total = GeneralBattleAssets.O_METADEMON_PASS.ocr_digit_counter(task.device.image)
    if total <= 0 or current < 0:
        logger.warning('超鬼王门票 OCR 未识别到有效计数')
        return False
    logger.info(f'超鬼王门票: {current}/{total}')
    return current >= total


class TicketRewardMixin:
    def check_ticket_reward(self):
        if getattr(self, '_ticket_farming_complete', False):
            return
        if metademon_tickets_full(self):
            logger.info('门票已满')
            raise TicketsFull

    def _handle_reward(self, context, config):
        self.check_ticket_reward()
        return super()._handle_reward(context, config)

    def install_ticket_reward_hook(self):
        reward = self.navigator.resolve_page(page_reward)
        # 导航进入奖励页时也必须先 OCR，再执行原来的结算点击。
        reward.on_enter_success.insert(0, lambda task: task.check_ticket_reward())


class AwakeTicketTask(TicketRewardMixin, AwakeTask):
    @cached_property
    def _config(self):
        return ticket_farming_config(self.config.meta_demon, MetaDemonMode.AWAKE)

    def _infer_task_category(self):
        return 'evo_zone'


class ExplorationTicketTask(TicketRewardMixin, ExplorationTask):
    @cached_property
    def _config(self):
        return ticket_farming_config(self.config.meta_demon, MetaDemonMode.EXPLORATION)

    def _infer_task_category(self):
        return 'exploration'

    @property
    def exp_page_handle_dict(self):
        handlers = super().exp_page_handle_dict
        original = handlers[page_reward]

        def reward():
            self.check_ticket_reward()
            return original()

        handlers[page_reward] = reward
        return handlers

    def activate_realm_raid(self, con_scrolls, con, current_page):
        """门票刷取不检测结界票，也不触发个人突破调度。"""
        return



def ticket_farming_config(meta, mode):
    """仅把超鬼王专用字段适配为复用流程需要的设置，不读取独立任务配置。"""
    common = meta.general_config
    soul = meta.switch_soul_config.switch_for('ticket_farming')
    battle = meta.ticket_farming_battle_conf
    if mode == MetaDemonMode.AWAKE:
        return EvoZone(
            evo_zone_config=EvoZoneConfig(user_status=AwakeUserStatus.ALONE, layer=Layer.SEVEN,
                limit_time=meta.meta_demon_config.limit_time, limit_count=2 ** 31 - 1,
                soul_buff_enable=common.awake_buff_enable),
            switch_soul_config=soul, general_battle_config=battle)
    if mode == MetaDemonMode.EXPLORATION:
        target = {ExplorationTarget.ALL: UpType.ALL, ExplorationTarget.COIN: UpType.COIN,
                  ExplorationTarget.EXP: UpType.EXP, ExplorationTarget.DARUMA: UpType.DARUMAA}
        return Exploration(
            exploration_config=ExplorationConfig(user_status=ExplorationUserStatus.ALONE,
                limit_time=meta.meta_demon_config.limit_time, minions_cnt=2 ** 31 - 1,
                auto_rotate=AutoRotate.yes if common.exploration_rotate else AutoRotate.no,
                up_type=target[common.exploration_target]),
            switch_soul_config=soul, general_battle_config=battle)
    raise ValueError(f'未知门票刷取模式: {mode}')


def tickets_full_before_farming(owner) -> bool:
    """Check the courtyard's exploration landing page before any farming setup."""
    owner.goto_page(pages.page_exploration)
    for _ in range(3):
        owner.screenshot()
        if not owner.appear(GameUiAssets.I_CHECK_EXPLORATION):
            continue
        current, _, total = MetaDemonAssets.O_METADEMON_PASS_EXP.ocr_digit_counter(owner.device.image)
        if current >= 0 and total > 0:
            logger.info(f'门票刷取前检测: {current}/{total}')
            return current >= total
    logger.warning('探索入口未识别到有效超鬼王门票计数，继续刷取并由奖励页检测满票')
    return False


def run_ticket_farming(owner, mode) -> bool:
    """共享运行环境；御魂和战斗参数直接使用超鬼王门票刷取配置。"""
    if mode not in (MetaDemonMode.AWAKE, MetaDemonMode.EXPLORATION):
        raise ValueError(f'未知门票刷取模式: {mode}')
    if tickets_full_before_farming(owner):
        logger.info('超鬼王门票已满，跳过门票刷取')
        return True
    config = owner.config
    limit_time = config.meta_demon.meta_demon_config.limit_time_v
    if mode == MetaDemonMode.AWAKE:
        runner = AwakeTicketTask(config, owner.device)
    elif mode == MetaDemonMode.EXPLORATION:
        runner = ExplorationTicketTask(config, owner.device)
    else:
        raise ValueError(f'未知门票刷取模式: {mode}')
    runner.start_time = datetime.now()
    runner.current_count = 0
    runner.limit_count = 2 ** 31 - 1
    runner.install_ticket_reward_hook()
    buff_opened = False
    try:
        if mode == MetaDemonMode.AWAKE:
            soul = runner._config.switch_soul_config
            if soul.enable or soul.enable_switch_by_name:
                runner.goto_page(pages.page_shikigami_records)
                if soul.enable_switch_by_name:
                    runner.run_switch_soul_by_name(soul.group_name, soul.team_name)
                else:
                    runner.run_switch_soul(soul.switch_group_team)
            if config.meta_demon.general_config.awake_buff_enable:
                runner.goto_page(pages.page_main)
                runner.open_buff()
                runner.awake(is_open=True)
                buff_opened = True
                runner.close_buff()
            runner.limit_time = limit_time
            runner.run_alone()
        else:
            runner.pre_process()
            runner.limit_time = limit_time
            runner.exec_exp_page()
    except TicketsFull:
        return True
    finally:
        runner._ticket_farming_complete = True
        if buff_opened:
            runner.goto_page(pages.page_main)
            runner.open_buff()
            runner.awake(is_open=False)
            runner.close_buff()
    return False
