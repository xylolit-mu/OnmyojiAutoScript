"""当期爬塔独有页面与执行逻辑。"""

from datetime import datetime
import random
import time

from module.exception import GameStuckError
from module.logger import logger
from tasks.ActivityShikigami.assets import ActivityShikigamiAssets
from tasks.ActivityShikigami.base_act import ActivityResourceNotEnough
import tasks.ActivityShikigami.page as pages


class NormalClimbAct:
    """体力、门票、首领和百体四种爬塔战斗。"""

    def setup_climb_pages(self):
        page_act = self.navigator.resolve_page(pages.page_act)
        page_climb_main = self.navigator.resolve_page(pages.page_climb_main)
        page_pass = self.navigator.resolve_page(pages.page_climb_pass)
        page_ap = self.navigator.resolve_page(pages.page_climb_ap)
        page_ap100 = self.navigator.resolve_page(pages.page_climb_ap100)
        page_boss = self.navigator.resolve_page(pages.page_climb_boss)

        page_act.connect(
            page_climb_main,
            ActivityShikigamiAssets.I_TO_BATTLE_MAIN,
            key='activity->climb_main',
        )
        page_climb_main.connect(
            page_ap,
            ActivityShikigamiAssets.I_TO_BATTLE_CLIMB,
            key='climb_main->climb_ap',
        )
        page_ap.add_enter_failure_hooks(pages.conditional_action(
            condition=ActivityShikigamiAssets.I_CLIMB_MODE_PASS,
            action=ActivityShikigamiAssets.I_CLIMB_MODE_SWITCH,
        ))
        page_climb_main.connect(
            page_pass,
            ActivityShikigamiAssets.I_TO_BATTLE_CLIMB,
            key='climb_main->climb_pass',
        )
        page_pass.add_enter_failure_hooks(pages.conditional_action(
            condition=ActivityShikigamiAssets.I_CLIMB_MODE_AP,
            action=ActivityShikigamiAssets.I_CLIMB_MODE_SWITCH,
        ))
        page_climb_main.connect(
            page_ap100,
            ActivityShikigamiAssets.I_TO_BATTLE_CLIMB,
            key='climb_main->climb_ap100',
        )
        # 本期活动 Boss 入口在活动主界面，直接从 page_act 进入。
        page_act.connect(
            page_boss,
            ActivityShikigamiAssets.I_TO_BATTLE_BOSS,
            key='activity->climb_boss',
        )
        page_pass.connect(page_ap, ActivityShikigamiAssets.I_CLIMB_MODE_SWITCH, key='climb_pass->climb_ap')
        page_ap.connect(page_pass, ActivityShikigamiAssets.I_CLIMB_MODE_SWITCH, key='climb_ap->climb_pass')

    def run_climb(self):
        logger.hr('Start activity: Climb', 1)
        self.setup_climb_pages()
        for action_type in self.conf.general_config.climb_sequence_v:
            if self.time_limit_reached():
                return
            self._run_climb_type(action_type)

    def _run_climb_type(self, action_type: str):
        if action_type == 'pass':
            # 进入共用爬塔界面，不要求切到门票模式，先统一读取门票。
            self.goto_page(pages.page_climb_ap, accepted_pages=(pages.page_climb_pass,))
            remain = self._read_shared_climb_tickets()
            # 困难模式收益优先；任一模式次数为 0 时直接跳过。
            for pass_mode in ('hard', 'easy'):
                pass_limit = self.conf.general_config.pass_limit_for(pass_mode)
                if pass_limit <= 0:
                    logger.info(f'Skip pass mode {pass_mode}: limit is 0')
                    continue
                if self.time_limit_reached():
                    return
                required = 5 if pass_mode == 'hard' else 1
                if remain < required:
                    logger.info(f'Skip pass mode {pass_mode}: tickets={remain}, required={required}')
                    continue
                self._run_climb_branch(action_type, pass_mode=pass_mode)
                # 两种难度共用门票，前一分支消耗后更新余量再筛选下一分支。
                remain = self._read_shared_climb_tickets()
            self.current_pass_mode = None
            return

        self._run_climb_branch(action_type)

    def _read_shared_climb_tickets(self):
        self.screenshot()
        return self._update_climb_consumable_count(
            'pass', self.O_REMAIN_PASS.ocr_digit(self.device.image))

    def _run_climb_branch(self, action_type: str, pass_mode: str = None):
        logger.hr(f'Start climb type: {action_type}', 2)
        self.current_action_type = action_type
        self.current_pass_mode = pass_mode
        destination = getattr(pages, f'page_climb_{action_type}')
        self.goto_page(destination)
        if pass_mode is not None:
            self._sync_pass_difficulty(pass_mode)
        self._sync_climb_team_lock(action_type)

        while True:
            if pass_mode is not None:
                mode_limit = self.conf.general_config.pass_limit_for(pass_mode)
                if self.pass_action_count[pass_mode] >= mode_limit:
                    logger.info(
                        f'Pass mode {pass_mode} count limit reached: '
                        f'{self.pass_action_count[pass_mode]}/{mode_limit}'
                    )
                    return
            self.screenshot()
            if self.appear_then_click(self.I_USELESS_MESSAGE_CLOSE, interval=1):
                continue
            if self.appear_then_click(
                self.I_AUTOFIGHT,
                action=self.C_RANDOM_CLOSE_AUTOFIGHT,
                interval=1,
            ):
                continue
            current_page = self.get_current_page()
            if current_page == destination:
                # 五倍卷只支持普通体力战斗，门票等分支不读取也不切换。
                if action_type == 'ap':
                    self._sync_climb_penta_pass()
                if not self.prepare_next_action(action_type):
                    return
                self._handle_climb_drink_break()
                try:
                    self._run_climb_action(action_type, destination)
                except ActivityResourceNotEnough:
                    branch = f'/{pass_mode}' if pass_mode else ''
                    logger.info(
                        f'Climb resource exhausted: {action_type}{branch}'
                    )
                    # 困难门票不足时关闭提示，仅结束困难分支，随后可执行简单模式。
                    self.screenshot()
                    if self.appear_then_click(self.I_UI_BACK_RED, interval=0):
                        self.device.click_record_clear()
                    return
                continue
            if current_page in (pages.page_battle_prepare, pages.page_battle):
                self.run_general_battle(
                    self.battle_config(action_type),
                    battle_key=f'activity_{action_type}',
                )
                continue
            if current_page == pages.page_reward:
                self.click(self._battle_settlement_click(), interval=1.5)
                continue
            if current_page is None:
                time.sleep(0.5)
                continue
            self.goto_page(destination)

    def _start_climb_drink_timer(self, interval_range: tuple[int, int]) -> None:
        limit_minutes = random.randint(*interval_range)
        self._climb_drink_started_at = time.monotonic()
        self._climb_drink_limit_seconds = limit_minutes * 60
        self._climb_drink_interval_range = interval_range
        logger.info(
            'Climb drink timer started: '
            f'limit={limit_minutes}m, '
            f'range={interval_range[0]}-{interval_range[1]}m'
        )

    def _handle_climb_drink_break(self) -> None:
        """爬塔内部连续运行达到间隔后，在下一场开始前暂停。"""
        config = self.conf.general_config
        if not config.climb_drink_break:
            return

        parts = config.climb_drink_interval.split(',')
        interval_range = int(parts[0]), int(parts[1])
        started_at = getattr(self, '_climb_drink_started_at', None)
        previous_range = getattr(self, '_climb_drink_interval_range', None)
        if started_at is None or previous_range != interval_range:
            self._start_climb_drink_timer(interval_range)
            return

        elapsed = time.monotonic() - started_at
        if elapsed < self._climb_drink_limit_seconds:
            return

        if random.random() < 0.9:
            rest_minutes = random.triangular(2, 8, 5)
            branch = 'short'
        else:
            rest_minutes = random.triangular(8, 20, 8)
            branch = 'long_tail'
        rest_seconds = max(120, min(1200, round(rest_minutes * 60)))
        logger.info(
            'Climb drink break started: '
            f'elapsed={elapsed / 60:.1f}m, '
            f'rest={rest_seconds / 60:.1f}m, distribution={branch}'
        )
        rest_started_at = datetime.now()
        time.sleep(rest_seconds)
        # 内部喝水休息不占用式神活动的任务时限。
        self.start_time += datetime.now() - rest_started_at
        logger.info('Climb drink break finished; continue current climb task')
        self._start_climb_drink_timer(interval_range)

    def _run_climb_action(self, action_type: str, destination):
        if not self._climb_resource_available(action_type):
            raise ActivityResourceNotEnough

        soul_action_type = action_type
        if action_type == 'pass' and self.current_pass_mode == 'hard':
            # 困难门票复用百体模式的御魂预设及切换状态。
            soul_action_type = 'ap100'
            logger.info('Pass hard mode uses ap100 soul preset')
        self.switch_soul_for(
            soul_action_type,
            self.I_BATTLE_MAIN_TO_RECORDS,
            return_page=destination,
            exit_records=True,
        )
        if action_type == 'pass' and self.current_pass_mode is not None:
            # 首次切换御魂返回后重新确认难度，避免页面往返重置选择。
            self._sync_pass_difficulty(self.current_pass_mode)
        entered = self._enter_climb_battle(action_type)
        if not entered:
            raise ActivityResourceNotEnough

        self._record_climb_consumption(action_type)
        self.record_action(action_type)
        if action_type == 'pass' and self.current_pass_mode is not None:
            self.pass_action_count[self.current_pass_mode] += 1
            mode_limit = self.conf.general_config.pass_limit_for(
                self.current_pass_mode
            )
            logger.info(
                f'Pass mode {self.current_pass_mode} action count: '
                f'{self.pass_action_count[self.current_pass_mode]}/{mode_limit}'
            )
        self.run_general_battle(
            self.battle_config(action_type),
            battle_key=f'activity_{action_type}',
        )

    def _climb_fire_rule(self, action_type: str):
        return self.I_AS_BOSS_FIRE if action_type == 'boss' else self.I_ACT_FIRE

    def _climb_penta_enabled(self, action_type: str) -> bool:
        """返回当前体力分支是否实际启用了五倍挑战。"""
        return (
            action_type == 'ap'
            and self.penta_pass_active
            and self.climb_consumable_count['penta_pass'] > 0
        )

    def _climb_resource_consumption(self, action_type: str) -> int:
        """返回当前分支一场战斗应消耗的主资源数量。"""
        if action_type == 'ap':
            return 30 if self._climb_penta_enabled(action_type) else 6
        hard_pass = (
            action_type == 'pass'
            and self.current_pass_mode == 'hard'
        )
        return 5 if hard_pass else 1

    def _climb_ap_pass_consumption(self, action_type: str) -> int:
        """返回体力挑战门票的单场消耗量。"""
        return 5 if self._climb_penta_enabled(action_type) else 1

    def _record_climb_consumption(self, action_type: str) -> None:
        """成功进入战斗时保存本场不可变的资源消耗快照。"""
        penta_enabled = self._climb_penta_enabled(action_type)
        resource_consumption = self._climb_resource_consumption(action_type)
        penta_consumption = 1 if penta_enabled else 0
        self.climb_pending_consumption[action_type] = resource_consumption
        ap_pass_consumption = '-'
        if action_type == 'ap':
            ap_pass_consumption = self._climb_ap_pass_consumption(action_type)
            self.climb_pending_consumption['ap_pass'] = ap_pass_consumption
        self.climb_pending_consumption['penta_pass'] = penta_consumption
        logger.info(
            'Record climb consumption snapshot: '
            f'resource={action_type}:{resource_consumption}, '
            f'ap_pass={ap_pass_consumption}, '
            f'penta_pass={penta_consumption}'
        )

    def _sync_climb_penta_pass(self) -> None:
        """按通用配置及剩余数量同步五倍卷开关。"""
        configured = self.conf.general_config.use_penta_pass
        remain = None
        desired_enabled = False
        pending_consumption = self.climb_pending_consumption['penta_pass']
        if configured or pending_consumption > 0:
            raw_remain = self.O_REMAIN_PENTA_PASS.ocr_digit(
                self.device.image
            )
            remain = self._update_climb_consumable_count(
                'penta_pass', raw_remain
            )
            desired_enabled = configured and remain > 0
            if not desired_enabled:
                logger.info('Climb penta pass exhausted; disable penta mode')

        enabled_rule = self.I_FIGHT_PENTA_USE
        disabled_rule = self.I_FIGHT_PENTA_DISUSE
        target_rule = enabled_rule if desired_enabled else disabled_rule
        click_rule = disabled_rule if desired_enabled else enabled_rule

        for attempt in range(1, 4):
            self.screenshot()
            if self.appear(target_rule):
                self.penta_pass_active = desired_enabled
                logger.debug(
                    'Climb penta mode synchronized: '
                    f'enabled={desired_enabled}, remain={remain}'
                )
                return
            if not self.appear(click_rule):
                self.penta_pass_active = self.appear(enabled_rule)
                logger.warning(
                    'Cannot identify climb penta toggle state; '
                    f'enabled={desired_enabled}, remain={remain}'
                )
                return
            self.click(click_rule, interval=0)
            time.sleep(0.5)
            logger.debug(
                'Toggle climb penta mode: '
                f'enabled={desired_enabled}, attempt={attempt}/3'
            )

        logger.warning(
            'Failed to synchronize climb penta mode after 3 attempts: '
            f'enabled={desired_enabled}, remain={remain}'
        )
        self.screenshot()
        self.penta_pass_active = self.appear(enabled_rule)

    def _sync_pass_difficulty(self, mode: str) -> None:
        """点击并确认门票简单/困难分支，失败三次后报错。"""
        if mode == 'hard':
            target_rule = self.I_CHECK_CLIMB_HARD
            click_rule = self.C_CL_SELECT_HARD
        elif mode == 'easy':
            target_rule = self.I_CHECK_CLIMB_EASY
            click_rule = self.C_CL_SELECT_EASY
        else:
            raise ValueError(f'Unsupported pass mode: {mode}')

        for attempt in range(1, 4):
            self.screenshot()
            if self.appear(target_rule):
                logger.info(f'Pass mode ready: {mode}')
                return
            self.click(click_rule, interval=0)
            if self.wait_until_appear(target_rule, wait_time=3):
                self.device.click_record_clear()
                logger.info(
                    f'Pass mode selected: {mode}, attempt={attempt}/3'
                )
                return
            logger.warning(
                f'Pass mode selection timeout: {mode}, attempt={attempt}/3'
            )

        raise GameStuckError(
            f'Failed to select pass mode {mode} after 3 attempts'
        )

    @staticmethod
    def _normalize_climb_consumable_count(
            name: str,
            raw_count: int,
            previous_count: int,
            expected_consumption: int,
    ) -> int:
        """根据上一场消耗快照修正任意爬塔资源的 OCR 异常下降。"""
        if previous_count < 0:
            if raw_count <= 0:
                logger.info(
                    f'Climb {name} count is 0 on entry; resource exhausted'
                )
            return max(raw_count, 0)

        if expected_consumption <= 0:
            return max(raw_count, 0)

        expected_count = max(previous_count - expected_consumption, 0)
        if raw_count < expected_count:
            logger.warning(
                f'Climb {name} OCR decreased beyond consumption snapshot: '
                f'previous={previous_count}, raw={raw_count}, '
                f'consumption={expected_consumption}, '
                f'corrected={expected_count}'
            )
            return expected_count

        if raw_count < previous_count:
            logger.info(
                f'Climb {name} count decreased: '
                f'{previous_count} -> {raw_count}, '
                f'expected_consumption={expected_consumption}'
            )
        return raw_count

    def _update_climb_consumable_count(
            self, name: str, raw_count: int
    ) -> int:
        """用公共修复器更新一种爬塔资源，并消费其待确认快照。"""
        previous_count = self.climb_consumable_count[name]
        expected_consumption = self.climb_pending_consumption[name]
        remain = self._normalize_climb_consumable_count(
            name=name,
            raw_count=raw_count,
            previous_count=previous_count,
            expected_consumption=expected_consumption,
        )
        self.climb_consumable_count[name] = remain
        self.climb_pending_consumption[name] = 0
        logger.info(
            f'Climb {name} remain: raw={raw_count}, normalized={remain}, '
            f'previous={previous_count}, '
            f'expected_consumption={expected_consumption}'
        )
        return remain

    def _enter_climb_battle(self, action_type: str) -> bool:
        click_times = 0
        max_times = random.randint(3, 5)
        fire_rule = self._climb_fire_rule(action_type)
        while True:
            self.screenshot()
            if self.is_in_battle(False):
                return True
            if click_times >= max_times:
                logger.warning(f'{action_type} cannot enter battle, click reach max times')
                raise ActivityResourceNotEnough
            if self.appear(self.I_UI_BACK_RED, interval=1):
                logger.warning(f'{action_type} cannot enter battle, resource dialog appeared')
                raise ActivityResourceNotEnough
            if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1) or \
                    self.appear_then_click(self.I_UI_CONFIRM, interval=1):
                continue
            if action_type == 'boss':
                clicked = self.appear_then_click(fire_rule, interval=1)
            else:
                clicked = False
                if self.appear(self.I_ACT_FIRE, interval=1):
                    clicked = self.click(self.C_START_FIRE, interval=1)
            if clicked:
                self.device.click_record_clear()
                click_times += 1
                logger.info(f'Try click fire, remain times[{max_times - click_times}]')

    def _sync_climb_team_lock(self, action_type: str):
        enable = self.battle_config(action_type).lock_team_enable
        if action_type == 'boss':
            lock_rule, unlock_rule = self.I_LOCK, self.I_UNLOCK
        else:
            lock_rule, unlock_rule = self.I_AP_LOCK, self.I_AP_UNLOCK
        if enable:
            logger.info(f'Lock {action_type} team')
            self.ui_click(unlock_rule, stop=lock_rule, interval=1.5)
        else:
            logger.info(f'Unlock {action_type} team')
            self.ui_click(lock_rule, stop=unlock_rule, interval=1.5)

    def _climb_resource_available(self, action_type: str) -> bool:
        logger.hr(f'Check {action_type} resource')
        self.screenshot()
        if action_type == 'pass':
            raw_remain = self.O_REMAIN_PASS.ocr_digit(self.device.image)
        elif action_type == 'ap':
            raw_remain = self.O_REMAIN_AP.ocr_quantity(self.device.image)
            raw_ap_pass = self.O_REMAIN_AP_PASS.ocr_digit(
                self.device.image
            )
        elif action_type == 'boss':
            _, raw_remain, _ = self.O_REMAIN_BOSS.ocr_digit_counter(self.device.image)
        else:
            raw_remain = self.O_REMAIN_AP100.ocr_digit(self.device.image)

        remain = self._update_climb_consumable_count(
            action_type, raw_remain
        )
        ap_pass_remain = None
        if action_type == 'ap':
            ap_pass_remain = self._update_climb_consumable_count(
                'ap_pass', raw_ap_pass
            )
        required = self._climb_resource_consumption(action_type)
        ap_pass_required = (
            self._climb_ap_pass_consumption(action_type)
            if action_type == 'ap'
            else None
        )
        if remain < required or (
            ap_pass_remain is not None
            and ap_pass_remain < ap_pass_required
        ):
            logger.info(
                f'Climb {action_type} resource below branch requirement: '
                f'remain={remain}, required={required}, '
                f'ap_pass_remain={ap_pass_remain}, '
                f'ap_pass_required={ap_pass_required}, '
                f'mode={self.current_pass_mode}'
            )
            return False
        return True
