"""Regression checks for new grigri, emergency cleanup and optional OCR logging."""
import unittest
from unittest.mock import Mock, patch
from copy import copy
from types import SimpleNamespace
from pathlib import Path
import cv2, numpy as np
from tasks.Chess.assets import ChessAssets
from tasks.Chess.runtime.frame_cards import read_image
from tasks.Chess.runtime.hand_operations import ChessHandOperationsMixin
from tasks.Chess.strategy.grigri import grigri_category, grigri_score, resolve_grigri_name
from tasks.Chess.resource.badge.badge_hand_icons import BADGE_FILE_INDEX, BADGE_QUALITY_INDEX
from tasks.Chess.strategy.lineup import LINEUP_REGISTRY
from module.ocr.sub_ocr import Single

ENHANCEMENTS={'弹射炸弹':'觉','怨恨连斩':'黑童子','终极吸取':'萤草','浪涌扇舞':'蝴蝶精','螺螺螺锤':'蟹姬','以伞为剑':'姑获鸟'}
NEW=('回响纹章·贰','回响纹章','六道朱印',*ENHANCEMENTS)
class UpdateTests(unittest.TestCase):
    def test_icons_categories_and_scores(self):
        base=Path(__file__).resolve().parents[2]/'tasks/Chess/resource/badge'
        for name in NEW:
            quality=BADGE_QUALITY_INDEX[name]
            self.assertEqual(resolve_grigri_name(name,quality),(name,1.0))
            self.assertEqual(read_image(base/BADGE_FILE_INDEX[name]).shape,(97,94,3))
        for name, target in ENHANCEMENTS.items():
            self.assertEqual(grigri_category(name),'shikigami_enhancement')
            self.assertEqual(grigri_score(name),0)
            self.assertEqual(grigri_score(name, {'shikigami': {}}),0)
            self.assertEqual(grigri_score(name, {'shikigami': {target: {'display_name': target}}}),22)
            for item in LINEUP_REGISTRY.values():
                strategy = item['strategy']
                expected = 22 if any(c['display_name'] == target for c in strategy['shikigami'].values()) else 0
                self.assertEqual(grigri_score(name, strategy),expected)
        self.assertEqual(grigri_score('六道朱印','六道'),16)
        self.assertEqual(grigri_score('回响纹章','回响'),16)
        self.assertEqual(grigri_score('回响纹章·贰','回响'),22)
        for key, entry in LINEUP_REGISTRY.items():
            # 荒川的次要羁绊为回响，按次要羁绊收益评分。
            expected_silver, expected_gold = (8, 14) if key == 'huangchuan' else (4, 6)
            with self.subTest(lineup=key):
                self.assertEqual(grigri_score('回响纹章', entry['strategy']), expected_silver)
                self.assertEqual(grigri_score('回响纹章·贰', entry['strategy']), expected_gold)

    def test_emergency_close_allows_battle(self):
        task=Mock();task._read_chess_mode.return_value='战'
        task._ensure_shop_closed.return_value=True
        task._soul_hand_cards.return_value=[]
        task.cleanup_non_lineup_hand_cards.return_value=[]
        self.assertIsNotNone(ChessHandOperationsMixin.emergency_cleanup_hand(task))
        task._ensure_shop_closed.assert_called_once_with(allowed_modes=('备','战'))
        task.cleanup_non_lineup_hand_cards.assert_called_once_with(allowed_modes=('战',),emergency=True)

    def test_emergency_lineup_sale_close_allows_battle(self):
        task=Mock()
        task._economy_guard_round=1
        task._read_round_number.return_value=1
        task._read_chess_mode.return_value='战'
        task.appear.return_value=False
        task._hand_capacity_count.return_value=10
        task._sell_unassignable_soul_if_full.return_value=None
        task._ensure_shop_closed.return_value=True
        task.sell_one_lineup_hand_card.return_value={'type':'lineup'}
        task._ensure_shop_open.return_value=True
        self.assertEqual(ChessHandOperationsMixin._free_one_hand_slot_for_purchase(task,True),{'type':'lineup'})
        task._ensure_shop_closed.assert_called_once_with(allowed_modes=('备','战'))

    def test_new_icons_work_in_existing_selection_matcher(self):
        from tasks.Chess.runtime.round_state import ChessRoundStateMixin
        base=Path(__file__).resolve().parents[2]/'tasks/Chess/resource/badge'
        templates={name:read_image(base/path) for name,path in BADGE_FILE_INDEX.items()}
        for name in NEW:
            image=np.zeros((720,1280,3),dtype=np.uint8)
            image[100:197,100:194]=templates[name]
            task=SimpleNamespace(device=SimpleNamespace(image=image),grigri_icon_templates=templates,GRIGRI_ICON_THRESHOLD=.72)
            rule=SimpleNamespace(roi_front=(100,100,94,110))
            actual,score=ChessRoundStateMixin._match_grigri_icon(task,rule,BADGE_QUALITY_INDEX[name])
            self.assertEqual(actual,name)
            self.assertGreater(score,.99)

    def test_optional_frame_ocr_marks_only_third_trait(self):
        for frame,optional in [(ChessAssets.F_HAND_CARD,2),(ChessAssets.F_STORE_CARD,3)]:
            local=copy(frame);local.optional_ocr_ranges=(optional,)
            _,targets=local.range_targets((720,1280,3),allow_missing_image=True)
            for index,target in enumerate(targets):
                if hasattr(target,'log_empty'):self.assertEqual(target.log_empty,index!=optional)

    def test_empty_optional_ocr_is_debug_required_stays_info(self):
        task=SimpleNamespace(roi=(0,0,10,10),name='TEST',ocr_single_line=Mock(return_value=''),detect_and_ocr=Mock(return_value=[]),log_empty=False)
        with patch('module.ocr.sub_ocr.logger') as log:
            self.assertEqual(Single.ocr_single(task,np.zeros((10,10,3),dtype=np.uint8)),'')
            log.debug.assert_called_once();log.info.assert_not_called()
        task.log_empty=True
        with patch('module.ocr.sub_ocr.logger') as log:
            Single.ocr_single(task,np.zeros((10,10,3),dtype=np.uint8))
            log.info.assert_called_once();log.debug.assert_not_called()
if __name__=='__main__':unittest.main()
