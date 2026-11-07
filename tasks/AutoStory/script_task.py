import time
import random

from module.exception import TaskEnd
from module.logger import logger
from tasks.AutoStory.assets import AutoStoryAssets
from tasks.AutoStory.config import AutoStory
from tasks.Component.GeneralBattle.general_battle import BattleAction, GeneralBattle
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.GameUi.default_pages import random_click
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_battle_prepare, page_battle_result, page_reward


class _StoryFinished(Exception):
    """识别到庭院主界面，正常结束当前辅助流程。"""


class ScriptTask(GeneralBattle, GameUi, AutoStoryAssets):
    conf: AutoStory

    def screenshot(self):
        while True:
            result = super().screenshot()
            # 加载覆盖层出现时只等待和刷新，不执行点击或庭院结束判断。
            if self.appear(self.I_LOADING_1) or self.appear(self.I_LOADING_2):
                time.sleep(0.4)
                continue
            if getattr(self, '_story_monitor_enabled', False):
                if self.appear(self.I_NEW_ONMYOJI_RED_CLOSE):
                    self.device.click_record_clear()
                    if self.appear_then_click(self.I_NEW_ONMYOJI_RED_CLOSE, interval=0.4):
                        continue
                self._check_story_finished()
            return result

    def _check_story_finished(self) -> None:
        """每秒检查庭院，使用全局皮肤配置替换后的主界面资源。"""
        now = time.monotonic()
        if now - self._story_last_check < 1:
            return
        self._story_last_check = now
        if (self._on_courtyard_main()
                and not self.appear(self.I_STORY_CHAT_1)
                and not self.appear(self.I_STORY_CHAT_SKIP)
                and not self.appear(self.I_NEW_ONMYOJI_RED_CLOSE)):
            raise _StoryFinished

    def _on_courtyard_main(self) -> bool:
        return self.appear(self.I_CHECK_MAIN) and not self.appear(self.I_CHECK_MAIN_SET)

    def run(self) -> None:
        self.conf = self.config.auto_story
        success = self.run_story()
        self.set_next_run('AutoStory', finish=True, success=success)
        raise TaskEnd

    def run_story(self) -> bool:
        self._story_monitor_enabled = False
        self.screenshot()
        if not (self.appear(self.I_STORY_BACK)
                or (self._on_courtyard_main() and self.appear(self.I_STORY_CHAT_1))):
            logger.info('未识别到剧情返回标志，跳过自动剧情')
            return False
        self._story_last_check = time.monotonic()
        self._reset_story_idle()
        self._story_unknown_click_at = 0
        self._story_monitor_enabled = True
        try:
            while True:
                self.screenshot()
                self._handle_story_frame()
                time.sleep(0.4)
        except _StoryFinished:
            logger.info('识别到庭院主界面且无待处理对话，自动剧情结束')
            return True
        finally:
            self._story_monitor_enabled = False

    def _handle_story_video(self) -> bool:
        # 视频确认和跳过均全局检测，确认优先。
        confirm = self.appear_then_click(self.I_STORY_VIDEO_SKIP_CONFIRM, interval=0.4)
        skip = self.appear_then_click(self.I_STORY_VIDEO_SKIP, interval=2)
        return bool(confirm or skip)

    def _run_story_battle(self) -> None:
        self.run_general_battle(
            config=GeneralBattleConfig(), battle_key='autostory',
            exit_matcher=self.I_STORY_BACK,
        )
        self._reset_story_idle()

    def _wait_story_prepare(self) -> bool:
        """入口点击后最多等待三秒；未进入准备页则回到外层重新识别。"""
        deadline = time.monotonic() + 3
        while True:
            self.screenshot()
            if self.detect_page_in(page_battle_prepare, include_global=False) is not None:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.info('点击剧情战斗后3秒未识别到准备页，重新识别当前页面')
                return False
            time.sleep(min(0.4, remaining))

    def _handle_story_frame(self) -> None:
        # 实时剧情会长期重复入口/跳过等合法操作，不跨轮累计点击保护记录。
        # 通用战斗仍使用自身的点击保护和清理流程。
        self.device.click_record_clear()
        # 事件内容独立循环处理，不依赖主页面，也不跟踪事件结束。
        if self._handle_story_video():
            self._reset_story_idle()
            return
        if self.appear_then_click(self.I_STORY_CHAT_SKIP, interval=0.4):
            self._reset_story_idle()
            return

        if self._on_courtyard_main():
            if self.appear_then_click(self.I_STORY_CHAT_1, interval=2):
                self._reset_story_idle()
            return

        # 视频结束后可能直接进入准备页，不经过剧情 fight 入口。
        if self.detect_page_in(page_battle_prepare, include_global=False) is not None:
            self._run_story_battle()
            return

        # 只有入口点击需要剧情主页面标志。
        if not self.appear(self.I_STORY_BACK):
            self._handle_story_idle()
            return
        if self.appear_then_click(self.I_STORY_FIGHT, interval=2):
            if self._wait_story_prepare():
                self._run_story_battle()
            else:
                self._reset_story_idle()
            return
        for target in (self.I_STORY_CHAT_1, self.I_STORY_CHAT_2, self.I_STORY_ONMYOJI_EYE):
            if self.appear_then_click(target, interval=2):
                self._reset_story_idle()
                return
        self._handle_story_idle()

    def _reset_story_idle(self) -> None:
        self._story_idle_since = time.monotonic()
        self._story_walk_at = None

    def _handle_story_idle(self) -> None:
        now = time.monotonic()
        if not self.appear(self.I_STORY_BACK):
            # 未知界面兜底；是否结束由全局庭院识别判断。
            self._reset_story_idle()
            if now >= self._story_unknown_click_at:
                self.click(random_click(), interval=0.4)
                self._story_unknown_click_at = now + random.uniform(2, 4)
            return
        self._story_unknown_click_at = 0
        if now - self._story_idle_since <= 5:
            return
        if self._story_walk_at is not None and now < self._story_walk_at:
            return
        direction = random.choice((self.C_STORY_WALK_LEFT, self.C_STORY_WALK_RIGHT))
        self.click(direction, interval=0.4)
        self._story_walk_at = now + random.uniform(2, 4)

    def _handle_prepare(self, context, config) -> BattleAction:
        """沿用当前阵容，只确认并开始，不执行预设、御魂或增益操作。"""
        if self.appear_then_click(self.I_CONFIRM, interval=0.4):
            return BattleAction.CONTINUE
        if self.appear_then_click(self.I_CONFIRM_CLOSE_DIFF_SOUL, interval=0.4):
            return BattleAction.CONTINUE
        self.appear_then_click(self.I_PREPARE_HIGHLIGHT, interval=0.4)
        return BattleAction.CONTINUE

    def _handle_missing_battle_page(self, context, config, exit_matcher) -> BattleAction:
        # 战斗前的确认弹窗可能遮住准备页识别标志。
        if self.appear_then_click(self.I_CONFIRM, interval=0.4):
            return BattleAction.CONTINUE
        if self.appear_then_click(self.I_CONFIRM_CLOSE_DIFF_SOUL, interval=0.4):
            return BattleAction.CONTINUE
        # 点击 fight 后的过渡帧仍可能保留 story_back，不能据此提前退出战斗。
        settlement_exit_matcher = (
            exit_matcher if context.last_page in (page_battle_result, page_reward) else None
        )
        return super()._handle_missing_battle_page(context, config, settlement_exit_matcher)
