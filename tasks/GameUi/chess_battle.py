from __future__ import annotations

"""百鬼棋局局内页面的全局恢复与退出流程。"""

import time
import random

from module.exception import GameStuckError
from module.logger import logger
from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
from tasks.Chess.assets import ChessAssets
from tasks.GlobalGame.assets import GlobalGameAssets


class ChessBattleNavigationMixin:
    """让所有继承 GameUi 的任务都能退出遗留的百鬼棋局。"""

    CHESS_EXIT_TIMEOUT = 60.0
    CHESS_EXIT_SCREENSHOT_INTERVAL = 0.35

    def select_chess_sixrealms_if_visible(self) -> bool:
        """完成开局必选场地；选定同一项后最多点击两次，等待页面消失。"""
        if not self.appear(ChessAssets.I_SELECT_SIXREALMS):
            return False
        target = random.choice((ChessAssets.C_SIXREALMS_1,
                                ChessAssets.C_SIXREALMS_2,
                                ChessAssets.C_SIXREALMS_3))
        click_limit = random.randint(1, 2)
        logger.info(f'Chess six-realms selection: target={target.name}, clicks={click_limit}')
        deadline = time.monotonic() + self.CHESS_EXIT_TIMEOUT
        clicks = 0
        while time.monotonic() < deadline:
            if not self.appear(ChessAssets.I_SELECT_SIXREALMS):
                logger.info('Chess six-realms selection completed')
                return True
            if clicks < click_limit:
                self.device.click_record_remove(target)
                self.click(target)
                clicks += 1
            self.device.stuck_record_clear()
            time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
            self.screenshot()
        raise GameStuckError('Chess: six-realms selection did not disappear after choosing a field')

    def _close_unknown_chess_result_page(self) -> bool:
        """结算返回流程的未知页面：尝试红色返回，交由下一张截图重新分流。"""
        if self.chess_result_flow_visible() or self.appear(ChessAssets.I_RESTART_AGAIN):
            return False
        if not self.appear(ChessAssets.I_BACK_RED):
            return False
        logger.warning('Chess result flow: unknown page with back red; close and recheck page')
        self._chess_share_advancing = False
        self.click(ChessAssets.I_BACK_RED, interval=0.8)
        return True

    def _advance_chess_result_stage(self) -> bool:
        """优先处理奖励和分享阶段，避免背景被识别成大厅。"""
        if self.appear(GlobalGameAssets.I_UI_REWARD):
            self._chess_share_advancing = False
            from tasks.GameUi.default_pages import random_click
            self.click(random_click(), interval=1.5)
            return True
        if self.appear(ChessAssets.I_SHARE):
            self._chess_share_advancing = True
        elif getattr(self, '_chess_share_advancing', False):
            if any(self.appear(marker) for marker in (
                ChessAssets.I_RESTART_AGAIN,
                self.I_CHECK_CHESS_RANK,
                self.I_CHESS_RANK_GOTO_LOBBY,
                self.I_CHESS_EXIT_TO_LOBBY,
                self.I_CHESS_EXIT_TO_LOBBY_2,
                self.I_CHECK_CHESS,
            )):
                self._chess_share_advancing = False
        if getattr(self, '_chess_share_advancing', False):
            if self._close_unknown_chess_result_page():
                return True
            # 分享标志消失后仍推进未知过渡画面。保持规则名称和点击
            # 记录不变，让设备层的过多点击检查正常生效。
            from tasks.GameUi.default_pages import random_click
            self.click(random_click(), interval=1.5)
            return True
        return False

    def chess_result_page_visible(self) -> bool:
        """检测百鬼棋局任一结算、分享或排名页面。"""
        return (
            self.appear(self.I_CHESS_EXIT_TO_LOBBY)
            or self.appear(self.I_CHESS_EXIT_TO_LOBBY_2)
            or self.appear(GlobalGameAssets.I_UI_REWARD)
            or self.appear(ChessAssets.I_SHARE)
            or self.appear(self.I_CHECK_CHESS_RANK)
            or self.appear(self.I_CHESS_RANK_GOTO_LOBBY)
        )

    def chess_result_flow_visible(self) -> bool:
        """检测百鬼棋局大厅或任一结算页面。"""
        return (
            self.appear(self.I_CHECK_CHESS)
            or self.chess_result_page_visible()
        )

    def return_to_chess_lobby(self) -> bool:
        """完成返回按钮、分享页与排名页流程，最终回到棋局大厅。"""
        logger.debug('Global Chess result flow: return to lobby')
        self._chess_share_advancing = False
        deadline = time.monotonic() + self.CHESS_EXIT_TIMEOUT
        share_seen = False
        exit_clicked = False
        safe_clicks = 0
        rank_recovery_started = False
        next_rank_safe_click_at = 0.0
        fallback_exit_at = time.monotonic() + 1.5

        while time.monotonic() < deadline:
            self.device.stuck_record_clear()
            self.screenshot()

            if self._advance_chess_result_stage():
                exit_clicked = True
                share_seen = True
                time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                continue

            if self.appear(self.I_CHECK_CHESS):
                logger.debug('Global Chess result flow: lobby reached')
                return True

            rank_page = self.appear(self.I_CHECK_CHESS_RANK)
            rank_button = self.appear(self.I_CHESS_RANK_GOTO_LOBBY)
            if rank_page or rank_button:
                rank_recovery_started = True
                if rank_button:
                    self.appear_then_click(
                        self.I_CHESS_RANK_GOTO_LOBBY,
                        interval=1.5,
                    )
                elif time.monotonic() >= next_rank_safe_click_at:
                    # 部分结算只显示“点击空白处继续”，没有返回大厅按钮。
                    logger.info(
                        'Global Chess result flow: advance rank page with '
                        'safe click'
                    )
                    self.click(GeneralBattleAssets.C_RANDOM_LEFT)
                    safe_clicks += 1
                    next_rank_safe_click_at = time.monotonic() + 1.5
                time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                continue

            if self._close_unknown_chess_result_page():
                time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                continue

            if not exit_clicked:
                if self.appear(self.I_CHESS_EXIT_TO_LOBBY):
                    self.appear_then_click(
                        self.I_CHESS_EXIT_TO_LOBBY,
                        interval=1.5,
                    )
                    exit_clicked = True
                    time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                    continue
                if self.appear(self.I_CHESS_EXIT_TO_LOBBY_2):
                    self.appear_then_click(
                        self.I_CHESS_EXIT_TO_LOBBY_2,
                        interval=1.5,
                    )
                    exit_clicked = True
                    time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                    continue
                if self.appear(ChessAssets.I_SHARE):
                    exit_clicked = True
                    share_seen = True
                    continue
                if time.monotonic() >= fallback_exit_at:
                    logger.warning(
                        'Global Chess result flow: return image missed; '
                        'click fixed return area'
                    )
                    self.click(self.I_CHESS_EXIT_TO_LOBBY)
                    exit_clicked = True
                    time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                    continue
                time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                continue

            if not share_seen and self.appear(ChessAssets.I_SHARE):
                share_seen = True

            if not share_seen:
                time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                continue

            if self.appear(self.I_CHECK_CHESS):
                logger.debug(
                    'Global Chess result flow: lobby reached after share, '
                    f'safe_clicks={safe_clicks}'
                )
                return True

            safe_clicks += 1
            from tasks.GameUi.default_pages import random_click
            self.click(random_click(), interval=1.5)
            time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)

        raise GameStuckError('Global Chess: failed to return to lobby after result')

    def Chess_Exit(self, return_to_lobby: bool = True) -> bool:
        """主动退出当前百鬼棋局，可选择停在结算流程。"""
        logger.warning('Global Chess page handler: exit interrupted battle')
        deadline = time.monotonic() + self.CHESS_EXIT_TIMEOUT
        while time.monotonic() < deadline:
            self.device.stuck_record_clear()
            self.screenshot()

            # 必选场地结束前，不能尝试退出或确认退出。
            if self.select_chess_sixrealms_if_visible():
                deadline = time.monotonic() + self.CHESS_EXIT_TIMEOUT
                continue

            # 与通用战斗一致：先确认退出，再检查结算，最后尝试退出入口。
            if self.appear_then_click(GeneralBattleAssets.I_EXIT_ENSURE, interval=0.8):
                time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                continue
            if self.chess_result_page_visible():
                self.ui_click_until_disappear(GeneralBattleAssets.I_EXIT_ENSURE, interval=0.8)
                logger.debug('Chess_Exit: result page reached')
                if return_to_lobby:
                    return self.return_to_chess_lobby()
                return True

            self.appear_then_click(self.I_CHESS_EXIT, interval=6)
            time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)

        logger.warning('Chess_Exit timed out before result page appeared')
        return False


def handle_chess_battle_page(task) -> bool:
    """GameUi 页面边动作：退出棋局战斗并返回棋局大厅。"""
    return task.Chess_Exit()


def handle_chess_result_page(task) -> bool:
    """GameUi 页面边动作：完成遗留结算并返回棋局大厅。"""
    return task.return_to_chess_lobby()
