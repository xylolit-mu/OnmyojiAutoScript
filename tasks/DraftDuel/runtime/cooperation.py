"""协同对弈：cooperation。"""

import time
import uuid
import math
from module.logger import logger
from tasks.DraftDuel.config import DraftMatchMode
from tasks.DraftDuel.strategy import normalize_name
from tasks.DraftDuel.strategy import battle_stats


class DraftDuelCooperationMixin:
    def _publish_draft_abort(self, reason):
        settings = getattr(getattr(self, 'config', None), 'draft_duel', None)
        runtime = getattr(getattr(self, 'config', None), 'cooperation_runtime', None)
        session = getattr(self, '_draft_session', '')
        if not settings or not runtime or not session:
            return
        peer = settings.invite_config.cooperation_script.strip()
        if not peer or settings.draft_duel_config.match_mode == DraftMatchMode.SOLO:
            return
        try:
            runtime.publish_battle_state('DraftDuel', peer, session, {
                'small_game': getattr(self, 'draft_small_game', 0),
                'picks': list(getattr(self, 'draft_selected', ())),
                'rounds': list(getattr(self, 'draft_deployment_rounds', ())),
                'abort_reason': reason,
            })
        except Exception as exc:
            logger.warning(f'DraftDuel cannot publish round abort: {exc}')

    def _sync_draft_state(self, require_small_game=False, require_auto=False, allow_partial=False) -> bool:
        """复用通用协同数据库，交换同一局备选和每轮已确认上阵信息。"""
        peer = self.config.draft_duel.invite_config.cooperation_script.strip()
        if not peer or self.config.draft_duel.draft_duel_config.match_mode == DraftMatchMode.SOLO:
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
                   'auto_ready_after': getattr(self, '_draft_auto_ready_after', None),
                   'offer': getattr(self, '_draft_offer', None),
                   'pick_decision': getattr(self, '_draft_pick_decision', None),
                   'lineup_plan': getattr(self, '_draft_lineup_plan', None),
                   'draft_rounds': list(getattr(self, '_draft_transcript', {}).values())}
        if payload != getattr(self, '_draft_published_state', None):
            runtime.publish_battle_state('DraftDuel', peer, self._draft_session, payload)
            self._draft_published_state = payload
        if not remote or remote['session'] != self._draft_session:
            self._log_sync_wait(f'等待协同脚本 {peer} 交换同一大轮的五名式神；尚未收到本轮名单')
            return False
        data = remote['payload']
        if data.get('abort_reason'):
            self._leave_draft_team(f"协同方已结束本轮：{data['abort_reason']}", success=False)
            return False
        self._draft_peer_offer = data.get('offer')
        self._draft_peer_decision = data.get('pick_decision')
        self._draft_peer_lineup_plan = data.get('lineup_plan')
        self._draft_peer_transcript = data.get('draft_rounds', [])
        picks = data.get('picks', [])
        rounds = data.get('rounds', [])
        if ((not allow_partial and len(picks) != 5) or len(picks) > 5 or
                len(set(picks)) != len(picks) or any(normalize_name(n) != n for n in picks)):
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
        if same_game and len(self.draft_selected) == len(picks) == 5:
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
                    self._log_sync_wait('等待队长发布本大轮的固定实战评分')
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

    def _captain_selection(self, number, names):
        """队员上报候选；决策绑定双方已选前缀和候选，拒绝旧轮指令。"""
        from tasks.DraftDuel.strategy import Pick
        from tasks.DraftDuel.strategy.runtime_policy import choose_pick
        self._draft_offer = dict(number=number, names=list(names), picks=list(self.draft_selected))
        self._sync_draft_state(allow_partial=True)
        captain = self.config.draft_duel.draft_duel_config.match_mode == DraftMatchMode.CAPTAIN
        if captain:
            other = getattr(self, '_draft_peer_offer', None)
            if (not isinstance(other, dict) or other.get('number') != number or
                    len(other.get('picks', ())) != number - 1 or
                    other.get('picks') != list(getattr(self, 'draft_peer_picks', ())) or
                    not isinstance(other.get('names'), list) or len(other['names']) != 3 or
                    any(n and normalize_name(n) != n for n in other['names'])):
                return None
            previous = getattr(self, '_draft_pick_decision', None)
            if (previous and previous.get('captain_offer') == self._draft_offer and
                    previous.get('member_offer') == other):
                decision = previous
            else:
                own_pick = choose_pick(self, [n for n in names if n], team=self.draft_selected)
                peer_pick = choose_pick(self, [n for n in other['names'] if n], team=other['picks'])
                a, b = own_pick.name, peer_pick.name
                score = own_pick.score + peer_pick.score
                decision = dict(captain_offer=self._draft_offer, member_offer=other,
                                captain_pick=a, member_pick=b, score=score)
                self._draft_pick_decision = decision
                self._save_draft_checkpoint('select')
            self._sync_draft_state(allow_partial=True)
            name = decision['captain_pick']
        else:
            decision = getattr(self, '_draft_peer_decision', None)
            if not isinstance(decision, dict) or decision.get('member_offer') != self._draft_offer:
                return None
            name = decision.get('member_pick')
        if name not in names:
            return None
        return Pick(name, decision['score'], ('队长使用固定策略分别选择双方候选',), ())

    def _prepare_captain_lineup(self):
        """首次匹配由队长下发双方三人名单；队员不调用阵容优化器。"""
        from tasks.DraftDuel.strategy.runtime_policy import plan_duel_lineup
        captain = self.config.draft_duel.draft_duel_config.match_mode == DraftMatchMode.CAPTAIN
        if captain:
            plan = getattr(self, '_draft_lineup_plan', None)
            if not plan:
                own, peer = plan_duel_lineup(self, self.draft_selected, self.draft_peer_picks,
                                             field_weights=self._draft_field_weights)
                plan = self._draft_lineup_plan = dict(captain=list(own), member=list(peer))
            self.draft_planned_lineup = tuple(plan['captain'])
            self._sync_draft_state()
        else:
            plan = getattr(self, '_draft_peer_lineup_plan', None)
            if not isinstance(plan, dict):
                return False
            own, peer = plan.get('member', []), plan.get('captain', [])
            if (len(own) != 3 or len(peer) != 3 or len(set(own)) != 3 or len(set(peer)) != 3 or
                    not set(own).issubset(self.draft_selected) or
                    not set(peer).issubset(self.draft_peer_picks)):
                return False
            self._draft_lineup_plan = plan
            self.draft_planned_lineup = tuple(own)
        self._save_draft_checkpoint('rank')
        return True

    def _log_sync_wait(self, reason):
        if getattr(self, '_draft_sync_wait_reason', None) != reason:
            logger.info(f'DraftDuel cooperation waiting: {reason}')
            self._draft_sync_wait_reason = reason
