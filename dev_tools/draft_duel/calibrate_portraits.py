"""用两张 1280x720 原图复核 Frame、全手牌定位及其他卡牌误匹配分数。"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from tasks.DraftDuel.runtime.portrait_match import (
    HAND_PORTRAIT_SCALES, FIGHT_PORTRAIT_SCALE, locate_portrait, portrait_score,
)


def read_image(path):
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[:2] != (720, 1280):
        raise ValueError('Expected an original 1280x720 screenshot')
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    source, target = read_image(args.source), read_image(args.target)
    root = Path(__file__).resolve().parents[2]
    rules = json.loads((root / 'tasks/DraftDuel/draft/frame.json').read_text(encoding='utf-8'))
    frame = next(r for r in rules if r['itemName'] == 'shikigami_head')
    rois = [tuple(map(int, value.split(','))) for value in frame['roiRange']]
    cards = [target[510:677, x:x+108] for x in (269, 382, 494, 607, 721)]
    old_rois = [(481, 35, 42, 44), (565, 34, 42, 44), (651, 33, 42, 44),
                (737, 33, 42, 44), (822, 33, 42, 44)]
    rows = []
    for index, (x, y, w, h) in enumerate(rois):
        template = source[y:y+h, x:x+w]
        candidates = []
        for scale in HAND_PORTRAIT_SCALES:
            located = locate_portrait(template, target, (8, 486, 1021, 223),
                                      scale=scale, threshold=-1)
            if located:
                candidates.append((located[1], scale, located[0]))
        score, scale, box = max(candidates)
        matrix = [max(portrait_score(template, card, scale=s) for s in HAND_PORTRAIT_SCALES)
                  for card in cards]
        ox, oy, ow, oh = old_rois[index]
        old = source[oy:oy+oh, ox:ox+ow]
        old_score = portrait_score(old, cards[index], scale=1.58)
        correct = int(np.argmax(matrix)) == index and (269, 382, 494, 607, 721)[index] <= box[0] \
            < (269, 382, 494, 607, 721)[index] + 108
        rows.append(dict(slot=index+1, roi=rois[index], scale=scale, score=score,
                         old_score=old_score, box=box, other_card_scores=matrix,
                         correct=correct))
    first = rows[0]
    template = source[rois[0][1]:rois[0][1]+rois[0][3], rois[0][0]:rois[0][0]+rois[0][2]]
    top_score = portrait_score(template, target[34:100, 86:153], scale=FIGHT_PORTRAIT_SCALE)
    result = dict(rows=rows, first_top_score=top_score,
                  all_correct=all(r['correct'] for r in rows),
                  note='Correlation scores on these screenshots; not real-world accuracy rates.')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    canvas = np.full((390, 850, 3), 30, np.uint8)
    for panel, (label, roi, color) in enumerate((
            ('OLD 42x44', old_rois[0], (90, 90, 255)),
            ('NEW 35x31', rois[0], (80, 230, 80)))):
        crop = source[24:94, 468:536].copy()
        x, y, w, h = roi
        cv2.rectangle(crop, (x-468, y-24), (x+w-469, y+h-25), color, 1)
        canvas[55:335, 10+panel*285:282+panel*285] = cv2.resize(crop, (272, 280), interpolation=cv2.INTER_NEAREST)
        cv2.putText(canvas, label, (12+panel*285, 34), cv2.FONT_HERSHEY_SIMPLEX, .65, color, 1)
    crop = target[510:677, 269:377].copy()
    x, y, w, h = first['box']
    cv2.rectangle(crop, (x-269, y-510), (x+w-270, y+h-511), (80, 230, 80), 1)
    canvas[55:355, 600:794] = cv2.resize(crop, (194, 300), interpolation=cv2.INTER_NEAREST)
    cv2.putText(canvas, f"TARGET x{first['scale']:.2f}", (580, 34), cv2.FONT_HERSHEY_SIMPLEX, .6, (80, 230, 80), 1)
    cv2.putText(canvas, f"{first['old_score']:.4f} -> {first['score']:.4f}", (20, 372),
                cv2.FONT_HERSHEY_SIMPLEX, .75, (240, 240, 240), 1)
    cv2.imwrite(str(output / 'comparison.png'), canvas)
    print(json.dumps(result, ensure_ascii=True))
    if not result['all_correct']:
        raise SystemExit('A template selected the wrong hand card')


if __name__ == '__main__':
    main()
