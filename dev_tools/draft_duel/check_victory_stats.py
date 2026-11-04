"""胜场 OCR、实战写入与大轮最终值的离线检查。"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from tasks.DraftDuel.script_task import ScriptTask
from tasks.DraftDuel.strategy import battle_stats


class VictoryStatsChecks(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'stats.json'
        patcher = patch.object(battle_stats, 'STATS_PATH', self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.owner = object.__new__(ScriptTask)
        self.owner.config = SimpleNamespace(config_name='MUMU', draft_duel=SimpleNamespace(
            draft_duel_config=SimpleNamespace(match_mode='队长')))
        self.owner.draft_selected = ['莹草', '鬼童丸', '茨木童子', '椒图', '辉夜姬']
        self.owner.draft_fielded = ('莹草', '鬼童丸', '辉夜姬')
        self.owner.draft_small_game = 1
        self.owner.draft_finished_small_games = 0
        self.owner.appear = Mock(return_value=True)
        self.owner.screenshot = Mock()
        self.owner.device = Mock()
        self.owner.O_VICTORY_COUNT = Mock()
        self.owner._initialize_round_statistics()

    def read(self, *texts):
        self.owner.O_VICTORY_COUNT.ocr.side_effect = texts
        with patch('tasks.DraftDuel.runtime.statistics.time.sleep'):
            self.owner._observe_victory_count()

    def test_count_parser(self):
        for raw, value in [('0番胜', 0), ('３番勝', 3), ('六番胜', 6),
                           ('O番胜', 0), ('未开启', None), ('9番胜', None)]:
            self.assertEqual(ScriptTask._parse_victory_count(raw), value)

    def test_win_and_loss_update_same_battle_without_duplicates(self):
        self.read('0番胜', '0番胜')
        self.owner.draft_finished_small_games = 1
        self.owner._record_small_game_stats()
        self.assertEqual(battle_stats.load()['battles'][0]['result'], 'unknown')
        self.read('1番胜', '1番胜')
        first = battle_stats.load()['battles'][0]
        self.assertEqual(first['result'], 'win')
        self.assertEqual(first['victory_before'], 0)
        self.assertEqual(first['victory_after'], 1)
        self.owner.draft_small_game = 2
        self.owner.draft_finished_small_games = 2
        self.owner._record_small_game_stats()
        self.read('1番胜', '1番胜')
        self.assertEqual([r['result'] for r in battle_stats.load()['battles']], ['win', 'loss'])

    def test_final_count_is_last_read_not_inferred_from_last_battle(self):
        self.read('2番胜', '2番胜')
        self.owner.draft_finished_small_games = 1
        self.owner._record_small_game_stats()
        self.owner._finalize_round_stats(completed=True)
        self.owner._finalize_round_stats(completed=True)
        data = battle_stats.load()
        self.assertEqual(len(data['rounds']), 1)
        self.assertEqual(data['rounds'][0]['final_victory_count'], 2)
        self.assertEqual(data['battles'][0]['result'], 'unknown')
        self.assertEqual(battle_stats._tally(), {})

    def test_failed_ocr_keeps_previous_final_value_without_guessing(self):
        self.read('2番胜', '2番胜')
        self.owner.draft_finished_small_games = 1
        self.owner._record_small_game_stats()
        self.read('?', '', '未开启', '?', '')
        self.assertEqual(self.owner.draft_victory_count, 2)
        self.owner._finalize_round_stats(completed=True)
        self.assertEqual(battle_stats.load()['rounds'][0]['final_victory_count'], 2)
        self.assertEqual(battle_stats.load()['battles'][0]['result'], 'unknown')

    def test_record_id_and_concurrent_writes_preserve_all_samples(self):
        def write(i):
            return battle_stats.record('win', ours=['莹草'], fielded=['莹草'], event_id=f'event-{i}')
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write, range(12)))
        self.assertEqual(len(battle_stats.load()['battles']), 12)
        self.assertEqual(battle_stats.pick_bonus('莹草'), 8.0)
        self.assertEqual(battle_stats.field_bonus('莹草'), 8.0)
        battle_stats.record('unknown', ours=['莹草'], event_id='event-0')
        self.assertEqual(len(battle_stats.load()['battles']), 12)
        self.assertEqual(battle_stats.load()['battles'][0]['result'], 'win')


if __name__ == '__main__':
    unittest.main()
