"""协同对弈：战斗标志防抖与结算。"""
import time
from module.logger import logger
from tasks.GameUi.default_pages import random_click


class DraftDuelBattleMixin:
    BATTLE_END_ABSENCE = 2.0

    def _confirm_small_game_end(self):
        if getattr(self, '_draft_end_recorded', False):
            return
        self.device.click_record_clear()
        self.draft_finished_small_games = getattr(self, 'draft_finished_small_games', 0) + 1
        self._draft_round_had_battle = True
        self._record_small_game_stats()
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
                    pages = (self.I_DRAFT_SHARE, self.I_FIGHT_STATISTIC, self.I_FIGHT_RESULT)
                    if any(self.appear(rule) for rule in pages):
                        self._confirm_small_game_end()
                        self.click(random_click(ltrb=(True, False, True, False)))
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
