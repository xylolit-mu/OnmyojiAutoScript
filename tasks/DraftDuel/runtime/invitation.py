"""协同对弈：invitation。"""

import time
from module.logger import logger
from tasks.DraftDuel.config import DraftMatchMode
from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.page import page_draft_duel


class DraftDuelInvitationMixin:
    def _invite_teammate(self):
        """协斗只负责打开大厅邀请入口，查找、选中和发送均调用通用邀请。"""
        if not self.appear_then_click(self.I_EMPTY_TEAMMATE, interval=1):
            return False
        if not self.wait_until_appear(self.I_INVITE_ENSURE, wait_time=5):
            return False
        return self.invite_friends(self.config.draft_duel.invite_config, open_invite=False)

    def is_in_room(self, is_screenshot=True):
        """为通用邀请提供基础图像判断，详细房间核验留给新流程。"""
        if is_screenshot:
            self.screenshot()
        if self.appear_accept() or self.appear(self.I_INVITE_ENSURE):
            return False
        if self.config.draft_duel.draft_duel_config.match_mode == DraftMatchMode.MEMBER:
            return self.appear(self.I_LOBBY_TEAMMATE_WAIT) or self._member_match_started()
        if not self._in_draft_lobby():
            return False
        return not self.appear(self.I_EMPTY_TEAMMATE)

    def _in_draft_lobby(self) -> bool:
        """大厅统一使用全局页面标志，业务按钮不作为页面判断。"""
        return self.appear(GameUiAssets.I_DRAFT_DUEL)

    def _goto_draft_lobby(self) -> bool:
        """复用全局路径：庭院 -> 町中 -> 协同对弈。"""
        self.goto_page(page_draft_duel)
        self._observe_duel_lobby()
        return self._in_draft_lobby()

    def _wait_for_teammate(self) -> bool:
        """发送邀请后，确认大厅空位消失；邀请面板遮挡不算入房。"""
        timeout = self.config.draft_duel.invite_config.wait_time_v.total_seconds()
        deadline = time.monotonic() + timeout
        while True:
            # 主动等人由邀请超时管理，避免被设备的无操作计时提前中断。
            self.device.stuck_record_clear()
            self.screenshot()
            if self.is_in_room(False):
                logger.info('DraftDuel empty_teammate disappeared; teammate joined')
                return True
            if time.monotonic() >= deadline:
                logger.warning('DraftDuel teammate wait timed out; do not start matching')
                return False
            time.sleep(0.3)

    def _enter_matchmaking(self) -> bool:
        """到达大厅后按匹配模式邀请队友或直接开始匹配。"""
        if not self._goto_draft_lobby():
            return False
        mode = self.config.draft_duel.draft_duel_config.match_mode
        if mode == DraftMatchMode.MEMBER:
            self.prepare_battle_cooperation('DraftDuel')
            return self._wait_member_start()
        if mode == DraftMatchMode.CAPTAIN:
            self.prepare_battle_cooperation('DraftDuel')
            self.screenshot()
            if self.appear(self.I_EMPTY_TEAMMATE):
                if not self._invite_teammate():
                    return False
                if not self._wait_for_teammate():
                    self._finish_invite_wait_timeout()
                    return False
        if not self.wait_until_appear(self.I_LOBBY_BATTLE, wait_time=5):
            return False
        if not self._in_draft_lobby():
            return False
        # 等待按钮期间队友可能离开，点击前再检查一次。
        if mode == DraftMatchMode.CAPTAIN and not self.is_in_room(False):
            return False
        clicked = bool(self.appear_then_click(self.I_LOBBY_BATTLE, interval=1))
        if clicked:
            logger.info(f'DraftDuel {mode.value}: clicked lobby_battle')
        return clicked

    def _member_match_started(self) -> bool:
        """以明确的选人/战斗标志确认开始，不把等待标志漏识别当开战。"""
        if self.appear(self.I_LOBBY_TEAMMATE_WAIT):
            return False
        return self._is_draft_selection_page() or any(self.appear(rule) for rule in (
            self.I_SELECT_1,
            self.I_SELECT_2,
            self.I_SELECT_3,
            self._battle_info_rule(),
            GeneralBattleAssets.I_PREPARE_HIGHLIGHT,
            GeneralBattleAssets.I_PREPARE_DARK,
        ))

    def _wait_member_start(self) -> bool:
        """在大厅接收通用邀请，入队后最多等待队长开战 600 秒。"""
        deadline = time.monotonic() + self.MEMBER_START_TIMEOUT
        waiting_seen = False
        while True:
            self.device.stuck_record_clear()
            self.screenshot()
            if self._member_match_started():
                logger.info('DraftDuel captain started; member entered draft or battle')
                return True
            now = time.monotonic()
            if self.appear(self.I_LOBBY_TEAMMATE_WAIT) and not waiting_seen:
                waiting_seen = True
                deadline = now + self.MEMBER_START_TIMEOUT
                logger.info('DraftDuel lobby_teammate_wait detected; wait for captain up to 600s')
            if now >= deadline:
                self._finish_member_wait_timeout()
            if self.appear_accept():
                # 每次接受操作有界，截图轮询继续负责等人及处理总超时。
                self.check_then_accept(
                    timeout=min(5, deadline - now),
                    default_invite=self.config.draft_duel.invite_config.default_invite,
                )
            time.sleep(0.3)
