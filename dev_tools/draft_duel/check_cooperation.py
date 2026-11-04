"""在临时配置和 SQLite 中验证协斗协同，不操作实际设备。"""

from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from datetime import datetime as ScheduleDateTime, timezone as ScheduleTimezone, timedelta as ScheduleDelta
from unittest.mock import Mock, patch

from module.cooperation import Cooperation, prepare_task_cooperation
from module.exception import ScriptError, TaskEnd
from tasks.DraftDuel.config import DraftDuel, DraftDuelConfig, DraftMatchMode
from tasks.DraftDuel.script_task import ScriptTask
from tasks.GameUi.game_ui import GameUi


class DraftCooperationTests(unittest.TestCase):
    def setUp(self):
        clock = patch('module.draft_duel_schedule.draft_duel_now', return_value=ScheduleDateTime(2026, 11, 1, 12, tzinfo=ScheduleTimezone(ScheduleDelta(hours=8))))
        clock.start()
        self.addCleanup(clock.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'config').mkdir()
        self.write('A', '队长', 'B')
        self.write('B', '队员', '')
        self.a = Cooperation('A', self.root)
        self.b = Cooperation('B', self.root)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, mode, peer, enabled=True):
        item = DraftDuel().model_dump(mode='json')
        item['scheduler']['enable'] = enabled
        item['draft_duel_config']['match_mode'] = mode
        item['invite_config']['cooperation_script'] = peer
        (self.root / 'config' / f'{name}.json').write_text(
            json.dumps({'draft_duel': item}), encoding='utf-8')

    def config(self, name):
        raw = json.loads((self.root / 'config' / f'{name}.json').read_text(encoding='utf-8'))
        item = DraftDuel(**raw['draft_duel'])
        config = SimpleNamespace(config_name=name, model=SimpleNamespace(draft_duel=item),
                                 draft_duel=item, task_delay=Mock(), task_call=Mock(),
                                 reload=Mock(), cooperation_runtime=self.a if name == 'A' else self.b)
        def delay(**kwargs):
            item.scheduler.next_run = datetime.now() + timedelta(minutes=17)
        config.task_delay.side_effect = delay
        return config

    def owner(self, name):
        task = object.__new__(ScriptTask)
        task.config = self.config(name)
        task.device = Mock()
        task._save_draft_checkpoint = Mock()
        task._battle_cooperation_task = "DraftDuel"
        return task

    def test_auto_requires_both_clients_and_current_completed_game(self):
        a, b = self.exchange_owners()
        a._sync_draft_state()
        b._sync_draft_state()
        a._sync_draft_state()
        for owner in (a, b):
            owner.draft_finished_small_games = 1
            owner._draft_auto_ready_after = None
        a._draft_auto_ready_after = 1
        self.assertFalse(a._sync_draft_state(require_auto=True))
        self.assertFalse(b._sync_draft_state(require_auto=True))
        b._draft_auto_ready_after = 1
        self.assertTrue(b._sync_draft_state(require_auto=True))
        self.assertTrue(a._sync_draft_state(require_auto=True))
        a.draft_finished_small_games = 2
        a._draft_auto_ready_after = 2
        self.assertFalse(a._sync_draft_state(require_auto=True))

    def test_modes_and_legacy_config_migration(self):
        self.assertEqual([mode.value for mode in DraftMatchMode], ['单人', '队员', '队长'])
        for role in ('队长', '队员'):
            settings = DraftDuelConfig(match_mode='组队', role=role)
            self.assertEqual(settings.match_mode, role)
            self.assertNotIn('role', settings.model_dump())

    def exchange_owners(self):
        self.a.peers['DraftDuel'] = 'B'
        self.b.peers['DraftDuel'] = 'A'
        a, b = self.owner('A'), self.owner('B')
        b.config.draft_duel.invite_config.cooperation_script = 'A'
        for owner in (a, b):
            owner.draft_selected = ['莹草', '鬼童丸', '茨木童子', '椒图', '辉夜姬']
            owner.draft_deployment_rounds = []
            owner.draft_fielded = ()
            owner._draft_exchange_since = 0
        b.draft_selected = ['座敷童子', '季', '百目鬼', '白藏主', '山兔']
        return a, b

    def test_partial_rosters_and_abort_are_exchanged_before_final_selection(self):
        a, b = self.exchange_owners()
        a.draft_selected = a.draft_selected[:2]
        b.draft_selected = b.draft_selected[:2]
        a._sync_draft_state(allow_partial=True)
        b._sync_draft_state(allow_partial=True)
        a._sync_draft_state(allow_partial=True)
        b._sync_draft_state(allow_partial=True)
        self.assertEqual(a.draft_peer_picks, tuple(b.draft_selected))
        self.assertEqual(b.draft_peer_picks, tuple(a.draft_selected))
        self.assertFalse(a._sync_draft_state())
        a._publish_draft_abort('候选无法满足上阵硬约束')
        b._leave_draft_team = Mock(side_effect=TaskEnd)
        with self.assertRaises(TaskEnd):
            b._sync_draft_state(allow_partial=True)
        b._leave_draft_team.assert_called_once_with(
            '协同方已结束本轮：候选无法满足上阵硬约束', success=False)

    def test_captain_selects_both_offers_and_member_never_scores(self):
        a, b = self.exchange_owners()
        a.draft_selected = []
        b.draft_selected = []
        own = ['化鲸', '稻荷神御馔津', '雪女']
        mate = ['粉婆婆', '天照', '雪御前']
        self.assertIsNone(a._captain_selection(1, own))
        self.assertIsNone(b._captain_selection(1, mate))
        picked = a._captain_selection(1, own)
        self.assertIn(picked.name, own)
        from tasks.DraftDuel.strategy.runtime_policy import choose_pick
        self.assertEqual(picked.name, choose_pick(a, own, team=[]).name)
        self.assertEqual(a._draft_pick_decision['member_pick'], choose_pick(a, mate, team=[]).name)
        # 队长已经确认选择后，队员仍可接收先前下发的本轮指令。
        a.draft_selected.append(picked.name)
        a._sync_draft_state(allow_partial=True)
        with patch('tasks.DraftDuel.strategy.runtime_policy.choose_pick', side_effect=AssertionError('member scored')):
            result = b._captain_selection(1, mate)
        self.assertIn(result.name, mate)
        self.assertEqual(result.name, a._draft_pick_decision['member_pick'])
        self.assertIsNone(b._captain_selection(1, list(reversed(mate))))

    def test_only_captain_plans_and_member_executes_received_lineup(self):
        a, b = self.exchange_owners()
        a._sync_draft_state()
        b._sync_draft_state()
        a._sync_draft_state()
        b._sync_draft_state()
        self.assertFalse(b._prepare_captain_lineup())
        self.assertTrue(a._prepare_captain_lineup())
        b._sync_draft_state()
        with patch('tasks.DraftDuel.strategy.runtime_policy.plan_duel_lineup', side_effect=AssertionError('member planned')):
            self.assertTrue(b._prepare_captain_lineup())
            self.assertEqual(b._deployment_plan(), b.draft_planned_lineup)
        self.assertEqual(list(b.draft_planned_lineup), a._draft_lineup_plan['member'])
        self.assertEqual(len(set((*a.draft_planned_lineup, *b.draft_planned_lineup))), 6)

    def test_blank_cooperation_skips_roster_even_with_runtime_peer(self):
        a, _ = self.exchange_owners()
        a.config.draft_duel.invite_config.cooperation_script = ''
        a.draft_peer_picks = ('莹草',)
        a.draft_peer_rounds = ({'round': 1, 'slot': 4, 'name': '莹草'},)
        with patch.object(self.a, 'peer_battle_state') as read, \
             patch.object(self.a, 'publish_battle_state') as publish:
            self.assertTrue(a._sync_draft_state())
            self.assertTrue(a._sync_draft_state(require_small_game=True))
        read.assert_not_called()
        publish.assert_not_called()
        self.assertEqual(a.draft_peer_picks, ())
        self.assertEqual(a.draft_peer_rounds, ())

    def test_solo_ignores_configured_peer_and_only_plans_own_pool(self):
        a, b = self.exchange_owners()
        a.config.draft_duel.draft_duel_config.match_mode = DraftMatchMode.SOLO
        a.draft_peer_picks = tuple(b.draft_selected)
        with patch.object(self.a, 'peer_battle_state') as read:
            self.assertTrue(a._sync_draft_state())
        read.assert_not_called()
        self.assertEqual(a.draft_peer_picks, ())
        self.assertTrue(set(a._deployment_plan()).issubset(a.draft_selected))

    def test_draft_session_exchanges_rosters_and_actual_rounds(self):
        a, b = self.exchange_owners()
        self.assertFalse(a._sync_draft_state())
        self.assertFalse(b._sync_draft_state())
        self.assertTrue(a._sync_draft_state())
        self.assertTrue(b._sync_draft_state())
        self.assertEqual(a._draft_session, b._draft_session)
        self.assertEqual(a.draft_peer_picks, tuple(b.draft_selected))
        b.draft_deployment_rounds.append({'round': 1, 'slot': 4, 'name': '座敷童子'})
        b.draft_fielded = ('座敷童子',)
        self.assertTrue(b._sync_draft_state())
        self.assertTrue(a._sync_draft_state())
        self.assertEqual(a.draft_peer_rounds, tuple(b.draft_deployment_rounds))
        a.draft_deployment_rounds.append({'round': 1, 'slot': 1, 'name': '鬼童丸'})
        a.draft_fielded = ('鬼童丸',)
        self.assertTrue(a._sync_draft_state())
        self.assertTrue(b._sync_draft_state())
        self.assertEqual(b.draft_peer_rounds, tuple(a.draft_deployment_rounds))
        # 同一份双方状态，无论从哪个实例调用，得到同一份联合规划。
        self.assertIn('鬼童丸', a._deployment_plan())
        self.assertIn('座敷童子', b._deployment_plan())

    def test_new_draft_session_rejects_previous_member_state(self):
        a, b = self.exchange_owners()
        a._sync_draft_state()
        b._sync_draft_state()
        a._sync_draft_state()
        old_session = a._draft_session
        fresh, _ = self.exchange_owners()
        self.assertFalse(fresh._sync_draft_state())
        self.assertNotEqual(fresh._draft_session, old_session)
        self.assertFalse(hasattr(fresh, 'draft_peer_picks'))

    def test_both_clients_wait_for_captains_frozen_weights_before_matching(self):
        a, b = self.exchange_owners()
        self.assertFalse(a._sync_draft_state())
        self.assertFalse(b._sync_draft_state())
        self.assertTrue(a._sync_draft_state())
        self.assertTrue(b._sync_draft_state())
        self.assertEqual(a._draft_field_weights, b._draft_field_weights)
        self.assertEqual(getattr(a, 'draft_small_game', 0), 0)
        own, mate = a._deployment_plan(), b._deployment_plan()
        with patch('tasks.DraftDuel.strategy.battle_stats._tally', side_effect=AssertionError('live weights')):
            self.assertEqual(a._deployment_plan(), own)
            self.assertEqual(b._deployment_plan(), mate)
        a._begin_small_game()
        b._begin_small_game()
        self.assertFalse(a._sync_draft_state(require_small_game=True))
        b._sync_draft_state(require_small_game=True)
        self.assertTrue(a._sync_draft_state(require_small_game=True))
        self.assertEqual(a._draft_field_weights, b._draft_field_weights)

    def test_previous_small_game_rounds_cannot_lock_new_small_game(self):
        a, b = self.exchange_owners()
        a._sync_draft_state()
        b._sync_draft_state()
        a._sync_draft_state()
        b.draft_deployment_rounds = [{'round': 1, 'slot': 4, 'name': '座敷童子'}]
        b._sync_draft_state()
        a._begin_small_game()
        self.assertFalse(a._sync_draft_state(require_small_game=True))
        self.assertEqual(a.draft_peer_rounds, ())
        b._begin_small_game()
        self.assertTrue(b._sync_draft_state(require_small_game=True))
        self.assertTrue(a._sync_draft_state(require_small_game=True))
        self.assertTrue(b._sync_draft_state(require_small_game=True))
        self.assertEqual(a.draft_peer_picks, tuple(b.draft_selected))

    def test_member_ignores_roster_older_than_current_run(self):
        a, b = self.exchange_owners()
        a._sync_draft_state()
        b._draft_exchange_since = float('inf')
        self.assertFalse(b._sync_draft_state())
        self.assertFalse(hasattr(b, '_draft_session'))

    def test_invalid_pairs_and_disabled_peer_rejected(self):
        self.a.validate('DraftDuel', 'B')
        for mode in ('单人', '队长'):
            self.write('B', mode, '')
            with self.assertRaises(ScriptError):
                self.a.validate('DraftDuel', 'B')
        self.write('B', '队员', '', enabled=False)
        with self.assertRaisesRegex(ScriptError, '协同任务未启用'):
            self.a.validate('DraftDuel', 'B')

    def test_self_and_third_party_rejected(self):
        with self.assertRaises(ScriptError):
            self.a.validate('DraftDuel', 'A')
        self.write('C', '队长', 'B')
        with self.assertRaises(ScriptError):
            self.a.validate('DraftDuel', 'B')

    def test_idle_peer_wakes_without_config_write(self):
        self.write('B', '队员', 'A')
        before = (self.root / 'config/B.json').read_bytes()
        self.b.publish('')
        prepare_task_cooperation(self.config('A'), 'DraftDuel')
        config = self.config('B')
        self.assertEqual(self.b.wake_next(config), 'DraftDuel')
        config.task_call.assert_called_once_with('DraftDuel', force_call=False)
        self.assertEqual(before, (self.root / 'config/B.json').read_bytes())
        self.assertEqual(self.b.peers['DraftDuel'], 'A')

    def test_busy_and_offline_peer_use_existing_delay_outcomes(self):
        for active, expected in (('Dokan', 'cooperation_waiting'), (None, 'cooperation_failed')):
            with self.subTest(active=active):
                if active:
                    self.b.publish(active)
                else:
                    self.b.close()
                config = self.config('A')
                with self.assertRaises(TaskEnd):
                    prepare_task_cooperation(config, 'DraftDuel')
                self.assertEqual(config.task_runtime_outcome['status'], expected)
                config.task_delay.assert_called_once_with(task='DraftDuel', success=False, server=False)

    def test_solo_never_wakes_or_requires_partner(self):
        self.write('A', '单人', 'B')
        config = self.config('A')
        prepare_task_cooperation(config, 'DraftDuel')
        self.assertEqual(self.b.requested(), [])

    def test_blank_setting_ignores_inherited_cooperation_on_prepare(self):
        config = self.config('B')
        self.b.peers['DraftDuel'] = 'A'
        config._cooperation_peers = {'DraftDuel': 'A'}
        with patch.object(self.b, 'prepare') as prepare, \
             patch.object(self.b, 'accepted_peer') as accepted:
            prepare_task_cooperation(config, 'DraftDuel')
        prepare.assert_not_called()
        accepted.assert_not_called()
        config.task_delay.assert_not_called()

    def test_blank_setting_ignores_incoming_wake(self):
        self.b.publish('')
        prepare_task_cooperation(self.config('A'), 'DraftDuel')
        config = self.config('B')
        self.assertIsNone(self.b.wake_next(config))
        config.task_call.assert_not_called()
        self.assertEqual(self.b.requested(), [])
        self.assertNotIn('DraftDuel', self.b.peers)

    def test_blank_setting_skips_room_requests_and_answers(self):
        member = self.owner('B')
        self.b.peers['DraftDuel'] = 'A'
        with patch.object(self.b, 'pending_room_check') as pending, \
             patch.object(self.b, 'request_room_check') as request, \
             patch.object(self.b, 'answer_room_check') as answer, \
             patch.object(GameUi, 'screenshot', return_value='fresh-frame'):
            self.assertTrue(member._cooperation_rooms_ready())
            self.assertEqual(member.screenshot(), 'fresh-frame')
        pending.assert_not_called()
        request.assert_not_called()
        answer.assert_not_called()

    def test_fresh_screenshot_room_handshake(self):
        self.a.peers['DraftDuel'] = 'B'
        self.b.peers['DraftDuel'] = 'A'
        self.a.publish('DraftDuel')
        self.b.publish('DraftDuel')
        captain, member = self.owner('A'), self.owner('B')
        member.config.draft_duel.invite_config.cooperation_script = 'A'
        member._in_draft_lobby = Mock(return_value=True)
        member.appear_accept = Mock(return_value=False)
        member.appear = Mock(side_effect=lambda rule: rule is member.I_LOBBY_TEAMMATE_WAIT)
        self.assertFalse(captain._cooperation_rooms_ready())
        with patch.object(GameUi, 'screenshot', return_value='fresh-frame'):
            self.assertEqual(member.screenshot(), 'fresh-frame')
        self.assertTrue(captain._cooperation_rooms_ready())
        # An offline runner cannot reuse a previous positive room confirmation.
        self.b.close()
        self.assertFalse(captain._cooperation_rooms_ready())

    def test_empty_peer_room_does_not_confirm(self):
        self.a.peers['DraftDuel'] = 'B'
        self.b.peers['DraftDuel'] = 'A'
        self.b.publish('DraftDuel')
        captain, member = self.owner('A'), self.owner('B')
        member.config.draft_duel.invite_config.cooperation_script = 'A'
        member._in_draft_lobby = Mock(return_value=True)
        member.appear_accept = Mock(return_value=False)
        member.appear = Mock(return_value=True)
        self.assertFalse(captain._cooperation_rooms_ready())
        with patch.object(GameUi, 'screenshot'):
            member.screenshot()
        self.assertFalse(captain._cooperation_rooms_ready())




if __name__ == '__main__':
    unittest.main()
