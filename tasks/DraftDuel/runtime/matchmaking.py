"""协同对弈：matchmaking。"""

import time
from module.logger import logger
from tasks.DraftDuel.config import DraftMatchMode
from tasks.Component.GeneralBattle.assets import GeneralBattleAssets


class DraftDuelMatchmakingMixin:
    def _match_battle(self) -> bool:
        """采集头像后匹配；取消必须确认回到待匹配页面才能重开。"""
        leader = self.config.draft_duel.draft_duel_config.match_mode != DraftMatchMode.MEMBER
        limit = self.config.draft_duel.draft_duel_config.match_wait_seconds
        state = getattr(self, '_draft_match_state', None) or {}
        attempts = state.get('attempts', 0)
        active_wall = state.get('active_wall')
        click_wall = state.get('click_wall')
        current = time.monotonic()
        active_since = current - max(0, time.time() - active_wall) if active_wall is not None else None
        click_started = current - max(0, time.time() - click_wall) if click_wall is not None else None
        cancelling = state.get('cancelling', False)
        last_battle_click = None
        progress_deadline = time.monotonic() + 600
        while True:
            self._draft_match_state = dict(attempts=attempts, active_wall=active_wall,
                                           click_wall=click_wall, cancelling=cancelling)
            self.device.stuck_record_clear()
            self.screenshot()
            self._observe_duel_lobby()
            self._save_draft_checkpoint('rank')
            now = time.monotonic()
            if not leader and self.appear(self.I_LEADER_EXIT):
                self._leave_draft_team('队长已离队')
                return False
            if self.appear(self.I_EXIT_CANCEL):
                self.appear_then_click(self.I_EXIT_CANCEL, interval=1)
            elif self.appear(self.I_FIGHT_WAIT):
                self._begin_small_game()
                if active_since is None:
                    active_since = now
                    active_wall = time.time()
                    click_started = None
                    click_wall = None
                    last_battle_click = None
                    if leader:
                        attempts += 1
                        logger.info(f'DraftDuel matching attempt {attempts}/3, timeout={limit}s')
                    progress_deadline = now + limit + 600
                if leader and now - active_since >= limit:
                    cancelling = True
                    self.appear_then_click(self.I_FIGHT_CANCEL_WAIT_LEADER, interval=1)
            elif self._is_draft_rank_page():
                self._observe_victory_count()
                require_auto = getattr(self, 'draft_finished_small_games', 0) > 0
                if require_auto:
                    # 每帧重新确认，点击后必须在后续截图看到 auto_right。
                    self._draft_auto_ready_after = (
                        self.draft_finished_small_games if self.appear(self.I_AUTO_RIGHT) else None)
                    if self._draft_auto_ready_after is None:
                        self.appear_then_click(self.I_AUTO_DOWN, interval=1)
                if not self._sync_draft_state(require_auto=require_auto) or (
                        require_auto and self._draft_auto_ready_after is None):
                    if now >= progress_deadline:
                        logger.warning('DraftDuel waiting for peer roster timed out')
                        return False
                    time.sleep(0.3)
                    continue
                if (not getattr(self, 'draft_planned_lineup', ())
                        and getattr(self, 'draft_small_game', 0) == 0):
                    try:
                        self.draft_planned_lineup = tuple(self._deployment_plan())
                    except ValueError as exc:
                        logger.warning(f'DraftDuel pre-match lineup unavailable: {exc}')
                        return False
                    logger.info(f'DraftDuel pre-match lineup: {self.draft_planned_lineup}')
                    self._save_draft_checkpoint('rank')
                if cancelling:
                    # lobby_battle 再次可见才算取消完成，不以等待标志漏识别为依据。
                    if self.appear(self.I_LOBBY_BATTLE):
                        cancelling = False
                        active_since = None
                        active_wall = None
                        click_started = None
                        click_wall = None
                        last_battle_click = None
                        progress_deadline = now + 600
                if leader and active_since is None and not cancelling:
                    if attempts >= 3:
                        self._finish_match_timeout()
                    if click_started is None:
                        click_started = now
                        click_wall = time.time()
                    if now - click_started >= 30:
                        logger.warning('DraftDuel battle button retried for 30s without entering matching')
                        return False
                    if last_battle_click is None or now - last_battle_click >= 3:
                        self.appear_then_click(self.I_LOBBY_BATTLE, interval=3)
                        last_battle_click = now
            elif self.appear(self.I_POST_DRAFT_FIGHT):
                self._begin_small_game()
                self._draft_matching_bout_active = False
                self._draft_match_state = None
                return True
            elif any(self.appear(rule) for rule in (
                    self._battle_info_rule(),
                    GeneralBattleAssets.I_PREPARE_HIGHLIGHT,
                    GeneralBattleAssets.I_PREPARE_DARK)):
                self._draft_match_state = None
                return True
            if click_started is not None and active_since is None and now - click_started >= 30:
                logger.warning('DraftDuel battle button retried for 30s without entering matching')
                return False
            if now >= progress_deadline:
                logger.warning('DraftDuel matching page made no progress for 600s')
                return False
            time.sleep(0.3)
