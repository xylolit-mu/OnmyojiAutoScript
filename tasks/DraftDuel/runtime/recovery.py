"""按实例原子保存大轮状态；恢复只使用已记录的名字和头像。"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from zipfile import BadZipFile

import numpy as np
from module.logger import logger
from tasks.DraftDuel.runtime.portrait_match import HAND_MIDDLE_FIGHT_SCALE


class DraftDuelRecoveryMixin:
    _CHECKPOINT_FIELDS = (
        'draft_selected', 'draft_fielded', 'draft_deployment_rounds',
        'draft_peer_picks', 'draft_peer_rounds', 'draft_big_round', 'draft_completed_rounds',
        'draft_small_game', 'draft_finished_small_games', '_draft_session',
        '_draft_exchange_since', '_draft_wait_lobby_seen', '_draft_matching_bout_active',
        '_draft_round_had_battle', '_draft_battle_seen', '_stats_round_id',
        '_stats_round_finalized', 'draft_victory_count', '_victory_observed_after',
        '_victory_valid_after', '_pending_battle_stats', '_draft_deploy_time',
        '_draft_deploy_epoch', '_draft_deployment_state', '_draft_selection_pending',
        '_draft_field_weights', 'draft_unresolved_slots',
        '_draft_offer', '_draft_pick_decision', '_draft_lineup_plan',
        '_draft_small_game_result', '_draft_end_recorded', '_draft_transcript', '_draft_peer_transcript',
        '_draft_deployment_overrides', '_draft_theoretical_lineup',
        '_draft_match_state',
        '_draft_deploy_clock_wall', 'draft_planned_lineup',
    )

    def _draft_checkpoint_path(self):
        name = getattr(self.config, 'config_name', None)
        if not isinstance(name, str) or not name:
            return None
        key = hashlib.sha256(name.encode()).hexdigest()[:24]
        return Path(__file__).resolve().parents[3] / 'data/DraftDuel/state' / f'{key}.npz'

    def _checkpoint_identity(self):
        settings = self.config.draft_duel
        return [self.config.config_name, settings.draft_duel_config.match_mode.value,
                settings.invite_config.cooperation_script.strip()]

    def _clear_draft_checkpoint(self):
        path = self._draft_checkpoint_path()
        if path is not None:
            path.unlink(missing_ok=True)
        self._draft_checkpoint_signature = None

    def _save_draft_checkpoint(self, phase=None):
        path = self._draft_checkpoint_path()
        if path is None:
            return
        if phase:
            self._draft_resume_phase = phase
        data = {key: getattr(self, key) for key in self._CHECKPOINT_FIELDS if hasattr(self, key)}
        data.update(version=1, identity=self._checkpoint_identity(),
                    phase=getattr(self, '_draft_resume_phase', 'lobby'))
        hand_templates = tuple(getattr(self, 'draft_hand_templates', ()))
        data['hand_template_names'] = [name for name, _ in hand_templates]
        data['hand_template_scale'] = HAND_MIDDLE_FIGHT_SCALE
        serialized = json.dumps(data, ensure_ascii=False, sort_keys=True)
        heads = tuple(getattr(self, 'draft_heads', ()))
        images = (*heads, *(h for _, h in hand_templates))
        signature = (serialized, tuple(hashlib.sha256(h.tobytes()).digest() for h in images))
        if signature == getattr(self, '_draft_checkpoint_signature', None):
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp = tempfile.mkstemp(dir=path.parent, suffix='.npz')
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                np.savez_compressed(stream, metadata=np.array(serialized),
                                    **{f'head{i}': h for i, h in enumerate(heads)},
                                    **{f'hand{i}': h for i, (_, h) in enumerate(hand_templates)})
            for attempt in range(5):
                try:
                    os.replace(temp, path)
                    break
                except PermissionError:
                    if attempt == 4:
                        if not getattr(self, '_draft_checkpoint_write_blocked', False):
                            logger.warning(f'DraftDuel checkpoint replacement denied: {path}; '
                                           'retain previous checkpoint and retry on next save')
                        self._draft_checkpoint_write_blocked = True
                        return
                    time.sleep(0.05 * 2 ** attempt)
            self._draft_checkpoint_signature = signature
            self._draft_checkpoint_write_blocked = False
        finally:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass
            except PermissionError:
                logger.warning(f'DraftDuel temporary checkpoint cleanup denied: {temp}')

    def _restore_draft_checkpoint(self):
        path = self._draft_checkpoint_path()
        if path is None or not path.exists():
            return False
        try:
            with np.load(path, allow_pickle=False) as saved:
                data = json.loads(str(saved['metadata']))
                if not isinstance(data, dict):
                    raise ValueError('invalid checkpoint metadata')
                if data.get('version') != 1 or data.get('identity') != self._checkpoint_identity():
                    return False
                names = data.get('draft_selected', [])
                fielded = data.get('draft_fielded', [])
                if (not isinstance(names, list) or len(names) > 5 or
                        any(not isinstance(n, str) or not n for n in names) or
                        len(set(names)) != len(names) or not isinstance(fielded, list) or
                        any(n not in names for n in fielded) or len(fielded) > 3 or
                        len(set(fielded)) != len(fielded) or data.get('phase') not in
                        ('lobby', 'select', 'rank', 'fight', 'battle', 'settlement')):
                    raise ValueError('invalid roster checkpoint')
                heads = tuple(saved[f'head{i}'].copy() for i in range(5)) \
                    if 'head0' in saved else ()
                if heads and (len(data.get('draft_selected', [])) != 5 or
                              any(h.ndim != 3 or not h.size for h in heads)):
                    raise ValueError('invalid portrait checkpoint')
                hand_names = data.get('hand_template_names', [])
                if (not isinstance(hand_names, list) or any(n not in names for n in hand_names)
                        or len(set(hand_names)) != len(hand_names)):
                    raise ValueError('invalid hand template names')
                hand_templates = tuple((name, saved[f'hand{i}'].copy())
                                       for i, name in enumerate(hand_names))
                if any(h.ndim != 3 or not h.size for _, h in hand_templates):
                    raise ValueError('invalid hand template images')
            for key in self._CHECKPOINT_FIELDS:
                if key in data:
                    value = data[key]
                    if key in ('draft_fielded', 'draft_peer_picks', 'draft_peer_rounds', 'draft_planned_lineup'):
                        value = tuple(value)
                    setattr(self, key, value)
            self._draft_transcript = {int(key): value for key, value in
                                      getattr(self, '_draft_transcript', {}).items()}
            self.draft_hand_templates = hand_templates
            self.draft_heads = heads
            self.draft_named_heads = tuple(zip(self.draft_selected, heads))
            self._draft_resume_phase = data.get('phase', 'lobby')
            self._draft_published_state = None
            logger.info(f'DraftDuel restored checkpoint: {self._draft_resume_phase}')
            return True
        except (OSError, ValueError, KeyError, TypeError, EOFError, BadZipFile):
            logger.warning('DraftDuel checkpoint unreadable; use current page without guessing roster')
            return False

    def _detect_draft_phase(self):
        self.screenshot()
        if self.appear(self._battle_info_rule()):
            self._draft_battle_seen = True
            return 'battle'
        if any(self.appear(rule) for rule in (
                self.I_DRAFT_SHARE, self.I_FIGHT_STATISTIC, self.I_FIGHT_RESULT)):
            return 'settlement'
        if self.appear(self.I_POST_DRAFT_FIGHT):
            return 'fight'
        if self._is_draft_rank_page() or self.appear(self.I_FIGHT_WAIT):
            return 'rank'
        if self._is_draft_selection_page():
            return 'select'
        if self.appear(self.I_WAIT_FIGHT_LOBBY):
            return 'lobby'
        # 未识别到过渡画面时继续最后阶段；首次任务仍走通用大厅入口。
        return getattr(self, '_draft_resume_phase', 'lobby')
