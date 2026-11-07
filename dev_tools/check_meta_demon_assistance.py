"""Offline offering state, named recipients and six-star-only checks."""
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from pydantic import ValidationError

from module.config.config_model import ConfigModel
from module.exception import RequestHumanTakeover, TaskEnd
from tasks.MetaDemon.assistance import GhostOffering, run_assistance
from tasks.MetaDemon.config import MetaDemon, MetaDemonMode, AssistanceRole, BossType
from tasks.MetaDemon.script_task import ScriptTask


class AssistanceChecks(unittest.TestCase):
    def make_task(self, **options):
        conf = MetaDemon(assistance_config={'friend_names': 'Alice;Bob', **options})
        t = SimpleNamespace(conf=conf, config=SimpleNamespace(meta_demon=conf),
            start_time=datetime.now(), limit_time=timedelta(minutes=5), limit_count=10,
            total_count=0, State=SimpleNamespace(done=False), cur_boss_type=BossType.SIX_STARS,
            _boss_generation=1, device=SimpleNamespace(image=object(), image_frame_id='frame'),
            screenshot=Mock(), swipe=Mock(), click=Mock(), appear_then_click=Mock(return_value=True),
            check_and_switch_ticket=Mock(return_value=True), start_battle=Mock(),
            set_next_run=Mock(), before_run=Mock(), goto_page=Mock())
        for name in ('I_MD_FIRE', 'I_MD_SUMMON_BOSS', 'I_MD_SETTLING', 'I_MD_INVITE',
                     'I_MD_INVITE_FRIENDS', 'I_UI_REWARD', 'C_UI_REWARD'):
            setattr(t, name, object())
        t.phase = 'fire'
        t.appear = Mock(side_effect=lambda rule: rule is {
            'fire': t.I_MD_FIRE, 'empty': t.I_MD_SUMMON_BOSS,
            'settling': t.I_MD_SETTLING, 'reward': t.I_UI_REWARD,
            'panel': t.I_MD_INVITE_FRIENDS}.get(t.phase))
        t.O_MD_FRIENDS = SimpleNamespace(roi=(435, 179, 486, 351), detect_and_ocr=Mock())
        t.I_MD_FRIEND_SELECTED = SimpleNamespace(match_all=Mock(return_value=[]))
        t.S_MD_SWIPE_FRIEND_UP = SimpleNamespace(roi_front=(784,398,55,20), roi_back=(792,340,56,23))
        return t

    def test_mode_and_api_defaults(self):
        conf = MetaDemon(meta_demon_config={'mode': ['鬼王协助']},
                         assistance_config={'friend_names': ' Alice ;Bob\nAlice；Carol '})
        self.assertEqual(conf.meta_demon_config.mode, [MetaDemonMode.BOSS_ASSIST])
        self.assertEqual(conf.assistance_config.friends, ['Alice', 'Bob', 'Carol'])
        self.assertEqual(conf.assistance_config.role, AssistanceRole.OFFERING)
        with self.assertRaises(ValidationError):
            MetaDemon(meta_demon_config={'mode': ['鬼王战斗', '鬼王协助']})
        payload = ConfigModel.script_task(SimpleNamespace(meta_demon=conf), 'MetaDemon')
        self.assertTrue(all('default' in row for row in payload['assistance_config']))

    def test_shares_once_waits_for_stable_defeat_then_summons_only_six(self):
        t = self.make_task()
        offering = GhostOffering(t)
        offering.share = Mock(return_value=True)
        self.assertTrue(offering.tick())
        self.assertTrue(offering.tick())
        offering.share.assert_called_once()
        t.phase = 'settling'
        offering.tick()
        t.phase = 'unknown'
        offering.tick()
        self.assertEqual(t.total_count, 0)
        t.phase = 'empty'
        offering.tick()
        t.check_and_switch_ticket.assert_not_called()
        offering.tick()
        self.assertEqual(t.total_count, 1)
        t.check_and_switch_ticket.assert_called_once_with(sequence=(BossType.SIX_STARS,))
        t.start_battle.assert_not_called()

    def test_unknown_or_low_existing_boss_is_never_shared(self):
        for star in (None, BossType.ONE_STAR, BossType.FIVE_STARS):
            t = self.make_task()
            t.cur_boss_type = star
            offering = GhostOffering(t)
            offering.share = Mock(return_value=True)
            offering.tick()
            offering.tick()
            offering.share.assert_not_called()
            t.check_and_switch_ticket.assert_not_called()
            t.start_battle.assert_not_called()

    def test_share_refuses_low_stars_and_requires_all_recipients(self):
        t = self.make_task()
        offering = GhostOffering(t)
        offering.open_share_panel = Mock(return_value=True)
        offering.select_friends = Mock(return_value=False)
        for star in range(1, 6):
            t.cur_boss_type = BossType(star)
            self.assertFalse(offering.share())
        offering.open_share_panel.assert_not_called()
        t.cur_boss_type = BossType.SIX_STARS
        self.assertFalse(offering.share())
        t.appear_then_click.assert_not_called()

    def test_share_submits_invite_button_and_confirms_panel_closed(self):
        t = self.make_task()
        offering = GhostOffering(t)
        offering.open_share_panel = Mock(return_value=True)
        offering.select_friends = Mock(return_value=True)
        self.assertTrue(offering.share())
        t.appear_then_click.assert_called_once_with(t.I_MD_INVITE_FRIENDS, interval=1.5)
        t.start_battle.assert_not_called()

    def test_named_matching_is_exact_and_boxes_use_resource_roi(self):
        t = self.make_task()
        t.O_MD_FRIENDS.detect_and_ocr.return_value = [SimpleNamespace(
            ocr_text=' Alice ', box=np.array([[5,10],[45,10],[45,30],[5,30]]))]
        rows = GhostOffering(t).friend_rows()
        self.assertEqual(rows, {'Alice': [(440, 189, 40, 20)]})
        self.assertNotIn('Alic', rows)

    def test_missing_similar_name_does_not_send_partial_selection(self):
        t = self.make_task(friend_names='Alice')
        t.phase = 'panel'
        offering = GhostOffering(t)
        offering.list_to_top = Mock(return_value=True)
        offering.friend_rows = Mock(return_value={'Alicex': [(1,2,3,4)]})
        offering.select_friend = Mock(return_value=True)
        self.assertFalse(offering.select_friends())
        offering.select_friend.assert_not_called()
        t.appear_then_click.assert_not_called()

    def test_exact_friends_selected_once_each(self):
        t = self.make_task()
        t.phase = 'panel'
        offering = GhostOffering(t)
        offering.list_to_top = Mock(return_value=True)
        offering.friend_rows = Mock(return_value={'Alice': [(1,2,3,4)], 'Bob': [(5,6,7,8)]})
        offering.select_friend = Mock(return_value=True)
        self.assertTrue(offering.select_friends())
        self.assertEqual(offering.select_friend.call_count, 2)
        t.appear_then_click.assert_not_called()

    def test_old_selections_are_not_sent_to_unconfigured_people(self):
        t = self.make_task()
        t.phase = 'panel'
        offering = GhostOffering(t)
        offering.list_to_top = Mock(return_value=True)
        offering.selected_count = Mock(return_value=1)
        self.assertFalse(offering.select_friends())
        t.appear_then_click.assert_not_called()

    def test_friend_click_waits_for_selected_marker_count(self):
        t = self.make_task()
        t.phase = 'panel'
        t.I_MD_FRIEND_SELECTED.match_all.side_effect = [[], [], [object()]]
        offering = GhostOffering(t)
        self.assertTrue(offering.select_friend('Alice', (440, 189, 40, 20)))
        self.assertEqual(t.click.call_args.args[0].roi_front, (440, 189, 40, 20))
        self.assertEqual(t.screenshot.call_count, 2)

    def test_share_failure_never_records_success_or_enters_battle(self):
        t = self.make_task()
        offering = GhostOffering(t)
        offering.share = Mock(return_value=False)
        self.assertFalse(offering.tick())
        self.assertIsNone(offering.shared_generation)
        self.assertEqual(t.total_count, 0)
        t.start_battle.assert_not_called()

    def test_kill_limit_prevents_another_summon(self):
        t = self.make_task()
        t.limit_count = 1
        offering = GhostOffering(t)
        offering.share = Mock(return_value=True)
        offering.tick()
        t.phase = 'empty'
        offering.tick()
        offering.tick()
        self.assertFalse(offering.within_limit())
        t.check_and_switch_ticket.assert_not_called()

    def test_placeholder_and_empty_friends_do_not_navigate(self):
        t = self.make_task(role=AssistanceRole.ASSIST)
        with self.assertRaises(TaskEnd):
            run_assistance(t)
        t.before_run.assert_not_called()
        t = self.make_task(friend_names='')
        with self.assertRaises(RequestHumanTakeover):
            run_assistance(t)
        t.before_run.assert_not_called()

    def test_mode_dispatches_to_assistance(self):
        t = object.__new__(ScriptTask)
        t.config = SimpleNamespace(meta_demon=MetaDemon(meta_demon_config={'mode': ['鬼王协助']}))
        with patch('tasks.MetaDemon.assistance.run_assistance', return_value='offering') as run:
            self.assertEqual(t.run(), 'offering')
            run.assert_called_once_with(t)


if __name__ == '__main__':
    unittest.main()
