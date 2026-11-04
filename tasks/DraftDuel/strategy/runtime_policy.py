"""Live latent16 adapters; legacy lineup remains the frozen distillation teacher."""
import hashlib
import json
from itertools import combinations

from module.logger import logger
from tasks.DraftDuel.strategy import lineup
from tasks.DraftDuel.strategy.latent16 import LatentPolicy, POLICY_PATH
from tasks.DraftDuel.strategy.latent_field16 import FieldPolicy, FIELD_PATH

DRAFT_PATH = POLICY_PATH.with_name('latent16_role.json')


def models(owner):
    cached = getattr(owner, '_draft_latent_cache', None)
    if cached is not None:
        return cached
    snapshot = getattr(owner, '_draft_policy_snapshot', None)
    if snapshot is None:
        snapshot = {}
        for key, path, cls in (('draft', DRAFT_PATH, LatentPolicy),
                               ('field', FIELD_PATH, FieldPolicy)):
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
                model = cls.from_dict(payload)
                if model.dim != 16 or (key == 'draft' and model.head != 'role'):
                    raise ValueError('Expected 16D role draft / 16D field model')
                snapshot[key] = payload
            except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                logger.warning(f'DraftDuel {key} model unavailable; using legacy rules: {exc}')
                snapshot[key] = None
        snapshot['ids'] = {key: (hashlib.sha256(json.dumps(snapshot[key], sort_keys=True,
            ensure_ascii=False).encode()).hexdigest() if snapshot[key] else 'legacy')
            for key in ('draft', 'field')}
        owner._draft_policy_snapshot = snapshot
        logger.info(f'DraftDuel policy snapshot: {snapshot["ids"]}')
    cached = tuple(cls.from_dict(snapshot[key]) if snapshot.get(key) else None
                   for key, cls in (('draft', LatentPolicy), ('field', FieldPolicy)))
    owner._draft_latent_cache = cached
    return cached


def latent_choose_pick(owner, offers, team=()):
    model, _ = models(owner)
    team = tuple(team)
    offers = list(dict.fromkeys(name for raw in offers
        if (name := lineup.normalize_name(raw)) and name not in team))
    if not offers:
        raise ValueError('No recognized candidates')
    if model is None or any(name not in model for name in (*offers, *team)):
        return lineup.choose_pick(offers, team=team)
    pick = model.choose(offers, team)
    return lineup.Pick(pick.name, pick.score, ('latent16 role', *pick.reasons),
                       lineup.suggested_souls(lineup.CATALOG[pick.name]))


def latent_plan_lineup(owner, available, *, team_size=3, locked=(), field_weights=None):
    _, model = models(owner)
    pool = sorted({name for raw in available if (name := lineup.normalize_name(raw))})
    required = tuple(locked)
    if len(set(required)) != len(required) or not set(required).issubset(pool):
        raise ValueError('Invalid locked lineup')
    if model is None or team_size != 3 or any(name not in model._index for name in pool):
        return lineup.plan_lineup(pool, team_size=team_size, locked=required,
                                  field_weights=field_weights)
    choices = [cards for cards in combinations(pool, team_size)
               if set(required).issubset(cards)]
    if not choices:
        raise ValueError('Insufficient candidates for locked lineup')
    return max(choices, key=model.score_set)


def latent_plan_duel_lineup(owner, own, mate, *, locked=(), mate_locked=(), field_weights=None):
    """Composition tiers across six slots; learned scores only evaluate triples."""
    _, model = models(owner)
    own = sorted({name for raw in own if (name := lineup.normalize_name(raw))})
    mate = sorted({name for raw in mate if (name := lineup.normalize_name(raw))})
    if model is None or any(name not in model._index for name in (*own, *mate)):
        return lineup.plan_duel_lineup(own, mate, locked=locked, mate_locked=mate_locked,
                                      field_weights=field_weights)
    for required, pool in ((tuple(locked), own), (tuple(mate_locked), mate)):
        if len(required) != len(set(required)) or not set(required).issubset(pool):
            raise ValueError('Invalid locked lineup')
    own_choices = [cards for cards in combinations(own, 3) if set(locked).issubset(cards)]
    mate_choices = [cards for cards in combinations(mate, 3) if set(mate_locked).issubset(cards)]
    if not own_choices or not mate_choices:
        raise ValueError('Insufficient candidates for joint lineup')
    scores = {cards: model.score_set(cards) for cards in (*own_choices, *mate_choices)}
    choices = []
    for a in own_choices:
        for b in mate_choices:
            counts = lineup.composition_counts((*a, *b))
            fire, damage = counts['fire'], counts['damage']
            protect, flex = counts['protect'], counts['flex']
            if fire >= 2 and damage >= 2 and protect >= 1 and flex >= 1:
                tier = 0
            elif fire >= 1 and damage >= 1 and protect >= 1 and flex >= 1:
                tier = 1
            elif fire >= 1 and damage >= 1 and (protect >= 1 or flex >= 1):
                tier = 2
            elif fire >= 1 and damage >= 1:
                tier = 3
            else:
                tier = 4
            excess = max(0, fire - 2) + max(0, damage - 2) + max(0, protect - 1)
            choices.append(((-tier, -excess, scores[a] + scores[b]), a, b, counts))
    rank, a, b, counts = max(choices, key=lambda item: item[0])
    logger.info(f'DraftDuel joint composition tier={1-rank[0]}, counts={counts}, '
                f'latent triple score sum={scores[a]+scores[b]:.3f}')
    return a, b


# Live collection phase: use the original rules; latent models stay offline.
def choose_pick(owner, offers, team=()):
    return lineup.choose_pick(offers, team=team)


def plan_lineup(owner, available, *, team_size=3, locked=(), field_weights=None):
    return lineup.plan_lineup(available, team_size=team_size, locked=locked,
                              field_weights=field_weights)


def plan_duel_lineup(owner, own, mate, *, locked=(), mate_locked=(), field_weights=None):
    return lineup.plan_duel_lineup(own, mate, locked=locked, mate_locked=mate_locked,
                                   field_weights=field_weights)
