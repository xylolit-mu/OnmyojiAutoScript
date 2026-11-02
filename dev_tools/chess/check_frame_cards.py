"""Offline Frame-card checks. Run with -m unittest dev_tools.chess.check_frame_cards."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from module.atom.ocr import RuleOcr
from tasks.base_task import BaseTask
from tasks.Chess.assets import ChessAssets
from tasks.Chess.runtime.frame_cards import ChessFrameCardsMixin, ROOT, EMBLEM_ROOT
from tasks.Chess.runtime.frame_cards import read_image


class OfflineTask(ChessFrameCardsMixin, BaseTask):
    def __init__(self):
        self.device = SimpleNamespace(image_frame_id=0, image=None)
        self.click = Mock()

    def appear(self, target, **kwargs):
        return target.template_match(self.device.image)


class FrameCardChecks(unittest.TestCase):
    def test_discovery_overlay_blocks_background_card_use(self):
        from tasks.Chess.runtime.hand_operations import ChessHandOperationsMixin
        task = Mock()
        task.appear.return_value = True
        self.assertEqual(ChessHandOperationsMixin._discover_named_hand_cards(task, '发现纹章'), [])
        task._frame_card_scan.assert_not_called()
        task.click.assert_not_called()

    def test_emblem_atlas_cards_use_frame_without_clicks(self):
        sources = Path(__file__).resolve().parent / 'fetter_sources'
        if not (sources / 'index.json').exists() or not (sources / 'calibration.json').exists():
            self.skipTest('External emblem source atlas is unavailable in this checkout')
        task = OfflineTask()
        scale = json.loads((Path(__file__).resolve().parent/'fetter_sources/calibration.json').read_text(encoding='utf-8'))['scale']
        for key, item in json.loads((Path(__file__).resolve().parent/'fetter_sources/index.json').read_text(encoding='utf-8'))['entries'].items():
            with self.subTest(emblem=key):
                x, y, w, h = item['source_roi']
                card = read_image(Path(__file__).resolve().parent/'fetter_sources'/item['source'])[y:y+h, x:x+w]
                card = cv2.resize(card, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                task.device.image = np.zeros((720,1280,3), dtype=np.uint8)
                task.device.image[535:535+card.shape[0], 310:310+card.shape[1]] = card
                task.device.image_frame_id += 1
                with patch.object(RuleOcr, 'ocr', return_value='') as ocr:
                    cards = task._frame_card_scan('emblem')
                    self.assertEqual(len(cards), 1)
                    self.assertEqual(cards[0]['name'], item['name'])
                    self.assertEqual(cards[0]['roi'][2:], (86,137))
                    self.assertIs(task._frame_card_scan('emblem'), cards)
                    self.assertEqual(ocr.call_count, 0)
                self.assertEqual(task.frame_emblem_portraits[key].shape[:2], (44,49))
        task.click.assert_not_called()

    def test_emblem_unknown_image_cannot_be_named_by_ocr_alone(self):
        task = OfflineTask()
        portrait = np.random.default_rng(42).integers(0,256,(48,53,3), dtype=np.uint8)
        name, _ = task._frame_emblem_identity(portrait, '分裂纹章')
        self.assertIsNone(name)

    def test_discovery_names_route_by_exact_ocr_without_icon_identity(self):
        sources = Path(__file__).resolve().parent / 'fetter_sources'
        if not (sources / 'index.json').exists() or not (sources / 'calibration.json').exists():
            self.skipTest('External emblem source atlas is unavailable in this checkout')
        task = OfflineTask()
        item = json.loads((Path(__file__).resolve().parent/'fetter_sources/index.json').read_text(encoding='utf-8'))['entries']['fenlie']
        x,y,w,h = item['source_roi']
        card = read_image(Path(__file__).resolve().parent/'fetter_sources'/item['source'])[y:y+h,x:x+w]
        card = cv2.resize(card,None,fx=.588,fy=.588,interpolation=cv2.INTER_AREA)
        for name, expected in [('发现纹章', 'discovery'), ('发现御魂', 'discovery')]:
            task.device.image = np.zeros((720,1280,3),dtype=np.uint8)
            task.device.image[535:535+card.shape[0],310:310+card.shape[1]] = card
            task.device.image_frame_id += 1
            with patch.object(RuleOcr, 'ocr', return_value=name), patch.object(task, '_frame_emblem_identity', return_value=(None, 0.0)):
                result = task._frame_card_scan('emblem')
            self.assertEqual(result[0]['type'], expected)
            self.assertEqual(result[0]['kind'], name)
            self.assertEqual(result[0]['similarity'], 1.0)
        task.click.assert_not_called()

    def test_all_reference_cards_use_real_frame_geometry(self):
        if not list((ROOT/'card').glob('cost*/*.png')) or not list((ROOT/'store').glob('cost*/*.png')):
            self.skipTest('Original card samples archived externally; restore backup to run source geometry checks')
        task = OfflineTask()
        for kind, entries in task.frame_card_library['cards'].items():
            frame = ChessAssets.F_HAND_CARD if kind == 'card' else ChessAssets.F_STORE_CARD
            x, y, w, h = frame.roi_ranges[-1]
            for name, reference in entries.items():
                with self.subTest(kind=kind, name=name):
                    sample = cv2.resize(read_image(ROOT/reference['source']), (w, h))
                    task.device.image = np.zeros((720,1280,3), dtype=np.uint8)
                    task.device.image[y:y+h, x:x+w] = sample
                    task.device.image_frame_id += 1
                    # OCR transport is independent; geometry and portrait matching are real.
                    texts = reference.get('text', []) or ['']*(len(frame.roi_ranges)-2)
                    with patch.object(RuleOcr, 'ocr', side_effect=texts) as ocr:
                        result = task._frame_card_scan(kind)
                        self.assertEqual(len(result), 1)
                        self.assertEqual(result[0]['name'], name)
                        self.assertLessEqual(abs(result[0]['roi'][0]-x), 3)
                        self.assertLessEqual(abs(result[0]['roi'][1]-y), 3)
                        self.assertEqual(result[0]['roi'][2:], (w,h))
                        self.assertEqual(result[0]['price'], reference['cost'])
                        self.assertIs(task._frame_card_scan(kind), result)
                        self.assertEqual(ocr.call_count, 0)
        task.click.assert_not_called()

    def test_image_identity_survives_wrong_bonds(self):
        task = OfflineTask()
        portrait = task.frame_portraits['card']['hakurou']
        name, score = task._frame_card_identity('card', portrait, ('wrong', '', ''))
        self.assertEqual(name, 'hakurou')
        self.assertGreater(score, .99)

    def test_uncertain_portrait_does_not_resolve_by_name_alone(self):
        task = OfflineTask()
        portrait = np.random.default_rng(71).integers(0,256,(71,97,3),dtype=np.uint8)
        name, _ = task._frame_card_identity('store', portrait, ('白狼','七角山','嗜战',''))
        self.assertIsNone(name)

    def test_ambiguous_portraits_use_corrected_catalog_bonds(self):
        task = OfflineTask()
        portrait = task.frame_portraits['card']['hakurou']
        task.__dict__['frame_portraits'] = {'card': {'hakurou': portrait, 'kappa': portrait}}
        name, _ = task._frame_card_identity('card', portrait, ('七角山', '追击', ''))
        self.assertEqual(name, 'hakurou')
        name, _ = task._frame_card_identity('card', portrait, ('七角山', '嗜战', ''))
        self.assertIsNone(name)

    def test_grayscale_templates_are_cached_without_changing_badges_or_frame_crops(self):
        task = OfflineTask()
        self.assertEqual(task.frame_portraits['card']['hakurou'].ndim, 2)
        self.assertEqual(task.frame_emblem_portraits['fenlie'].ndim, 2)
        self.assertIs(task.frame_portraits, task.frame_portraits)
        self.assertEqual(task.frame_badges['card'][0].ndim, 3)
        rgb = read_image(ROOT/task.frame_card_library['cards']['card']['hakurou']['portrait'])
        before = rgb.copy()
        name, _ = task._frame_card_identity('card', rgb, ('', '', ''))
        self.assertEqual(name, 'hakurou')
        np.testing.assert_array_equal(rgb, before)
        color = OfflineTask()
        color.FRAME_PORTRAIT_GRAYSCALE = False
        self.assertEqual(color.frame_portraits['card']['hakurou'].ndim, 3)
        self.assertEqual(task.frame_portraits['card']['hakurou'].nbytes * 3,
                         color.frame_portraits['card']['hakurou'].nbytes)

    def test_empty_frame_does_not_read_or_click(self):
        task = OfflineTask()
        task.device.image = np.zeros((720,1280,3), dtype=np.uint8)
        with patch.object(RuleOcr, 'ocr') as ocr:
            self.assertEqual(task._frame_card_scan('card'), [])
            self.assertEqual(task._frame_card_scan('store'), [])
            ocr.assert_not_called()
        task.click.assert_not_called()

    def test_multi_card_locations_and_expected_purchase_identity(self):
        if not list((ROOT/'store').glob('cost*/*.png')):
            self.skipTest('Original store samples archived externally; restore backup to run source geometry checks')
        task = OfflineTask()
        task.device.image = np.zeros((720,1280,3), dtype=np.uint8)
        names = ['hakurou', 'kotodama', 'odokuro', 'kujaku_myoo', 'tsukuyomi']
        for i, name in enumerate(names):
            source = task.frame_card_library['cards']['store'][name]['source']
            sample = cv2.resize(read_image(ROOT/source), (175,237))
            x = 343+i*187
            task.device.image[57:294, x:x+175] = sample
        with patch.object(RuleOcr, 'ocr', return_value='') as ocr:
            cards = task._frame_card_scan('store')
            self.assertEqual([c['name'] for c in cards], names)
            before = ocr.call_count
            self.assertEqual(task._frame_shop_in_slot(ChessAssets.C_SHIKIGAMI_5)['name'], 'hakurou')
            self.assertIsNone(task._frame_shop_in_slot(ChessAssets.C_SHIKIGAMI_5, 'kotodama'))
            self.assertEqual(ocr.call_count, before)
        task.click.assert_not_called()

    def test_reference_front_is_inset_two_pixels(self):
        task = OfflineTask()
        for kind, frame in [('card',ChessAssets.F_HAND_CARD), ('store',ChessAssets.F_STORE_CARD)]:
            _, _, w, h = frame.roi_ranges[-2]
            for portrait in task.frame_portraits[kind].values():
                self.assertEqual(portrait.shape[:2], (h-4,w-4))


if __name__ == '__main__':
    unittest.main()
