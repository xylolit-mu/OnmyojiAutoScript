"""实战记录路径迁移后的离线检查，仅写临时目录。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tasks.DraftDuel.strategy import battle_stats
from tasks.DraftDuel.strategy import lineup


class BattleStatsChecks(unittest.TestCase):
    def test_joint_planning_uses_member_field_win_rate(self):
        counts = dict.fromkeys(('fire', 'damage', 'protect', 'flex', 'heal_only'), 0)
        choices = ('座敷童子', '辉夜姬')
        for preferred in choices:
            with self.subTest(preferred=preferred):
                table = {name: (3, 0) if name == preferred else (0, 3)
                         for name in choices}
                with patch.object(battle_stats, '_tally', return_value=table), \
                        patch.object(lineup, 'team_score', return_value=0), \
                        patch.object(lineup, 'composition_counts', return_value=counts):
                    own, mate = lineup.plan_duel_lineup(['莹草'], choices, team_size=1)
                self.assertEqual(own, ('莹草',))
                self.assertEqual(mate, (preferred,))

    def test_local_records_survive_template_changes_and_feed_field_planning(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'duel_stats.json'
            template = Path(folder) / 'empty.json'
            template.write_text('{"battles": [], "rounds": []}', encoding='utf-8')
            with patch.object(battle_stats, 'STATS_PATH', path), \
                    patch.object(battle_stats, 'STATS_TEMPLATE_PATH', template):
                for i in range(3):
                    battle_stats.record('win', fielded=['莹草'], event_id=str(i))
                before = path.read_bytes()
                template.write_text('{"battles": [], "rounds": [], "version": 2}', encoding='utf-8')
                self.assertEqual(len(battle_stats.load()['battles']), 3)
                self.assertEqual(path.read_bytes(), before)
                weights = {name: battle_stats.field_bonus(name) for name in ('莹草', '辉夜姬')}
                with patch.object(lineup, 'team_score', return_value=0):
                    self.assertEqual(lineup.plan_lineup(weights, team_size=1, field_weights=weights), ('莹草',))
                self.assertEqual(weights['莹草'], 8.0)

    def test_record_and_weighted_results_in_separate_data_directory(self):
        self.assertIs(lineup.battle_stats, battle_stats)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'DraftDuel' / 'duel_stats.json'
            with patch.object(battle_stats, 'STATS_PATH', path):
                self.assertEqual(battle_stats.load(), {'battles': [], 'rounds': []})
                for _ in range(3):
                    battle_stats.record('win', ours=['莹草'], fielded=['莹草'])
                self.assertEqual(len(battle_stats.load()['battles']), 3)
                self.assertEqual(battle_stats._tally(), {'莹草': (3, 0)})
                self.assertEqual(battle_stats.pick_bonus('莹草'), 8.0)
                self.assertEqual(battle_stats.field_bonus('莹草'), 8.0)


if __name__ == '__main__':
    unittest.main()
