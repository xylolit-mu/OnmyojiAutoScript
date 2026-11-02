"""协同对弈：cooperation。"""

import time
import uuid
import math
from module.logger import logger
from tasks.DraftDuel.config import DraftMatchMode
from tasks.DraftDuel.strategy import normalize_name
from tasks.DraftDuel.strategy import battle_stats


class DraftDuelCooperationMixin:
    def _sync_draft_state(self, require_small_game=False, require_auto=False) -> bool:
        """复用通用协同数据库，交换同一局备选和每轮已确认上阵信息。"""
        peer = self.config.draft_duel.invite_config.cooperation_script.strip()
        if not peer:
            # 显式留空即关闭名单协同，忽略自动接入或上次运行残留的 peer。
            self.draft_peer_picks = ()
            self.draft_peer_rounds = ()
            self._draft_sync_wait_reason = None
            return True
        runtime = getattr(self.config, 'cooperation_runtime', None)
        if runtime is None:
            self._log_sync_wait(f'已配置协同脚本 {peer}，等待协同运行时就绪')
            return False
        if not hasattr(self, '_draft_exchange_since'):
            self._draft_exchange_since = time.time()
        mode = self.config.draft_duel.draft_duel_config.match_mode
        remote = runtime.peer_battle_state('DraftDuel', peer)
        if not getattr(self, '_draft_session', ''):
            if mode == DraftMatchMode.CAPTAIN:
                self._draft_session = uuid.uuid4().hex
            elif remote and remote['updated'] >= self._draft_exchange_since:
                self._draft_session = remote['session']
            else:
                self._log_sync_wait(f'等待队长 {peer} 发布本大轮名单')
                return False
        payload = {'small_game': getattr(self, 'draft_small_game', 0),
                   'picks': list(self.draft_selected),
                   'rounds': list(getattr(self, 'draft_deployment_rounds', ())),
                   'field_weights': getattr(self, '_draft_field_weights', None),
                   'auto_ready_after': getattr(self, '_draft_auto_ready_after', None)}
        if payload != getattr(self, '_draft_published_state', None):
            runtime.publish_battle_state('DraftDuel', peer, self._draft_session, payload)
            self._draft_published_state = payload
        if not remote or remote['session'] != self._draft_session:
            self._log_sync_wait(f'等待协同脚本 {peer} 交换同一大轮的五名式神；尚未收到本轮名单')
            return False
        data = remote['payload']
        picks = data.get('picks', [])
        rounds = data.get('rounds', [])
        if len(picks) != 5 or any(normalize_name(n) != n for n in picks):
            self._log_sync_wait(f'协同脚本 {peer} 的五名式神名单不完整或包含未知名称')
            return False
        if (len(rounds) > 3 or any(r.get('name') not in picks or
                                 r.get('round') not in range(1, 4) or r.get('slot') not in range(1, 7)
                                 for r in rounds) or
                [r['round'] for r in rounds] != sorted(set(r['round'] for r in rounds))):
            return False
        self.draft_peer_picks = tuple(picks)
        same_game = data.get('small_game', 0) == getattr(self, 'draft_small_game', 0)
        self.draft_peer_rounds = tuple(rounds) if same_game else ()
        if same_game and getattr(self, 'draft_small_game', 0) > 0:
            if mode == DraftMatchMode.CAPTAIN:
                if getattr(self, '_draft_field_weights', None) is None:
                    table = battle_stats._tally('fielded')
                    self._draft_field_weights = {
                        name: battle_stats.field_bonus(name, table)
                        for name in set((*self.draft_selected, *picks))}
                    payload['field_weights'] = self._draft_field_weights
                    runtime.publish_battle_state('DraftDuel', peer, self._draft_session, payload)
                    self._draft_published_state = payload
                    self._save_draft_checkpoint()
            else:
                weights = data.get('field_weights')
                expected = set((*self.draft_selected, *picks))
                if not isinstance(weights, dict) or set(weights) != expected or any(
                        type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 8
                        for v in weights.values()):
                    if require_small_game:
                        self._log_sync_wait('等待队长发布本小局的固定实战评分')
                        return False
                else:
                    self._draft_field_weights = dict(weights)
                    self._save_draft_checkpoint()
        if require_auto:
            finished = getattr(self, 'draft_finished_small_games', 0)
            if (finished < 1 or getattr(self, '_draft_auto_ready_after', None) != finished
                    or type(data.get('auto_ready_after')) is not int
                    or data['auto_ready_after'] != finished):
                self._log_sync_wait(f'等待双方确认 auto_right（已完成{finished}小局）')
                return False
        if require_small_game and not same_game:
            self._log_sync_wait(f'等待协同脚本 {peer} 进入第{getattr(self, "draft_small_game", 0)}小局')
        else:
            if getattr(self, '_draft_sync_wait_reason', None):
                logger.info(f'DraftDuel cooperation roster ready: {peer}')
            self._draft_sync_wait_reason = None
        return same_game if require_small_game else True

    def _log_sync_wait(self, reason):
        if getattr(self, '_draft_sync_wait_reason', None) != reason:
            logger.info(f'DraftDuel cooperation waiting: {reason}')
            self._draft_sync_wait_reason = reason
