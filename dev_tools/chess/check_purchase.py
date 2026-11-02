"""Chess purchase confirmation and lazy recognition checks; no game actions."""
import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace
import numpy as np
import cv2
from tasks.base_task import BaseTask
from tasks.Chess.runtime.economy import ChessEconomyMixin
from tasks.Chess.runtime.hand_operations import ChessHandOperationsMixin
from tasks.Chess.assets import ChessAssets
from tasks.Chess.runtime.frame_cards import ROOT,read_image
from dev_tools.chess.check_frame_cards import OfflineTask
from module.atom.ocr import RuleOcr

class PurchaseChecks(unittest.TestCase):
    def purchase_task(self):
        task = Mock()
        task.SHOP_BUY_TIMEOUT = 30
        task.ACTION_ICON_WAIT_TIMEOUT = 3
        task._hand_capacity_count.return_value = 0
        task._recognize_shop_slot.return_value = {'source': 'frame', 'score': .99}
        task._economy_action_allowed.return_value = True
        task._is_purchase_allowed.return_value = True
        task._is_shop_open.return_value = True
        task._can_afford_shop_shikigami.return_value = True
        badge = task._shop_purchase_badge.return_value
        task.appear.side_effect = lambda rule: rule is badge
        task._confirm_shop_purchase.return_value = False
        return task

    def cleanup_task(self):
        task = Mock()
        task._economy_guard_round = None
        task._hand_capacity_count.return_value = 10
        task._sell_unassignable_soul_if_full.return_value = None
        task._ensure_shop_open.return_value = True
        return task

    def test_purchase_click_cap_and_last_click_confirmation(self):
        task=Mock(); task.click.return_value=True
        self.assertFalse(BaseTask.ui_click_until_disappear(task,'slot',click_target='slot',visible=lambda:True,timeout=2,max_clicks=3))
        self.assertEqual(task.click.call_count,3)
        task=Mock(); task.click.return_value=True
        visible=Mock(side_effect=[True,True,True,False,False])
        self.assertTrue(BaseTask.ui_click_until_disappear(task,'slot',click_target='slot',visible=visible,timeout=2,max_clicks=3,confirm_frames=2))
        self.assertEqual(task.click.call_count,3)

    def test_failed_purchase_stops_after_two_recoveries(self):
        task = self.purchase_task()
        task._hand_capacity_count.side_effect = [0, 10, 0, 10, 0]
        task._free_one_hand_slot_for_purchase.side_effect = [
            {'type': 'emergency_cleanup'}, {'type': 'lineup'}]
        self.assertFalse(ChessEconomyMixin._buy_shop_slot(task, 1, 'slot', 'hakurou'))
        self.assertEqual(task.click.call_count, 3)
        self.assertEqual(task._confirm_shop_purchase.call_count, 3)
        self.assertEqual([c.kwargs['sell_lineup'] for c in
                          task._free_one_hand_slot_for_purchase.call_args_list], [False, True])

    def test_two_missing_frames_required_transient_missing_does_not_finish(self):
        task=Mock();task.click.return_value=True
        visible=Mock(side_effect=[True,False,True,False,False])
        self.assertTrue(BaseTask.ui_click_until_disappear(task,Mock(),click_target='slot',visible=visible,confirm_frames=2,timeout=2,interval=.3))
        self.assertEqual(task.click.call_count,2)
        self.assertEqual(task.screenshot.call_count,5)

    def test_guard_blocks_click(self):
        task=Mock()
        self.assertFalse(BaseTask.ui_click_until_disappear(task,Mock(),click_target='slot',guard=lambda:False,timeout=2))
        task.click.assert_not_called()

    def test_absent_before_click_not_confirmed_purchase(self):
        task=Mock();visible=Mock(return_value=False)
        self.assertFalse(BaseTask.ui_click_until_disappear(task,Mock(),click_target='slot',visible=visible,confirm_frames=2,timeout=2))
        task.click.assert_not_called()

    def test_buy_confirms_gold_badge_disappearance_without_fixed_sleep(self):
        task = self.purchase_task()
        task._confirm_shop_purchase.return_value = True
        with patch('tasks.Chess.runtime.economy.time.sleep') as sleep:
            self.assertTrue(ChessEconomyMixin._buy_shop_slot(task, 1, 'slot', 'hakurou'))
            sleep.assert_not_called()
        task.click.assert_called_once_with('slot')
        task._confirm_shop_purchase.assert_called_once_with(
            1, 'slot', 'hakurou', task._shop_purchase_badge.return_value)
        task._free_one_hand_slot_for_purchase.assert_not_called()

    def test_real_purchase_confirmation_waits_for_gold_badge_to_disappear(self):
        task = self.purchase_task()
        badge = task._shop_purchase_badge.return_value
        visible = [True]
        frames = iter([True, False])
        task.screenshot.side_effect = lambda: visible.__setitem__(0, next(frames))
        task.appear.side_effect = lambda rule: visible[0] if rule is badge else False
        with patch('tasks.Chess.runtime.economy.time.monotonic', side_effect=[0, 0, 1]):
            self.assertTrue(ChessEconomyMixin._confirm_shop_purchase(task, 1, 'slot', 'hakurou', badge))
        self.assertEqual(task.screenshot.call_count, 2)
        task.click.assert_not_called()

    def test_real_purchase_confirmation_stops_when_shop_closes(self):
        task = self.purchase_task()
        task._is_shop_open.return_value = False
        badge = task._shop_purchase_badge.return_value
        with patch('tasks.Chess.runtime.economy.time.monotonic', side_effect=[0, 0]):
            self.assertFalse(ChessEconomyMixin._confirm_shop_purchase(task, 1, 'slot', 'hakurou', badge))
        task.click.assert_not_called()
        task.screenshot.assert_called_once()

    def test_failed_confirmation_without_target_does_not_count_success(self):
        task = self.purchase_task()
        task._recognize_shop_slot.side_effect = [{'source': 'frame', 'score': .99}, None]
        self.assertFalse(ChessEconomyMixin._buy_shop_slot(task, 1, 'slot', 'hakurou'))
        task.click.assert_called_once_with('slot')
        task._confirm_shop_purchase.assert_called_once()
        task._free_one_hand_slot_for_purchase.assert_not_called()

    def test_blocked_purchase_keeps_emergency_cleanup_and_retry(self):
        task = self.purchase_task()
        task._hand_capacity_count.side_effect = [0, 10, 0]
        task._confirm_shop_purchase.side_effect = [False, True]
        task._free_one_hand_slot_for_purchase.return_value = {'type': 'emergency_cleanup'}
        self.assertTrue(ChessEconomyMixin._buy_shop_slot(task, 1, 'slot', 'hakurou'))
        task._free_one_hand_slot_for_purchase.assert_called_once_with(sell_lineup=False)
        self.assertEqual(task.click.call_count, 2)

    def test_empty_emergency_cleanup_sells_before_reopening_shop(self):
        task=self.cleanup_task()
        task.emergency_cleanup_hand.return_value={'type':'emergency_cleanup','equipped':[],'sold_non_lineup':[]}
        task.sell_one_lineup_hand_card.return_value={'type':'lineup'}
        task._is_hand_cleanup_allowed.return_value=True
        task._ensure_shop_open.return_value=True
        result=ChessHandOperationsMixin._free_one_hand_slot_for_purchase(task)
        self.assertEqual(result,{'type':'lineup'})
        self.assertLess(task.mock_calls.index(unittest.mock.call.sell_one_lineup_hand_card()),task.mock_calls.index(unittest.mock.call._ensure_shop_open()))

    def test_productive_cleanup_does_not_sell_lineup(self):
        for field in ('equipped','sold_non_lineup'):
            task=self.cleanup_task()
            result={'type':'emergency_cleanup','equipped':[],'sold_non_lineup':[]}
            result[field]=['cleared']
            task.emergency_cleanup_hand.return_value=result
            self.assertEqual(ChessHandOperationsMixin._free_one_hand_slot_for_purchase(task),result)
            task.sell_one_lineup_hand_card.assert_not_called()

    def test_direct_lineup_sale_not_repeated_when_purchase_still_blocked(self):
        task = self.purchase_task()
        task._hand_capacity_count.side_effect = [0, 10, 0, 10]
        task._free_one_hand_slot_for_purchase.return_value = {'type': 'lineup'}
        self.assertFalse(ChessEconomyMixin._buy_shop_slot(task, 1, 'slot', 'hakurou'))
        task._free_one_hand_slot_for_purchase.assert_called_once_with(sell_lineup=False)
        self.assertEqual(task.click.call_count, 2)

    def test_empty_cleanup_with_no_sale_or_phase_change_blocks_retry(self):
        for allowed in (True,False):
            task=self.cleanup_task()
            task.emergency_cleanup_hand.return_value={'type':'emergency_cleanup','equipped':[],'sold_non_lineup':[]}
            task._is_hand_cleanup_allowed.return_value=allowed
            task.sell_one_lineup_hand_card.return_value=None
            self.assertIsNone(ChessHandOperationsMixin._free_one_hand_slot_for_purchase(task))
            task._ensure_shop_open.assert_not_called()
            self.assertEqual(task.sell_one_lineup_hand_card.call_count,int(allowed))

    def test_known_slot_uses_only_its_portrait_after_initial_scan(self):
        task=OfflineTask();frame=ChessAssets.F_STORE_CARD
        task.device.image=np.zeros((720,1280,3),dtype=np.uint8)
        bx,by,bw,bh=map(int,frame.badge_references[0]);badge=task.frame_badges['store'][0]
        task.device.image[by:by+bh,bx:bx+bw]=badge
        x,y,w,h=frame.roi_ranges[-2]
        rgb=read_image(ROOT/task.frame_card_library['cards']['store']['hakurou']['portrait'])
        task.device.image[y+2:y+h-2,x+2:x+w-2]=rgb
        rule=SimpleNamespace(roi_back=frame.roi_ranges[-1])
        with patch.object(RuleOcr,'ocr') as ocr:
            match=task._frame_shop_in_slot(rule)
            self.assertEqual(match['name'],'hakurou')
            ocr.assert_not_called()
        task.device.image_frame_id+=1;task.device.image=task.device.image.copy()
        with patch.object(task,'_frame_card_scan',side_effect=AssertionError('Unexpected full rescan')):
            self.assertEqual(task._frame_shop_in_slot(rule,'hakurou')['name'],'hakurou')
            task.device.image[y:y+h,x:x+w]=0
            self.assertIsNone(task._frame_shop_in_slot(rule,'hakurou'))

    def test_ambiguous_frame_requests_ocr_then_resolves(self):
        task=OfflineTask();frame=ChessAssets.F_STORE_CARD
        task.device.image=np.zeros((720,1280,3),dtype=np.uint8)
        bx,by,bw,bh=map(int,frame.badge_references[0]);task.device.image[by:by+bh,bx:bx+bw]=task.frame_badges['store'][0]
        x,y,w,h=frame.roi_ranges[-2]
        rgb=read_image(ROOT/task.frame_card_library['cards']['store']['hakurou']['portrait'])
        task.device.image[y+2:y+h-2,x+2:x+w-2]=rgb
        with patch.object(task,'_frame_card_identity',side_effect=[(None,.99),('hakurou',.99)]),patch.object(RuleOcr,'ocr',side_effect=['追击','七角山','']) as ocr:
            result=task._frame_card_scan('store')
            self.assertEqual(result[0]['name'],'hakurou')
            self.assertEqual(ocr.call_count,3)

if __name__=='__main__':unittest.main()
