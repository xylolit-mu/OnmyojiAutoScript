"""把协同对弈的旧规则选人策略蒸馏成 16 维隐向量策略。

流程
----
1. 用旧策略 ``lineup.choose_pick`` 生成 100 局 × 5 轮 × 3 选 1 的决策；
2. 按"局"做 4:1 划分（同一局的 5 次决策只落在训练或测试一侧，避免泄漏）；
3. 用梯度下降 + 反向传播（手写解析梯度、Adam）最小化名单内交叉熵：

       L = -log softmax( s(c|team) )[旧策略的选择] + λ/2·‖θ‖²
       s(c|team) = q·z_c + mean_{t∈team} z_c^T M z_t

   参数 θ = (Z, q, M)，Z 为 243 × 16 的式神隐向量；
4. 同一份数据再训练一个浅层网络头（同一套 Z、M，一层 32 宽隐藏层），
   对比"简单计算够不够"；两种头的单次推理耗时都会实测写进报告；
5. 评估、导出 ``latent16.json``（双线性，默认）、``latent16_mlp.json``（浅层网络）、
   指标 CSV/曲线和拟合报告。

用法
----
    python -m dev_tools.draft_duel.fit_latent16_policy
    python -m dev_tools.draft_duel.fit_latent16_policy --episodes 100 --epochs 250 --lr 0.05
    python -m dev_tools.draft_duel.fit_latent16_policy --skip-mlp
    python -m dev_tools.draft_duel.fit_latent16_policy --gradcheck
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tasks.DraftDuel.strategy import lineup  # noqa: E402
from tasks.DraftDuel.strategy.latent16 import LATENT_DIM, MAX_TEAM, LatentPolicy  # noqa: E402

DEFAULT_EPISODES = 100
DEFAULT_ROUNDS = 5
DEFAULT_OFFERS = 3
DEFAULT_SEED = 20261103
SPLIT_SEED = 20261104
TRAIN_RATIO = 0.8
DEFAULT_OUTPUT = REPO_ROOT / 'data' / 'DraftDuel' / 'latent16'


# ---------------------------------------------------------------------------
# 1. 用旧策略造数据
# ---------------------------------------------------------------------------
def build_dataset(*, episodes: int = DEFAULT_EPISODES, rounds: int = DEFAULT_ROUNDS,
                  offers: int = DEFAULT_OFFERS, seed: int = DEFAULT_SEED,
                  use_battle_stats: bool = False,
                  names: list[str] | None = None) -> dict:
    """跑旧策略生成决策记录：一局 = 连续 rounds 轮三选一。"""
    from tasks.DraftDuel.strategy import battle_stats

    pool_names = list(names or sorted(lineup.CATALOG))
    if len(pool_names) < rounds * offers:
        raise ValueError('式神池不足以支撑这么多轮次')
    rng = random.Random(seed)
    # 旧策略会读取真实实战统计；训练默认关掉，保证标签可复现（纯静态规则）。
    original_tally = battle_stats._tally
    if not use_battle_stats:
        battle_stats._tally = lambda key='ours': {}
    decisions: list[dict] = []
    skipped = 0
    try:
        for episode in range(episodes):
            pool = pool_names[:]
            rng.shuffle(pool)
            team: list[str] = []
            cursor = 0
            for round_number in range(1, rounds + 1):
                offered = pool[cursor:cursor + offers]
                cursor += offers
                try:
                    pick = lineup.choose_pick(offered, team)
                except ValueError:
                    skipped += 1
                    continue
                decisions.append({
                    'episode': episode,
                    'round': round_number,
                    'offers': list(offered),
                    'team': list(team),
                    'pick': pick.name,
                    'picked_index': offered.index(pick.name),
                    'old_score': round(float(pick.score), 4),
                    'reasons': list(pick.reasons)[:3],
                })
                team.append(pick.name)
    finally:
        battle_stats._tally = original_tally
    return {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'seed': seed,
        'episodes': episodes,
        'rounds': rounds,
        'offers': offers,
        'old_policy': 'tasks.DraftDuel.strategy.lineup.choose_pick',
        'include_battle_stats': use_battle_stats,
        'skipped': skipped,
        'decisions': decisions,
    }


def split_dataset(dataset: dict, *, ratio: float = TRAIN_RATIO,
                  seed: int = SPLIT_SEED) -> dict:
    """按局划分，返回 {'train': [...], 'test': [...], 以及局号列表}。"""
    episodes = sorted({row['episode'] for row in dataset['decisions']})
    order = episodes[:]
    random.Random(seed).shuffle(order)
    train_count = max(1, min(len(order) - 1, int(round(len(order) * ratio))))
    train_episodes = sorted(order[:train_count])
    test_episodes = sorted(order[train_count:])
    keep = set(train_episodes)
    return {
        'ratio': ratio,
        'seed': seed,
        'train_episodes': train_episodes,
        'test_episodes': test_episodes,
        'train': [row for row in dataset['decisions'] if row['episode'] in keep],
        'test': [row for row in dataset['decisions'] if row['episode'] not in keep],
    }


# ---------------------------------------------------------------------------
# 2. 编码成数组
# ---------------------------------------------------------------------------
class SplitData:
    """一条决策 = 3 个候选 + 至多 4 个已选式神 + 标签。"""

    def __init__(self, rows: list[dict], name_index: dict[str, int], team_size: int):
        self.size = len(rows)
        candidates = np.zeros((self.size, 3), dtype=np.int64)
        team = np.full((self.size, team_size), -1, dtype=np.int64)
        counts = np.zeros(self.size, dtype=np.int64)
        labels = np.zeros(self.size, dtype=np.int64)
        rounds = np.zeros(self.size, dtype=np.int64)
        for row_number, row in enumerate(rows):
            candidates[row_number] = [name_index[name] for name in row['offers']]
            members = [name_index[name] for name in row['team']]
            team[row_number, :len(members)] = members
            counts[row_number] = len(members)
            labels[row_number] = row['picked_index']
            rounds[row_number] = row['round']
        self.candidates = candidates
        self.team = team
        self.counts = counts
        self.labels = labels
        self.rounds = rounds
        self.rows = rows

    def __len__(self) -> int:
        return self.size


class EpisodeData:
    """一局 = 5 轮连续三选一；批量按局切，局内 5 次选择按顺序喂给模型。

    第 r 轮的输入里带着前 r-1 轮的真实选择（teacher forcing），所以"后面四次
    参考前面几次"不是隐含假设，而是数据本身的结构。奖励/损失按局归一化，
    不把 5 条决策当成 5 个独立样本。
    """

    def __init__(self, rows: list[dict], name_index: dict[str, int], rounds: int):
        grouped: dict[int, list[dict]] = {}
        for row in rows:
            grouped.setdefault(row['episode'], []).append(row)
        episodes = []
        for episode in sorted(grouped):
            ordered = sorted(grouped[episode], key=lambda row: row['round'])
            if len(ordered) == rounds:
                episodes.append(ordered)
        self.size = len(episodes)
        self.rounds = rounds
        self.rows = episodes
        offers = np.zeros((self.size, rounds, 3), dtype=np.int64)
        teams = np.full((self.size, rounds, rounds - 1), -1, dtype=np.int64)
        counts = np.zeros((self.size, rounds), dtype=np.int64)
        labels = np.zeros((self.size, rounds), dtype=np.int64)
        for episode_number, episode in enumerate(episodes):
            for step, row in enumerate(episode):
                offers[episode_number, step] = [name_index[name] for name in row['offers']]
                members = [name_index[name] for name in row['team']]
                teams[episode_number, step, :len(members)] = members
                counts[episode_number, step] = len(members)
                labels[episode_number, step] = row['picked_index']
        self.offers = offers
        self.teams = teams
        self.counts = counts
        self.labels = labels

    def __len__(self) -> int:
        return self.size

    def batch(self, indices):
        """把若干局摊平成 (候选, 队伍, 人数, 标签)，顺序保持不变。"""
        return (self.offers[indices].reshape(-1, 3),
                self.teams[indices].reshape(-1, self.teams.shape[2]),
                self.counts[indices].reshape(-1),
                self.labels[indices].reshape(-1))


# ---------------------------------------------------------------------------
# 3. 模型与解析梯度
# ---------------------------------------------------------------------------
class LatentModel:
    """score = q·z_c + mean_t(z_c^T M z_t)，带手写反向传播。"""

    PARAMS = ('Z', 'query', 'matrix')

    def __init__(self, size: int, dim: int = LATENT_DIM, *, seed: int = 0,
                 z_scale: float = 0.05, m_scale: float = 0.01):
        rng = np.random.default_rng(seed)
        self.Z = rng.normal(0.0, z_scale, (size, dim))
        self.query = rng.normal(0.0, 0.10, dim)
        self.matrix = rng.normal(0.0, m_scale, (dim, dim))

    # -- 前向 -----------------------------------------------------------
    def scores(self, candidates, team, counts) -> np.ndarray:
        cand = self.Z[candidates]                              # (B,k,d)
        scores = cand @ self.query                             # (B,k)
        valid = counts > 0
        if valid.any():
            keep = team >= 0
            team_vectors = self.Z[np.where(keep, team, 0)] * keep[..., None]
            pair = np.einsum('bkd,bsd->bks', cand @ self.matrix, team_vectors)
            scores = scores + pair.sum(axis=2) / np.maximum(counts, 1)[:, None] * valid[:, None]
        return scores

    # -- 损失与梯度 -----------------------------------------------------
    def loss_grad(self, candidates, team, counts, labels,
                  weight_decay: float | dict[str, float] = 0.0
                  ) -> tuple[float, dict[str, np.ndarray]]:
        if isinstance(weight_decay, dict):
            decay = {name: float(weight_decay.get(name, 0.0)) for name in self.PARAMS}
        else:
            decay = dict.fromkeys(self.PARAMS, float(weight_decay))
        batch = candidates.shape[0]
        cand = self.Z[candidates]                              # (B,k,d)
        self_score = cand @ self.query                         # (B,k)
        valid = counts > 0
        keep = team >= 0
        team_vectors = self.Z[np.where(keep, team, 0)] * keep[..., None]   # (B,S,d)
        inv_counts = 1.0 / np.maximum(counts, 1)
        pair = np.zeros_like(self_score)
        if valid.any():
            pair = (np.einsum('bkd,bsd->bks', cand @ self.matrix, team_vectors)
                    .sum(axis=2) * inv_counts[:, None] * valid[:, None])
        scores = self_score + pair

        # softmax 交叉熵
        shifted = scores - scores.max(axis=1, keepdims=True)
        exp = np.exp(shifted)
        denominator = exp.sum(axis=1, keepdims=True)
        probabilities = exp / denominator
        rows = np.arange(batch)
        loss = -np.mean(scores[rows, labels] - scores.max(axis=1)
                        - np.log(denominator[:, 0]))
        d_scores = probabilities.copy()
        d_scores[rows, labels] -= 1.0
        d_scores /= batch

        # 反向传播
        d_query = np.einsum('bk,bkd->d', d_scores, cand)
        weighted = np.einsum('bk,bkd->bd', d_scores, cand)      # u_b = Σ_i δ_bi z_ci
        team_sum = team_vectors.sum(axis=1)                     # v_b = Σ_j z_tj
        d_matrix = (weighted * inv_counts[:, None]).T @ team_sum
        d_cand = d_scores[..., None] * (
            self.query + inv_counts[:, None, None] * (team_sum @ self.matrix.T)[:, None, :])
        d_team = (inv_counts[:, None] * (weighted @ self.matrix))[:, None, :] * keep[..., None]

        d_Z = np.zeros_like(self.Z)
        np.add.at(d_Z, candidates, d_cand)
        np.add.at(d_Z, np.where(keep, team, 0), d_team)

        if any(decay.values()):
            loss = float(loss) + 0.5 * (
                decay['Z'] * float(np.sum(self.Z ** 2))
                + decay['query'] * float(np.sum(self.query ** 2))
                + decay['matrix'] * float(np.sum(self.matrix ** 2)))
            d_Z = d_Z + decay['Z'] * self.Z
            d_query = d_query + decay['query'] * self.query
            d_matrix = d_matrix + decay['matrix'] * self.matrix
        return float(loss), {'Z': d_Z, 'query': d_query, 'matrix': d_matrix}

    def parameters(self) -> dict[str, np.ndarray]:
        return {name: getattr(self, name) for name in self.PARAMS}


class Adam:
    """标准 Adam；梯度里已含 L2 正则项。"""

    def __init__(self, parameters: dict[str, np.ndarray], *, lr: float = 0.05,
                 beta1: float = 0.9, beta2: float = 0.999, eps: float = 1e-8):
        self.lr = lr
        self.beta1 = beta1
        self.beta2 = beta2
        self.eps = eps
        self.step_count = 0
        self.first = {name: np.zeros_like(value) for name, value in parameters.items()}
        self.second = {name: np.zeros_like(value) for name, value in parameters.items()}

    def step(self, parameters: dict[str, np.ndarray],
             grads: dict[str, np.ndarray]) -> None:
        self.step_count += 1
        correction1 = 1.0 - self.beta1 ** self.step_count
        correction2 = 1.0 - self.beta2 ** self.step_count
        for name, param in parameters.items():
            grad = grads[name]
            self.first[name] = self.beta1 * self.first[name] + (1 - self.beta1) * grad
            self.second[name] = self.beta2 * self.second[name] + (1 - self.beta2) * grad * grad
            update = (self.lr * (self.first[name] / correction1)
                      / (np.sqrt(self.second[name] / correction2) + self.eps))
            param -= update


# ---------------------------------------------------------------------------
# 4. 浅层网络备选头（同一套隐向量，一层隐藏层）
# ---------------------------------------------------------------------------
class ShallowNet:
    """score = MLP([z_c, 队伍均值, z_c⊙队伍均值, 平均般配分, 人数])。

    和 LatentModel 共用 Z 与 M，只是把打分头从"线性 + 双线性"换成一层 32 宽的
    隐藏层，用来验证"简单计算够不够"。前向与反向都手写，梯度同样做中心差分校验。
    """

    PARAMS = ('Z', 'matrix', 'W1', 'b1', 'W2', 'b2')

    def __init__(self, size: int, dim: int = LATENT_DIM, hidden: int = 32, *, seed: int = 0,
                 z_scale: float = 0.05, m_scale: float = 0.01):
        rng = np.random.default_rng(seed)
        self.dim = dim
        self.hidden = hidden
        features = 3 * dim + 2
        self.Z = rng.normal(0.0, z_scale, (size, dim))
        self.matrix = rng.normal(0.0, m_scale, (dim, dim))
        self.W1 = rng.normal(0.0, math.sqrt(2.0 / features), (features, hidden))
        self.b1 = np.zeros(hidden)
        self.W2 = rng.normal(0.0, math.sqrt(2.0 / hidden), hidden)
        self.b2 = np.zeros(1)

    def _features(self, candidates, team, counts):
        batch, offers = candidates.shape
        dim = self.dim
        cand = self.Z[candidates]                                  # (B,k,d)
        keep = team >= 0
        members = self.Z[np.where(keep, team, 0)] * keep[..., None]  # (B,S,d)
        denominator = np.maximum(counts, 1).astype(np.float64)
        member_sum = members.sum(axis=1)                           # (B,d)
        mean = member_sum / denominator[:, None]
        pair = (np.einsum('bkd,bsd->bks', cand @ self.matrix, members).sum(axis=2)
                / denominator[:, None]) * (counts > 0)[:, None]
        count = (counts / float(MAX_TEAM))[:, None]
        features = np.concatenate([
            cand,
            np.broadcast_to(mean[:, None, :], (batch, offers, dim)),
            cand * mean[:, None, :],
            pair[..., None],
            np.broadcast_to(count[:, None, :], (batch, offers, 1)),
        ], axis=2)
        return features, {'cand': cand, 'member_sum': member_sum, 'mean': mean,
                          'members': members, 'keep': keep, 'denominator': denominator}

    def _head(self, features):
        flat = features.reshape(-1, features.shape[-1])
        pre = flat @ self.W1 + self.b1
        activated = np.maximum(pre, 0.0)
        out = activated @ self.W2 + float(self.b2[0])
        return out.reshape(features.shape[0], features.shape[1]), (flat, pre, activated)

    def scores(self, candidates, team, counts):
        features, _ = self._features(candidates, team, counts)
        return self._head(features)[0]

    def parameters(self) -> dict[str, np.ndarray]:
        return {name: getattr(self, name) for name in self.PARAMS}

    def loss_grad(self, candidates, team, counts, labels,
                  weight_decay: float | dict[str, float] = 0.0
                  ) -> tuple[float, dict[str, np.ndarray]]:
        if isinstance(weight_decay, dict):
            decay = {name: float(weight_decay.get(name, 0.0)) for name in self.PARAMS}
        else:
            decay = dict.fromkeys(self.PARAMS, float(weight_decay))
        batch, offers = candidates.shape
        dim = self.dim
        features, cache = self._features(candidates, team, counts)
        scores, (flat, pre, activated) = self._head(features)
        shifted = scores - scores.max(axis=1, keepdims=True)
        exp = np.exp(shifted)
        denominator = exp.sum(axis=1, keepdims=True)
        rows = np.arange(batch)
        loss = -np.mean(shifted[rows, labels] - np.log(denominator[:, 0]))
        d_scores = exp / denominator
        d_scores[rows, labels] -= 1.0
        d_scores /= batch
        d_flat = d_scores.reshape(-1)

        d_W2 = activated.T @ d_flat
        d_b2 = np.array([d_flat.sum()])
        d_pre = (d_flat[:, None] * self.W2[None, :]) * (pre > 0)
        d_W1 = flat.T @ d_pre
        d_b1 = d_pre.sum(axis=0)
        d_features = (d_pre @ self.W1.T).reshape(features.shape)

        cand = cache['cand']
        denominator = cache['denominator']
        inverse = 1.0 / denominator
        keep = cache['keep']
        d_pair = d_features[..., 3 * dim].copy()
        d_cand = d_features[..., :dim].copy()
        d_mean = d_features[..., dim:2 * dim].sum(axis=1)
        d_product = d_features[..., 2 * dim:3 * dim]
        d_cand += d_product * cache['mean'][:, None, :]
        d_mean += (d_product * cand).sum(axis=1)

        d_pair_scaled = d_pair * inverse[:, None]
        d_cand += d_pair_scaled[..., None] * (cache['member_sum'] @ self.matrix.T)[:, None, :]
        weighted = np.einsum('bk,bkd->bd', d_pair, cand)
        d_members = keep[..., None] * ((d_mean + weighted @ self.matrix) * inverse[:, None])[:, None, :]
        d_matrix = np.einsum('bk,bkd,be->de', d_pair_scaled, cand, cache['member_sum'])

        d_Z = np.zeros_like(self.Z)
        np.add.at(d_Z, candidates, d_cand)
        np.add.at(d_Z, np.where(keep, team, 0), d_members)

        grads = {'Z': d_Z, 'matrix': d_matrix, 'W1': d_W1, 'b1': d_b1,
                 'W2': d_W2, 'b2': d_b2}
        if any(decay.values()):
            for name in self.PARAMS:
                if decay[name]:
                    param = getattr(self, name)
                    loss += 0.5 * decay[name] * float(np.sum(param ** 2))
                    grads[name] = grads[name] + decay[name] * param
        return float(loss), grads


# ---------------------------------------------------------------------------
# 4b. 角色计数特征 + 浅层网络头
# ---------------------------------------------------------------------------
ROLE_NAMES = ('fire', 'sustain', 'shield', 'control', 'damage', 'tempo', 'support',
              'core_fire', 'summon')


def role_codes(names: list[str]) -> np.ndarray:
    """每张卡的角色多热：7 个角色位 + 真火 + 召唤位。"""
    codes = np.zeros((len(names), len(ROLE_NAMES)))
    for row, name in enumerate(names):
        tags = lineup.role_tags(lineup.CATALOG[name])
        for column, role in enumerate(ROLE_NAMES):
            if role in tags:
                codes[row, column] = 1.0
        if name in lineup.CORE_FIRE:
            codes[row, ROLE_NAMES.index('core_fire')] = 1.0
        if name in lineup.SUMMON_SLOT_USERS:
            codes[row, ROLE_NAMES.index('summon')] = 1.0
    return codes


class RoleNet:
    """score = MLP([z_c, 队伍均值, z_c⊙均值, 般配分, 人数, 候选角色, 队伍角色计数])。

    参与两两打分的仍是 16 维隐向量；额外的角色多热/计数是静态特征，专门补
    "缺火/缺输出/保护位只能一个"这类双线性表达不了的阈值规则。
    """

    PARAMS = ('Z', 'matrix', 'W1', 'b1', 'W2', 'b2')

    def __init__(self, size: int, dim: int = LATENT_DIM, roles: np.ndarray | None = None,
                 hidden: int = 48, *, seed: int = 0, z_scale: float = 0.05,
                 m_scale: float = 0.01):
        rng = np.random.default_rng(seed)
        self.dim = dim
        self.hidden = hidden
        self.roles = (np.zeros((size, len(ROLE_NAMES))) if roles is None
                      else np.array(roles, dtype=np.float64))
        features = 3 * dim + 2 + 2 * self.roles.shape[1]
        self.Z = rng.normal(0.0, z_scale, (size, dim))
        self.matrix = rng.normal(0.0, m_scale, (dim, dim))
        self.W1 = rng.normal(0.0, math.sqrt(2.0 / features), (features, hidden))
        self.b1 = np.zeros(hidden)
        self.W2 = rng.normal(0.0, math.sqrt(2.0 / hidden), hidden)
        self.b2 = np.zeros(1)

    @property
    def role_dim(self) -> int:
        return self.roles.shape[1]

    def _features(self, candidates, team, counts):
        batch, offers = candidates.shape
        dim = self.dim
        cand = self.Z[candidates]
        keep = team >= 0
        members = self.Z[np.where(keep, team, 0)] * keep[..., None]
        denominator = np.maximum(counts, 1).astype(np.float64)
        member_sum = members.sum(axis=1)
        mean = member_sum / denominator[:, None]
        pair = (np.einsum('bkd,bsd->bks', cand @ self.matrix, members).sum(axis=2)
                / denominator[:, None]) * (counts > 0)[:, None]
        count = (counts / float(MAX_TEAM))[:, None]
        role_c = self.roles[candidates]
        team_roles = self.roles[np.where(keep, team, 0)] * keep[..., None]
        team_role_counts = team_roles.sum(axis=1) / float(MAX_TEAM)
        features = np.concatenate([
            cand,
            np.broadcast_to(mean[:, None, :], (batch, offers, dim)),
            cand * mean[:, None, :],
            pair[..., None],
            np.broadcast_to(count[:, None, :], (batch, offers, 1)),
            role_c,
            np.broadcast_to(team_role_counts[:, None, :], (batch, offers, self.role_dim)),
        ], axis=2)
        return features, {'cand': cand, 'member_sum': member_sum, 'mean': mean,
                          'members': members, 'keep': keep, 'denominator': denominator}

    def _head(self, features):
        flat = features.reshape(-1, features.shape[-1])
        pre = flat @ self.W1 + self.b1
        activated = np.maximum(pre, 0.0)
        out = activated @ self.W2 + float(self.b2[0])
        return out.reshape(features.shape[0], features.shape[1]), (flat, pre, activated)

    def scores(self, candidates, team, counts):
        features, _ = self._features(candidates, team, counts)
        return self._head(features)[0]

    def parameters(self) -> dict[str, np.ndarray]:
        return {name: getattr(self, name) for name in self.PARAMS}

    def loss_grad(self, candidates, team, counts, labels,
                  weight_decay: float | dict[str, float] = 0.0,
                  weights: np.ndarray | None = None
                  ) -> tuple[float, dict[str, np.ndarray]]:
        if isinstance(weight_decay, dict):
            decay = {name: float(weight_decay.get(name, 0.0)) for name in self.PARAMS}
        else:
            decay = dict.fromkeys(self.PARAMS, float(weight_decay))
        batch, offers = candidates.shape
        dim = self.dim
        features, cache = self._features(candidates, team, counts)
        scores, (flat, pre, activated) = self._head(features)
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
        d_flat = d_scores.reshape(-1)

        d_W2 = activated.T @ d_flat
        d_b2 = np.array([d_flat.sum()])
        d_pre = (d_flat[:, None] * self.W2[None, :]) * (pre > 0)
        d_W1 = flat.T @ d_pre
        d_b1 = d_pre.sum(axis=0)
        d_features = (d_pre @ self.W1.T).reshape(features.shape)

        cand = cache['cand']
        denominator_b = cache['denominator']
        inverse = 1.0 / denominator_b
        keep = cache['keep']
        d_pair = d_features[..., 3 * dim].copy()
        d_cand = d_features[..., :dim].copy()
        d_mean = d_features[..., dim:2 * dim].sum(axis=1)
        d_product = d_features[..., 2 * dim:3 * dim]
        d_cand += d_product * cache['mean'][:, None, :]
        d_mean += (d_product * cand).sum(axis=1)
        # 角色特征块（3*dim+2 起）是静态常量，不产生梯度

        d_pair_scaled = d_pair * inverse[:, None]
        d_cand += d_pair_scaled[..., None] * (cache['member_sum'] @ self.matrix.T)[:, None, :]
        weighted = np.einsum('bk,bkd->bd', d_pair, cand)
        d_members = keep[..., None] * ((d_mean + weighted @ self.matrix)
                                       * inverse[:, None])[:, None, :]
        d_matrix = np.einsum('bk,bkd,be->de', d_pair_scaled, cand, cache['member_sum'])

        d_Z = np.zeros_like(self.Z)
        np.add.at(d_Z, candidates, d_cand)
        np.add.at(d_Z, np.where(keep, team, 0), d_members)

        grads = {'Z': d_Z, 'matrix': d_matrix, 'W1': d_W1, 'b1': d_b1,
                 'W2': d_W2, 'b2': d_b2}
        if any(decay.values()):
            for name in self.PARAMS:
                if decay[name]:
                    param = getattr(self, name)
                    loss += 0.5 * decay[name] * float(np.sum(param ** 2))
                    grads[name] = grads[name] + decay[name] * param
        return float(loss), grads


# ---------------------------------------------------------------------------
# 5. 评估
# ---------------------------------------------------------------------------
ScoringModel = LatentModel | ShallowNet


def _nll(scores: np.ndarray, labels: np.ndarray) -> float:
    shifted = scores - scores.max(axis=1, keepdims=True)
    log_normalizer = np.log(np.exp(shifted).sum(axis=1))
    picked = shifted[np.arange(len(labels)), labels]
    return float(np.mean(log_normalizer - picked))


def _mean_rank(scores: np.ndarray, labels: np.ndarray) -> float:
    target = scores[np.arange(len(labels)), labels][:, None]
    greater = (scores > target).sum(axis=1)
    equal = (scores == target).sum(axis=1) - 1
    return float(np.mean(1 + greater + equal / 2.0))


def evaluate(model: 'ScoringModel', data: SplitData) -> dict:
    scores = model.scores(data.candidates, data.team, data.counts)
    prediction = scores.argmax(axis=1)
    metrics = {
        'n': len(data),
        'accuracy': float(np.mean(prediction == data.labels)),
        'nll': _nll(scores, data.labels),
        'mean_rank': _mean_rank(scores, data.labels),
        'per_round': {},
    }
    for round_number in sorted(set(data.rounds.tolist())):
        mask = data.rounds == round_number
        metrics['per_round'][int(round_number)] = {
            'n': int(mask.sum()),
            'accuracy': float(np.mean(prediction[mask] == data.labels[mask])),
        }
    return metrics


def evaluate_episodes(model: 'ScoringModel', data: EpisodeData) -> dict:
    """按局评估：局内 5 次条件选择的准确率 + 整局完全一致率。"""
    candidates, team, counts, labels = data.batch(np.arange(data.size))
    scores = model.scores(candidates, team, counts).reshape(data.size, data.rounds, 3)
    truth = labels.reshape(data.size, data.rounds)
    matched = scores.argmax(axis=2) == truth
    return {
        'episodes': data.size,
        'n': int(matched.size),
        'accuracy': float(matched.mean()),
        'nll': _nll(scores.reshape(-1, 3), truth.reshape(-1)),
        'mean_rank': _mean_rank(scores.reshape(-1, 3), truth.reshape(-1)),
        'episode_exact': float(matched.all(axis=1).mean()),
        'mean_matched': float(matched.sum(axis=1).mean()),
        'per_round': {step: {'n': data.size, 'accuracy': float(matched[:, step - 1].mean())}
                      for step in range(1, data.rounds + 1)},
    }


def rollout_metrics(policy: LatentPolicy, data: EpisodeData) -> dict:
    """让模型自己下完整局：每轮只看它自己前几轮选出的式神，衡量误差累积。"""
    exact = 0
    matched = 0
    per_round = {step: [0, 0] for step in range(1, data.rounds + 1)}
    for episode in data.rows:
        team: list[str] = []
        hits = 0
        for step, row in enumerate(episode, 1):
            pick = policy.choose(row['offers'], team)
            hit = int(pick.name == row['pick'])
            hits += hit
            matched += hit
            per_round[step][0] += hit
            per_round[step][1] += 1
            team.append(pick.name)
        exact += int(hits == data.rounds)
    return {
        'episodes': data.size,
        'episode_exact': exact / max(1, data.size),
        'accuracy': matched / max(1, data.size * data.rounds),
        'mean_matched': matched / max(1, data.size),
        'per_round': {step: {'n': count, 'accuracy': hit / max(1, count)}
                      for step, (hit, count) in per_round.items()},
    }


def static_strength(name: str) -> float:
    """旧策略资料里的"自身强度"：优先评分表 strength，其次等级分。"""
    row = lineup.STRENGTH_SCORES.get(name) or {}
    value = row.get('strength')
    if isinstance(value, (int, float)):
        return float(value)
    entry = lineup.CATALOG.get(name)
    if entry is None:
        return 0.0
    return float(lineup.TIER_POINTS.get(entry.tier, 0))


def baseline_accuracy(rows: list[dict]) -> float:
    """不看看阵容、只按静态自身强度选人的基线。"""
    hits = 0
    for row in rows:
        strengths = [static_strength(name) for name in row['offers']]
        hits += int(int(np.argmax(strengths)) == row['picked_index'])
    return hits / max(1, len(rows))


# ---------------------------------------------------------------------------
# 6. 训练
# ---------------------------------------------------------------------------
def gradient_check(model: 'ScoringModel', data: SplitData, *, seed: int = 0,
                   samples: int = 4, eps: float = 1e-6,
                   weight_decay: float = 0.0) -> dict[str, float]:
    """数值梯度校验：中心差分 vs 解析梯度。"""
    if isinstance(data, EpisodeData):
        picked = np.random.default_rng(seed).choice(
            data.size, size=min(4, data.size), replace=False)
        candidates, team, counts, labels = data.batch(picked)
    else:
        picked = np.random.default_rng(seed).choice(
            len(data), size=min(8, len(data)), replace=False)
        candidates, team = data.candidates[picked], data.team[picked]
        counts, labels = data.counts[picked], data.labels[picked]
    _, grads = model.loss_grad(candidates, team, counts, labels, weight_decay)
    rng = np.random.default_rng(seed + 1)
    errors = {}
    for name, param in model.parameters().items():
        flat = param.reshape(-1)
        analytic = grads[name].reshape(-1)
        worst = 0.0
        for position in rng.choice(flat.size, size=min(samples, flat.size), replace=False):
            original = flat[position]
            flat[position] = original + eps
            plus, _ = model.loss_grad(candidates, team, counts, labels, weight_decay)
            flat[position] = original - eps
            minus, _ = model.loss_grad(candidates, team, counts, labels, weight_decay)
            flat[position] = original
            numeric = (plus - minus) / (2 * eps)
            worst = max(worst, abs(numeric - analytic[position])
                        / max(1.0, abs(numeric), abs(analytic[position])))
        errors[name] = worst
    return errors


def train(model: 'ScoringModel', train_data: SplitData, test_data: SplitData, *,
          epochs: int = 250, batch_size: int = 32, lr: float = 0.05,
          weight_decay: float | dict[str, float] = 0.01, eval_every: int = 5,
          seed: int = 0, clip_norm: float = 5.0, verbose: bool = True) -> list[dict]:
    parameters = model.parameters()
    optimizer = Adam(parameters, lr=lr)
    rng = np.random.default_rng(seed)
    history: list[dict] = []

    def snapshot(epoch: int) -> None:
        train_metrics = evaluate(model, train_data)
        test_metrics = evaluate(model, test_data)
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
    indices = np.arange(len(train_data))
    for epoch in range(1, epochs + 1):
        rng.shuffle(indices)
        for start in range(0, len(indices), batch_size):
            batch = indices[start:start + batch_size]
            loss, grads = model.loss_grad(
                train_data.candidates[batch], train_data.team[batch],
                train_data.counts[batch], train_data.labels[batch], weight_decay)
            norm = math.sqrt(sum(float(np.sum(grad * grad)) for grad in grads.values()))
            if norm > clip_norm:
                grads = {name: grad * (clip_norm / norm) for name, grad in grads.items()}
            optimizer.step(parameters, grads)
        if epoch % max(eval_every, 1) == 0 or epoch == epochs:
            snapshot(epoch)
    return history


def train_episodes(model: 'ScoringModel', train_data: EpisodeData, test_data: EpisodeData, *,
                   epochs: int = 250, batch_size: int = 8, lr: float = 0.05,
                   weight_decay: float | dict[str, float] = 0.01, eval_every: int = 5,
                   seed: int = 0, clip_norm: float = 5.0,
                   freeze: tuple[str, ...] = (),
                   verbose: bool = True) -> list[dict]:
    """按局训练：一个批量就是若干整局，局内 5 次条件选择一起出梯度。

    损失仍按"每次决策"归一（局内 5 次取平均），这样学习率与逐决策训练可直接比较；
    区别在于同一局 5 次选择不会被拆到不同批量里，后 4 轮始终带着前几轮的真实选择。
    """
    parameters = model.parameters()
    optimizer = Adam(parameters, lr=lr)
    rng = np.random.default_rng(seed)
    history: list[dict] = []

    def snapshot(epoch: int) -> None:
        train_metrics = evaluate_episodes(model, train_data)
        test_metrics = evaluate_episodes(model, test_data)
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
            candidates, team, counts, labels = train_data.batch(batch)
            loss, grads = model.loss_grad(candidates, team, counts, labels, weight_decay)
            for name in freeze:
                if name in grads:
                    grads[name] = np.zeros_like(grads[name])
            norm = math.sqrt(sum(float(np.sum(grad * grad)) for grad in grads.values()))
            if norm > clip_norm:
                grads = {name: grad * (clip_norm / norm) for name, grad in grads.items()}
            optimizer.step(parameters, grads)
        if epoch % max(eval_every, 1) == 0 or epoch == epochs:
            snapshot(epoch)
    return history


def train_two_stage(model: 'ScoringModel', train_data: EpisodeData,
                    test_data: EpisodeData, *, epochs: int = 250, batch_size: int = 8,
                    lr: float = 0.05, weight_decay: float = 0.01,
                    stage2_lr: float = 0.02, stage1_weight_decay: float = 0.001,
                    eval_every: int = 5, seed: int = 0,
                    verbose: bool = True) -> list[dict]:
    """两阶段训练：先冻结 q 只学"般配"，再恢复自身强度联合微调。

    直接联合训练时 q·z 的梯度远大于交互项，M 会被 L2 一路压到 0，模型退化成
    "只看单卡强度、完全无视阵容"。先令 q=0、冻结它，让交互项把"看阵容"的部分
    学出来（此时模型必须靠般配分区分候选），再把 q 恢复成小随机值联合微调。
    这样后面几轮才真的会参考前几轮选出的阵容。
    """
    model.query[:] = 0.0
    if verbose:
        print('  阶段 1：q=0，只学般配项（专门学"看阵容"）...', flush=True)
    first = train_episodes(model, train_data, test_data, epochs=epochs,
                           batch_size=batch_size, lr=lr,
                           weight_decay=stage1_weight_decay, eval_every=eval_every,
                           seed=seed, freeze=('query',), verbose=verbose)
    rng = np.random.default_rng(seed + 1)
    model.query[:] = rng.normal(0.0, 0.1, model.query.shape[0])
    if verbose:
        print(f'  阶段 2：恢复自身强度，联合微调（lr={stage2_lr}）...', flush=True)
    second = train_episodes(model, train_data, test_data, epochs=epochs,
                            batch_size=batch_size, lr=stage2_lr,
                            weight_decay=weight_decay, eval_every=eval_every,
                            seed=seed + 1, verbose=verbose)
    history = [dict(row) for row in first]
    for row in second[1:]:
        history.append({**row, 'epoch': row['epoch'] + epochs})
    return history


# ---------------------------------------------------------------------------
# 7. 导出：JSON / CSV / SVG / 报告
# ---------------------------------------------------------------------------
def split_episodes_into_folds(episodes: list[int], folds: int, seed: int) -> list[set[int]]:
    """按局轮转分折，保证每折局数相差不超过 1。"""
    order = sorted(set(episodes))
    random.Random(seed).shuffle(order)
    buckets = [set() for _ in range(folds)]
    for position, episode in enumerate(order):
        buckets[position % folds].add(episode)
    return buckets


def cross_validate(dataset: dict, names: list[str], name_index: dict[str, int], *,
                   folds: int = 5, rounds: int = DEFAULT_ROUNDS, epochs: int = 250,
                   batch_size: int = 32, lr: float = 0.05, weight_decay: float = 0.01,
                   seed: int = DEFAULT_SEED, train_mlp: bool = True, mlp_hidden: int = 32,
                   mlp_lr: float = 0.02, mlp_weight_decay: float = 0.03,
                   mlp_epochs: int | None = None, two_stage: bool = True,
                   verbose: bool = True) -> dict[str, list[float]]:
    """k 折交叉验证：每折仍是 4:1，用来估计"换一批抽样会怎样"。"""
    buckets = split_episodes_into_folds([row['episode'] for row in dataset['decisions']],
                                        folds, seed + 7)
    results: dict[str, list[float]] = {'bilinear': []}
    if train_mlp:
        results['mlp'] = []
    for number, held_out in enumerate(buckets, 1):
        train_rows = [row for row in dataset['decisions'] if row['episode'] not in held_out]
        test_rows = [row for row in dataset['decisions'] if row['episode'] in held_out]
        train_data = EpisodeData(train_rows, name_index, rounds)
        test_data = EpisodeData(test_rows, name_index, rounds)
        episodes_per_batch = max(1, round(batch_size / max(1, rounds)))
        model = LatentModel(len(names), LATENT_DIM, seed=seed + number,
                            m_scale=0.1 if two_stage else 0.01)
        trainer = train_two_stage if two_stage else train_episodes
        trainer(model, train_data, test_data, epochs=epochs,
                batch_size=episodes_per_batch, lr=lr, weight_decay=weight_decay,
                eval_every=epochs, seed=seed + number, verbose=False)
        results['bilinear'].append(evaluate_episodes(model, test_data)['accuracy'])
        if train_mlp:
            net = ShallowNet(len(names), LATENT_DIM, hidden=mlp_hidden, seed=seed + number)
            train_episodes(net, train_data, test_data, epochs=mlp_epochs or epochs,
                           batch_size=episodes_per_batch, lr=mlp_lr,
                           weight_decay=mlp_weight_decay,
                           eval_every=mlp_epochs or epochs, seed=seed + number, verbose=False)
            results['mlp'].append(evaluate_episodes(net, test_data)['accuracy'])
        if verbose:
            print(f'      第 {number} 折（测试 {len(test_rows)} 条）：'
                  + '，'.join(f'{head} {values[-1]:.3f}' for head, values in results.items()),
                  flush=True)
    return results


def measure_latency(policy: LatentPolicy, candidates: list[str], team: list[str],
                    repeats: int = 3000) -> float:
    """实测一次"三选一"前向计算的平均耗时（毫秒）。"""
    policy.choose(candidates, team)
    started = time.perf_counter()
    for _ in range(repeats):
        policy.choose(candidates, team)
    return (time.perf_counter() - started) / repeats * 1000.0


def write_csv(path: Path, history: list[dict]) -> None:
    columns = ('epoch', 'train_loss', 'train_accuracy', 'test_loss', 'test_accuracy')
    lines = [','.join(columns)]
    for row in history:
        lines.append(','.join(str(row[column]) for column in columns))
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _chart_geometry(series: list[dict], width: int, height: int) -> dict:
    left, right, top, bottom = 68, width - 20, 46, height - 42
    xs = [x for item in series for x, _ in item['points']]
    ys = [y for item in series for _, y in item['points']]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    if x_max - x_min < 1e-9:
        x_max = x_min + 1
    if y_max - y_min < 1e-9:
        y_max = y_min + 1
    pad = (y_max - y_min) * 0.08
    y_min, y_max = y_min - pad, y_max + pad

    def px(x: float) -> float:
        return left + (x - x_min) / (x_max - x_min) * (right - left)

    def py(y: float) -> float:
        return bottom - (y - y_min) / (y_max - y_min) * (bottom - top)

    return {'px': px, 'py': py, 'x': (x_min, x_max), 'y': (y_min, y_max),
            'box': (left, right, top, bottom)}


def write_curve_svg(path: Path, series: list[dict], *, title: str, ylabel: str,
                    width: int = 760, height: int = 320) -> None:
    """不依赖绘图库的折线图。"""
    geometry = _chart_geometry(series, width, height)
    px, py = geometry['px'], geometry['py']
    left, right, top, bottom = geometry['box']
    x_min, x_max = geometry['x']
    y_min, y_max = geometry['y']
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="Segoe UI, Microsoft YaHei, sans-serif">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{left}" y="28" font-size="16" fill="#1f2328">{title}</text>',
        f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="#8c959f"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" stroke="#8c959f"/>',
    ]
    for tick in range(5):
        value = y_min + (y_max - y_min) * tick / 4
        y = py(value)
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" '
                     f'stroke="#d8dee4" stroke-dasharray="3 3"/>')
        parts.append(f'<text x="{left - 8}" y="{y + 4:.1f}" font-size="11" fill="#57606a" '
                     f'text-anchor="end">{value:.2f}</text>')
    for tick in range(5):
        value = x_min + (x_max - x_min) * tick / 4
        parts.append(f'<text x="{px(value):.1f}" y="{bottom + 18}" font-size="11" '
                     f'fill="#57606a" text-anchor="middle">{value:.0f}</text>')
    parts.append(f'<text x="{(left + right) / 2:.0f}" y="{height - 8}" font-size="11" '
                 f'fill="#57606a" text-anchor="middle">epoch</text>')
    parts.append(f'<text x="16" y="{(top + bottom) / 2:.0f}" font-size="11" fill="#57606a" '
                 f'transform="rotate(-90 16 {(top + bottom) / 2:.0f})" '
                 f'text-anchor="middle">{ylabel}</text>')
    for item in series:
        points = ' '.join(f'{px(x):.1f},{py(y):.1f}' for x, y in item['points'])
        parts.append(f'<polyline points="{points}" fill="none" stroke="{item["color"]}" '
                     f'stroke-width="2"/>')
    legend_x = right - 12
    for position, item in enumerate(series):
        y = top + 14 + position * 16
        label = item['label']
        parts.append(f'<text x="{legend_x}" y="{y}" font-size="12" fill="{item["color"]}" '
                     f'text-anchor="end">{label}</text>')
    parts.append('</svg>')
    path.write_text('\n'.join(parts) + '\n', encoding='utf-8')


def write_curve_png(path: Path, series: list[dict], *, title: str,
                    width: int = 760, height: int = 320) -> bool:
    """同一张图的 PNG 版（Pillow 可用时生成，方便直接预览）。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:  # 没有 Pillow 时只留 SVG
        return False
    font_file = Path('C:/Windows/Fonts/msyh.ttc')
    if not font_file.exists():
        return False
    geometry = _chart_geometry(series, width, height)
    px, py = geometry['px'], geometry['py']
    left, right, top, bottom = geometry['box']
    x_min, x_max = geometry['x']
    y_min, y_max = geometry['y']
    canvas = Image.new('RGB', (width, height), 'white')
    draw = ImageDraw.Draw(canvas)
    title_font = ImageFont.truetype(str(font_file), 16)
    label_font = ImageFont.truetype(str(font_file), 11)
    legend_font = ImageFont.truetype(str(font_file), 12)
    draw.text((left, 28), title, font=title_font, fill='#1f2328', anchor='lm')
    draw.line([(left, top), (left, bottom), (right, bottom)], fill='#8c959f', width=1)
    for tick in range(5):
        value = y_min + (y_max - y_min) * tick / 4
        y = py(value)
        draw.line([(left, y), (right, y)], fill='#d8dee4', width=1)
        draw.text((left - 8, y), f'{value:.2f}', font=label_font, fill='#57606a', anchor='rm')
    for tick in range(5):
        value = x_min + (x_max - x_min) * tick / 4
        draw.text((px(value), bottom + 18), f'{value:.0f}', font=label_font,
                  fill='#57606a', anchor='mm')
    draw.text(((left + right) / 2, height - 10), 'epoch', font=label_font,
              fill='#57606a', anchor='mm')
    for item in series:
        points = [(px(x), py(y)) for x, y in item['points']]
        draw.line(points, fill=item['color'], width=2, joint='curve')
    for position, item in enumerate(series):
        draw.text((right - 12, top + 14 + position * 16), item['label'],
                  font=legend_font, fill=item['color'], anchor='rm')
    canvas.save(path)
    return True


def write_policy_csv(output: Path, policy: LatentPolicy) -> None:
    """把隐向量与交互矩阵导出成 Excel 可直接打开的 CSV。"""
    header = ['name', *(f'z{i + 1}' for i in range(policy.dim)), 'strength']
    rows = [header]
    for name, vector in zip(policy.names, policy.vectors):
        rows.append([name, *(f'{value:.4f}' for value in vector),
                     f'{policy.strength(name):.4f}'])
    (output / 'vectors.csv').write_text(
        '\n'.join(','.join(row) for row in rows) + '\n', encoding='utf-8-sig')
    matrix_rows = [['', *(f'z{i + 1}' for i in range(policy.dim))]]
    for row_number, vector in enumerate(policy.matrix):
        matrix_rows.append([f'z{row_number + 1}', *(f'{value:.4f}' for value in vector)])
    (output / 'interaction_matrix.csv').write_text(
        '\n'.join(','.join(row) for row in matrix_rows) + '\n', encoding='utf-8-sig')


def _markdown_table(headers: list[str], rows: list[list]) -> str:
    lines = ['| ' + ' | '.join(headers) + ' |',
             '| ' + ' | '.join('---' for _ in headers) + ' |']
    for row in rows:
        lines.append('| ' + ' | '.join(str(cell) for cell in row) + ' |')
    return '\n'.join(lines)


def role_label(name: str) -> str:
    entry = lineup.CATALOG.get(name)
    tags = lineup.role_tags(entry) if entry else set()
    if 'fire' in tags:
        return '打火'
    if {'sustain', 'shield'} & tags:
        return '治疗/护盾'
    if 'damage' in tags:
        return '输出'
    return '辅助/控制/其它'


def role_breakdown(predictions: list[dict]) -> tuple[list[tuple], tuple[int, int]]:
    """测试集按旧选择的职能拆分一致率，并统计"缺火补火"场景。"""
    buckets: dict[str, list[int]] = {}
    fire_need = [0, 0]
    for row in predictions:
        label = role_label(row['old_pick'])
        bucket = buckets.setdefault(label, [0, 0])
        bucket[0] += 1
        bucket[1] += int(row['agree'])
        team_has_fire = any('fire' in lineup.role_tags(lineup.CATALOG[name])
                            for name in row['team'] if name in lineup.CATALOG)
        if label == '打火' and not team_has_fire:
            fire_need[0] += 1
            fire_need[1] += int(row['agree'])
    order = ['打火', '输出', '治疗/护盾', '辅助/控制/其它']
    rows = [(label, buckets[label][0], buckets[label][1]) for label in order if label in buckets]
    return rows, (fire_need[0], fire_need[1])


def latent_spectrum(policy: LatentPolicy) -> dict:
    """隐向量矩阵的方差谱：看 16 维里真正用上了几维。"""
    values = np.linalg.svd(policy.vectors, compute_uv=False) ** 2
    share = values / values.sum() if values.sum() > 0 else values
    cumulative = np.cumsum(share)
    rank = int(np.searchsorted(cumulative, 0.99) + 1)
    return {'share': [float(value) for value in share],
            'cumulative': [float(value) for value in cumulative],
            'rank_99': min(rank, policy.dim)}


def context_breakdown(policies: dict[str, LatentPolicy], rows: list[dict]) -> dict:
    """把测试集拆成"旧策略受队伍影响 / 不受影响"两组，分别看各头的准确率。

    这是检验"后几轮到底有没有参考前几轮"的直接指标：受队伍影响的那部分如果一个
    都学不到，说明模型其实是按单卡强度在选。
    """
    original = lineup.battle_stats._tally
    lineup.battle_stats._tally = lambda key='ours': {}
    groups = {head: {'sensitive': [0, 0], 'free': [0, 0]} for head in policies}
    try:
        for row in rows:
            with_team = lineup.choose_pick(row['offers'], row['team']).name
            without = lineup.choose_pick(row['offers'], []).name
            key = 'free' if with_team == without else 'sensitive'
            for head, policy in policies.items():
                hit = policy.choose(row['offers'], row['team']).name == row['pick']
                groups[head][key][0] += int(hit)
                groups[head][key][1] += 1
    finally:
        lineup.battle_stats._tally = original
    return {head: {key: {'accuracy': value[0] / max(1, value[1]), 'n': value[1]}
                   for key, value in buckets.items()}
            for head, buckets in groups.items()}


def strength_correlation(policy: LatentPolicy) -> float:
    """学到的自身强度 q·z 与旧评分表 strength 的皮尔逊相关系数。"""
    pairs = [(name, float((lineup.STRENGTH_SCORES.get(name) or {}).get('strength')))
             for name in policy.names
             if isinstance((lineup.STRENGTH_SCORES.get(name) or {}).get('strength'),
                           (int, float))]
    if len(pairs) < 3:
        return 0.0
    learned = np.array([policy.strength(name) for name, _ in pairs])
    reference = np.array([value for _, value in pairs])
    if learned.std() < 1e-12 or reference.std() < 1e-12:
        return 0.0
    return float(np.corrcoef(learned, reference)[0, 1])


def top_pairs(policy: LatentPolicy, limit: int = 8) -> tuple[list, list]:
    """在全名单上找交互矩阵最强/最弱的配对（只取正反两个方向都同号的组合）。"""
    scores = policy.vectors @ policy.matrix @ policy.vectors.T
    names = list(policy.names)
    best, worst = [], []
    for row in range(len(names)):
        for column in range(row + 1, len(names)):
            forward, backward = scores[row, column], scores[column, row]
            if forward > 0 and backward > 0:
                best.append((min(forward, backward), names[row], names[column]))
            elif forward < 0 and backward < 0:
                worst.append((max(forward, backward), names[row], names[column]))
    best.sort(reverse=True)
    worst.sort()
    return best[:limit], worst[:limit]


def build_report(*, dataset: dict, split: dict, history: list[dict], train_metrics: dict,
                 test_metrics: dict, baseline: dict, grad_errors: dict, policy: LatentPolicy,
                 config: dict, elapsed: float, predictions: list[dict],
                 charts: list[str], comparison: list[dict], folds: int,
                 cross_validation: dict[str, list[float]], rollouts: dict,
                 blanked_accuracy: float, context: dict | None = None) -> str:
    first = history[0]
    final = history[-1]
    best_test = max(history, key=lambda row: (row['test_accuracy'], -row['test_loss']))
    coverage = len({name for row in dataset['decisions']
                    for name in row['offers'] + row['team']})
    best_pairs, worst_pairs = top_pairs(policy)
    role_rows, fire_rows = role_breakdown(predictions)
    spectrum = latent_spectrum(policy)
    bilinear_row = next(row for row in comparison if row['head'] == 'bilinear')
    mlp_row = next((row for row in comparison if row['head'] == 'mlp'), None)
    role_row = next((row for row in comparison if row['head'] == 'role'), None)
    context_lines = []
    if context:
        context_lines = [
            '上下文敏感性（按"旧策略会不会因队伍改选"拆分测试集）：',
            '',
            _markdown_table(['打分头', '受队伍影响（旧策略会改选）', '不受影响'],
                            [[row['label'],
                              f"{context.get(row['head'], {}).get('sensitive', {}).get('accuracy', 0):.0%}"
                              f" / {context.get(row['head'], {}).get('sensitive', {}).get('n', 0)} 条",
                              f"{context.get(row['head'], {}).get('free', {}).get('accuracy', 0):.0%}"
                              f" / {context.get(row['head'], {}).get('free', {}).get('n', 0)} 条"]
                             for row in comparison]),
            '',
        ]
    comparison_lines = [
        '## 5. 简单计算 vs 浅层网络',
        '',
        _markdown_table(['打分头', '参数量', '训练一致率', '测试一致率', '测试交叉熵', '单次推理'],
                        [[row['label'], row['params'], f"{row['train_accuracy']:.1%}",
                          f"{row['test_accuracy']:.1%}", f"{row['test_nll']:.4f}",
                          f"{row['latency_ms']:.3f} ms"] for row in comparison]),
        '',
        f"双线性配置：{bilinear_row['config']}；"
        + (f"浅层网络配置：{mlp_row['config']}。" if mlp_row else '')
        + (f"角色计数头配置：{role_row['config']}。" if role_row else ''),
        '',
        *context_lines,
    ]
    best = max(comparison, key=lambda row: row['test_accuracy'])
    delta = best['test_accuracy'] - bilinear_row['test_accuracy']
    if best['head'] != 'bilinear' and delta >= 0.02:
        comparison_lines.append(
            f"{best['label']} 比双线性高 {delta:.1%}，建议默认用它"
            f"（单次推理 {best['latency_ms']:.3f} ms）。")
    elif best['head'] != 'bilinear':
        comparison_lines.append(
            f"最好的头（{best['label']}）只比双线性高 {delta:.1%}，落在噪声内，"
            '默认仍保留双线性（更快、可解释）。')
    else:
        comparison_lines.append(
            f"双线性仍是最好的一档，单次推理 {bilinear_row['latency_ms']:.3f} ms。")
    comparison_lines.append(
        '自回归整局一致率：' + '，'.join(f"{row['label']} {row['rollout_accuracy']:.1%}"
                                        for row in comparison) + '。')
    if context:
        comparison_lines.append(
            '受队伍影响的那部分决策上：'
            + '，'.join(f"{row['label']} {row['context_sensitive']:.0%}"
                        for row in comparison)
            + '；不受影响时 '
            + '，'.join(f"{row['context_free']:.0%}" for row in comparison) + '。')
    if cross_validation:
        labels = {'bilinear': bilinear_row['label'],
                  'mlp': mlp_row['label'] if mlp_row else '浅层网络',
                  'role': role_row['label'] if role_row else '角色计数头'}
        rows = []
        for head, values in cross_validation.items():
            mean = float(np.mean(values))
            std = float(np.std(values))
            rows.append([labels[head], *[f'{value:.0%}' for value in values],
                         f'{mean:.1%}', f'{std:.1%}'])
        comparison_lines += [
            '',
            f'{folds} 折交叉验证（每折仍是 4:1，轮流留出 20 局，合起来覆盖全部 '
            f'{len(dataset["decisions"])} 条决策）：',
            '',
            _markdown_table(['打分头', *[f'折 {number}' for number in
                                          range(1, len(cross_validation['bilinear']) + 1)],
                             '平均', '标准差'], rows),
            '',
            f"单次 4:1 划分的测试一致率（{bilinear_row['test_accuracy']:.1%}）只代表这一批抽样；"
            f"双线性 {folds} 折平均 {np.mean(cross_validation['bilinear']):.1%}"
            f"（±{np.std(cross_validation['bilinear']):.1%}）才是可预期的水平。",
        ]
        for head in ('mlp', 'role'):
            if head in cross_validation:
                comparison_lines.append(
                    f"{labels[head]} {folds} 折平均 {np.mean(cross_validation[head]):.1%}"
                    f"（±{np.std(cross_validation[head]):.1%}）。")
    comparison_lines.append('')
    lines = [
        '# 协同对弈：16 维隐向量策略拟合报告',
        '',
        f"生成时间：{dataset['generated_at']}　|　总耗时：{elapsed:.1f}s",
        '',
        '## 1. 数据',
        '',
        f"- 旧策略：`{dataset['old_policy']}`（人工评定规则：等级 / 对弈百科 / 公式 / 六番阵容）",
        f"- 采样：{dataset['episodes']} 局 × {dataset['rounds']} 轮 × {dataset['offers']} 选 1 = "
        f"{len(dataset['decisions'])} 次选择（训练以局为单位：局内 5 轮按顺序喂给模型，"
        '后 4 轮都带着前几轮的选择，不把 5 轮当成 5 个独立样本）',
        f"- 覆盖式神：{coverage} / {len(lineup.CATALOG)}",
        f"- 划分：按局 4:1 → 训练 {len(split['train_episodes'])} 局 / {len(split['train'])} 条，"
        f"测试 {len(split['test_episodes'])} 局 / {len(split['test'])} 条（同一局不跨集）",
        f"- 实战统计参与打分：{'是' if dataset['include_battle_stats'] else '否（只拟合静态规则，保证可复现）'}",
        '',
        '## 2. 模型',
        '',
        '```text',
        's(c | team) = q·z_c + mean_{t∈team} z_cᵀ M z_t',
        '```',
        '',
        f"- 式神隐向量 Z：{len(policy.names)} × {policy.dim} = {len(policy.names) * policy.dim} 个参数",
        f"- 自身强度查询 q：{policy.dim} 个参数",
        f"- 交互矩阵 M：{policy.dim} × {policy.dim} = {policy.dim ** 2} 个参数",
        '- 首轮 team 为空，只剩 q·z_c（自身强度）；之后与已选式神逐对打分取平均。',
        '- 角色计数头在同样 16 维向量上再拼 `[候选角色多热(7), 队伍各角色计数(7)]`，'
        '过一层隐藏层——专门补双线性表达不了的"缺火/缺输出/保护位只能一个"这类阈值规则。',
        '- 损失：名单内（3 选 1）softmax 交叉熵 + L2，按局把 5 次条件选择一起求梯度；'
        '优化器 Adam，梯度由解析反向传播给出。',
        '',
        '## 3. 训练',
        '',
        _markdown_table(['超参数', '值'], [
            ['训练方式', '两阶段（先冻结 q 学般配，再联合微调）'
             if config.get('two_stage') else '单阶段联合训练'],
            ['epochs', config['epochs']],
            ['每批局数', config.get('episodes_per_batch', config['batch_size'])],
            ['learning rate', config['lr']],
            ['L2 系数 λ', config['weight_decay']],
            ['梯度裁剪', config['clip_norm']],
            ['随机种子', config['seed']],
        ]),
        '',
        f"- 起始：train loss {first['train_loss']:.4f} / acc {first['train_accuracy']:.3f}",
        f"- 结束：train loss {final['train_loss']:.4f} / acc {final['train_accuracy']:.3f}，"
        f"test loss {final['test_loss']:.4f} / acc {final['test_accuracy']:.3f}",
        f"- 反向传播数值校验（中心差分，相对误差）："
        + '，'.join(f'{name} {error:.2e}' for name, error in grad_errors.items()),
        '',
        *([] if 'loss_curve.png' not in charts else ['![交叉熵损失](loss_curve.png)', '']),
        *([] if 'accuracy_curve.png' not in charts else ['![一致率](accuracy_curve.png)', '']),
        '## 4. 结果（与旧策略的一致率）',
        '',
        _markdown_table(['指标', '训练集', '测试集'], [
            ['局数 / 决策数', f"{train_metrics['episodes']} / {train_metrics['n']}",
             f"{test_metrics['episodes']} / {test_metrics['n']}"],
            ['top-1 一致率', f"{train_metrics['accuracy']:.1%}", f"{test_metrics['accuracy']:.1%}"],
            ['交叉熵', f"{train_metrics['nll']:.4f}", f"{test_metrics['nll']:.4f}"],
            ['旧选择平均名次', f"{train_metrics['mean_rank']:.2f}",
             f"{test_metrics['mean_rank']:.2f}"],
            ['整局 5/5 完全一致', f"{train_metrics['episode_exact']:.1%}",
             f"{test_metrics['episode_exact']:.1%}"],
            ['平均每局对几个', f"{train_metrics['mean_matched']:.2f}",
             f"{test_metrics['mean_matched']:.2f}"],
        ]),
        '',
        f"按局顺序放（测试集 {test_metrics['episodes']} 局，模型自己选完 5 轮、"
        '误差会累积）：',
        '',
        _markdown_table(['口径', '逐轮一致率', '整局 5/5', '平均每局对几个'], [
            ['每轮都给真实前几轮（教学强制）', f"{test_metrics['accuracy']:.1%}",
             f"{test_metrics['episode_exact']:.1%}", f"{test_metrics['mean_matched']:.2f}"],
            ['模型自己下完整局（自回归）', f"{rollouts['bilinear']['accuracy']:.1%}",
             f"{rollouts['bilinear']['episode_exact']:.1%}",
             f"{rollouts['bilinear']['mean_matched']:.2f}"],
        ]),
        '',
        '自回归逐轮一致率：' + '，'.join(
            f"第 {step} 轮 {value['accuracy']:.0%}"
            for step, value in rollouts['bilinear']['per_round'].items()),
        '',
        f"把队伍清空再选一次：一致率 {blanked_accuracy:.1%}"
        + ('，与带队伍时几乎相同 —— 说明当前参数下"般配项"影响很小，'
           '模型主要是按单卡强度在选。'
           if abs(blanked_accuracy - test_metrics['accuracy']) < 0.01
           else f"，比带队伍时低 {test_metrics['accuracy'] - blanked_accuracy:.1%}，"
                '说明前几轮的选择确实在影响后面几轮。'),
        '',
        '分轮次 top-1（第 1 轮只考验自身强度；第 2 轮起以上一轮的真实选择为输入）：',
        '',
        _markdown_table(['轮次', '训练集', '测试集'], [
            [f'第 {round_number} 轮',
             f"{train_metrics['per_round'][round_number]['accuracy']:.1%}"
             f"（{train_metrics['per_round'][round_number]['n']}）",
             f"{test_metrics['per_round'][round_number]['accuracy']:.1%}"
             f"（{test_metrics['per_round'][round_number]['n']}）"]
            for round_number in sorted(train_metrics['per_round'])
        ]),
        '',
        _markdown_table(['基线', '训练集', '测试集'], [
            ['随机三选一', '33.3%', '33.3%'],
            ['只按静态自身强度', f"{baseline['train']:.1%}", f"{baseline['test']:.1%}"],
        ]),
        '',
        '按旧选择的职能拆分（测试集）：',
        '',
        _markdown_table(['旧选择的职能', '样本', '一致率'],
                        [[label, count, f'{hits}/{count}（{hits / count:.0%}）']
                         for label, count, hits in role_rows]),
        '',
        f"其中队里还没有打火机、旧策略补火的 {fire_rows[0]} 条里，新模型跟对 {fire_rows[1]} 条。",
        '',
        f"测试集上最好的一轮（仅作乐观上界，最终模型固定取 epoch {config['epochs']}）："
        f"epoch {best_test['epoch']}，acc {best_test['test_accuracy']:.3f} / "
        f"loss {best_test['test_loss']:.4f}",
        '',
        *comparison_lines,
        '## 6. 学到的结构（双线性头）',
        '',
        '自身强度最高的式神（q·z）：',
        '',
        ', '.join(f'{name} {value:+.2f}' for name, value in policy.top_strength(12)),
        '',
        f"q·z 与旧评分表 strength 的相关系数：r = {strength_correlation(policy):+.3f}"
        f"（共 {len(policy)} 个式神中 {sum(1 for name in policy.names if name in lineup.STRENGTH_SCORES)} 个有评分）。"
        '该系数只作参考：参数有等价变换空间，q·z 的绝对刻度不唯一，'
        '自身强度学得好不好应看第 1 轮一致率。',
        '',
        f"隐向量方差谱：第 1 维 {spectrum['share'][0]:.1%}、"
        f"前 2 维 {spectrum['cumulative'][1]:.1%}、"
        f"前 3 维 {spectrum['cumulative'][2]:.1%}；"
        f"按 99% 方差计有效维度约 {spectrum['rank_99']}。"
        '16 维是容量上限，用不上的维度会被 L2 压到 0；'
        '要让 16 维都用满，可以减小 λ 或增加采样局数，但一致率通常会下降。',
        '',
        '交互矩阵里最"般配"的配对（z_cᵀMz_t，取双向同号）：',
        '',
        ', '.join(f'{left}+{right} {value:+.2f}' for value, left, right in best_pairs) or '（无）',
        '',
        '最互斥的配对：',
        '',
        ', '.join(f'{left}+{right} {value:+.2f}' for value, left, right in worst_pairs) or '（无）',
        '',
        '## 7. 说明与局限',
        '',
        '- 数据由旧的确定性规则生成，同一局不会同时出现在训练/测试集，因此测试集衡量的是'
        '"新模型能否复现旧规则"，而不是实战胜率。',
        '- 旧规则含"六番阵容重合 ≥3 人加 32~45 分"这类高阶组合项与硬过滤；'
        '两两交互模型只能近似其中一部分，剩余差异会体现为一致率的上限。',
        '- 首轮样本较少（每局 1 条），自身强度只由一个 16 维投影 q 表达，'
        '等级之间差距很小的式神容易被互换。',
        '- 参数存在等价变换（Z→ZA、q→A⁻¹q、M→A⁻¹MA⁻ᵀ 不改变任何分数），'
        '所以"q·z 的绝对值"不是唯一的强度刻度；自身强度是否学到，应看第 1 轮一致率。',
        '- 新策略上线后仍建议用实战胜负回头校准：同一套参数可以继续用胜负标签微调。',
        '- λ=0.01、250 epoch 是在 λ∈[0.006, 0.03]、epoch∈[150, 300] 上粗扫选定的；'
        '同一训练平台内测试一致率在 0.75~0.79 之间波动，100 条测试样本的 95% 置信区间约 ±8 个百分点。',
    ]
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------------------
# 8. 入口
# ---------------------------------------------------------------------------
def run(*, episodes: int = DEFAULT_EPISODES, rounds: int = DEFAULT_ROUNDS,
        latent: int = LATENT_DIM, epochs: int = 250, batch_size: int = 32,
        lr: float = 0.05, weight_decay: float = 0.01, eval_every: int = 5,
        seed: int = DEFAULT_SEED, output: Path = DEFAULT_OUTPUT,
        use_battle_stats: bool = False, gradcheck: bool = False,
        train_mlp: bool = True, mlp_hidden: int = 32, mlp_lr: float = 0.02,
        mlp_weight_decay: float = 0.03, mlp_epochs: int | None = None,
        folds: int = 5, dataset: dict | None = None, two_stage: bool = True,
        train_role: bool = True, role_hidden: int = 64, role_lr: float = 0.02,
        role_weight_decay: float = 0.005, role_epochs: int | None = None) -> dict:
    started = time.perf_counter()
    if episodes < 2:
        raise ValueError('至少需要 2 局才能做 4:1 划分')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    names = sorted(lineup.CATALOG)
    name_index = {name: row for row, name in enumerate(names)}

    print(f'[1/5] 用旧策略生成 {episodes} 局 × {rounds} 轮决策 ...', flush=True)
    dataset = dataset or build_dataset(episodes=episodes, rounds=rounds, seed=seed,
                                       use_battle_stats=use_battle_stats)
    split = split_dataset(dataset)
    (output / 'dataset.json').write_text(
        json.dumps(dataset, ensure_ascii=False, indent=1), encoding='utf-8')
    (output / 'split.json').write_text(json.dumps({
        'ratio': split['ratio'], 'seed': split['seed'],
        'train_episodes': split['train_episodes'],
        'test_episodes': split['test_episodes'],
    }, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'      决策 {len(dataset["decisions"])} 条；训练 {len(split["train"])} 条 / '
          f'测试 {len(split["test"])} 条', flush=True)

    train_data = EpisodeData(split['train'], name_index, rounds)
    test_data = EpisodeData(split['test'], name_index, rounds)
    prediction_data = SplitData(split['test'], name_index, rounds - 1)
    episodes_per_batch = max(1, round(batch_size / max(1, rounds)))
    print(f'      局数：训练 {len(train_data)} 局 / 测试 {len(test_data)} 局'
          f'（每局 {rounds} 轮，后 {rounds - 1} 轮都带前几轮的选择）', flush=True)

    print(f'[2/5] 初始化 {latent} 维隐向量（{len(names)} 个式神）并校验反向传播 ...', flush=True)
    model = LatentModel(len(names), latent, seed=seed,
                        m_scale=0.1 if two_stage else 0.01)
    grad_errors = gradient_check(model, train_data, weight_decay=weight_decay,
                                 samples=8 if gradcheck else 4)
    worst = max(grad_errors.values())
    print('      双线性头数值梯度相对误差：' + '，'.join(
        f'{name} {error:.2e}' for name, error in grad_errors.items()), flush=True)
    if worst > 1e-4:
        raise RuntimeError(f'反向传播校验未通过：最大相对误差 {worst:.2e}')

    print(f'[3/5] 双线性头：{"两阶段" if two_stage else "单阶段"}按局训练 {epochs} epoch'
          f'（Adam, lr={lr}, 每批 {episodes_per_batch} 局, λ={weight_decay}）...',
          flush=True)
    if two_stage:
        history = train_two_stage(model, train_data, test_data, epochs=epochs,
                                  batch_size=episodes_per_batch, lr=lr,
                                  weight_decay=weight_decay, eval_every=eval_every,
                                  seed=seed)
    else:
        history = train_episodes(model, train_data, test_data, epochs=epochs,
                                 batch_size=episodes_per_batch, lr=lr,
                                 weight_decay=weight_decay, eval_every=eval_every,
                                 seed=seed)
    train_metrics = evaluate_episodes(model, train_data)
    test_metrics = evaluate_episodes(model, test_data)
    baseline = {'train': baseline_accuracy(split['train']),
                'test': baseline_accuracy(split['test'])}

    net = net_history = None
    net_train_metrics = net_test_metrics = None
    net_errors: dict[str, float] = {}
    if train_mlp:
        net_epochs = mlp_epochs or epochs
        print(f'[3/5] 浅层网络头：hidden={mlp_hidden}，{net_epochs} epoch'
              f'（Adam, lr={mlp_lr}, λ={mlp_weight_decay}）...', flush=True)
        net = ShallowNet(len(names), latent, hidden=mlp_hidden, seed=seed)
        net_errors = gradient_check(net, train_data, weight_decay=mlp_weight_decay,
                                    samples=8 if gradcheck else 4)
        worst = max(net_errors.values())
        print('      浅层网络头数值梯度相对误差：' + '，'.join(
            f'{name} {error:.2e}' for name, error in net_errors.items()), flush=True)
        if worst > 1e-4:
            raise RuntimeError(f'浅层网络反向传播校验未通过：最大相对误差 {worst:.2e}')
        net_history = train_episodes(net, train_data, test_data, epochs=net_epochs,
                                     batch_size=episodes_per_batch, lr=mlp_lr,
                                     weight_decay=mlp_weight_decay,
                                     eval_every=eval_every, seed=seed)
        net_train_metrics = evaluate_episodes(net, train_data)
        net_test_metrics = evaluate_episodes(net, test_data)

    role_net = role_history = None
    role_train_metrics = role_test_metrics = None
    role_errors: dict[str, float] = {}
    role_table = role_codes(names)
    if train_role:
        role_epochs = role_epochs or 500
        print(f'[3/5] 角色计数头：hidden={role_hidden}，{role_epochs} epoch'
              f'（Adam, lr={role_lr}, λ={role_weight_decay}）...', flush=True)
        role_net = RoleNet(len(names), latent, roles=role_table, hidden=role_hidden,
                           seed=seed)
        role_errors = gradient_check(role_net, train_data,
                                     weight_decay=role_weight_decay,
                                     samples=8 if gradcheck else 4)
        worst = max(role_errors.values())
        print('      角色头数值梯度相对误差：' + '，'.join(
            f'{name} {error:.2e}' for name, error in role_errors.items()), flush=True)
        if worst > 1e-4:
            raise RuntimeError(f'角色头反向传播校验未通过：最大相对误差 {worst:.2e}')
        role_history = train_episodes(role_net, train_data, test_data, epochs=role_epochs,
                                      batch_size=episodes_per_batch, lr=role_lr,
                                      weight_decay=role_weight_decay,
                                      eval_every=eval_every, seed=seed)
        role_train_metrics = evaluate_episodes(role_net, train_data)
        role_test_metrics = evaluate_episodes(role_net, test_data)

    cross_validation: dict[str, list[float]] = {}
    if folds >= 2:
        print(f'[4/5] {folds} 折交叉验证（每折仍是 4:1）...', flush=True)
        cross_validation = cross_validate(
            dataset, names, name_index, folds=folds, rounds=rounds, epochs=epochs,
            batch_size=batch_size, lr=lr, weight_decay=weight_decay, seed=seed,
            train_mlp=train_mlp, mlp_hidden=mlp_hidden, mlp_lr=mlp_lr,
            mlp_weight_decay=mlp_weight_decay, mlp_epochs=mlp_epochs,
            two_stage=two_stage)

    print(f'[5/5] 导出策略与报告 ...', flush=True)
    sample = test_data.rows[0][-1]  # 最后一轮队伍最长，按最坏情况测耗时
    policies = {
        'bilinear': LatentPolicy(names, model.Z, model.query, model.matrix, head='bilinear'),
    }
    latencies = {'bilinear': measure_latency(policies['bilinear'], sample['offers'],
                                             sample['team'])}
    if net is not None:
        policies['mlp'] = LatentPolicy(
            names, net.Z, None, net.matrix, head='mlp',
            head_params={'W1': net.W1, 'b1': net.b1, 'W2': net.W2, 'b2': net.b2})
        latencies['mlp'] = measure_latency(policies['mlp'], sample['offers'], sample['team'])
    if role_net is not None:
        policies['role'] = LatentPolicy(
            names, role_net.Z, None, role_net.matrix, head='role',
            head_params={'W1': role_net.W1, 'b1': role_net.b1,
                         'W2': role_net.W2, 'b2': role_net.b2},
            roles=role_table)
        latencies['role'] = measure_latency(policies['role'], sample['offers'],
                                            sample['team'])

    rollouts = {'bilinear': rollout_metrics(policies['bilinear'], test_data)}
    for head, policy in policies.items():
        if head != 'bilinear':
            rollouts[head] = rollout_metrics(policy, test_data)
    blanked = 0
    for episode in test_data.rows:
        for row in episode:
            blanked += int(policies['bilinear'].choose(row['offers'], ()).name == row['pick'])
    blanked_accuracy = blanked / max(1, test_data.size * test_data.rounds)
    context = context_breakdown(policies, prediction_data.rows)

    common_meta = {
        'trained_at': datetime.now().isoformat(timespec='seconds'),
        'source': dataset['old_policy'],
        'episodes': episodes,
        'decisions': len(dataset['decisions']),
        'train_episodes': len(train_data),
        'test_episodes': len(test_data),
        'baseline_accuracy': round(baseline['test'], 4),
    }
    policies['bilinear'].meta.update(common_meta, head='bilinear',
                                     test_accuracy=round(test_metrics['accuracy'], 4),
                                     test_nll=round(test_metrics['nll'], 4),
                                     rollout_accuracy=round(rollouts['bilinear']['accuracy'], 4),
                                     latency_ms=round(latencies['bilinear'], 4))
    if 'mlp' in policies:
        policies['mlp'].meta.update(common_meta, head='mlp',
                                    hidden=mlp_hidden,
                                    test_accuracy=round(net_test_metrics['accuracy'], 4),
                                    test_nll=round(net_test_metrics['nll'], 4),
                                    rollout_accuracy=round(rollouts['mlp']['accuracy'], 4),
                                    latency_ms=round(latencies['mlp'], 4))
    if 'role' in policies:
        policies['role'].meta.update(common_meta, head='role', hidden=role_hidden,
                                     test_accuracy=round(role_test_metrics['accuracy'], 4),
                                     test_nll=round(role_test_metrics['nll'], 4),
                                     rollout_accuracy=round(rollouts['role']['accuracy'], 4),
                                     latency_ms=round(latencies['role'], 4))
    policy_path = policies['bilinear'].save(output / 'latent16.json')
    write_policy_csv(output, policies['bilinear'])
    if 'mlp' in policies:
        policies['mlp'].save(output / 'latent16_mlp.json')
        write_csv(output / 'training_metrics_mlp.csv', net_history)
    if 'role' in policies:
        policies['role'].save(output / 'latent16_role.json')
        write_csv(output / 'training_metrics_role.csv', role_history)

    write_csv(output / 'training_metrics.csv', history)
    loss_series = [
        {'label': '训练集', 'color': '#1f6feb',
         'points': [(row['epoch'], row['train_loss']) for row in history]},
        {'label': '测试集', 'color': '#d1242f',
         'points': [(row['epoch'], row['test_loss']) for row in history]},
    ]
    accuracy_series = [
        {'label': '训练集', 'color': '#1f6feb',
         'points': [(row['epoch'], row['train_accuracy']) for row in history]},
        {'label': '测试集', 'color': '#d1242f',
         'points': [(row['epoch'], row['test_accuracy']) for row in history]},
    ]
    write_curve_svg(output / 'loss_curve.svg', loss_series,
                    title='交叉熵损失', ylabel='loss')
    write_curve_svg(output / 'accuracy_curve.svg', accuracy_series,
                    title='与旧策略的 top-1 一致率', ylabel='accuracy')
    charts = []
    if write_curve_png(output / 'loss_curve.png', loss_series, title='交叉熵损失'):
        charts.append('loss_curve.png')
    if write_curve_png(output / 'accuracy_curve.png', accuracy_series,
                       title='与旧策略的 top-1 一致率'):
        charts.append('accuracy_curve.png')

    predictions = []
    scores = model.scores(prediction_data.candidates, prediction_data.team,
                          prediction_data.counts)
    for row_number, row in enumerate(prediction_data.rows):
        best = int(np.argmax(scores[row_number]))
        predictions.append({
            'episode': row['episode'],
            'round': row['round'],
            'offers': row['offers'],
            'team': row['team'],
            'old_pick': row['pick'],
            'new_pick': row['offers'][best],
            'agree': bool(best == row['picked_index']),
            'scores': [round(float(value), 4) for value in scores[row_number]],
        })
    (output / 'test_predictions.json').write_text(
        json.dumps(predictions, ensure_ascii=False, indent=1), encoding='utf-8')

    comparison = [{
        'head': 'bilinear', 'label': '双线性（q·z + 平均配对）',
        'params': policies['bilinear'].param_count(),
        'train_accuracy': train_metrics['accuracy'],
        'test_accuracy': test_metrics['accuracy'],
        'test_nll': test_metrics['nll'],
        'episode_exact': test_metrics['episode_exact'],
        'rollout_accuracy': rollouts['bilinear']['accuracy'],
        'rollout_episode_exact': rollouts['bilinear']['episode_exact'],
        'latency_ms': latencies['bilinear'],
        'context_sensitive': context['bilinear']['sensitive']['accuracy'],
        'context_free': context['bilinear']['free']['accuracy'],
        'config': f'lr={lr}, λ={weight_decay}, {epochs} epoch',
    }]
    if 'mlp' in policies:
        comparison.append({
            'head': 'mlp', 'label': f'浅层网络（16→{mlp_hidden}→1）',
            'params': policies['mlp'].param_count(),
            'train_accuracy': net_train_metrics['accuracy'],
            'test_accuracy': net_test_metrics['accuracy'],
            'test_nll': net_test_metrics['nll'],
            'episode_exact': net_test_metrics['episode_exact'],
            'rollout_accuracy': rollouts['mlp']['accuracy'],
            'rollout_episode_exact': rollouts['mlp']['episode_exact'],
            'latency_ms': latencies['mlp'],
            'context_sensitive': context['mlp']['sensitive']['accuracy'],
            'context_free': context['mlp']['free']['accuracy'],
            'config': f'lr={mlp_lr}, λ={mlp_weight_decay}, {mlp_epochs or epochs} epoch',
        })
    if 'role' in policies:
        comparison.append({
            'head': 'role', 'label': f'角色计数头（16→{role_hidden}→1）',
            'params': policies['role'].param_count(),
            'train_accuracy': role_train_metrics['accuracy'],
            'test_accuracy': role_test_metrics['accuracy'],
            'test_nll': role_test_metrics['nll'],
            'episode_exact': role_test_metrics['episode_exact'],
            'rollout_accuracy': rollouts['role']['accuracy'],
            'rollout_episode_exact': rollouts['role']['episode_exact'],
            'latency_ms': latencies['role'],
            'context_sensitive': context['role']['sensitive']['accuracy'],
            'context_free': context['role']['free']['accuracy'],
            'config': f'lr={role_lr}, λ={role_weight_decay}, '
                      f'{role_epochs or 500} epoch',
        })

    elapsed = time.perf_counter() - started
    config = {'epochs': epochs, 'batch_size': batch_size, 'lr': lr,
              'weight_decay': weight_decay, 'clip_norm': 5.0, 'seed': seed,
              'episodes_per_batch': episodes_per_batch, 'rounds': rounds,
              'two_stage': two_stage}
    report = build_report(dataset=dataset, split=split, history=history,
                          train_metrics=train_metrics, test_metrics=test_metrics,
                          baseline=baseline, grad_errors=grad_errors,
                          policy=policies['bilinear'],
                          config=config, elapsed=elapsed, predictions=predictions,
                          charts=charts, comparison=comparison, folds=folds,
                          cross_validation=cross_validation, rollouts=rollouts,
                          blanked_accuracy=blanked_accuracy, context=context)
    (output / 'report.md').write_text(report, encoding='utf-8')

    summary = {
        'policy': str(policy_path),
        'train_accuracy': train_metrics['accuracy'],
        'test_accuracy': test_metrics['accuracy'],
        'test_nll': test_metrics['nll'],
        'baseline_test_accuracy': baseline['test'],
        'latency_ms': latencies['bilinear'],
        'comparison': comparison,
        'cross_validation': {head: {'mean': float(np.mean(values)),
                                    'std': float(np.std(values))}
                             for head, values in cross_validation.items()},
        'elapsed': elapsed,
    }
    print(f'      策略：{policy_path}', flush=True)
    print(f'      双线性：训练集 {train_metrics["accuracy"]:.1%} / '
          f'测试集 {test_metrics["accuracy"]:.1%}（只按自身强度 {baseline["test"]:.1%}，'
          f'随机 33.3%），单次推理 {latencies["bilinear"]:.3f} ms', flush=True)
    if 'mlp' in policies:
        print(f'      浅层网络：训练集 {net_train_metrics["accuracy"]:.1%} / '
              f'测试集 {net_test_metrics["accuracy"]:.1%}，'
              f'单次推理 {latencies["mlp"]:.3f} ms', flush=True)
    if cross_validation:
        parts = [f'{head} {np.mean(values):.1%}±{np.std(values):.1%}'
                 for head, values in cross_validation.items()]
        print(f'      {folds} 折平均：' + '，'.join(parts), flush=True)
    print(f'      报告：{output / "report.md"}', flush=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='拟合协同对弈 16 维隐向量选人策略')
    parser.add_argument('--episodes', type=int, default=DEFAULT_EPISODES,
                        help='采样局数，每局 5 轮三选一（默认 100）')
    parser.add_argument('--rounds', type=int, default=DEFAULT_ROUNDS, help='每局轮数')
    parser.add_argument('--latent', type=int, default=LATENT_DIM, help='隐向量维度')
    parser.add_argument('--epochs', type=int, default=250)
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--lr', type=float, default=0.05)
    parser.add_argument('--weight-decay', type=float, default=0.01)
    parser.add_argument('--eval-every', type=int, default=5)
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--use-battle-stats', action='store_true',
                        help='让旧策略计入实战统计（标签将随 duel_stats.json 变化）')
    parser.add_argument('--gradcheck', action='store_true', help='每个参数多取几个点做梯度校验')
    parser.add_argument('--single-stage', action='store_true',
                        help='关闭两阶段训练（默认先冻结 q 学般配，再联合微调）')
    parser.add_argument('--skip-mlp', action='store_true',
                        help='只训练双线性头（默认两种头都训练并对比）')
    parser.add_argument('--mlp-hidden', type=int, default=32, help='浅层网络隐藏层宽度')
    parser.add_argument('--mlp-epochs', type=int, default=None, help='默认与 --epochs 相同')
    parser.add_argument('--mlp-lr', type=float, default=0.02)
    parser.add_argument('--mlp-weight-decay', type=float, default=0.03)
    parser.add_argument('--skip-role', action='store_true',
                        help='不训练角色计数头（默认训练：16 维向量 + 队伍角色计数）')
    parser.add_argument('--role-hidden', type=int, default=64)
    parser.add_argument('--role-epochs', type=int, default=500)
    parser.add_argument('--role-lr', type=float, default=0.02)
    parser.add_argument('--role-weight-decay', type=float, default=0.005)
    parser.add_argument('--folds', type=int, default=5,
                        help='交叉验证折数，0 表示只做单次 4:1 划分')
    args = parser.parse_args(argv)
    run(episodes=args.episodes, rounds=args.rounds, latent=args.latent,
        epochs=args.epochs, batch_size=args.batch_size, lr=args.lr,
        weight_decay=args.weight_decay, eval_every=args.eval_every, seed=args.seed,
        output=args.output, use_battle_stats=args.use_battle_stats, gradcheck=args.gradcheck,
        train_mlp=not args.skip_mlp, mlp_hidden=args.mlp_hidden, mlp_lr=args.mlp_lr,
        mlp_weight_decay=args.mlp_weight_decay, mlp_epochs=args.mlp_epochs,
        folds=args.folds, two_stage=not args.single_stage,
        train_role=not args.skip_role, role_hidden=args.role_hidden,
        role_lr=args.role_lr, role_weight_decay=args.role_weight_decay,
        role_epochs=args.role_epochs)
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
