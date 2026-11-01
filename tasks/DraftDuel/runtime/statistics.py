"""匹配等待页胜场读数与大轮、小局实战记录。"""
import re
import time
import unicodedata
import uuid
from module.logger import logger
from tasks.DraftDuel.strategy import battle_stats


class DraftDuelStatisticsMixin:
    def _initialize_round_statistics(self):
        self._stats_round_id = uuid.uuid4().hex
        self._stats_round_finalized = False
        self.draft_victory_count = None
        self._victory_observed_after = None
        self._victory_valid_after = None
        self._pending_battle_stats = None

    @staticmethod
    def _parse_victory_count(text):
        text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text)))
        match = re.fullmatch(r'([0-6零〇一二三四五六Oo])(?:番|场|場)?(?:胜|勝)?', text)
        if not match:
            return None
        value = match[1]
        if value in '零〇Oo':
            return 0
        return int(value) if value.isdigit() else '一二三四五六'.index(value) + 1

    def _observe_victory_count(self):
        """每次战后回到等待页读取一次，连续两帧一致；失败不覆盖旧读数。"""
        finished = getattr(self, 'draft_finished_small_games', 0)
        if getattr(self, '_victory_observed_after', None) == finished:
            return
        candidate, stable = None, 0
        for attempt in range(5):
            if attempt:
                time.sleep(0.15)
                self.screenshot()
            if not self._is_draft_rank_page():
                break
            value = self._parse_victory_count(self.O_VICTORY_COUNT.ocr(self.device.image))
            if value is not None and value == candidate:
                stable += 1
            else:
                candidate, stable = value, 1 if value is not None else 0
            if stable >= 2:
                self.draft_victory_count = value
                self._victory_valid_after = finished
                logger.info(f'DraftDuel victory_count={value}, finished_small_games={finished}')
                pending = getattr(self, '_pending_battle_stats', None)
                if pending:
                    before = pending['before']
                    result = ('win' if value == before + 1 else 'loss' if value == before else 'unknown') \
                        if before is not None else 'unknown'
                    battle_stats.resolve_result(pending['event_id'], result, value)
                    self._pending_battle_stats = None
                    logger.info(f'DraftDuel recorded small-game result: {result}')
                self._victory_observed_after = finished
                return
        self._victory_observed_after = finished
        logger.warning('DraftDuel victory_count unreadable; retain last valid value')

    def _record_small_game_stats(self):
        if len(getattr(self, 'draft_selected', ())) != 5:
            return
        if not hasattr(self, '_stats_round_id'):
            self._initialize_round_statistics()
        instance = getattr(self.config, 'config_name', '')
        small_game = self.draft_small_game
        event_id = f'{instance}:{self._stats_round_id}:{small_game}'
        before = self.draft_victory_count if self._victory_valid_after == self.draft_finished_small_games - 1 else None
        battle_stats.record('unknown', self.draft_selected, self.draft_fielded,
                            getattr(self, 'draft_peer_picks', ()), event_id=event_id,
                            instance=instance, round_id=self._stats_round_id, small_game=small_game,
                            victory_before=before,
                            unresolved_slots=getattr(self, 'draft_unresolved_slots', ()))
        self._pending_battle_stats = {'event_id': event_id, 'before': before}

    def _finalize_round_stats(self, *, completed, reason=''):
        if len(getattr(self, 'draft_selected', ())) != 5 or not hasattr(self, '_stats_round_id'):
            return
        if self._stats_round_finalized:
            return
        battle_stats.record_round(self._stats_round_id, getattr(self.config, 'config_name', ''),
                                  self.draft_victory_count, self.draft_selected,
                                  completed=completed, reason=reason)
        self._stats_round_finalized = True
        logger.info(f'DraftDuel round recorded: final_victory_count={self.draft_victory_count}, '
                    f'completed={completed}')
