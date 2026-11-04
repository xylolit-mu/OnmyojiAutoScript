"""Hand selection and theoretical lineup recording, no top-avatar checks."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import cv2
import numpy as np
from tasks.DraftDuel.script_task import ScriptTask
from tasks.DraftDuel.config import DraftDuel

AREAS=[(283,554,56,41),(397,554,56,41),(511,554,56,41),(625,554,56,41),(739,554,56,41)]


class YellowCrossChecks(unittest.TestCase):
    def owner(self):
        owner=object.__new__(ScriptTask)
        owner.config=SimpleNamespace(draft_duel=DraftDuel(draft_duel_config={'match_mode':'队长'}))
        owner.device=Mock()
        owner.screenshot=Mock()
        owner._save_draft_checkpoint=Mock()
        owner._observe_duel_lobby=Mock()
        owner._sync_draft_state=Mock(return_value=True)
        owner._update_deployment_clock=Mock()
        owner.draft_selected=list('abcde')
        owner.draft_planned_lineup=tuple('bda')
        owner.F_HAND_CARD=SimpleNamespace(matched_ranges=AREAS)
        owner._recognize_deployment_portrait=Mock(side_effect=AssertionError('top checked'))
        owner._capture_deployment_hand_templates=Mock(side_effect=AssertionError('portrait captured'))
        return owner

    def test_active_cross_threshold_is_point_eight(self):
        from tasks.DraftDuel.assets import DraftDuelAssets
        self.assertEqual(DraftDuelAssets.I_HAND_SELECTED_CROSS.threshold, .8)

    def test_reference_crop_and_mask_locate_second_hand_card(self):
        root=Path('tasks/DraftDuel/draft')
        template=cv2.imread(str(root/'draft_hand_selected_cross.png'))
        mask=cv2.imread(str(root/'draft_hand_selected_cross_mask.png'),0)
        self.assertEqual(template.shape[:2], mask.shape)
        self.assertGreater(np.count_nonzero(mask), 50)
        # Shift the masked cross to any card; unrelated artwork is ignored.
        for index, area in enumerate(AREAS):
            image=np.zeros((720,1280,3),dtype=np.uint8)
            x=int(area[0]+area[2]/2-template.shape[1]/2)
            image[510:510+template.shape[0],x:x+template.shape[1]]=template
            values=cv2.matchTemplate(image[490:560,250:850],template,cv2.TM_CCOEFF_NORMED,mask=mask)
            values=np.nan_to_num(values,nan=-1,posinf=-1,neginf=-1)
            _,score,_,pos=cv2.minMaxLoc(values)
            self.assertGreaterEqual(score,.9)
            owner=self.owner()
            owner.appear=Mock(return_value=True)
            owner.I_HAND_SELECTED_CROSS=SimpleNamespace(front_center=lambda:(250+pos[0]+18,490+pos[1]+14))
            self.assertEqual(owner._selected_deployment_card(AREAS),index)

    def test_missing_cross_does_not_select_a_card(self):
        owner=self.owner()
        owner.appear=Mock(return_value=False)
        self.assertIsNone(owner._selected_deployment_card(AREAS))

    def play(self, already_selected, obstruct_first_drag=False, fail_all_drags=False):
        owner=self.owner()
        scene={'selected':1 if already_selected else 4,'clicks':0}
        owner.appear=Mock(side_effect=lambda rule: False if rule is owner._battle_info_rule() else True)
        owner._selected_deployment_card=Mock(side_effect=lambda areas: scene['selected'])
        scene.update(obstructed=False, frame=0)
        def screenshot():
            scene['frame'] += 1
        owner.screenshot.side_effect = screenshot
        def clear(_):
            scene['obstructed'] = False
            scene['clear_frame'] = scene['frame']
        owner.click = Mock(side_effect=clear)
        def frame(rule):
            if rule is owner.F_HAND_CARD:
                return None if scene['obstructed'] else [np.zeros((41,56,3),dtype=np.uint8)]*5
            if rule is owner.F_FIGHT_SHIKIGAMI_SET:return [(i*100,300,60,60) for i in range(6)]
            raise AssertionError('Unexpected top frame')
        owner.runframe=Mock(side_effect=frame)
        def drag(source,*args,**kwargs):
            if fail_all_drags:
                return
            if obstruct_first_drag and owner.device.press_and_drag.call_count == 1:
                scene['obstructed'] = True
                return
            if obstruct_first_drag:
                self.assertGreater(scene['frame'], scene['clear_frame'])
            scene['selected']=min(range(5),key=lambda i:abs(source[0]-(AREAS[i][0]+28)))
        owner.device.press_and_drag.side_effect=drag
        def click(*args,**kwargs):
            scene['clicks']+=1
            if scene['clicks']<3:scene['selected']=owner.draft_selected.index(owner.draft_planned_lineup[scene['clicks']]) if already_selected else 4
            return True
        owner.appear_then_click=Mock(side_effect=click)
        def clock():owner._draft_deploy_epoch=scene['clicks']
        owner._update_deployment_clock.side_effect=clock
        with patch('tasks.DraftDuel.runtime.deployment.time.sleep'), \
                patch('tasks.DraftDuel.runtime.deployment.time.time', side_effect=lambda: 100+3*scene['frame']):
            self.assertTrue(owner._deploy_shikigami())
        self.assertEqual(owner.draft_fielded,tuple('eee') if fail_all_drags else tuple('bda'))
        self.assertEqual(owner.appear_then_click.call_count,3)
        source = 'yellow_cross_fallback' if fail_all_drags else 'planned'
        self.assertTrue(all(row['source']==source for row in owner.draft_deployment_rounds))
        owner._recognize_deployment_portrait.assert_not_called()
        return owner

    def test_target_cross_confirms_without_drag(self):
        self.play(True).device.press_and_drag.assert_not_called()

    def test_wrong_cross_drags_then_waits_for_target_before_confirm(self):
        owner=self.play(False)
        self.assertEqual(owner.device.press_and_drag.call_count,3)

    def test_obstructed_hand_clears_floating_card_before_fresh_drag(self):
        owner = self.play(False, obstruct_first_drag=True)
        owner.click.assert_called_once_with(owner.C_DEPLOYMENT_CLEAR_DRAG)
        self.assertEqual(owner.device.press_and_drag.call_count, 4)

    def test_drag_limit_records_cross_card_and_confirms_each_round(self):
        owner = self.play(False, fail_all_drags=True)
        self.assertEqual(owner.device.press_and_drag.call_count, 9)
        self.assertEqual(owner.appear_then_click.call_count, 3)
        self.assertEqual(owner.draft_fielded, tuple('eee'))


if __name__=='__main__':unittest.main()
