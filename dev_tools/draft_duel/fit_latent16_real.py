"""用真实对局胜负微调选人 / 上阵策略（在蒸馏先验上做小步更新）。

数据来自 ``data/DraftDuel/duel_stats.json``（``battle_stats.record`` 写入）：

- 选人：``battle['draft_rounds']`` 是逐轮三选一（新版记录才带；旧记录没有就跳过）；
- 上阵：``battle['ours']``（5 张）与 ``battle['fielded']``（实际上的 3 张自己人）；
- 标签：``win`` = +1，``loss`` = -1，``unknown`` 丢弃。

目标（REINFORCE 式，带先验锚）：

    L(θ) = -mean( A · log π(a|s) ) + λ/2 · ‖θ-θ₀‖²

A 是胜负优势（默认 win=+1/loss=-1，可改成按胜率居中的 ±1），θ₀ 是蒸馏出来的
先验参数。这样只在真实对局与旧策略冲突的地方做小修正，样本少时不会被带跑。

样本量门槛：默认至少 ``--min-battles 100`` 场有胜负的对局才更新，否则只打印统计
并返回 2（便于接定时任务先攒数据）。门槛通过且留出集 log 概率有提升才导出
``latent16_real.json`` / ``field16_real.json``，不会覆盖蒸馏产物。

用法
----
    python -m dev_tools.draft_duel.fit_latent16_real
    python -m dev_tools.draft_duel.fit_latent16_real --epochs 30 --lr 0.002 --anchor 0.05
    python -m dev_tools.draft_duel.fit_latent16_real --force   # 数据不够也跑（只做演示）
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tasks.DraftDuel.strategy import lineup  # noqa: E402
from tasks.DraftDuel.strategy.latent16 import LatentPolicy  # noqa: E402
from tasks.DraftDuel.strategy.latent_field16 import FieldPolicy  # noqa: E402
from dev_tools.draft_duel.fit_latent16_policy import Adam, RoleNet  # noqa: E402
from dev_tools.draft_duel.fit_latent_field16 import FieldData, FieldModel  # noqa: E402

DEFAULT_BATTLES = REPO_ROOT / 'data' / 'DraftDuel' / 'duel_stats.json'
DEFAULT_DRAFT_PRIOR = REPO_ROOT / 'data' / 'DraftDuel' / 'latent16' / 'latent16_role.json'
DEFAULT_FIELD_PRIOR = REPO_ROOT / 'data' / 'DraftDuel' / 'latent16' / 'field16.json'
DEFAULT_OUTPUT = REPO_ROOT / 'data' / 'DraftDuel' / 'latent16' / 'real'
FIELD_SIZE = 3


# ---------------------------------------------------------------------------
# 1. 读真实记录
# ---------------------------------------------------------------------------
def load_battles(path: Path) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    return list(data.get('battles', []))


def _known(name: str, names: set[str]) -> bool:
    return bool(name) and name in names


def _training_sides(battles):
    """Keep both sides attached to the same big-round split group."""
    for battle in battles:
        yield battle
        if len(battle.get('mate') or []) == 5 and 'mate_fielded' in battle:
            yield {**battle, 'ours': battle['mate'],
                   'own_fielded': battle['mate_fielded'],
                   'draft_rounds': battle.get('peer_draft_rounds', [])}


def draft_samples(battles: list[dict], names: set[str]) -> tuple[list[dict], int]:
    """逐轮三选一：只有带 draft_rounds 的新记录才能用于选人微调。"""
    samples, skipped = [], 0
    for battle in _training_sides(battles):
        if battle.get('result') not in ('win', 'loss'):
            continue
        if battle.get('unresolved_slots'):
            skipped += 1
            continue
        rounds = battle.get('draft_rounds') or []
        if not rounds:
            skipped += 1
            continue
        for row in rounds:
            offers = [str(name) for name in row.get('offers') or []]
            pick = str(row.get('pick') or '')
            team = [str(name) for name in row.get('team') or []]
            if (len(offers) != 3 or pick not in offers
                    or not all(_known(name, names) for name in offers)
                    or len(team) > 4 or len(set(team)) != len(team) or pick in team
                    or len(set(offers)) != 3
                    or not all(_known(name, names) for name in team)):
                skipped += 1
                continue
            samples.append({'offers': offers, 'team': team, 'pick': pick,
                            'index': offers.index(pick),
                            'result': battle['result'],
                            'event_id': battle.get('round_id') or battle.get('event_id', '')})
    return samples, skipped


def field_samples(battles: list[dict], names: set[str]) -> tuple[list[dict], int]:
    """上阵：5 张自己人里实际上哪 3 张（协同模式下 fielded 含队友，取交集）。"""
    samples, skipped = [], 0
    for battle in _training_sides(battles):
        if battle.get('result') not in ('win', 'loss'):
            continue
        if battle.get('unresolved_slots'):
            skipped += 1
            continue
        ours = [str(name) for name in battle.get('ours') or []]
        fielded = [str(name) for name in battle.get('fielded') or []]
        if len(ours) != 5 or not all(_known(name, names) for name in ours):
            skipped += 1
            continue
        own = (list(battle['own_fielded']) if 'own_fielded' in battle else
               [name for name in fielded if name in ours])
        if len(own) != FIELD_SIZE or len(set(own)) != FIELD_SIZE or not set(own).issubset(ours):
            skipped += 1
            continue
        samples.append({'team': ours, 'field': own, 'result': battle['result'],
                        'event_id': battle.get('round_id') or battle.get('event_id', '')})
    return samples, skipped


# ---------------------------------------------------------------------------
# 2. 优势加权微调
# ---------------------------------------------------------------------------
def _advantages(samples: list[dict]) -> np.ndarray:
    """胜负优势：win +1、loss -1（不做居中，避免小批量里全胜时优势全为 0）。"""
    return np.array([1.0 if sample['result'] == 'win' else -1.0 for sample in samples])


def _split(samples: list[dict], ratio: float, seed: int) -> tuple[list[int], list[int]]:
    """按对局（event_id）划分，避免同一局的 5 轮样本跨集。"""
    events = sorted({sample['event_id'] for sample in samples})
    if len(events) < 2:
        raise ValueError('At least two independent rounds required for a holdout')
    rng = np.random.default_rng(seed)
    rng.shuffle(events)
    train_count = min(len(events) - 1, max(1, int(round(len(events) * ratio))))
    train_events = set(events[:train_count])
    train = [index for index, sample in enumerate(samples) if sample['event_id'] in train_events]
    test = [index for index, sample in enumerate(samples) if sample['event_id'] not in train_events]
    return train, test


def _draft_arrays(samples, name_index, team_size):
    candidates = np.array([[name_index[name] for name in sample['offers']] for sample in samples])
    team = np.full((len(samples), team_size), -1, dtype=np.int64)
    counts = np.zeros(len(samples), dtype=np.int64)
    labels = np.array([sample['index'] for sample in samples], dtype=np.int64)
    for row, sample in enumerate(samples):
        members = [name_index[name] for name in sample['team']]
        team[row, :len(members)] = members
        counts[row] = len(members)
    return candidates, team, counts, labels


def _draft_model_from_policy(policy: LatentPolicy):
    if policy.head != 'role':
        raise ValueError('真实对局微调目前只支持角色计数头（head="role"）')
    hidden = policy.head_params['W1'].shape[1]
    model = RoleNet(len(policy.names), policy.dim, roles=policy.roles, hidden=hidden, seed=0)
    model.Z = policy.vectors.copy()
    model.matrix = policy.matrix.copy()
    for name in ('W1', 'b1', 'W2', 'b2'):
        value = np.array(policy.head_params[name], dtype=np.float64, copy=True)
        setattr(model, name, value if value.ndim else value.reshape(1))
    return model


def _model_scores(model, candidates, team, counts):
    return model.scores(candidates, team, counts)


def _log_probability(model, candidates, team, counts, labels):
    scores = _model_scores(model, candidates, team, counts)
    shifted = scores - scores.max(axis=1, keepdims=True)
    log_normalizer = np.log(np.exp(shifted).sum(axis=1))
    return shifted[np.arange(len(labels)), labels] - log_normalizer


def fine_tune_draft(prior: LatentPolicy, samples: list[dict], *, epochs: int, lr: float,
                    anchor: float, batch_size: int, seed: int,
                    verbose: bool = True) -> tuple[object, dict]:
    names = list(prior.names)
    name_index = {name: row for row, name in enumerate(names)}
    team_size = max((len(sample['team']) for sample in samples), default=0)
    candidates, team, counts, labels = _draft_arrays(samples, name_index, team_size)
    advantages = _advantages(samples)
    model = _draft_model_from_policy(prior)
    prior_params = {name: value.copy() for name, value in model.parameters().items()}
    parameters = model.parameters()
    optimizer = Adam(parameters, lr=lr)
    rng = np.random.default_rng(seed)
    before = float(np.mean(_log_probability(model, candidates, team, counts, labels)
                           * advantages)) if len(samples) else 0.0
    for epoch in range(1, epochs + 1):
        order = rng.permutation(len(samples))
        for start in range(0, len(order), batch_size):
            batch = order[start:start + batch_size]
            loss, grads = model.loss_grad(candidates[batch], team[batch], counts[batch],
                                          labels[batch], 0.0, weights=advantages[batch])
            for name in grads:
                grads[name] = grads[name] + anchor * (parameters[name] - prior_params[name])
            norm = math.sqrt(sum(float(np.sum(grad * grad)) for grad in grads.values()))
            if norm > 5.0:
                grads = {name: grad * (5.0 / norm) for name, grad in grads.items()}
            optimizer.step(parameters, grads)
        if verbose and (epoch % max(1, epochs // 5) == 0 or epoch == epochs):
            after = float(np.mean(_log_probability(model, candidates, team, counts, labels)
                                  * advantages))
            print(f'  draft epoch {epoch}: advantage-weighted logp {after:+.4f} '
                  f'(before {before:+.4f})', flush=True)
    return model, {'before': before}


def fine_tune_field(prior: FieldPolicy, samples: list[dict], *, epochs: int, lr: float,
                    anchor: float, batch_size: int, seed: int,
                    verbose: bool = True) -> tuple[FieldModel, dict]:
    records = [{'episode': number, 'team': sample['team'], 'field': sample['field']}
               for number, sample in enumerate(samples)]
    name_index = {name: row for row, name in enumerate(prior.names)}
    data = FieldData(records, name_index)
    advantages = _advantages(samples)
    model = FieldModel(len(prior.names), prior.dim, seed=0)
    model.U = prior.vectors.copy()
    model.query = prior.query.copy()
    model.matrix = prior.matrix.copy()
    prior_params = {name: value.copy() for name, value in model.parameters().items()}
    parameters = model.parameters()
    optimizer = Adam(parameters, lr=lr)
    rng = np.random.default_rng(seed)
    sets, labels = data.sets, data.labels

    def logp():
        scores = model.scores(sets)
        shifted = scores - scores.max(axis=1, keepdims=True)
        return (shifted[np.arange(len(labels)), labels]
                - np.log(np.exp(shifted).sum(axis=1)))

    before = float(np.mean(logp() * advantages)) if len(samples) else 0.0
    for epoch in range(1, epochs + 1):
        order = rng.permutation(len(samples))
        for start in range(0, len(order), batch_size):
            batch = order[start:start + batch_size]
            loss, grads = model.loss_grad(sets[batch], labels[batch], 0.0,
                                          weights=advantages[batch])
            for name in grads:
                grads[name] = grads[name] + anchor * (parameters[name] - prior_params[name])
            norm = math.sqrt(sum(float(np.sum(grad * grad)) for grad in grads.values()))
            if norm > 5.0:
                grads = {name: grad * (5.0 / norm) for name, grad in grads.items()}
            optimizer.step(parameters, grads)
        if verbose and (epoch % max(1, epochs // 5) == 0 or epoch == epochs):
            print(f'  field epoch {epoch}: advantage-weighted logp '
                  f'{float(np.mean(logp() * advantages)):+.4f} (before {before:+.4f})',
                  flush=True)
    return model, {'before': before}


def export_draft(model, prior: LatentPolicy, meta: dict) -> LatentPolicy:
    policy = LatentPolicy(list(prior.names), model.Z, None, model.matrix,
                          head='role',
                          head_params={'W1': model.W1, 'b1': model.b1,
                                       'W2': model.W2, 'b2': model.b2},
                          roles=prior.roles)
    policy.meta.update(meta)
    return policy


def export_field(model: FieldModel, prior: FieldPolicy, meta: dict) -> FieldPolicy:
    policy = FieldPolicy(list(prior.names), model.U, model.query, model.matrix)
    policy.meta.update(meta)
    return policy


# ---------------------------------------------------------------------------
# 3. 入口
# ---------------------------------------------------------------------------
def run(*, battles: Path = DEFAULT_BATTLES, draft_prior: Path = DEFAULT_DRAFT_PRIOR,
        field_prior: Path = DEFAULT_FIELD_PRIOR, output: Path = DEFAULT_OUTPUT,
        min_battles: int = 100, force: bool = False, epochs: int = 30, lr: float = 0.002,
        anchor: float = 0.05, batch_size: int = 16, seed: int = 20261103,
        ratio: float = 0.8, verbose: bool = True) -> dict:
    output = Path(output)
    names = set(lineup.CATALOG)
    records = load_battles(battles)
    labeled = [battle for battle in records if battle.get('result') in ('win', 'loss')]
    draft, draft_skipped = draft_samples(records, names)
    field, field_skipped = field_samples(records, names)
    stats = {
        'battles': len(records),
        'labeled': len(labeled),
        'wins': sum(1 for battle in labeled if battle['result'] == 'win'),
        'losses': sum(1 for battle in labeled if battle['result'] == 'loss'),
        'draft_samples': len(draft),
        'draft_skipped': draft_skipped,
        'field_samples': len(field),
        'field_skipped': field_skipped,
    }
    if verbose:
        print('真实对局统计：' + '，'.join(f'{key}={value}' for key, value in stats.items()),
              flush=True)
    has_draft, has_field = bool(draft), bool(field)
    if not (has_draft or has_field):
        if verbose:
            print('没有可用于训练的轨迹（选人需要 draft_rounds，上阵需要 ours+fielded）。',
                  flush=True)
        return {'updated': False, 'reason': 'no-samples', 'stats': stats}
    usable_groups = {sample['event_id'] for sample in (*draft, *field) if sample['event_id']}
    stats['usable_rounds'] = len(usable_groups)
    draft_groups = {row['event_id'] for row in draft if row['event_id']}
    field_groups = {row['event_id'] for row in field if row['event_id']}
    stats.update(draft_rounds=len(draft_groups), field_rounds=len(field_groups))
    has_draft = len(draft_groups) >= max(2, min_battles)
    has_field = len(field_groups) >= max(2, min_battles)
    dry_run = not (has_draft or has_field)
    if force and dry_run:
        has_draft, has_field = len(draft_groups) >= 2, len(field_groups) >= 2
    if len(usable_groups) < 2:
        return {'updated': False, 'reason': 'insufficient-data', 'stats': stats}
    if dry_run and not force:
        if verbose:
            print(f'样本不足（独立可用大轮 {len(usable_groups)} < {min_battles}，'
                  f'逐轮轨迹 {len(draft)} 条，上阵 {len(field)} 条），本次不更新。'
                  '继续自动对局攒到门槛，或加 --force 只做演示。', flush=True)
        return {'updated': False, 'reason': 'insufficient-data', 'stats': stats}

    draft_eval = field_eval = None
    draft_path = field_path = None
    draft_model = field_model = None
    draft_policy = LatentPolicy.load(draft_prior) if has_draft else None
    field_policy = FieldPolicy.load(field_prior) if has_field else None
    meta_base = {
        'trained_at': datetime.now().isoformat(timespec='seconds'),
        'source': str(battles),
        'stats': stats,
        'epochs': epochs, 'lr': lr, 'anchor': anchor,
    }
    if has_draft:
        train, test = _split(draft, ratio, seed)
        if verbose:
            print(f'选人：训练 {len(train)} / 留出 {len(test)} 条', flush=True)
        draft_model, _ = fine_tune_draft(
            draft_policy, [draft[index] for index in train],
            epochs=epochs, lr=lr, anchor=anchor, batch_size=batch_size, seed=seed,
            verbose=verbose)
        draft_eval = _evaluate_draft(draft_policy, draft_model,
                                     [draft[index] for index in test])
        if verbose:
            print(f'留出集选人：{draft_eval["before"]:+.4f} → '
                  f'{draft_eval["after"]:+.4f}', flush=True)
    if has_field:
        train, test = _split(field, ratio, seed + 1)
        if verbose:
            print(f'上阵：训练 {len(train)} / 留出 {len(test)} 条', flush=True)
        field_model, _ = fine_tune_field(
            field_policy, [field[index] for index in train],
            epochs=epochs, lr=lr, anchor=anchor, batch_size=batch_size, seed=seed,
            verbose=verbose)
        field_eval = _evaluate_field(field_policy, field_model,
                                     [field[index] for index in test])
        if verbose:
            print(f'留出集上阵：{field_eval["before"]:+.4f} → '
                  f'{field_eval["after"]:+.4f}', flush=True)

    improved = all(result['after'] >= result['before']
                   for result in (draft_eval, field_eval) if result is not None)
    meta = dict(meta_base, holdout={'draft': draft_eval, 'field': field_eval})
    if dry_run:
        if verbose:
            print(f'样本不足（{len(usable_groups)} < {min_battles}），只做演示训练、不导出策略。',
                  flush=True)
        return {'updated': False, 'reason': 'insufficient-data', 'stats': stats,
                'holdout': meta['holdout']}
    if not improved:
        if verbose:
            print('留出集没有提升，不导出新策略。', flush=True)
        return {'updated': False, 'reason': 'no-improvement', 'stats': stats,
                'holdout': meta['holdout']}

    output.mkdir(parents=True, exist_ok=True)
    if draft_model is not None:
        draft_path = export_draft(draft_model, draft_policy, meta).save(
            output / 'latent16_real.json')
    if field_model is not None:
        field_path = export_field(field_model, field_policy, meta).save(
            output / 'field16_real.json')
    if verbose:
        print(f'已导出：{draft_path or "（无）"}、{field_path or "（无）"}', flush=True)
    return {'updated': True, 'stats': stats,
            'draft': str(draft_path) if draft_path else None,
            'field': str(field_path) if field_path else None,
            'holdout': meta['holdout']}


def _evaluate_draft(prior, model, samples):
    if not samples:
        return {'before': 0.0, 'after': 0.0}
    name_index = {name: row for row, name in enumerate(prior.names)}
    team_size = max(len(sample['team']) for sample in samples)
    candidates, team, counts, labels = _draft_arrays(samples, name_index, team_size)
    advantages = _advantages(samples)
    before = float(np.mean(_log_probability(
        _draft_model_from_policy(prior), candidates, team, counts, labels) * advantages))
    after = float(np.mean(_log_probability(model, candidates, team, counts, labels)
                          * advantages))
    return {'before': before, 'after': after, 'n': len(samples)}


def _evaluate_field(prior, model, samples):
    if not samples:
        return {'before': 0.0, 'after': 0.0}
    records = [{'episode': number, 'team': sample['team'], 'field': sample['field']}
               for number, sample in enumerate(samples)]
    name_index = {name: row for row, name in enumerate(prior.names)}
    data = FieldData(records, name_index)
    advantages = _advantages(samples)

    def logp(which):
        scores = which.scores(data.sets)
        shifted = scores - scores.max(axis=1, keepdims=True)
        return (shifted[np.arange(len(data.labels)), data.labels]
                - np.log(np.exp(shifted).sum(axis=1)))

    prior_model = FieldModel(len(prior.names), prior.dim, seed=0)
    prior_model.U = prior.vectors.copy()
    prior_model.query = prior.query.copy()
    prior_model.matrix = prior.matrix.copy()
    return {'before': float(np.mean(logp(prior_model) * advantages)),
            'after': float(np.mean(logp(model) * advantages)), 'n': len(samples)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='用真实对局胜负微调选人/上阵策略')
    parser.add_argument('--battles', type=Path, default=DEFAULT_BATTLES)
    parser.add_argument('--draft-prior', type=Path, default=DEFAULT_DRAFT_PRIOR)
    parser.add_argument('--field-prior', type=Path, default=DEFAULT_FIELD_PRIOR)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--min-battles', type=int, default=100,
                        help='独立可用大轮达到多少才更新')
    parser.add_argument('--force', action='store_true', help='样本不足也跑（只做演示）')
    parser.add_argument('--epochs', type=int, default=30)
    parser.add_argument('--lr', type=float, default=0.002)
    parser.add_argument('--anchor', type=float, default=0.05, help='向蒸馏先验的 L2 强度')
    parser.add_argument('--batch-size', type=int, default=16)
    parser.add_argument('--ratio', type=float, default=0.8)
    parser.add_argument('--seed', type=int, default=20261103)
    args = parser.parse_args(argv)
    result = run(battles=args.battles, draft_prior=args.draft_prior,
                 field_prior=args.field_prior, output=args.output,
                 min_battles=args.min_battles, force=args.force, epochs=args.epochs,
                 lr=args.lr, anchor=args.anchor, batch_size=args.batch_size,
                 ratio=args.ratio, seed=args.seed)
    return 0 if result.get('updated') else 2


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
