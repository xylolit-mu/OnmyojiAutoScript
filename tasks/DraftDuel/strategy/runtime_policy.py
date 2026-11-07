"""Use simulation weights when the visible cards resolve to full templates."""
from itertools import combinations, product

from module.logger import logger
from tasks.DraftDuel.strategy import lineup
from tasks.DraftDuel.strategy.panel_weights import model, template_for


def choose_pick(owner, offers, team=(), *, template_keys=None):
    offers = tuple(offers)
    if template_keys:
        candidates = [(name, template_for(name, template_keys.get(name))) for name in offers if name not in team]
        if candidates and all(row for _, row in candidates):
            name, row = max(candidates, key=lambda candidate: candidate[1]['draft'])
            return lineup.Pick(name, row['draft'], ('第 50 轮模板选卡权重', row['key']), ())
    return lineup.choose_pick(offers, team=team)


def _rows(names, transcript):
    if isinstance(transcript, dict):
        transcript = transcript.values()
    keys = {row.get('pick'): row.get('template_key') for row in transcript if isinstance(row, dict)}
    return {name: template_for(name, keys.get(name)) for name in names}


def _score(rows):
    data, _ = model()
    vectors = [row['vector'] for row in rows]
    pair_scores = [sum(x * sum(m * y for m, y in zip(axis, b)) for x, axis in zip(a, data['matrix']))
                   for a, b in combinations(vectors, 2)]
    return (data['intercept'] + sum(row['bias'] for row in rows) / len(rows)
            + (sum(pair_scores) / len(pair_scores) if pair_scores else 0.))


def _lineups(names, locked, size):
    return [group for group in combinations(names, size) if set(locked).issubset(group)]


def _log_composition(names):
    counts = lineup.composition_counts(names)
    tags = [f'{label}×{counts[tag]}' for tag, label in lineup.ROLE_LABELS.items() if counts[tag]]
    total = sum(counts[tag] for tag in lineup.ROLE_LABELS)
    logger.info(f'DraftDuel 阵容标签 {len(tags)}/7 类、共 {total} 个 | {", ".join(tags)} | '
                f'保护合计×{counts["protect"]} | 多样性分 {lineup.role_diversity_score(counts):+.3f}')


def plan_lineup(owner, available, *, team_size=3, locked=(), field_weights=None):
    available, locked = tuple(available), tuple(locked)
    rows = _rows(available, getattr(owner, '_draft_transcript', {}))
    if team_size == 3 and rows and all(rows.values()):
        groups = _lineups(available, locked, team_size)
        if groups:
            best = max(groups, key=lambda group: _score([rows[name] for name in group]))
            logger.info(f'DraftDuel original matrix solo lineup: {best}')
            return best
    return lineup.plan_lineup(available, team_size=team_size, locked=locked,
                              field_weights=field_weights)


def plan_duel_lineup(owner, own, mate, *, locked=(), mate_locked=(), field_weights=None):
    own, mate, locked, mate_locked = tuple(own), tuple(mate), tuple(locked), tuple(mate_locked)
    local = getattr(owner, '_draft_transcript', {})
    peer = getattr(owner, '_draft_peer_transcript', ())
    config = getattr(getattr(getattr(owner, 'config', None), 'draft_duel', None), 'draft_duel_config', None)
    from tasks.DraftDuel.config import DraftMatchMode
    if getattr(config, 'match_mode', None) == DraftMatchMode.MEMBER:
        local, peer = peer, local
    own_rows, mate_rows = _rows(own, local), _rows(mate, peer)
    if own_rows and mate_rows and all(own_rows.values()) and all(mate_rows.values()):
        choices = [(a, b) for a, b in product(_lineups(own, locked, 3), _lineups(mate, mate_locked, 3))
                   if len(set(a + b)) == 6]
        if choices:
            def score(choice):
                a, b = choice
                counts = lineup.composition_counts(a + b)
                coverage = lineup.minimum_role_coverage(counts)
                return coverage, (_score([own_rows[n] for n in a] + [mate_rows[n] for n in b])
                                  + lineup.role_diversity_score(counts))
            best = max(choices, key=score)
            logger.info(f'DraftDuel original matrix joint lineup: {best}')
            _log_composition(best[0] + best[1])
            return best
    best = lineup.plan_duel_lineup(own, mate, locked=locked, mate_locked=mate_locked,
                                   field_weights=field_weights, prioritize_minimum_roles=True)
    _log_composition(best[0] + best[1])
    return best
