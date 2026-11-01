"""上阵半区、拖拽与头像确认的离线检查。"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np

from tasks.DraftDuel.config import DraftDuel
from tasks.DraftDuel.script_task import ScriptTask
from tasks.DraftDuel.runtime.portrait_match import identify_portrait, locate_portrait, HAND_PORTRAIT_SCALE


class DeploymentTests(unittest.TestCase):
    def test_hand_threshold_falls_by_point_zero_five_and_stops_at_first_match(self):
        owner = self.owner('队员')
        owner.I_HAND_CARD_AREA = SimpleNamespace(threshold=0.65, roi_back=(0, 0, 100, 100))
        hit = ((10, 20, 30, 40), 0.67)
        with patch('tasks.DraftDuel.runtime.deployment.locate_portrait',
                   side_effect=[None, None, hit]) as locate:
            self.assertEqual(owner._locate_deployment_portrait('head'), hit)
        self.assertEqual([c.kwargs['threshold'] for c in locate.call_args_list], [.75, .70, .65])
        self.assertTrue(all(c.kwargs['scale'] == 1.60 and 'scales' not in c.kwargs
                            for c in locate.call_args_list))

    def test_hand_threshold_never_falls_below_point_six_five(self):
        for initial, expected in ((.75, [.75, .70, .65]), (.63, [.75, .70, .65])):
            owner = self.owner('队长')
            owner.I_HAND_CARD_AREA = SimpleNamespace(threshold=initial, roi_back=(0, 0, 100, 100))
            with patch('tasks.DraftDuel.runtime.deployment.locate_portrait', return_value=None) as locate:
                self.assertIsNone(owner._locate_deployment_portrait('head'))
            self.assertEqual([c.kwargs['threshold'] for c in locate.call_args_list], expected)

    def test_hand_matching_selects_highest_score_among_multiple_hits(self):
        scores = np.array([[.51, .72, .61], [.65, .89, .55]], dtype=np.float32)
        template = np.ones((2, 2, 3), dtype=np.uint8)
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        with patch('tasks.DraftDuel.runtime.portrait_match.cv2.matchTemplate', return_value=scores):
            area, score = locate_portrait(template, image, (5, 6, 10, 10), scale=1, threshold=.5)
        self.assertEqual(area, (6, 7, 2, 2))
        self.assertAlmostEqual(score, .89, places=6)

    def test_later_game_only_waits_for_battle(self):
        owner = self.owner('队长')
        owner.draft_small_game = 2
        owner.draft_fielded = ('old1', 'old2', 'old3')
        owner._save_draft_checkpoint = Mock()
        owner.runframe = Mock()
        frames = iter(['down', 'right', 'down', 'battle'])
        state = {}
        owner.screenshot.side_effect = lambda: state.update(page=next(frames))
        owner.appear = Mock(side_effect=lambda rule: (
            state['page'] == 'battle' if rule is owner._battle_info_rule() else
            state['page'] == 'right' if rule is owner.I_AUTO_RIGHT else False))
        owner.appear_then_click = Mock(return_value=True)
        with patch('tasks.DraftDuel.runtime.deployment.time.sleep'):
            self.assertTrue(owner._deploy_shikigami())
        owner.appear_then_click.assert_not_called()
        owner.runframe.assert_not_called()
        owner.device.press_and_drag.assert_not_called()
        self.assertTrue(owner._draft_battle_seen)
        owner._save_draft_checkpoint.assert_called_with('battle')

    def test_auto_confirmation_alone_does_not_finish_deployment(self):
        owner = self.owner('队员')
        owner.draft_small_game = 3
        owner._save_draft_checkpoint = Mock()
        owner.appear = Mock(side_effect=lambda rule: rule is owner.I_AUTO_RIGHT)
        owner.appear_then_click = Mock()
        with patch('tasks.DraftDuel.runtime.deployment.time.monotonic',
                   side_effect=[0, 1, 121]), \
                patch('tasks.DraftDuel.runtime.deployment.time.sleep'):
            self.assertFalse(owner._deploy_shikigami())
        owner.appear_then_click.assert_not_called()
        owner.device.press_and_drag.assert_not_called()

    def test_new_round_first_game_keeps_manual_deployment(self):
        owner = self.owner('单人')
        owner.draft_small_game = 1
        owner.draft_fielded = ('a', 'b', 'c')
        owner._auto_deploy_shikigami = Mock()
        self.assertTrue(owner._deploy_shikigami())
        owner._auto_deploy_shikigami.assert_not_called()

    def owner(self, mode):
        owner = object.__new__(ScriptTask)
        owner.config = SimpleNamespace(draft_duel=DraftDuel(draft_duel_config={'match_mode': mode}))
        owner.device = Mock()
        owner.screenshot = Mock()
        owner._observe_duel_lobby = Mock()
        return owner

    def test_slots(self):
        for mode, slots in [('队长', (0, 1, 2)), ('队员', (3, 4, 5))]:
            self.assertEqual(self.owner(mode)._deployment_slots([]), slots)
        owner = self.owner('单人')
        owner.I_TEAMATE_SELECT = Mock()
        for scores, slots in [([.9, 0, 0, 0, 0, 0], (3, 4, 5)),
                              ([0, 0, 0, 0, .9, 0], (0, 1, 2)),
                              ([0] * 6, None), ([.9] * 6, None)]:
            with patch('tasks.DraftDuel.runtime.deployment.portrait_score', side_effect=scores):
                self.assertEqual(owner._deployment_slots([None] * 6), slots)

    def test_battle_info_exits_before_any_deployment_prerequisites(self):
        owner = self.owner('队长')
        owner.draft_named_heads = ()
        owner.appear = Mock(side_effect=lambda rule: rule is owner._battle_info_rule())
        owner._sync_draft_state = Mock(return_value=False)
        owner.runframe = Mock()
        owner.appear_then_click = Mock()
        self.assertTrue(owner._deploy_shikigami())
        self.assertTrue(owner._draft_battle_seen)
        owner._sync_draft_state.assert_not_called()
        owner.runframe.assert_not_called()
        owner.appear_then_click.assert_not_called()
        owner.device.press_and_drag.assert_not_called()

    def test_scaled_portrait(self):
        import cv2
        head = np.random.default_rng(42).integers(0, 255, (30, 30, 3), dtype=np.uint8)
        card = np.zeros((120, 100, 3), dtype=np.uint8)
        card[20:65, 20:65] = cv2.resize(head, (45, 45))
        self.assertEqual(identify_portrait(card, [('a', head)]), 'a')

    def test_hand_scale_search_keeps_best_match_instead_of_first_above_threshold(self):
        import cv2
        rng = np.random.default_rng(55)
        head = rng.integers(0, 255, (31, 35, 3), dtype=np.uint8)
        image = np.zeros((130, 300, 3), dtype=np.uint8)
        first = cv2.resize(head, None, fx=1.5, fy=1.5)
        noisy = np.clip(first.astype(float) + rng.normal(0, 35, first.shape), 0, 255).astype(np.uint8)
        image[10:10+first.shape[0], 10:10+first.shape[1]] = noisy
        exact = cv2.resize(head, None, fx=1.69, fy=1.69)
        image[50:50+exact.shape[0], 180:180+exact.shape[1]] = exact
        area, score = locate_portrait(head, image, (0, 0, 300, 130), scales=(1.5, 1.69))
        self.assertEqual(area, (180, 50, exact.shape[1], exact.shape[0]))
        self.assertGreater(score, .99)

    def test_locate_target_anywhere_in_back_and_return_global_coordinates(self):
        import cv2
        head = np.random.default_rng(9).integers(0, 255, (30, 30, 3), dtype=np.uint8)
        scaled = cv2.resize(head, None, fx=HAND_PORTRAIT_SCALE, fy=HAND_PORTRAIT_SCALE)
        image = np.zeros((300, 600, 3), dtype=np.uint8)
        h, w = scaled.shape[:2]
        image[149:149+h, 417:417+w] = scaled
        area, score = locate_portrait(head, image, (100, 100, 450, 150))
        self.assertEqual(area, (417, 149, w, h))
        self.assertGreater(score, .99)
        self.assertIsNone(locate_portrait(head, image, (0, 0, 100, 100)))

    def test_hand_resource_is_image_area_without_template_or_old_frame(self):
        from tasks.DraftDuel.assets import DraftDuelAssets
        self.assertFalse(hasattr(DraftDuelAssets, 'F_HAND_CARD'))
        self.assertEqual(DraftDuelAssets.I_HAND_CARD_AREA.file, '')
        self.assertEqual(DraftDuelAssets.I_HAND_CARD_AREA.roi_back, (8, 486, 1021, 223))

    def test_only_countdown_increase_changes_deployment_epoch(self):
        owner = self.owner('队长')
        owner._draft_deploy_time = None
        owner._draft_deploy_epoch = 0
        owner.O_FIGHT_WAIT_TIME = Mock()
        owner.O_FIGHT_WAIT_TIME.ocr.side_effect = [30, 29, 29, 0, '', 4, 30, 29]
        epochs = []
        for _ in range(8):
            owner._update_deployment_clock()
            epochs.append(owner._draft_deploy_epoch)
        self.assertEqual(epochs, [0, 0, 0, 0, 0, 0, 0, 1])

    def test_battle_skin_rule_is_isolated_per_client(self):
        from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
        from tasks.Component.Costume.config import BattleType
        config = SimpleNamespace(global_game=SimpleNamespace(costume_config=None))
        with patch.object(ScriptTask, 'check_costume'):
            captain = ScriptTask(config, Mock())
            member = ScriptTask(config, Mock())
        default_file = GeneralBattleAssets.I_BATTLE_INFO.file
        captain.check_costume_battle(BattleType.COSTUME_BATTLE_7)
        member.check_costume_battle(BattleType.COSTUME_BATTLE_9)
        self.assertIsNot(captain._battle_info_rule(), member._battle_info_rule())
        self.assertEqual(GeneralBattleAssets.I_BATTLE_INFO.file, default_file)
        self.assertNotEqual(captain._battle_info_rule().file, member._battle_info_rule().file)

    def deployment(self, fail_first=False, ready=True, reset_clock=True, expected=True,
                   missing_top=False, recover_after=None):
        owner = self.owner('队员')
        owner.draft_named_heads = [(n, n) for n in 'abcde']
        owner.draft_selected = list('abcde')
        owner.appear = Mock(side_effect=lambda rule: (
            False if rule is owner._battle_info_rule() else
            ready if rule is owner.I_SELECT_ENSURE_READY else True))
        owner.F_FIGHT_SHIKIGAMI = object()
        owner.F_FIGHT_SHIKIGAMI_SET = object()
        owner.runframe = Mock(side_effect=lambda rule: (
            None if missing_top and rule is owner.F_FIGHT_SHIKIGAMI
            and owner.device.press_and_drag.call_count else
            [('top', i) for i in range(6)] if rule is owner.F_FIGHT_SHIKIGAMI else
            [(i * 100, 300, 60, 60) for i in range(6)]))
        state = {'round': 0, 'clicked': False, 'checks': 0}
        owner.O_FIGHT_WAIT_TIME = Mock()
        def countdown(_):
            if not reset_clock:
                return 20
            round_number = len(owner.draft_fielded)
            if round_number != state['round']:
                state['round'] = round_number
                state['seconds'] = 30
            state['seconds'] = max(1, state.get('seconds', 30) - 1)
            if not ready and state['clicked']:
                state['after_ticks'] = state.get('after_ticks', 0) + 1
                if state['after_ticks'] == 3:
                    state['seconds'] = 30
            return state['seconds']
        owner.O_FIGHT_WAIT_TIME.ocr.side_effect = countdown
        candidates = []
        def drag(*args, **kw):
            state['clicked'] = False
            state['checks'] = 0
            state['after_ticks'] = 0
        def click(*args, **kw):
            state.setdefault('confirm_checks', []).append(state['checks'])
            state['clicked'] = True
            return True
        owner.device.press_and_drag.side_effect = drag
        owner.appear_then_click = Mock(side_effect=click)
        def locate(head, image, area, **kw):
            self.assertNotIn(head, owner.draft_fielded)
            candidates.append((len(owner.draft_fielded),
                               tuple(n for n in 'abcde' if n not in owner.draft_fielded)))
            self.assertEqual(tuple(area), tuple(owner.I_HAND_CARD_AREA.roi_back))
            return (0, 500, 80, 160), 0.9
        def identify(region, heads, **kw):
            if fail_first and not owner.draft_fielded:
                state['checks'] += 1
                if recover_after is not None and state['checks'] > recover_after:
                    return 'a'
                if not state['clicked']:
                    return 'd' if fail_first == 'wrong' else None
                return 'd'
            return next(n for n in 'abc' if n not in owner.draft_fielded)
        ticks = iter(range(10000))
        with patch('tasks.DraftDuel.runtime.deployment.time.sleep'), \
             patch('tasks.DraftDuel.runtime.deployment.time.monotonic', side_effect=lambda: next(ticks)), \
             patch('tasks.DraftDuel.runtime.deployment.plan_lineup', return_value=('a', 'b', 'c')), \
             patch('tasks.DraftDuel.runtime.deployment.identify_portrait', side_effect=identify), \
             patch('tasks.DraftDuel.runtime.deployment.locate_portrait', side_effect=locate):
            self.assertEqual(owner._deploy_shikigami(), expected)
        return owner, candidates, state

    def test_each_round_confirms_and_excludes_actual_names(self):
        owner, candidates, _ = self.deployment()
        self.assertEqual(owner.draft_fielded, ('a', 'b', 'c'))
        self.assertEqual(owner.device.press_and_drag.call_count, 3)
        self.assertEqual(owner.appear_then_click.call_count, 3)
        self.assertEqual(owner.device.press_and_drag.call_args_list[0].args,
                         ((40, 580), (330, 330)))
        self.assertTrue(all('a' not in names for r, names in candidates if r >= 1))
        self.assertTrue(all('b' not in names for r, names in candidates if r >= 2))
        self.assertEqual(owner.draft_deployment_rounds,
                         [{'round': i + 1, 'slot': i + 4, 'name': n} for i, n in enumerate('abc')])

    def test_unknown_or_wrong_top_confirms_after_three_failures_and_records_actual(self):
        for failure in (True, 'wrong'):
            owner, _, state = self.deployment(fail_first=failure)
            self.assertEqual(state['confirm_checks'][0], 3)
            self.assertEqual(owner.appear_then_click.call_count, 3)
            self.assertEqual(owner.draft_fielded, ('d', 'a', 'b'))

    def test_missing_top_frame_confirms_but_does_not_invent_actual_name(self):
        owner, _, _ = self.deployment(missing_top=True, expected=False)
        self.assertEqual(owner.device.press_and_drag.call_count, 1)
        self.assertEqual(owner.appear_then_click.call_count, 1)
        self.assertEqual(owner.draft_fielded, ())

    def test_confirms_only_after_wrong_top_recovers_to_target(self):
        owner, _, state = self.deployment(fail_first='wrong', recover_after=2)
        self.assertEqual(state['confirm_checks'][0], 4)
        self.assertEqual(owner.draft_fielded, ('a', 'b', 'c'))
        self.assertEqual(owner.appear_then_click.call_count, 3)

    def test_ensure_without_ready_and_without_countdown_reset_does_not_advance(self):
        owner, _, _ = self.deployment(ready=False, reset_clock=False, expected=False)
        self.assertEqual(owner.device.press_and_drag.call_count, 1)
        self.assertEqual(owner.appear_then_click.call_count, 1)
        self.assertEqual(owner.draft_fielded, ())

    def test_last_player_can_skip_ready_when_countdown_restarts(self):
        owner, _, _ = self.deployment(ready=False)
        self.assertEqual(owner.draft_fielded, ('a', 'b', 'c'))
        self.assertEqual(owner.device.press_and_drag.call_count, 3)

    def test_ready_locks_current_pick_but_does_not_start_next_round(self):
        owner, _, _ = self.deployment(ready=True, reset_clock=False, expected=False)
        self.assertEqual(owner.device.press_and_drag.call_count, 1)
        self.assertEqual(owner.draft_fielded, ('a',))


if __name__ == '__main__':
    unittest.main()
