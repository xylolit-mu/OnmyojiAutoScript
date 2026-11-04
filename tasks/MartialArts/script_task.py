# This Python file uses the following encoding: utf-8
"""武道大会战斗任务。"""

from tasks.GameUi.assets import GameUiAssets
import time
from datetime import datetime, timedelta

from cached_property import cached_property

from module.base.timer import Timer
from module.exception import TaskEnd
from module.logger import logger
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.GeneralBattle.general_battle import BattleBehaviorScope, GeneralBattle
from tasks.Component.BaseActivity.base_activity import BaseActivity
from tasks.Component.QuickLoadout.quick_loadout import QuickLoadout
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.matcher import any_of
from tasks.MartialArts.assets import MartialArtsAssets
from tasks.MartialArts.config import MartialArts
import tasks.GameUi.task_pages as pages


class ScriptTask(GeneralBattle, GameUi, SwitchSoul, QuickLoadout, BaseActivity, MartialArtsAssets):
    AP_COST = 30
    TICKET_COST = 1
    BATTLE_TIMEOUT = 600
    RESOURCE_OCR_RETRIES = 3
    CLICK_WAIT_TIMEOUT = 10
    SEARCH_BOSS_MAX_ATTEMPTS = 3

    battle_type = 'ap'

    @cached_property
    def conf(self) -> MartialArts:
        return self.config.model.martial_arts

    def _exit_matcher(self):
        """根据当前类型返回战斗结算后的任务页面标志。"""
        if self.battle_type == 'ap':
            return self.I_MAR_FIRE_AP
        if self.battle_type == 'boss':
            return any_of(GameUiAssets.I_PAGE_MARTIALARTS_CHECK_BATTLE_BOSS, GameUiAssets.I_PAGE_MARTIALARTS_CHECK_BATTLE_BOSS_MAIN)
        return None

    def _resolve_battle_timeout(self, config: GeneralBattleConfig) -> int:
        """首领战默认允许十分钟，体力战沿用通用战斗超时。"""
        if config.battle_timeout is not None and config.battle_timeout > 0:
            return config.battle_timeout
        if self.battle_type == 'boss':
            return self.BATTLE_TIMEOUT
        return super()._resolve_battle_timeout(config)

    def _get_battle_behavior_scopes(
        self,
        config: GeneralBattleConfig,
        battle_key: str,
    ) -> dict[str, BattleBehaviorScope]:
        """首领战每轮重新应用阵容预设，体力战沿用通用作用域。"""
        scopes = super()._get_battle_behavior_scopes(config, battle_key)
        if battle_key == 'martial_arts_boss':
            scopes['preset'] = BattleBehaviorScope.ROUND
        return scopes

    def before_run(self):
        configured_limit_time = self.conf.general_climb.limit_time
        self.limit_time = timedelta(
            hours=configured_limit_time.hour,
            minutes=configured_limit_time.minute,
            seconds=configured_limit_time.second,
        )
        self._task_time_limit_reached = False
        sequence = self.conf.general_climb.run_sequence_v
        logger.info(
            f'MartialArts run sequence: {sequence}, '
            f'limit_time={self.limit_time}'
        )

    def _can_start_next_battle(self) -> bool:
        """仅在新一场战斗的边界检查任务软时间限制。"""
        if self._task_time_limit_reached:
            return False
        if datetime.now() - self.start_time < self.limit_time:
            return True
        self._task_time_limit_reached = True
        logger.info(
            'MartialArts task time reached; '
            'stop before starting next battle'
        )
        return False

    def enter_ap_battle(self):
        """从任意已知页面导航至武道大会日常训练页面。"""
        logger.hr('Enter MartialArts AP battle page', 2)
        self.goto_page(pages.page_martial_arts_ap)
        logger.info('Entered MartialArts AP battle page')

    def enter_boss_battle(self):
        """从任意已知页面导航至武道大会修行合训地图。"""
        logger.hr('Enter MartialArts boss map', 2)
        self.goto_page(pages.page_martial_arts_boss)
        logger.info('Entered MartialArts boss map')

    def read_resources(self) -> tuple[int, int]:
        """读取体力和门票，多次识别取最大值以降低偶发空识别影响。"""
        best_ap = 0
        best_ticket = 0
        for attempt in range(1, self.RESOURCE_OCR_RETRIES + 1):
            self.screenshot()
            ap = self.O_AP_COUNT.ocr(self.device.image)
            ticket = self.O_AP_TICKET.ocr_digit(self.device.image)
            best_ap = max(best_ap, ap)
            best_ticket = max(best_ticket, ticket)
            logger.info(
                f'MartialArts resources OCR {attempt}/{self.RESOURCE_OCR_RETRIES}: '
                f'AP={ap}, ticket={ticket}'
            )
            if ap > self.AP_COST and ticket >= self.TICKET_COST:
                return ap, ticket
            if attempt < self.RESOURCE_OCR_RETRIES:
                time.sleep(0.5)
        return best_ap, best_ticket

    def resources_enough(self) -> bool:
        ap, ticket = self.read_resources()
        ap_enough = ap > self.AP_COST
        ticket_enough = ticket >= self.TICKET_COST
        logger.info(
            f'MartialArts resources: AP={ap} (need > {self.AP_COST}), '
            f'ticket={ticket} (need >= {self.TICKET_COST}), '
            f'enough={ap_enough and ticket_enough}'
        )
        if not ap_enough:
            return False
        if ticket_enough:
            return True

        entered = self.verify_zero_ticket(
            'MartialArts AP ticket',
            lambda: self.enter_battle(max_attempts=1),
        )
        self._ap_battle_preentered = entered
        return entered

    def read_boss_ap(self) -> int:
        """在首领战斗小界面读取体力，多次识别取最大值。"""
        best_ap = 0
        for attempt in range(1, self.RESOURCE_OCR_RETRIES + 1):
            self.screenshot()
            ap = self.O_BOSS_AP_COUNT.ocr(self.device.image)
            best_ap = max(best_ap, ap)
            logger.info(
                f'MartialArts boss AP OCR {attempt}/{self.RESOURCE_OCR_RETRIES}: '
                f'AP={ap}'
            )
            if ap > self.AP_COST:
                return ap
            if attempt < self.RESOURCE_OCR_RETRIES:
                time.sleep(0.5)
        return best_ap

    def boss_ap_enough(self) -> bool:
        """首领战每轮开始前要求体力严格大于单次消耗。"""
        ap = self.read_boss_ap()
        enough = ap > self.AP_COST
        logger.info(
            f'MartialArts boss AP={ap} (need > {self.AP_COST}), enough={enough}'
        )
        return enough

    def read_boss_tickets(self) -> tuple[int, int]:
        """读取普通和注灵门票，多次识别取最大值。"""
        best_ticket = 0
        best_gold = 0
        for attempt in range(1, self.RESOURCE_OCR_RETRIES + 1):
            self.screenshot()
            ticket = self.O_BOSS_TICKET.ocr_digit(self.device.image)
            gold = self.O_BOSS_TICKET_GOLD.ocr_digit(self.device.image)
            best_ticket = max(best_ticket, ticket)
            best_gold = max(best_gold, gold)
            logger.info(
                f'MartialArts boss tickets OCR {attempt}/{self.RESOURCE_OCR_RETRIES}: '
                f'normal={ticket}, gold={gold}'
            )
            # 普通票优先；即使已识别到金票，也继续重试可能漏识别的普通票。
            if ticket >= self.TICKET_COST:
                return ticket, gold
            if attempt < self.RESOURCE_OCR_RETRIES:
                time.sleep(0.5)
        return best_ticket, best_gold

    def select_boss_search_mode(self, use_gold: bool) -> bool:
        """切换到普通搜寻或注灵搜寻模式。"""
        target = self.I_MAR_FIRE_BOSS_GOLD if use_gold else self.I_MAR_FIRE_BOSS
        mode_name = 'gold' if use_gold else 'normal'
        timer = Timer(self.CLICK_WAIT_TIMEOUT).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(target):
                logger.info(f'MartialArts boss search mode: {mode_name}')
                return True
            if self.appear_then_click(self.I_MAR_CHANGE_BOSS_MODE, interval=0.8):
                continue
            time.sleep(0.4)
        logger.warning(f'Cannot switch MartialArts boss search mode to {mode_name}')
        return False

    def open_existing_boss(self) -> bool:
        """打开地图上已经完成搜寻、可继续挑战的首领。"""
        logger.info('MartialArts existing boss detected, open challenge panel')
        timer = Timer(self.CLICK_WAIT_TIMEOUT).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(GameUiAssets.I_PAGE_MARTIALARTS_CHECK_BATTLE_BOSS_MAIN):
                logger.info('Entered existing MartialArts boss challenge panel')
                return True
            if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1) or \
                    self.appear_then_click(self.I_UI_CONFIRM, interval=1):
                continue
            if self.appear(self.I_FIRE_OVER):
                self.click(self.C_SELECT_FIRE_BOSS, interval=1.2)
                self.device.click_record_clear()
                continue
            time.sleep(0.5)
        logger.warning(
            f'Cannot open existing MartialArts boss within {self.CLICK_WAIT_TIMEOUT}s'
        )
        return False

    def search_boss_in_mode(
        self,
        use_gold: bool,
        max_attempts: int | None = None,
    ) -> bool:
        """使用指定门票对应的搜寻模式尝试进入首领小界面。"""
        if not self.select_boss_search_mode(use_gold):
            return False
        fire_rule = self.I_MAR_FIRE_BOSS_GOLD if use_gold else self.I_MAR_FIRE_BOSS
        mode_name = 'gold' if use_gold else 'normal'
        max_attempts = max_attempts or self.SEARCH_BOSS_MAX_ATTEMPTS
        for attempt in range(1, max_attempts + 1):
            self.screenshot()
            if self.appear(GameUiAssets.I_PAGE_MARTIALARTS_CHECK_BATTLE_BOSS_MAIN):
                logger.info('MartialArts boss found, entered challenge panel')
                return True

            if not self.appear_then_click(fire_rule, interval=0):
                logger.warning(
                    f'MartialArts boss search button not found '
                    f'({attempt}/{max_attempts})'
                )
            else:
                logger.info(
                    f'MartialArts boss {mode_name} search clicked '
                    f'({attempt}/{max_attempts}), '
                    f'wait up to {self.CLICK_WAIT_TIMEOUT}s for challenge panel'
                )
                self.device.click_record_clear()

            wait_timer = Timer(self.CLICK_WAIT_TIMEOUT).start()
            while not wait_timer.reached():
                self.screenshot()
                if self.appear(GameUiAssets.I_PAGE_MARTIALARTS_CHECK_BATTLE_BOSS_MAIN):
                    logger.info('MartialArts boss found, entered challenge panel')
                    return True
                if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=0.5) or \
                        self.appear_then_click(self.I_UI_CONFIRM, interval=0.5):
                    continue
                time.sleep(0.2)

        logger.warning(
            f'MartialArts boss {mode_name} search rejected, '
            'corresponding ticket may be insufficient'
        )
        return False

    def search_boss(self) -> bool:
        """按各自门票状态执行普通搜寻或注灵搜寻。"""
        self.screenshot()
        if self.appear(self.I_FIRE_OVER):
            return self.open_existing_boss()

        ticket, gold = self.read_boss_tickets()
        # 普通票始终优先。
        if ticket >= self.TICKET_COST:
            logger.info('Try MartialArts boss normal search')
            if self.search_boss_in_mode(False):
                return True
        else:
            # OCR 为 0 可能是漏识别。实际探查一次，未进入首领小界面才确认无票。
            if self.verify_zero_ticket(
                'MartialArts normal boss ticket',
                lambda: self.search_boss_in_mode(False, max_attempts=1),
            ):
                return True

        if gold >= self.TICKET_COST:
            logger.info('Try MartialArts boss gold search')
            if self.search_boss_in_mode(True):
                return True
        else:
            if self.verify_zero_ticket(
                'MartialArts gold boss ticket',
                lambda: self.search_boss_in_mode(True, max_attempts=1),
            ):
                return True
            logger.info('MartialArts boss tickets are unavailable, stop boss search')
            return False

        logger.info('No MartialArts boss search mode succeeded')
        return False

    def switch_soul_before_battle(self, battle_type: str):
        """按体力战/首领战各自配置，在该类型首次执行前切换御魂。"""
        conf = self.conf.switch_soul_config
        enable_number = getattr(conf, f'enable_switch_{battle_type}')
        enable_name = getattr(conf, f'enable_switch_{battle_type}_by_name')
        if not enable_number and not enable_name:
            return

        conf.validate_switch_soul()
        logger.hr(f'Switch MartialArts {battle_type} soul', 2)
        enter_records = (
            self.I_BATTLE_BOSS_TO_RECORDS
            if battle_type == 'boss'
            else self.I_BATTLE_MAIN_TO_RECORDS
        )
        self.ui_click(enter_records, stop=self.I_CHECK_RECORDS, interval=1)
        if enable_name:
            group, team = getattr(conf, f'{battle_type}_group_team_name').split(',')
            self.run_switch_soul_by_name(group, team)
        else:
            self.run_switch_soul(getattr(conf, f'{battle_type}_group_team'))

        if battle_type == 'ap':
            self.goto_page(pages.page_martial_arts_ap)
        elif battle_type == 'boss':
            self.goto_page(pages.page_martial_arts_boss_main)

    def lock_team(self, battle_conf: GeneralBattleConfig):
        """根据当前战斗类型配置锁定或解锁阵容。"""
        lock_rule = self.I_BOSS_LOCK if self.battle_type == 'boss' else self.I_AP_LOCK
        unlock_rule = self.I_BOSS_UNLOCK if self.battle_type == 'boss' else self.I_AP_UNLOCK
        if battle_conf.lock_team_enable:
            logger.info(f'Lock MartialArts {self.battle_type} team')
            self.ui_click(unlock_rule, stop=lock_rule, interval=1.5)
            return
        logger.info(f'Unlock MartialArts {self.battle_type} team')
        self.ui_click(lock_rule, stop=unlock_rule, interval=1.5)

    def enter_battle(self, max_attempts: int | None = None) -> bool:
        """点击体力挑战并等待进入通用准备/战斗页面。"""
        timer = Timer(self.CLICK_WAIT_TIMEOUT).start()
        click_count = 0
        while not timer.reached():
            self.screenshot()
            if self.is_in_battle(False):
                logger.info(f'Entered battle after {click_count} challenge clicks')
                return True
            if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1) or \
                    self.appear_then_click(self.I_UI_CONFIRM, interval=1):
                continue
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                logger.warning('Challenge rejected, AP or ticket may be insufficient')
                return False
            if self.appear(self.I_MAR_FIRE_AP):
                if max_attempts is not None and click_count >= max_attempts:
                    logger.warning('AP challenge fallback did not enter battle after one click')
                    return False
                self.click(self.I_MAR_FIRE_AP, interval=1.5)
                click_count += 1
                self.device.click_record_clear()
                continue
            time.sleep(0.5)
        logger.warning(f'Cannot enter MartialArts battle within {self.CLICK_WAIT_TIMEOUT}s')
        return False

    def run_battle_round(self, battle_conf: GeneralBattleConfig) -> bool:
        """执行一轮体力挑战并等待结算返回日常训练页面。"""
        if self._ap_battle_preentered:
            self._ap_battle_preentered = False
        elif not self.enter_battle():
            return False
        win = self.run_general_battle(
            battle_conf,
            battle_key='martial_arts_ap',
            exit_matcher=self.I_MAR_FIRE_AP,
        )
        logger.info(f'MartialArts AP battle {self.current_count} result: {"win" if win else "lose"}')
        return True

    def run_ap_battles(self):
        """按体力战配置执行完整循环。"""
        self.battle_type = 'ap'
        self.current_count = 0
        self._ap_battle_preentered = False
        limit = self.conf.general_climb.ap_limit
        battle_conf = self.conf.ap_battle_conf

        self.enter_ap_battle()
        self.switch_soul_before_battle('ap')
        self.lock_team(battle_conf)
        while self.current_count < limit:
            if not self._can_start_next_battle():
                break
            self.goto_page(pages.page_martial_arts_ap)
            if not self.resources_enough():
                logger.info('MartialArts resources are insufficient, stop AP battles')
                break
            if not self.run_battle_round(battle_conf):
                break
        logger.info(f'MartialArts AP battles finished: {self.current_count}/{limit}')

    def enter_boss_fight(self) -> bool:
        """在战斗小界面点击开始挑战并等待进入通用战斗页面。"""
        timer = Timer(self.CLICK_WAIT_TIMEOUT).start()
        click_count = 0
        while not timer.reached():
            self.screenshot()
            if self.is_in_battle(False):
                logger.info(f'Entered boss battle after {click_count} challenge clicks')
                return True
            if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1) or \
                    self.appear_then_click(self.I_UI_CONFIRM, interval=1):
                continue
            if self.appear_then_click(self.I_MAR_FIRE_BOSS_MAIN, interval=1.5) or self.appear_then_click(self.I_MAR_FIRE_BOSS_MAIN_AGAIN, interval=1.5):
                click_count += 1
                self.device.click_record_clear()
                continue
            time.sleep(0.5)
        if click_count:
            logger.warning(
                f'Boss challenge did not enter battle after {click_count} clicks; '
                'AP may be insufficient'
            )
        else:
            logger.warning(
                f'Boss challenge button not found within {self.CLICK_WAIT_TIMEOUT}s'
            )
        return False

    def run_boss_battle_round(self, battle_conf: GeneralBattleConfig) -> bool:
        """执行一轮首领挑战并等待结算返回首领界面。"""
        if not self.enter_boss_fight():
            return False
        win = self.run_general_battle(
            battle_conf,
            battle_key='martial_arts_boss',
            exit_matcher=any_of(GameUiAssets.I_PAGE_MARTIALARTS_CHECK_BATTLE_BOSS, GameUiAssets.I_PAGE_MARTIALARTS_CHECK_BATTLE_BOSS_MAIN),
        )
        logger.info(f'MartialArts boss battle {self.current_count} result: {"win" if win else "lose"}')
        return True

    def run_boss_battles(self):
        """按首领战配置搜寻并挑战首领，普通券优先、注灵券兜底。"""
        self.battle_type = 'boss'
        self.current_count = 0
        limit = self.conf.general_climb.boss_limit
        quick_loadout_conf = self.conf.boss_quick_loadout_config
        battle_conf = self.conf.boss_battle_conf
        if quick_loadout_conf.enable and battle_conf.preset_enable:
            logger.warning('Boss quick loadout enabled, disable legacy battle preset for this MartialArts run')
            battle_conf = battle_conf.model_copy(update={'preset_enable': False})
        soul_switched = False

        self.enter_boss_battle()
        while self.current_count < limit:
            if not self._can_start_next_battle():
                break
            self.goto_page(pages.page_martial_arts_boss)
            if not self.search_boss():
                break
            if not self.boss_ap_enough():
                logger.info('MartialArts AP is insufficient, stop boss battles')
                break
            if not quick_loadout_conf.enable and not soul_switched:
                self.switch_soul_before_battle('boss')
                soul_switched = True
            if quick_loadout_conf.enable and not self.run_quick_loadout(
                quick_loadout_conf,
                frame=self.F_MR_BOSS_QUICK_LOADOUT_FRAME,
                name_ocr=self.O_BOSS_NAME,
            ):
                logger.warning('MartialArts boss quick loadout failed, stop boss battles')
                break
            self.lock_team(battle_conf)
            if not self.run_boss_battle_round(battle_conf):
                break
        logger.info(f'MartialArts boss battles finished: {self.current_count}/{limit}')

    def run(self):
        self.before_run()
        for battle_type in self.conf.general_climb.run_sequence_v:
            if not self._can_start_next_battle():
                break
            if battle_type == 'ap':
                self.run_ap_battles()
                continue
            if battle_type == 'boss':
                self.run_boss_battles()

        self.finish_task_navigation()
        self.set_next_run(task='MartialArts', success=True, finish=True)
        raise TaskEnd('MartialArts')
