"""协同对弈：matchmaking。"""

import time
from module.logger import logger
from tasks.DraftDuel.config import DraftMatchMode
from tasks.Component.GeneralBattle.assets import GeneralBattleAssets


class DraftDuelMatchmakingMixin:
    def _match_battle(self) -> bool | str:
        """交换名单后匹配；取消必须确认回到待匹配页面才能重开。"""
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
            self._save_draft_checkpoint('rank')
            now = time.monotonic()
            if not leader and self.appear(self.I_LEADER_EXIT):
                self._leave_draft_team('队长已离队')
                return False
            if self._dismiss_draft_lobby_overlay() is True:
                time.sleep(0.3)
                continue
            self._observe_duel_lobby()
            if self.appear(self.I_WAIT_FIGHT_LOBBY):
                self._draft_match_state = None
                self._save_draft_checkpoint('lobby')
                logger.info('DraftDuel returned to team lobby during matching; resume next big round')
                return 'lobby'
            # 取消返回页时“匹配中”标志可能仍命中，开始按钮优先证明已回到待匹配页。
            cancel_returned = leader and cancelling and self.appear(self.I_LOBBY_BATTLE)
            if cancel_returned:
                cancelling = False
                active_since = active_wall = None
                click_started = click_wall = None
                last_battle_click = None
                progress_deadline = now + 600
                logger.info(f'DraftDuel match cancellation confirmed after attempt {attempts}/3')
            if self.appear(self.I_EXIT_CANCEL):
                self.appear_then_click(self.I_EXIT_CANCEL, interval=1)
            elif not cancel_returned and self.appear(self.I_FIGHT_WAIT):
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
            elif cancel_returned or self._is_draft_rank_page():
                self._observe_victory_count()
                require_auto = getattr(self, 'draft_finished_small_games', 0) > 0
                if require_auto:
                    # 每帧重新确认，点击后必须在后续截图看到 auto_right。
                    self._draft_auto_ready_after = (
                        self.draft_finished_small_games if self.appear(self.I_AUTO_RIGHT) else None)
                    if self._draft_auto_ready_after is None:
                        self.appear_then_click(self.I_AUTO_DOWN, interval=1)
                    else:
                        self._release_deployment_templates()
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
                        if self._selection_cooperation_enabled():
                            if not self._prepare_captain_lineup():
                                self._log_sync_wait('等待队长下发首次上阵名单')
                                time.sleep(0.3)
                                continue
                        else:
                            self.draft_planned_lineup = tuple(self._deployment_plan())
                    except ValueError as exc:
                        logger.warning(f'DraftDuel pre-match lineup unavailable: {exc}')
                        self._leave_draft_team('无法规划上阵名单', success=False)
                        return False
                    logger.info(f'DraftDuel pre-match lineup: {self.draft_planned_lineup}')
                    self._save_draft_checkpoint('rank')
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
