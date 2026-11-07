"""Resolve a read-only detail Frame to the exported simulation template."""
from collections import defaultdict
from functools import lru_cache
import json
from pathlib import Path
from random import choice

import cv2
import numpy as np

from tasks.DraftDuel.strategy.lineup import normalize_name

ROOT = Path(__file__).resolve().parents[1]
DETAIL_FRAMES = ('LEFT', 'MEDIUM', 'RIGHT')


@lru_cache(maxsize=1)
def model():
    path = ROOT / 'panel_model.json'
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    return data, {row['key']: row for row in data['templates']}


@lru_cache(maxsize=1)
def name_soul_index():
    index = defaultdict(list)
    data = model()
    if data:
        for row in data[1].values():
            name = normalize_name(row['name']) or row['name']
            index[name, row['soul']].append(row)
    return index


@lru_cache(maxsize=1)
def soul_templates():
    masks = {}
    for size in (52, 56, 60):
        y, x = np.ogrid[:size, :size]
        masks[size] = ((x - size / 2) ** 2 + (y - size / 2) ** 2 < (size * .38) ** 2).astype('uint8')
    templates = {}
    for path in sorted((ROOT / 'souls').glob('*.webp')):
        image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            continue
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        templates[path.stem] = [(cv2.resize(rgb, (size, size)), mask) for size, mask in masks.items()]
    return templates


def recognize_soul(crop):
    scores = []
    for name, variants in soul_templates().items():
        values = [cv2.minMaxLoc(cv2.matchTemplate(crop, icon, cv2.TM_CCORR_NORMED, mask=mask))[1]
                  for icon, mask in variants if all(a >= b for a, b in zip(crop.shape[:2], icon.shape[:2]))]
        if values:
            scores.append((max(values), name))
    scores.sort(reverse=True)
    if not scores:
        return None, None, None
    best, name = scores[0]
    margin = best - (scores[1][0] if len(scores) > 1 else 0.)
    return (name if best >= .94 and margin >= .03 else None), best, margin


def read_panel(result, sampled_templates=None):
    if not result or len(result) != 10:
        return None
    panel = tuple(result[:8])
    raw_name = result[8]
    name = normalize_name(raw_name)
    soul, similarity, margin = recognize_soul(result[9])
    matches = name_soul_index().get((name, soul), ())
    match_method = 'name_soul'
    for index, value in enumerate(panel):
        if len(matches) <= 1:
            break
        narrowed = [row for row in matches if row['panel'][index] == value]
        # An unreadable or misread field must not discard otherwise valid candidates.
        if narrowed and len(narrowed) < len(matches):
            matches = narrowed
            match_method = 'panel'
    keys = tuple(row['key'] for row in matches)
    template_key = None
    if len(keys) == 1:
        template_key = keys[0]
    elif keys:
        if sampled_templates is None:
            template_key = choice(keys)
        else:
            if keys not in sampled_templates:
                sampled_templates[keys] = choice(keys)
            template_key = sampled_templates[keys]
    return dict(name=name, raw_name=raw_name, panel=panel, soul=soul,
                soul_similarity=similarity, soul_margin=margin,
                template_key=template_key,
                template_match=match_method if template_key else None)


def template_for(name, key):
    data = model()
    row = data[1].get(key) if data and isinstance(key, str) else None
    return row if row and (normalize_name(row['name']) or row['name']) == name else None
