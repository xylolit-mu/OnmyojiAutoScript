"""Frame ranges and OCR-only quick loadout regression checks."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.atom.frame import Click, Image, RuleFrame
from tasks.Component.QuickLoadout.config import NamedQuickLoadoutConfig, QuickLoadoutConfig
from tasks.Component.QuickLoadout.quick_loadout import QuickLoadout
from tasks.MetaDemon.assets import MetaDemonAssets


FIGHT_IMAGE = './tasks/MetaDemon/md/md_quick_loadout_frame_fight.png'


def result(name, top=80):
    return SimpleNamespace(ocr_text=name,
        box=np.array([[0, top], [40, top], [40, top+20], [0, top+20]]))


class QuickLoadoutChecks(unittest.TestCase):
    def setUp(self):
        self.owner = object.__new__(QuickLoadout)
        self.owner.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8), click=Mock())
        self.owner.screenshot = Mock()
        self.owner.click = Mock()
        self.owner.swipe = Mock()
        self.frame = deepcopy(MetaDemonAssets.F_QUICK_LOADOUT_FRAME)

    def test_panel_and_fight_are_taken_from_two_ranges(self):
        layout = self.owner._build_quick_loadout_layout(self.frame)
        self.assertEqual(layout.panel, tuple(self.frame.roi_ranges[0]))
        target = self.owner._quick_loadout_fight(self.frame)
        self.assertEqual(tuple(target.roi_back), (762, 557, 108, 36))
        self.assertEqual(target.file, FIGHT_IMAGE)
        self.owner.click.assert_not_called()

    def test_frame_translation_moves_panel_button_ocr_and_controls(self):
        before = self.owner._build_quick_loadout_layout(self.frame)
        self.frame.roi_front[0] -= 100
        self.frame.roi_front[1] -= 40
        after = self.owner._build_quick_loadout_layout(self.frame)
        self.assertEqual(after.panel, (before.panel[0]-100, before.panel[1]-40, *before.panel[2:]))
        self.assertEqual(after.group_ocr.roi[0], before.group_ocr.roi[0]-100)
        self.assertEqual(after.group_ocr.roi[1], before.group_ocr.roi[1]-40)
        self.assertEqual(after.preset_equip_x, before.preset_equip_x-100)
        fight = self.owner._quick_loadout_fight(self.frame)
        self.assertEqual(tuple(fight.roi_back), (662, 517, 108, 36))

    def test_resize_keeps_all_internal_regions_inside_panel(self):
        self.frame.roi_front = [1000, 350, 22, 19.5]
        layout = self.owner._build_quick_loadout_layout(self.frame)
        x, y, w, h = layout.panel
        for area in [layout.group_ocr.roi, layout.preset_ocr.roi,
                     layout.group_swipe_down.roi_front, layout.group_swipe_down.roi_back,
                     layout.preset_swipe_down.roi_front, layout.preset_swipe_down.roi_back]:
            ax, ay, aw, ah = area
            self.assertTrue(x <= ax < ax+aw <= x+w)
            self.assertTrue(y <= ay < ay+ah <= y+h)
        for cx in (layout.group_click_x, layout.preset_select_x, layout.preset_equip_x):
            self.assertTrue(x <= cx < x+w)

    def test_missing_template_and_out_of_screen_panel_are_rejected(self):
        self.frame.operations[1]['file'] = 'does-not-exist.png'
        with self.assertRaises(ValueError):
            self.owner._quick_loadout_fight(self.frame)
        self.frame.roi_front[0] += 1000
        with self.assertRaises(ValueError):
            self.owner._build_quick_loadout_layout(self.frame)
        self.owner.click.assert_not_called()

    def test_existing_tasks_supply_valid_frames(self):
        from tasks.MartialArts.assets import MartialArtsAssets
        from tasks.AbyssShadows.assets import AbyssShadowsAssets
        for frame, image in (
            (MartialArtsAssets.F_MR_BOSS_QUICK_LOADOUT_FRAME,
             MartialArtsAssets.I_MR_BOSS_QUICK_LOADOUT_FIGHT.file),
            (AbyssShadowsAssets.F_ABYSS_QUICK_LOADOUT_FRAME,
             AbyssShadowsAssets.I_ABYSS_QUICK_LOADOUT_FIGHT.file),
        ):
            frame = deepcopy(frame)
            self.assertEqual(self.owner._build_quick_loadout_layout(frame).panel, frame.roi_ranges[0])
            self.assertEqual(tuple(self.owner._quick_loadout_fight(frame).roi_back), frame.roi_ranges[1])

    def test_custom_named_preset_resolves_only_names(self):
        self.owner._read_quick_loadout_stage_name = Mock(return_value='鬼王')
        conf = NamedQuickLoadoutConfig(enable=True, group_name='默认分组', preset_name='默认',
            custom_preset_enable=True, custom_preset='ALL:("活动分组","超鬼王");')
        resolved, _ = self.owner._resolve_named_quick_loadout(conf, Mock())
        self.assertEqual((resolved.group_name, resolved.preset_name), ('活动分组', '超鬼王'))

    def test_only_frame_is_required_and_back_is_preserved(self):
        import inspect
        self.assertEqual(set(inspect.signature(QuickLoadout.run_quick_loadout).parameters),
                         {'self', 'config', 'frame', 'name_ocr', 'dismiss'})
        back = self.frame.roi_back
        self.owner.appear = Mock(return_value=True)
        self.assertTrue(self.owner._quick_loadout_panel_visible(self.frame))
        self.assertIs(self.owner.appear.call_args_list[0].args[0], self.frame)
        self.assertEqual(self.frame.roi_back, back)
        self.assertEqual(tuple(self.owner.appear.call_args_list[1].args[0].roi_back),
                         self.frame.roi_ranges[1])
        self.owner.click.assert_not_called()

    def test_generator_preserves_explicit_file_and_preview_behavior(self):
        from dev_tools.assets_extract import FrameExtractor
        import json
        path = Path('tasks/MetaDemon/md/frame.json')
        rows = json.loads(path.read_text(encoding='utf-8'))
        extractor = FrameExtractor(str(path), rows)
        line = extractor.extract_item(rows[0])
        self.assertIn(FIGHT_IMAGE, line)
        preview = deepcopy(rows[0])
        preview['rangeOperations'][1].pop('file')
        self.assertNotIn(FIGHT_IMAGE, extractor.extract_item(preview))

    def test_ocr_only_schema_and_target_validation(self):
        self.assertEqual(set(QuickLoadoutConfig.model_fields), {'enable','group_name','preset_name'})
        with self.assertRaises(ValueError):
            QuickLoadoutConfig(enable=True, group_number=1, preset_number=1).validate_target()
        QuickLoadoutConfig(enable=True, group_name='默认分组', preset_name='超鬼王').validate_target()
        self.assertEqual(QuickLoadout._parse_custom_presets('ALL:("默认分组","超鬼王");'),
                         {'ALL': ('默认分组', '超鬼王')})
        with self.assertRaises(ValueError):
            QuickLoadout._parse_custom_presets('ALL:(1,1);')

    def test_group_and_preset_click_follow_ocr_y_not_numbered_rows(self):
        layout = self.owner._build_quick_loadout_layout(self.frame)
        layout.group_ocr.detect_and_ocr = Mock(return_value=[result('默认分组', 60)])
        layout.preset_ocr.detect_and_ocr = Mock(return_value=[result('3超鬼王', 170)])
        self.owner._rewind_list = Mock()
        conf = QuickLoadoutConfig(enable=True, group_name='默认分组', preset_name='超鬼王')
        with patch('tasks.Component.QuickLoadout.quick_loadout.sleep'):
            self.assertTrue(self.owner._select_group(layout, conf))
            row_y = self.owner._select_preset_row(layout, conf)
        self.owner.device.click.assert_called_once_with(
            layout.group_click_x, layout.group_ocr.roi[1]+70, control_name='QUICK_LOADOUT_GROUP_OCR')
        self.assertEqual(row_y, layout.preset_ocr.roi[1]+180)

    def test_similar_name_is_not_treated_as_target(self):
        layout = self.owner._build_quick_loadout_layout(self.frame)
        self.assertEqual(self.owner._find_name_y(layout.group_ocr, '分组3', [result('分组2')]), (None, 0.0))

    def test_disabled_config_does_not_open_panel(self):
        self.owner._open_quick_loadout = Mock()
        self.assertTrue(self.owner.run_quick_loadout(QuickLoadoutConfig(), frame=self.frame))
        self.owner._open_quick_loadout.assert_not_called()

    def test_default_close_clicks_random_left(self):
        from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
        self.owner._quick_loadout_panel_visible = Mock(side_effect=[True, False])
        self.assertTrue(self.owner._dismiss_quick_loadout(self.frame))
        self.owner.click.assert_called_once_with(GeneralBattleAssets.C_RANDOM_LEFT, interval=1)

    def test_explicit_close_area_is_used(self):
        from module.atom.click import RuleClick
        custom = RuleClick(roi_front=(10,20,30,40), roi_back=(10,20,30,40), name='custom_close')
        self.owner._quick_loadout_panel_visible = Mock(side_effect=[True, False])
        self.assertTrue(self.owner._dismiss_quick_loadout(self.frame, dismiss=custom))
        self.owner.click.assert_called_once_with(custom, interval=1)

    def test_failure_forwards_explicit_close_area(self):
        from module.atom.click import RuleClick
        custom = RuleClick(roi_front=(10,20,30,40), roi_back=(10,20,30,40), name='custom_close')
        self.owner._open_quick_loadout = Mock(return_value=True)
        self.owner._select_group = Mock(return_value=False)
        self.owner._dismiss_quick_loadout = Mock()
        conf = QuickLoadoutConfig(enable=True, group_name='默认分组', preset_name='超鬼王')
        self.assertFalse(self.owner.run_quick_loadout(conf, frame=self.frame, dismiss=custom))
        self.owner._dismiss_quick_loadout.assert_called_once_with(self.frame, dismiss=custom)

    def test_group_failure_closes_panel_without_equipping_or_deploying(self):
        self.owner._open_quick_loadout = Mock(return_value=True)
        self.owner._select_group = Mock(return_value=False)
        self.owner._dismiss_quick_loadout = Mock()
        self.owner._equip_quick_loadout_souls = Mock()
        conf = QuickLoadoutConfig(enable=True, group_name='默认分组', preset_name='超鬼王')
        self.assertFalse(self.owner.run_quick_loadout(conf, frame=self.frame))
        self.owner._dismiss_quick_loadout.assert_called_once_with(self.frame, dismiss=None)
        self.owner._equip_quick_loadout_souls.assert_not_called()


if __name__ == '__main__':
    unittest.main()
