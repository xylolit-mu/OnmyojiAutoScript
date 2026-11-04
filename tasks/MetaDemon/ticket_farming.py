"""超鬼王门票刷取，复用觉醒和探索的战斗流程。"""
from copy import copy
from datetime import datetime

from module.exception import TaskEnd
from module.logger import logger
from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
from tasks.EvoZone.config import Layer, UserStatus as AwakeUserStatus
from tasks.EvoZone.script_task import ScriptTask as AwakeTask
from tasks.Exploration.config import UserStatus as ExplorationUserStatus
from tasks.Exploration.script_task import ScriptTask as ExplorationTask
from tasks.GameUi.default_pages import page_reward
import tasks.GameUi.task_pages as pages
from tasks.MetaDemon.config import MetaDemonMode


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
        if not con_scrolls.scrolls_enable or current_page not in (
            pages.page_mainline, pages.page_gameplay,
            pages.page_exploration, pages.page_exp_entrance,
        ):
            return
        rule = (self.O_REALM_RAID_NUMBER1_META if current_page == pages.page_exp_entrance
                else self.O_REALM_RAID_NUMBER_META)
        current, _, total = rule.ocr_digit_counter(self.device.image)
        if total <= 0 or current < con_scrolls.scrolls_threshold:
            return
        if con.buff_gold_50_click or con.buff_gold_100_click or con.buff_exp_50_click or con.buff_exp_100_click:
            self.goto_page(pages.page_main)
            self.open_buff()
            self.gold_50(is_open=False)
            self.gold_100(is_open=False)
            self.exp_50(is_open=False)
            self.exp_100(is_open=False)
            self.close_buff()
        self.goto_page(pages.page_mainline)
        # 通过原任务配置写调度，防止刷票用的配置副本被保存。
        owner = self.ticket_farming_owner
        owner.set_next_run(task='MetaDemon', server=False,
                           target=datetime.now() + con_scrolls.scrolls_cd)
        owner.set_next_run(task='RealmRaid', server=False, target=datetime.now())
        logger.info('突破票达到阈值，唤醒个人突破；超鬼王探索刷票等待下一次调度')
        raise TaskEnd


def run_ticket_farming(owner, mode) -> bool:
    """使用内存配置副本；突破衔接通过原任务写入调度。"""
    config = copy(owner.config)
    config.model = owner.config.model.model_copy(deep=True)
    limit_time = config.meta_demon.meta_demon_config.limit_time_v
    if mode == MetaDemonMode.AWAKE:
        settings = config.evo_zone.evo_zone_config
        settings.user_status = AwakeUserStatus.ALONE
        settings.layer = Layer.SEVEN
        runner = AwakeTicketTask(config, owner.device)
    elif mode == MetaDemonMode.EXPLORATION:
        settings = config.exploration.exploration_config
        settings.user_status = ExplorationUserStatus.ALONE
        settings.minions_cnt = 2 ** 31 - 1
        config.exploration.scrolls.scrolls_enable = True
        runner = ExplorationTicketTask(config, owner.device)
        runner.ticket_farming_owner = owner
    else:
        raise ValueError(f'未知门票刷取模式: {mode}')
    runner.start_time = datetime.now()
    runner.current_count = 0
    runner.limit_count = 2 ** 31 - 1
    runner.install_ticket_reward_hook()
    try:
        if mode == MetaDemonMode.AWAKE:
            runner.limit_time = limit_time
            runner.run_alone()
        else:
            runner.pre_process()
            runner.limit_time = limit_time
            runner.exec_exp_page()
    except TicketsFull:
        return True
    return False
