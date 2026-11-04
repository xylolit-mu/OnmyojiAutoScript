"""协同对弈：战斗标志防抖与结算。"""
import time

from module.logger import logger
from tasks.DraftDuel.config import DraftMatchMode
from tasks.DraftDuel.strategy import battle_stats
from tasks.Component.GeneralInvite.general_invite import CONTINUE_INVITE_RETRY_SECONDS
from tasks.GameUi.default_pages import page_reward, random_click


class DraftDuelBattleMixin:
    BATTLE_END_ABSENCE = 2.0

    def _click_draft_settlement_continue(self):
        """Only captain sends the next invitation; member dismisses and waits."""
        settings = getattr(getattr(self, 'config', None), 'draft_duel', None)
        mode = settings.draft_duel_config.match_mode if settings else None
        if mode == DraftMatchMode.CAPTAIN and self.appear(self.I_INVITE_CONTINUE):
            last = getattr(self, '_draft_last_invite_at', None)
            if isinstance(last, (int, float)) and time.time() - last < CONTINUE_INVITE_RETRY_SECONDS:
                return
            if self.appear_then_click(self.I_INVITE_CONTINUE, interval=CONTINUE_INVITE_RETRY_SECONDS):
                self._draft_last_invite_at = time.time()
                self._draft_invite_retry_seconds = CONTINUE_INVITE_RETRY_SECONDS
            return
        self.click(random_click(ltrb=(True, False, True, False)))

    def _read_statistic_result(self):
        """Called only after statistic is recognized; ambiguous templates wait."""
        win = bool(self.appear(self.I_FIGHT_WIN))
        lose = bool(self.appear(self.I_FIGHT_LOSE))
        if win == lose:
            return None
        result = 'win' if win else 'loss'
        if result != getattr(self, '_draft_small_game_result', None):
            logger.info(f'DraftDuel statistic result: {result}')
        self._draft_small_game_result = result
        return result

    def _resolve_statistic_result(self):
        result = getattr(self, '_draft_small_game_result', None)
        pending = getattr(self, '_pending_battle_stats', None)
        if result and pending and self._can_record_stats():
            from tasks.DraftDuel.strategy import battle_stats
            battle_stats.resolve_result(pending['event_id'], result, None)
            self._pending_battle_stats = None
            self._save_draft_checkpoint('settlement')

    def _confirm_small_game_end(self):
        if getattr(self, '_draft_end_recorded', False):
            self._resolve_statistic_result()
            return
        self.device.click_record_clear()
        self.draft_finished_small_games = getattr(self, 'draft_finished_small_games', 0) + 1
        self._draft_round_had_battle = True
        self._record_small_game_stats()
        self._resolve_statistic_result()
        self._draft_end_recorded = True
        self._save_draft_checkpoint('settlement')
        logger.info(f'DraftDuel small game ended: {self.draft_finished_small_games}')

    def _battle_and_settlement(self):
        """标志持续消失才等待结算；真实结算页面确认结束，重现则恢复战斗。"""
        seen = getattr(self, '_draft_battle_seen', False)
        settling = getattr(self, '_draft_resume_phase', '') == 'settlement'
        absent_since, absent = None, 0
        battle_clicks_reset = False
        deadline = time.monotonic() + (120 if settling else self.SMALL_GAME_BATTLE_WINDOW)
        while True:
            self.device.stuck_record_clear()
            self.screenshot()
            now = time.monotonic()
            if self.appear(self._battle_info_rule()):
                # 上阵阶段可能已设置 seen；仍在本阶段首次见到战斗时清除跨局点击。
                if not battle_clicks_reset:
                    self.device.click_record_clear()
                    battle_clicks_reset = True
                if not seen or settling:
                    deadline = now + self.SMALL_GAME_BATTLE_WINDOW
                    logger.info('DraftDuel entered/resumed small-game battle; wait window=300s')
                elif now >= deadline:
                    deadline = now + self.SMALL_GAME_BATTLE_WINDOW
                    logger.info('DraftDuel still in battle after 300s; extend another 300s')
                self._sync_draft_state()
                self._draft_round_had_battle = True
                self._draft_battle_seen = True
                seen, settling = True, False
                absent_since, absent = None, 0
                self._save_draft_checkpoint('battle')
            else:
                if seen and not settling:
                    absent_since = now if absent_since is None else absent_since
                    absent += 1
                    if absent >= 2 and now - absent_since >= self.BATTLE_END_ABSENCE:
                        settling = True
                        deadline = now + 120
                        self._save_draft_checkpoint('settlement')
                if settling:
                    pages = (self.I_DRAFT_SHARE, self.I_FIGHT_RESULT)
                    if self.appear(self.I_FIGHT_STATISTIC):
                        if self._read_statistic_result() is not None:
                            self._confirm_small_game_end()
                            self._click_draft_settlement_continue()
                    elif (page_reward.recognizer.evaluate(self)
                            or any(self.appear(rule) for rule in pages)):
                        self._confirm_small_game_end()
                        self._click_draft_settlement_continue()
                    elif self.appear(self.I_WAIT_FIGHT_LOBBY):
                        self._confirm_small_game_end()
                        self._observe_duel_lobby()
                        return 'lobby'
                    elif self._is_draft_rank_page():
                        self._confirm_small_game_end()
                        self._observe_victory_count()
                        self._save_draft_checkpoint('rank')
                        return 'rank'
                if now >= deadline and (settling or not seen):
                    logger.warning('DraftDuel battle/settlement made no confirmed progress')
                    return None
            time.sleep(0.3)
