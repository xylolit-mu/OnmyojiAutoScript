from tasks.GameUi.assets import GameUiAssets
"""式神活动探索：关键字入口、宝箱、系统式神战斗及遭遇战。"""

import time

from module.atom.click import RuleClick
from module.exception import GameStuckError
from module.logger import logger
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.GeneralBattle.general_battle import BattleAction
from tasks.GameUi.default_pages import settlement_random_click
import tasks.GameUi.task_pages as pages


class ExplorationAct:
    def _exp_normal_fight_appear(self):
        """普通战斗与 Boss 战斗仅事件标题标志不同，后续流程相同。"""
        return self.appear(self.I_EVENT_FIGHT) or self.appear(self.I_EVENT_FIGHT_BOSS)

    def _wait_exp(self, predicate, timeout=10, click=None):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.screenshot()
            if predicate():
                return True
            if click is not None:
                self.appear_then_click(click, interval=1)
            time.sleep(0.2)
        return False

    def _handle_reward(self, context, config):
        if self.current_action_type.startswith('exp_') and self._clear_exp_rewards():
            return BattleAction.CONTINUE
        return super()._handle_reward(context, config)

    def _battle_settlement_click(self):
        if self.current_action_type.startswith('exp_'):
            return settlement_random_click(self.C_SAFE_RANDOM_CLICK_AREA_ACT)
        return super()._battle_settlement_click()

    def _handle_prepare(self, context, config):
        if self.current_action_type in ('exp_main', 'exp_branch'):
            self.appear_then_click(self.I_PREPARE_HIGHLIGHT, interval=0.8)
            return BattleAction.CONTINUE
        return super()._handle_prepare(context, config)

    def _handle_missing_battle_page(self, context, config, exit_matcher):
        if self.current_action_type.startswith('exp_') and self._clear_exp_rewards():
            return BattleAction.CONTINUE
        return super()._handle_missing_battle_page(context, config, exit_matcher)

    def _clear_exp_rewards(self):
        if self.appear(GameUiAssets.I_ACTIVITY_GENERAL_CLOSE_RED):
            # 部分宝箱及战斗结算会额外弹出带关闭按钮的奖励页。
            self.click(GameUiAssets.I_ACTIVITY_GENERAL_CLOSE_RED, interval=0.8)
            if not self._wait_exp(lambda: not self.appear(GameUiAssets.I_ACTIVITY_GENERAL_CLOSE_RED), timeout=5):
                raise GameStuckError('Exploration reward close button did not disappear')
            return True
        for marker, click in (
            (self.I_UI_REWARD, self.C_UI_REWARD),
            (
                self.I_SHIKIGAMI_HELP,
                settlement_random_click(self.C_SAFE_RANDOM_CLICK_AREA_ACT),
            ),
        ):
            if not self.appear(marker):
                continue
            # 每次只点一下；连点属性不能继承通用结算的概率连点。
            click.burst_count = 1
            self.click(click, interval=0.8)
            if not self._wait_exp(lambda: not self.appear(marker), timeout=5):
                raise GameStuckError(f'Exploration reward did not close: {marker.name}')
            return True
        return False

    @staticmethod
    def _exp_entry_roi(box, viewport, height):
        """OCR相对坐标映射为全入口宽度、固定高度；不点击边缘裁切行。"""
        x, y, width, view_height = viewport
        ys = [float(point[1]) for point in box]
        center_y = y + (min(ys) + max(ys)) / 2
        top = round(center_y - height / 2)
        if top < y or top + height > y + view_height:
            return None
        return x, top, width, height

    def _find_exp_entry(self, rule):
        viewport = tuple(rule.roi_back)
        x, y, _, _ = viewport
        self.screenshot()
        candidates = []
        for _, match_x, match_y, match_width, match_height in rule.match_all(self.device.image):
            # match_all返回屏幕绝对坐标；入口保持图片高度，扩展到任务栏宽度。
            box = [(match_x - x, match_y - y),
                   (match_x - x + match_width, match_y - y + match_height)]
            roi = self._exp_entry_roi(box, viewport, match_height)
            if roi is not None:
                candidates.append(roi)
        if not candidates:
            return False
        roi = min(candidates, key=lambda value: value[1])
        return RuleClick(roi, roi, name=f'{rule.name}_entry')

    def _enter_exp_entry(self, mode, rule):
        entry = self._find_exp_entry(rule)
        if not entry:
            logger.info(f'Exploration {mode}: no entry after scanning, mode complete')
            return False
        # 入口仅点击一次，等待事件最多5秒。
        entry.burst_count = 1
        self.click(entry, interval=0)
        if self._wait_exp(lambda: self.appear(self.I_EVENT_REWARD)
                          or self._exp_normal_fight_appear()
                          or self.appear(self.I_EVENT_STORY)
                          or self.appear(self.I_EVENT_STORY_SKIP_ENSURE), timeout=5):
            return True
        if mode == 'main':
            logger.info('Exploration main: no event after one click, mode complete')
            return False
        raise GameStuckError(f'Exploration {mode}: entry did not open after one click')

    def _prepare_exp_encounter(self):
        soul = self.conf.switch_soul_config
        if not self._exp_soul_switched and (soul.enable_switch_exp_encounter or soul.enable_switch_exp_encounter_by_name):
            if not self._wait_exp(lambda: self.appear(self.I_CHECK_RECORDS),
                                  click=self.I_ENCOUNTER_TO_RECORDS):
                raise GameStuckError('Cannot enter encounter shikigami records')
            if soul.enable_switch_exp_encounter_by_name:
                names = soul.exp_encounter_group_team_name.split(',', 1)
                if len(names) != 2 or not all(name.strip() for name in names):
                    raise ValueError('遭遇战御魂组名和预设名不能为空')
                self.run_switch_soul_by_name(*(name.strip() for name in names))
            else:
                parts = tuple(int(part.strip()) for part in soul.exp_encounter_group_team.split(','))
                if len(parts) != 2 or not 1 <= parts[0] <= 7 or not 1 <= parts[1] <= 5:
                    raise ValueError('遭遇战御魂预设必须为有效的组号,队伍号')
                self.run_switch_soul(parts)
            self.exit_shikigami_records()
            if not self.wait_until_appear(self.I_EVENT_FIGHT, wait_time=8):
                raise GameStuckError('Encounter did not return from shikigami records')
            self._exp_soul_switched = True
        cfg = self.conf.exp_encounter_battle_conf
        self.ui_click_until_appear(self.I_EVENT_FIGHT_UNLOCK,
            page_check=self.I_EVENT_FIGHT,
            expected_present=not (cfg.lock_team_enable),
            interval=0.8)

    def _handle_exp_story(self):
        def step():
            # 每轮优先处理确认，再点剧情跳过，支持多段接续剧情。
            if self.appear_then_click(self.I_EVENT_STORY_SKIP_ENSURE, interval=0.8):
                return False
            if self.appear_then_click(self.I_EVENT_STORY, interval=0.8):
                return False
            if self._clear_exp_rewards():
                return True
            return self._exp_main_ready()

        if not self._wait_exp(step, timeout=20):
            raise GameStuckError('Story did not finish after skipping and confirming')

    def _finish_exp_battle(self, mode):
        """战后先处理可能接续的剧情，再确认回到探索或遭遇战事件页。"""
        def ready():
            if self.appear(self.I_EVENT_STORY_SKIP_ENSURE) or self.appear(self.I_EVENT_STORY):
                self._handle_exp_story()
                return False
            if self._clear_exp_rewards():
                return False
            if mode == 'encounter' and self.appear(self.I_EVENT_FIGHT):
                return True
            return self._exp_main_ready()

        if not self._wait_exp(ready):
            raise GameStuckError('Exploration battle did not finish story or return to main page')

    def _run_exp_event(self, mode):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            self.screenshot()
            if self.appear(self.I_EVENT_REWARD):
                if mode == 'encounter':
                    raise GameStuckError('Encounter unexpectedly opened a chest event')
                if not self._wait_exp(lambda: self.appear(self.I_UI_REWARD),
                                      click=self.I_EVENT_REWARD_OPEN):
                    raise GameStuckError('Chest reward did not appear')
                self._clear_exp_rewards()
                return True
            if self._exp_normal_fight_appear():
                if mode == 'encounter':
                    self._prepare_exp_encounter()
                cfg = (self.conf.exp_encounter_battle_conf.model_copy(deep=True)
                       if mode == 'encounter' else GeneralBattleConfig())
                cfg.continuous_battle = False
                if not self._wait_exp(lambda: self.is_in_battle(False),
                                      click=self.I_EVENT_FIGHT_FIGHT):
                    raise GameStuckError('Exploration battle did not start')
                self.run_general_battle(
                    cfg, battle_key=f'activity_exp_{mode}',
                    exit_matcher=lambda: (
                        (mode == 'encounter' and self.appear(self.I_EVENT_FIGHT))
                        or (self.appear(GameUiAssets.I_PAGE_CHECK_ACT_GENERAL_1)
                            and not self._exp_normal_fight_appear()))
                    and not self.appear(self.I_SHIKIGAMI_HELP)
                    and not self.appear(self.I_UI_REWARD),
                )
                self._finish_exp_battle(mode)
                return True
            if self.appear(self.I_EVENT_STORY_SKIP_ENSURE) or self.appear(self.I_EVENT_STORY):
                self._handle_exp_story()
                return True
            time.sleep(0.2)
        raise GameStuckError(f'Exploration {mode} entry did not open an event')

    def _finish_exp_encounter_entry(self):
        """只按系统返回页面连战：事件页继续，探索页结束当前入口。"""
        while True:
            def ready():
                if self._clear_exp_rewards():
                    return False
                return self.appear(self.I_EVENT_FIGHT) or self._exp_main_ready()

            if not self._wait_exp(ready):
                raise GameStuckError('Encounter did not return to event or exploration page')
            if not self.appear(self.I_EVENT_FIGHT):
                return True
            if self.time_limit_reached():
                return False
            self._run_exp_event('encounter')

    def run_exploration(self):
        self._exp_soul_switched = False
        selected = self.conf.general_config.exploration_task_sequence
        enabled_modes = [mode for mode, label in (
            ('main', '主线'), ('encounter', '遭遇战'), ('branch', '支线')
        ) if label in selected]
        if not enabled_modes:
            logger.info('Exploration: no modes enabled, skip')
            return
        self.goto_page(pages.PAGE_ACT_GENERAL_1)
        for mode, rule in (('main', self.I_EXP_MAIN), ('encounter', self.I_EXP_ENCOUNTER),
                           ('branch', self.I_EXP_BRANCH)):
            if mode not in enabled_modes:
                continue
            self.current_action_type = f'exp_{mode}'
            count = 0
            while True:
                if self.time_limit_reached():
                    return
                self.screenshot()
                self._clear_exp_rewards()
                if not self._wait_exp(lambda: self.appear(GameUiAssets.I_PAGE_CHECK_ACT_GENERAL_1)):
                    raise GameStuckError('Expected exploration main page before selecting entry')
                if not self._enter_exp_entry(mode, rule):
                    break
                self._run_exp_event(mode)
                if mode == 'encounter' and not self._finish_exp_encounter_entry():
                    return
                if not self._wait_exp(lambda: self._exp_main_ready()):
                    raise GameStuckError('Exploration event did not return to main page')
                count += 1
                logger.info(f'Exploration {mode}: completed {count} events')

    def _exp_main_ready(self):
        if self._clear_exp_rewards():
            return False
        return (self.appear(GameUiAssets.I_PAGE_CHECK_ACT_GENERAL_1)
                and not self._exp_normal_fight_appear()
                and not self.appear(self.I_EVENT_REWARD)
                and not self.appear(self.I_EVENT_STORY)
                and not self.appear(self.I_EVENT_STORY_SKIP_ENSURE)
                and not self.appear(GameUiAssets.I_ACTIVITY_SKIP_ENSURE))
