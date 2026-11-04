"""把协同对弈的上阵规则（5 张里挑 3 张）拟合成 16 维隐向量策略。

流程
----
1. 用旧选人策略生成 N 局，每局 5 张卡组成一支队伍；
2. 旧上阵规则 ``lineup.plan_lineup(team, team_size=3)`` 给出该队该上哪 3 张；
3. 按局 4:1 划分（同一局的队伍只落在训练或测试一侧）；
4. 对每支队枚举 C(5,3)=10 种组合，用名单内 softmax 交叉熵 + L2 拟合：

       score(S) = Σ_{c∈S} b·u_c + Σ_{c<d∈S} u_cᵀ N u_d

   参数 (U, b, N)，手写解析梯度 + Adam；
5. 评估 top-1（是否与旧规则选同一套 3 张）、平均重合张数，并做 k 折交叉验证；
6. 导出 ``field16.json`` 和报告。

用法
----
    python -m dev_tools.draft_duel.fit_latent_field16
    python -m dev_tools.draft_duel.fit_latent_field16 --episodes 1000 --epochs 250
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from datetime import datetime
from itertools import combinations
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tasks.DraftDuel.strategy import lineup  # noqa: E402
from tasks.DraftDuel.strategy.latent_field16 import FIELD_DIM, FIELD_SIZE, FieldPolicy  # noqa: E402

from dev_tools.draft_duel.fit_latent16_policy import (  # noqa: E402
    Adam, _markdown_table, build_dataset, split_dataset,
    write_curve_png, write_curve_svg, write_csv)

DEFAULT_EPISODES = 1000
DEFAULT_TEAM_SIZE = 5
DEFAULT_SEED = 20261103
TRAIN_RATIO = 0.8
DEFAULT_OUTPUT = REPO_ROOT / 'data' / 'DraftDuel' / 'latent16'


# ---------------------------------------------------------------------------
# 1. 用旧上阵规则造数据
# ---------------------------------------------------------------------------
def build_field_records(dataset: dict, *, team_size: int = DEFAULT_TEAM_SIZE,
                        field_size: int = FIELD_SIZE) -> tuple[list[dict], int]:
    """每一局取最终 5 张卡，交给旧上阵规则决定上哪 3 张。"""
    grouped: dict[int, list[dict]] = {}
    for row in dataset['decisions']:
        grouped.setdefault(row['episode'], []).append(row)
    records = []
    skipped = 0
    for episode in sorted(grouped):
        rows = sorted(grouped[episode], key=lambda row: row['round'])
        if len(rows) != team_size:
            continue
        team = [row['pick'] for row in rows]
        try:
            chosen = lineup.plan_lineup(team, team_size=field_size)
        except ValueError:
            skipped += 1
            continue
        records.append({'episode': episode, 'team': team, 'field': sorted(chosen)})
    return records, skipped


class FieldData:
    """一支队伍 = 10 个候选 3 人组合 + 旧规则选中的那个。"""

    def __init__(self, records: list[dict], name_index: dict[str, int], *,
                 team_size: int = DEFAULT_TEAM_SIZE, field_size: int = FIELD_SIZE):
        self.combos = list(combinations(range(team_size), field_size))
        self.combo_array = np.array(self.combos, dtype=np.int64)
        self.size = len(records)
        self.rows = records
        self.teams = np.zeros((self.size, team_size), dtype=np.int64)
        self.labels = np.zeros(self.size, dtype=np.int64)
        for number, record in enumerate(records):
            members = [name_index[name] for name in record['team']]
            self.teams[number] = members
            target = tuple(sorted(members.index(name_index[name]) for name in record['field']))
            self.labels[number] = self.combos.index(target)
        self.sets = self.teams[:, self.combo_array]

    def __len__(self) -> int:
        return self.size

    def batch(self, indices):
        return self.sets[indices], self.labels[indices]


# ---------------------------------------------------------------------------
# 2. 集合打分模型与解析梯度
# ---------------------------------------------------------------------------
class FieldModel:
    """score(S) = Σ b·u_c + Σ u_cᵀ N u_d，N 对称。"""

    PARAMS = ('U', 'query', 'matrix')

    def __init__(self, size: int, dim: int = FIELD_DIM, *, seed: int = 0,
                 u_scale: float = 0.05, n_scale: float = 0.01):
        rng = np.random.default_rng(seed)
        self.U = rng.normal(0.0, u_scale, (size, dim))
        self.query = rng.normal(0.0, 0.10, dim)
        self.matrix = rng.normal(0.0, n_scale, (dim, dim))

    @staticmethod
    def _symmetric(matrix: np.ndarray) -> np.ndarray:
        return 0.5 * (matrix + matrix.T)

    def scores(self, sets: np.ndarray) -> np.ndarray:
        vectors = self.U[sets]                              # (B,C,f,d)
        unary = (vectors @ self.query).sum(axis=-1)         # (B,C)
        pairs = vectors @ self._symmetric(self.matrix)      # (B,C,f,d)
        pair_scores = np.einsum('bcfd,bcgd->bcfg', pairs, vectors)
        upper = np.triu_indices(vectors.shape[2], 1)
        return unary + pair_scores[:, :, upper[0], upper[1]].sum(axis=-1)

    def parameters(self) -> dict[str, np.ndarray]:
        return {name: getattr(self, name) for name in self.PARAMS}

    def loss_grad(self, sets: np.ndarray, labels: np.ndarray,
                  weight_decay: float | dict[str, float] = 0.0,
                  weights: np.ndarray | None = None
                  ) -> tuple[float, dict[str, np.ndarray]]:
        if isinstance(weight_decay, dict):
            decay = {name: float(weight_decay.get(name, 0.0)) for name in self.PARAMS}
        else:
            decay = dict.fromkeys(self.PARAMS, float(weight_decay))
        batch, combos, field_size = sets.shape
        dim = self.dim
        symmetric = self._symmetric(self.matrix)
        vectors = self.U[sets]                              # (B,C,f,d)
        unary = (vectors @ self.query).sum(axis=-1)
        rotated = vectors @ symmetric                       # (B,C,f,d)
        pair_scores = np.einsum('bcfd,bcgd->bcfg', rotated, vectors)
        upper = np.triu_indices(field_size, 1)
        scores = unary + pair_scores[:, :, upper[0], upper[1]].sum(axis=-1)

        shifted = scores - scores.max(axis=1, keepdims=True)
        exp = np.exp(shifted)
        denominator = exp.sum(axis=1, keepdims=True)
        rows = np.arange(batch)
        log_prob = shifted[rows, labels] - np.log(denominator[:, 0])
        d_scores = exp / denominator
        d_scores[rows, labels] -= 1.0
        if weights is None:
            loss = -np.mean(log_prob)
            d_scores /= batch
        else:
            weights = np.asarray(weights, dtype=np.float64).reshape(-1)
            loss = -np.mean(weights * log_prob)
            d_scores *= weights[:, None]
            d_scores /= batch

        d_query = np.zeros(dim)
        d_vectors = np.zeros_like(self.U)
        d_matrix = np.zeros_like(symmetric)
        for combo in range(combos):
            weight = d_scores[:, combo]                     # (B,)
            unit = vectors[:, combo]                        # (B,f,d)
            turned = rotated[:, combo]                      # (B,f,d)
            d_query += weight @ unit.sum(axis=1)
            for position in range(field_size):
                others = turned.sum(axis=1) - turned[:, position]
                gradient = self.query[None, :] + others
                np.add.at(d_vectors, sets[:, combo, position],
                          weight[:, None] * gradient)
            for first in range(field_size):
                for second in range(first + 1, field_size):
                    d_matrix += np.einsum('b,bd,be->de', weight, unit[:, first],
                                          unit[:, second])
        d_matrix_raw = 0.5 * (d_matrix + d_matrix.T)

        grads = {'U': d_vectors, 'query': d_query, 'matrix': d_matrix_raw}
        if any(decay.values()):
            for name in self.PARAMS:
                if decay[name]:
                    param = getattr(self, name)
                    loss += 0.5 * decay[name] * float(np.sum(param ** 2))
                    grads[name] = grads[name] + decay[name] * param
        return float(loss), grads

    @property
    def dim(self) -> int:
        return self.U.shape[1]


# ---------------------------------------------------------------------------
# 3. 训练与评估
# ---------------------------------------------------------------------------
def evaluate_field(model: FieldModel, data: FieldData) -> dict:
    sets, labels = data.batch(np.arange(data.size))
    scores = model.scores(sets)
    prediction = scores.argmax(axis=1)
    exact = np.mean(prediction == labels)
    overlap = []
    for number in range(data.size):
        chosen = set(data.combos[int(prediction[number])])
        truth = set(data.combos[int(labels[number])])
        overlap.append(len(chosen & truth))
    shifted = scores - scores.max(axis=1, keepdims=True)
    nll = float(np.mean(np.log(np.exp(shifted).sum(axis=1))
                        - shifted[np.arange(data.size), labels]))
    return {
        'n': data.size,
        'accuracy': float(exact),
        'mean_overlap': float(np.mean(overlap)),
        'nll': nll,
    }


def train_field(model: FieldModel, train_data: FieldData, test_data: FieldData, *,
                epochs: int = 250, batch_size: int = 32, lr: float = 0.05,
                weight_decay: float = 0.01, eval_every: int = 10, seed: int = 0,
                clip_norm: float = 5.0, verbose: bool = True) -> list[dict]:
    parameters = model.parameters()
    optimizer = Adam(parameters, lr=lr)
    rng = np.random.default_rng(seed)
    history: list[dict] = []

    def snapshot(epoch: int) -> None:
        train_metrics = evaluate_field(model, train_data)
        test_metrics = evaluate_field(model, test_data)
        history.append({
            'epoch': epoch,
            'train_loss': train_metrics['nll'],
            'train_accuracy': train_metrics['accuracy'],
            'test_loss': test_metrics['nll'],
            'test_accuracy': test_metrics['accuracy'],
        })
        if verbose and (epoch == 0 or epoch % max(eval_every, 1) == 0 or epoch == epochs):
            print(f'  epoch {epoch:4d}  train loss {train_metrics["nll"]:.4f}  '
                  f'acc {train_metrics["accuracy"]:.3f}  |  '
                  f'test loss {test_metrics["nll"]:.4f}  acc {test_metrics["accuracy"]:.3f}',
                  flush=True)

    snapshot(0)
    indices = np.arange(train_data.size)
    for epoch in range(1, epochs + 1):
        rng.shuffle(indices)
        for start in range(0, len(indices), batch_size):
            batch = indices[start:start + batch_size]
            sets, labels = train_data.batch(batch)
            loss, grads = model.loss_grad(sets, labels, weight_decay)
            norm = math.sqrt(sum(float(np.sum(grad * grad)) for grad in grads.values()))
            if norm > clip_norm:
                grads = {name: grad * (clip_norm / norm) for name, grad in grads.items()}
            optimizer.step(parameters, grads)
        if epoch % max(eval_every, 1) == 0 or epoch == epochs:
            snapshot(epoch)
    return history


def gradient_check_field(model: FieldModel, data: FieldData, *, seed: int = 0,
                         samples: int = 4, eps: float = 1e-6,
                         weight_decay: float = 0.0) -> dict[str, float]:
    picked = np.random.default_rng(seed).choice(data.size, size=min(8, data.size),
                                                replace=False)
    sets, labels = data.batch(picked)
    _, grads = model.loss_grad(sets, labels, weight_decay)
    rng = np.random.default_rng(seed + 1)
    errors = {}
    for name, param in model.parameters().items():
        flat = param.reshape(-1)
        analytic = grads[name].reshape(-1)
        worst = 0.0
        for position in rng.choice(flat.size, size=min(samples, flat.size), replace=False):
            original = flat[position]
            flat[position] = original + eps
            plus, _ = model.loss_grad(sets, labels, weight_decay)
            flat[position] = original - eps
            minus, _ = model.loss_grad(sets, labels, weight_decay)
            flat[position] = original
            numeric = (plus - minus) / (2 * eps)
            worst = max(worst, abs(numeric - analytic[position])
                        / max(1.0, abs(numeric), abs(analytic[position])))
        errors[name] = worst
    return errors


def cross_validate_field(records: list[dict], name_index: dict[str, int], *,
                         folds: int = 5, epochs: int = 250, batch_size: int = 32,
                         lr: float = 0.05, weight_decay: float = 0.01,
                         seed: int = DEFAULT_SEED,
                         verbose: bool = True) -> list[float]:
    episodes = sorted({record['episode'] for record in records})
    order = episodes[:]
    random.Random(seed + 11).shuffle(order)
    buckets = [set() for _ in range(folds)]
    for position, episode in enumerate(order):
        buckets[position % folds].add(episode)
    results = []
    for number, held_out in enumerate(buckets, 1):
        train_records = [record for record in records if record['episode'] not in held_out]
        test_records = [record for record in records if record['episode'] in held_out]
        train_data = FieldData(train_records, name_index)
        test_data = FieldData(test_records, name_index)
        model = FieldModel(len(name_index), FIELD_DIM, seed=seed + number)
        train_field(model, train_data, test_data, epochs=epochs, batch_size=batch_size,
                    lr=lr, weight_decay=weight_decay, eval_every=epochs,
                    seed=seed + number, verbose=False)
        metrics = evaluate_field(model, test_data)
        results.append(metrics['accuracy'])
        if verbose:
            print(f'      第 {number} 折（{len(test_records)} 支队伍）：'
                  f'top-1 {metrics["accuracy"]:.1%}，平均重合 '
                  f'{metrics["mean_overlap"]:.2f}/3', flush=True)
    return results


# ---------------------------------------------------------------------------
# 4. 入口
# ---------------------------------------------------------------------------
def run(*, episodes: int = DEFAULT_EPISODES, epochs: int = 600, batch_size: int = 32,
        lr: float = 0.05, weight_decay: float = 0.003, eval_every: int = 10,
        seed: int = DEFAULT_SEED, output: Path = DEFAULT_OUTPUT, folds: int = 5,
        dataset: dict | None = None, verbose: bool = True) -> dict:
    started = time.perf_counter()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    names = sorted(lineup.CATALOG)
    name_index = {name: row for row, name in enumerate(names)}

    if verbose:
        print(f'[1/4] 生成 {episodes} 局选人数据并交给旧上阵规则 ...', flush=True)
    dataset = dataset or build_dataset(episodes=episodes, rounds=5, seed=seed)
    records, skipped = build_field_records(dataset)
    split = split_dataset(dataset)
    train_episodes = set(split['train_episodes'])
    train_records = [record for record in records if record['episode'] in train_episodes]
    test_records = [record for record in records if record['episode'] not in train_episodes]
    train_data = FieldData(train_records, name_index)
    test_data = FieldData(test_records, name_index)
    if verbose:
        print(f'      上阵样本 {len(records)} 支队伍（跳过 {skipped} 支：召唤位冲突），'
              f'训练 {len(train_data)} / 测试 {len(test_data)}', flush=True)

    if verbose:
        print(f'[2/4] 初始化 {FIELD_DIM} 维上阵向量并校验反向传播 ...', flush=True)
    model = FieldModel(len(names), FIELD_DIM, seed=seed)
    errors = gradient_check_field(model, train_data, weight_decay=weight_decay)
    worst = max(errors.values())
    if verbose:
        print('      数值梯度相对误差：' + '，'.join(
            f'{name} {error:.2e}' for name, error in errors.items()), flush=True)
    if worst > 1e-4:
        raise RuntimeError(f'上阵模型反向传播校验未通过：最大相对误差 {worst:.2e}')

    if verbose:
        print(f'[3/4] 梯度下降 {epochs} epoch（Adam, lr={lr}, batch={batch_size}, '
              f'λ={weight_decay}）...', flush=True)
    history = train_field(model, train_data, test_data, epochs=epochs,
                          batch_size=batch_size, lr=lr, weight_decay=weight_decay,
                          eval_every=eval_every, seed=seed, verbose=verbose)
    train_metrics = evaluate_field(model, train_data)
    test_metrics = evaluate_field(model, test_data)
    baseline = {
        'top_strength': _baseline_top_strength(test_data),
        'random': 0.1,
    }

    cross_validation = []
    if folds >= 2:
        if verbose:
            print(f'[3/4] {folds} 折交叉验证 ...', flush=True)
        cross_validation = cross_validate_field(
            records, name_index, folds=folds, epochs=epochs, batch_size=batch_size,
            lr=lr, weight_decay=weight_decay, seed=seed, verbose=verbose)

    if verbose:
        print('[4/4] 导出上阵策略与报告 ...', flush=True)
    policy = FieldPolicy(names, model.U, model.query, model.matrix, meta={
        'trained_at': datetime.now().isoformat(timespec='seconds'),
        'source': 'tasks.DraftDuel.strategy.plan_lineup(team_size=3)',
        'episodes': episodes,
        'teams': len(records),
        'train_teams': len(train_data),
        'test_teams': len(test_data),
        'test_accuracy': round(test_metrics['accuracy'], 4),
        'test_mean_overlap': round(test_metrics['mean_overlap'], 4),
        'cv_accuracy': (round(float(np.mean(cross_validation)), 4)
                        if cross_validation else None),
    })
    policy_path = policy.save(output / 'field16.json')

    write_csv(output / 'field_training_metrics.csv', history)
    series = [
        {'label': '训练集', 'color': '#1f6feb',
         'points': [(row['epoch'], row['train_loss']) for row in history]},
        {'label': '测试集', 'color': '#d1242f',
         'points': [(row['epoch'], row['test_loss']) for row in history]},
    ]
    write_curve_svg(output / 'field_loss_curve.svg', series,
                    title='上阵交叉熵损失', ylabel='loss')
    write_curve_png(output / 'field_loss_curve.png', series, title='上阵交叉熵损失')

    sample = test_records[0]['team'] if test_records else names[:5]
    latency = measure_latency_field(policy, sample)

    report = build_field_report(
        records=records, skipped=skipped, train_metrics=train_metrics,
        test_metrics=test_metrics, baseline=baseline, history=history,
        policy=policy, cross_validation=cross_validation, latency=latency,
        config={'epochs': epochs, 'batch_size': batch_size, 'lr': lr,
                'weight_decay': weight_decay, 'seed': seed},
        grad_errors=errors, elapsed=time.perf_counter() - started)
    (output / 'field_report.md').write_text(report, encoding='utf-8')

    summary = {
        'policy': str(policy_path),
        'teams': len(records),
        'test_accuracy': test_metrics['accuracy'],
        'test_mean_overlap': test_metrics['mean_overlap'],
        'cv_accuracy': (float(np.mean(cross_validation)) if cross_validation else None),
        'latency_ms': latency,
        'elapsed': time.perf_counter() - started,
    }
    if verbose:
        print(f'      策略：{policy_path}', flush=True)
        print(f'      上阵：训练 top-1 {train_metrics["accuracy"]:.1%} / '
              f'测试 top-1 {test_metrics["accuracy"]:.1%}（平均重合 '
              f'{test_metrics["mean_overlap"]:.2f}/3），单次 {latency:.3f} ms', flush=True)
        if cross_validation:
            print(f'      {folds} 折平均：{np.mean(cross_validation):.1%}'
                  f'（±{np.std(cross_validation):.1%}）', flush=True)
    return summary


def measure_latency_field(policy: FieldPolicy, team: list[str], repeats: int = 2000) -> float:
    policy.choose(team)
    started = time.perf_counter()
    for _ in range(repeats):
        policy.choose(team)
    return (time.perf_counter() - started) / repeats * 1000.0


def _baseline_top_strength(data: FieldData) -> float:
    """不看搭配、只按静态强度挑 3 张的基线。"""
    looks = data.rows
    hits = 0
    for record in looks:
        strengths = [lineup.STRENGTH_SCORES.get(name, {}).get('strength', 0) or 0
                     for name in record['team']]
        order = sorted(range(len(record['team'])), key=lambda index: -strengths[index])[:3]
        chosen = {record['team'][index] for index in order}
        hits += int(chosen == set(record['field']))
    return hits / max(1, len(looks))


def build_field_report(*, records: list[dict], skipped: int, train_metrics: dict,
                       test_metrics: dict, baseline: dict, history: list[dict],
                       policy: FieldPolicy, cross_validation: list[float],
                       latency: float, config: dict, grad_errors: dict,
                       elapsed: float) -> str:
    final = history[-1]
    values = policy.field_values(8)
    pairs = _top_field_pairs(policy)
    lines = [
        '# 协同对弈：上阵决策（5 选 3）的 16 维隐向量拟合',
        '',
        f"生成时间：{datetime.now().isoformat(timespec='seconds')}　|　总耗时：{elapsed:.1f}s",
        '',
        '## 1. 数据',
        '',
        f"- 旧规则：`lineup.plan_lineup(team, team_size=3)`（等级/百科/公式/六番阵容的 team_score）",
        f"- 队伍：{len(records)} 支（每支 5 张，来自旧选人策略的完整一局；跳过 {skipped} 支召唤位冲突）",
        f"- 划分：按局 4:1 → 训练 {train_metrics['n']} 支 / 测试 {test_metrics['n']} 支",
        f"- 每支队伍枚举 C(5,3)=10 种上阵组合作为候选",
        '',
        '## 2. 模型',
        '',
        '```text',
        'score(S) = Σ_{c∈S} b·u_c + Σ_{c<d∈S} u_cᵀ N u_d        (N 对称)',
        '```',
        '',
        f"- 上阵向量 U：{len(policy.names)} × {policy.dim} = {len(policy.names) * policy.dim} 个参数",
        f"- 单卡价值 b：{policy.dim} 个参数；般配矩阵 N：{policy.dim} × {policy.dim} 个参数",
        '- 损失：10 个组合上的 softmax 交叉熵 + L2；Adam + 手写解析反向传播。',
        f"- 反向传播数值校验（中心差分）："
        + '，'.join(f'{name} {error:.2e}' for name, error in grad_errors.items()),
        '',
        '## 3. 结果',
        '',
        _markdown_table(['指标', '训练集', '测试集'], [
            ['队伍数', train_metrics['n'], test_metrics['n']],
            ['top-1（整套 3 张完全一致）', f"{train_metrics['accuracy']:.1%}",
             f"{test_metrics['accuracy']:.1%}"],
            ['平均重合张数（满分 3）', f"{train_metrics['mean_overlap']:.2f}",
             f"{test_metrics['mean_overlap']:.2f}"],
            ['交叉熵', f"{train_metrics['nll']:.4f}", f"{test_metrics['nll']:.4f}"],
        ]),
        '',
        _markdown_table(['基线', 'top-1'], [
            ['随机抽一种组合', '10.0%'],
            ['只按静态强度挑 3 张', f"{baseline['top_strength']:.1%}"],
        ]),
        '',
        f"结束：train loss {final['train_loss']:.4f} / acc {final['train_accuracy']:.1%}，"
        f"test loss {final['test_loss']:.4f} / acc {final['test_accuracy']:.1%}",
        '',
        ((f"{len(cross_validation)} 折交叉验证：" +
          '，'.join(f'{value:.0%}' for value in cross_validation) +
          f"，平均 {np.mean(cross_validation):.1%}（±{np.std(cross_validation):.1%}）")
         if cross_validation else '本次未做交叉验证'),
        '',
        f"单次上阵推理（10 个组合）：{latency:.3f} ms，远低于 100 ms 的上限。",
        '',
        '## 4. 学到的结构',
        '',
        '单卡价值最高的式神（b·u）：',
        '',
        ', '.join(f'{name} {value:+.2f}' for name, value in values),
        '',
        '最般配的组合（u_cᵀ N u_d）：',
        '',
        ', '.join(f'{left}+{right} {value:+.2f}' for value, left, right in pairs) or '（无）',
        '',
        '## 5. 说明',
        '',
        '- 标签由旧上阵规则的决定性搜索产生，测试集衡量"能否复现旧规则"，不是实战胜率。',
        '- 上阵的旧规则本身以阵容评分为主，5 选 3 的状态空间只有 10 种，'
        '所以拟合难度比选人阶段低得多。',
        '- 同一套 16 维向量可以和选人阶段的向量共用名字表；如果后续要联合训练，'
        '可以把两边的损失相加。',
    ]
    return '\n'.join(lines) + '\n'


def _top_field_pairs(policy: FieldPolicy, limit: int = 8) -> list[tuple[float, str, str]]:
    scores = policy.vectors @ policy.matrix @ policy.vectors.T
    names = list(policy.names)
    ranked = []
    for row in range(len(names)):
        for column in range(row + 1, len(names)):
            value = min(scores[row, column], scores[column, row])
            if value > 0:
                ranked.append((float(value), names[row], names[column]))
    ranked.sort(reverse=True)
    return ranked[:limit]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='拟合协同对弈上阵（5 选 3）16 维隐向量策略')
    parser.add_argument('--episodes', type=int, default=DEFAULT_EPISODES,
                        help='采样局数，每局产生一支 5 人队（默认 1000）')
    parser.add_argument('--epochs', type=int, default=600)
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--lr', type=float, default=0.05)
    parser.add_argument('--weight-decay', type=float, default=0.003)
    parser.add_argument('--eval-every', type=int, default=10)
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED)
    parser.add_argument('--folds', type=int, default=5)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    run(episodes=args.episodes, epochs=args.epochs, batch_size=args.batch_size,
        lr=args.lr, weight_decay=args.weight_decay, eval_every=args.eval_every,
        seed=args.seed, output=args.output, folds=args.folds)
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
