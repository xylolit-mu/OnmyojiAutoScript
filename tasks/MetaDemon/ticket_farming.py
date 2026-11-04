"""超鬼王门票刷取，复用觉醒和探索的战斗流程。"""
from copy import copy
from datetime import datetime

from module.exception import TaskEnd
from module.logger import logger
from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
from tasks.EvoZone.config import Layer, UserStatus as AwakeUserStatus
from tasks.EvoZone.script_task import ScriptTask as AwakeTask
from tasks.Exploration.config import AutoRotate, UpType, UserStatus as ExplorationUserStatus
from tasks.Exploration.script_task import ScriptTask as ExplorationTask
from tasks.GameUi.default_pages import page_reward
import tasks.GameUi.task_pages as pages
from tasks.MetaDemon.config import MetaDemonMode, ExplorationTarget


class TicketsFull(Exception):
    """奖励页门票已满，立即展开调用栈以阻止下一场战斗。"""


def metademon_tickets_full(task) -> bool:
    """读取当前奖励截图的门票/上限；无有效上限时不判满。"""
    current, remaining, total = GeneralBattleAssets.O_METADEMOM_PASS.ocr_digit_counter(task.device.image)
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
    def _infer_task_category(self):
        return 'evo_zone'


class ExplorationTicketTask(TicketRewardMixin, ExplorationTask):
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



def configure_ticket_farming(meta, awake, exploration):
    """将超鬼王刷票设置映射到独立的觉醒、探索配置副本。"""
    common = meta.general_config
    target = {ExplorationTarget.ALL: UpType.ALL, ExplorationTarget.COIN: UpType.COIN,
              ExplorationTarget.EXP: UpType.EXP, ExplorationTarget.DARUMA: UpType.DARUMAA}
    exploration.exploration_config.auto_rotate = AutoRotate.yes if common.exploration_rotate else AutoRotate.no
    exploration.exploration_config.up_type = target[common.exploration_target]
    exploration.scrolls.scrolls_enable = False
    awake.evo_zone_config.soul_buff_enable = common.awake_buff_enable
    soul = meta.switch_soul_config.switch_for('ticket_farming')
    preset = meta.battle_config('ticket_farming')
    for config in (awake, exploration):
        config.switch_soul_config = soul.model_copy(deep=True)
        config.general_battle_config = config.general_battle_config.model_copy(
            update=preset.model_dump())


def run_ticket_farming(owner, mode) -> bool:
    """使用内存配置副本执行门票刷取。"""
    config = copy(owner.config)
    config.model = owner.config.model.model_copy(deep=True)
    limit_time = config.meta_demon.meta_demon_config.limit_time_v
    configure_ticket_farming(config.meta_demon, config.evo_zone, config.exploration)
    if mode == MetaDemonMode.AWAKE:
        settings = config.evo_zone.evo_zone_config
        settings.user_status = AwakeUserStatus.ALONE
        settings.layer = Layer.SEVEN
        runner = AwakeTicketTask(config, owner.device)
    elif mode == MetaDemonMode.EXPLORATION:
        settings = config.exploration.exploration_config
        settings.user_status = ExplorationUserStatus.ALONE
        settings.minions_cnt = 2 ** 31 - 1
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
            soul = config.evo_zone.switch_soul_config
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
