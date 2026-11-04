"""协同对弈：十六维隐向量选人策略。

旧策略 ``lineup.choose_pick`` 是一套人工评定的规则（等级/百科/公式/六番阵容……），
本模块把它量化成可以计算的向量模型：

    每个式神 c 有一个 16 维隐向量 z_c；
    查询向量 q 给出"自身强度"：        s_self(c)   = q · z_c
    交互矩阵 M 给出"与已选式神的般配"：s_pair(c,t) = z_c^T M z_t
    选人分数                            s(c|team)   = s_self(c) + mean_{t∈team} s_pair(c,t)

首轮 team 为空，只剩 s_self，也就是"选自身强度最高的"；第 2 轮起与已选式神
（包含首轮定下的初始式神）两两打分取平均，等价于"选与这套阵容最般配的"。

``head='mlp'`` 是同一套隐向量上的浅层网络备选头：把 [z_c, 队伍均值, z_c⊙队伍均值,
与队伍的平均般配分, 队伍人数] 送进一层隐藏层再输出分数，用来验证"简单计算是否不够"。
两种头的单次推理都在毫秒级（见 ``report.md`` 的耗时表）。

(Z, q, M) 不是手工填的，而是用 ``dev_tools/draft_duel/fit_latent16_policy.py``
以旧策略的决策为标签、用梯度下降 + 反向传播拟合出来的，落盘为
``data/DraftDuel/latent16/latent16.json``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

LATENT_DIM = 16
POLICY_FORMAT = 'draft-duel-latent16/2'
COMPATIBLE_FORMATS = ('draft-duel-latent16/1', POLICY_FORMAT)
POLICY_PATH = (Path(__file__).resolve().parents[3]
               / 'data' / 'DraftDuel' / 'latent16' / 'latent16.json')
HEADS = ('bilinear', 'mlp', 'role')
UNKNOWN_REASON = '未收录：按中性分 0 处理'
MAX_TEAM = 4


@dataclass(frozen=True)
class LatentPick:
    """与 ``lineup.Pick`` 兼容的最小结果：name/score/reasons。"""

    name: str
    score: float
    reasons: tuple[str, ...] = ()
    suggested_souls: tuple[str, ...] = ()


def _relu(values: np.ndarray) -> np.ndarray:
    return np.maximum(values, 0.0)


class LatentPolicy:
    """不可变的隐向量策略：只做前向计算，不参与训练。"""

    def __init__(self, names: Sequence[str], vectors, query=None, matrix=None, *,
                 head: str = 'bilinear', head_params: dict | None = None,
                 roles=None, meta=None):
        if head not in HEADS:
            raise ValueError(f'未知的策略头：{head!r}')
        self.head = head
        self.names = tuple(str(name) for name in names)
        if len(set(self.names)) != len(self.names):
            raise ValueError('式神名单含重复项')
        vectors = np.array(vectors, dtype=np.float64, copy=True)
        if vectors.ndim != 2 or vectors.shape[0] != len(self.names):
            raise ValueError(f'隐向量形状应为 ({len(self.names)}, dim)，实际 {vectors.shape}')
        dim = vectors.shape[1]
        self.vectors = vectors
        self.meta = dict(meta or {})
        self._index = {name: row for row, name in enumerate(self.names)}

        if matrix is None:
            raise ValueError('缺少交互矩阵')
        matrix = np.array(matrix, dtype=np.float64, copy=True)
        if matrix.shape != (dim, dim):
            raise ValueError(f'交互矩阵形状应为 ({dim}, {dim})，实际 {matrix.shape}')
        self.matrix = matrix
        self.roles = np.zeros((len(self.names), 0))
        if head == 'role':
            if roles is None:
                raise ValueError("角色头需要 roles（每张卡的角色多热向量）")
            roles = np.array(roles, dtype=np.float64, copy=True)
            if roles.ndim != 2 or roles.shape[0] != len(self.names) or not roles.shape[1]:
                raise ValueError(f'角色表形状应为 ({len(self.names)}, R)，实际 {roles.shape}')
            self.roles = roles

        if head == 'bilinear':
            if query is None:
                raise ValueError('双线性头需要查询向量 q')
            query = np.array(query, dtype=np.float64, copy=True).reshape(-1)
            if query.shape != (dim,):
                raise ValueError(f'查询向量长度应为 {dim}，实际 {query.shape}')
            self.query = query
            self.head_params: dict[str, np.ndarray] = {}
        else:
            self.query = (np.zeros(dim) if query is None
                          else np.array(query, dtype=np.float64, copy=True).reshape(-1))
            if self.query.shape != (dim,):
                raise ValueError(f'查询向量长度应为 {dim}，实际 {self.query.shape}')
            if not head_params:
                raise ValueError('浅层网络头需要 head_params')
            features = 3 * dim + 2 + 2 * self.roles.shape[1]
            params = {name: np.array(value, dtype=np.float64, copy=True)
                      for name, value in head_params.items()}
            expected = {'W1': (features, None), 'b1': (None,), 'W2': (None,), 'b2': (1,)}
            if set(params) != set(expected):
                raise ValueError(f'head_params 字段应为 {sorted(expected)}')
            hidden = params['b1'].reshape(-1).shape[0]
            if params['W1'].shape[0] != features or params['W1'].shape[1] != hidden:
                raise ValueError(f"W1 形状应为 ({features}, {hidden})，实际 {params['W1'].shape}")
            if params['W2'].reshape(-1).shape[0] != hidden:
                raise ValueError('W2 长度与隐藏层不一致')
            self.head_params = params

        for value in (self.vectors, self.matrix, self.query, self.roles, *self.head_params.values()):
            if not np.isfinite(value).all():
                raise ValueError('参数含非有限值')

    # ---- 基本信息 -------------------------------------------------------
    @property
    def dim(self) -> int:
        return self.vectors.shape[1]

    def __len__(self) -> int:
        return len(self.names)

    def __contains__(self, name: str) -> bool:
        return name in self._index

    def vector(self, name: str) -> np.ndarray:
        try:
            return self.vectors[self._index[name]]
        except KeyError:
            raise KeyError(f'策略中没有式神 {name!r}') from None

    def param_count(self) -> int:
        total = self.vectors.size + self.matrix.size
        if self.head == 'bilinear':
            total += self.query.size
        else:
            total += sum(value.size for value in self.head_params.values())
        return int(total)

    # ---- 前向计算 -------------------------------------------------------
    def _vectors_for(self, names: Iterable[str]) -> tuple[np.ndarray, list[str]]:
        rows = []
        missing = []
        for name in names:
            row = self._index.get(name)
            if row is None:
                missing.append(name)
                rows.append(None)
            else:
                rows.append(row)
        stacked = np.zeros((len(rows), self.dim))
        for position, row in enumerate(rows):
            if row is not None:
                stacked[position] = self.vectors[row]
        return stacked, missing

    def _mlp(self, candidate_vectors: np.ndarray, team_vectors: np.ndarray) -> np.ndarray:
        count = len(team_vectors)
        if count:
            team_mean = team_vectors.mean(axis=0)
            pair = (candidate_vectors @ self.matrix @ team_vectors.T).mean(axis=1)
        else:
            team_mean = np.zeros(self.dim)
            pair = np.zeros(candidate_vectors.shape[0])
        features = np.concatenate([
            candidate_vectors,
            np.tile(team_mean, (candidate_vectors.shape[0], 1)),
            candidate_vectors * team_mean,
            pair[:, None],
            np.full((candidate_vectors.shape[0], 1), min(count, MAX_TEAM) / MAX_TEAM),
        ], axis=1)
        hidden = _relu(features @ self.head_params['W1'] + self.head_params['b1'])
        return (hidden @ self.head_params['W2']
                + float(self.head_params['b2'].reshape(-1)[0]))

    def _roles_for(self, names: Sequence[str]) -> np.ndarray:
        """候选/队伍的角色多热向量；未收录按全 0 处理。"""
        rows = self.roles.shape[1]
        stacked = np.zeros((len(names), rows))
        for position, name in enumerate(names):
            row = self._index.get(str(name))
            if row is not None:
                stacked[position] = self.roles[row]
        return stacked

    def _role_mlp(self, candidate_vectors: np.ndarray, team_vectors: np.ndarray,
                  candidate_roles: np.ndarray, team_roles: np.ndarray) -> np.ndarray:
        count = len(team_vectors)
        if count:
            team_mean = team_vectors.mean(axis=0)
            pair = (candidate_vectors @ self.matrix @ team_vectors.T).mean(axis=1)
            team_role_counts = team_roles.sum(axis=0) / MAX_TEAM
        else:
            team_mean = np.zeros(self.dim)
            pair = np.zeros(candidate_vectors.shape[0])
            team_role_counts = np.zeros(self.roles.shape[1])
        features = np.concatenate([
            candidate_vectors,
            np.tile(team_mean, (candidate_vectors.shape[0], 1)),
            candidate_vectors * team_mean,
            pair[:, None],
            np.full((candidate_vectors.shape[0], 1), min(count, MAX_TEAM) / MAX_TEAM),
            candidate_roles,
            np.tile(team_role_counts, (candidate_vectors.shape[0], 1)),
        ], axis=1)
        hidden = _relu(features @ self.head_params['W1'] + self.head_params['b1'])
        return (hidden @ self.head_params['W2']
                + float(self.head_params['b2'].reshape(-1)[0]))

    def score(self, candidates: Sequence[str], team: Sequence[str] = ()) -> np.ndarray:
        """返回每个候选的分数；未收录的候选按中性 0 分处理。"""
        candidates = [str(name) for name in candidates]
        if not candidates:
            raise ValueError('没有候选式神')
        candidate_vectors, _ = self._vectors_for(candidates)
        members = [str(name) for name in team if name and name in self._index]
        team_vectors, _ = self._vectors_for(members)
        if self.head == 'mlp':
            return self._mlp(candidate_vectors, team_vectors)
        if self.head == 'role':
            return self._role_mlp(candidate_vectors, team_vectors,
                                  self._roles_for(candidates), self._roles_for(members))
        scores = candidate_vectors @ self.query
        if members:
            scores = scores + (candidate_vectors @ self.matrix @ team_vectors.T).mean(axis=1)
        return scores

    def strength(self, name: str) -> float:
        """自身强度：队伍为空时的分数（双线性头即 q·z）。"""
        if name not in self._index:
            return 0.0
        return float(self.score([name])[0])

    def top_strength(self, limit: int = 10) -> list[tuple[str, float]]:
        values = self.score(self.names)
        order = np.argsort(-values)
        return [(self.names[row], float(values[row])) for row in order[:max(0, limit)]]

    def choose(self, candidates: Sequence[str], team: Sequence[str] = ()) -> LatentPick:
        candidates = [str(name) for name in candidates]
        scores = self.score(candidates, team)
        best = int(np.argmax(scores))  # argmax 取第一个最大值，同分保持候选顺序
        return LatentPick(candidates[best], float(scores[best]),
                          self.explain(candidates[best], team))

    def _pair_value(self, name: str, member: str) -> float:
        return float(self.vector(name) @ self.matrix @ self.vector(member))

    def explain(self, name: str, team: Sequence[str] = ()) -> tuple[str, ...]:
        if name not in self._index:
            return (UNKNOWN_REASON,)
        self_score = self.strength(name)
        if self.head == 'bilinear':
            reasons = [f'自身强度 {self_score:+.2f}']
            for member in team:
                if str(member) in self._index:
                    reasons.append(f'与{member}般配 {self._pair_value(name, str(member)):+.2f}')
            return tuple(reasons)
        total = float(self.score([name], team)[0])
        return (f'自身强度 {self_score:+.2f}', f'整套阵容评分 {total:+.2f}')

    def pair_scores(self, name: str) -> list[tuple[str, float]]:
        """某个式神与全名单的般配分，按从高到低排序（离线分析用）。"""
        vector = self.vector(name)
        values = vector @ self.matrix @ self.vectors.T
        order = np.argsort(-values)
        return [(self.names[row], float(values[row])) for row in order]

    # ---- 序列化 ---------------------------------------------------------
    def to_dict(self) -> dict:
        payload = {
            'format': POLICY_FORMAT,
            'dim': self.dim,
            'head': self.head,
            'names': list(self.names),
            'vectors': np.round(self.vectors, 6).tolist(),
            'matrix': np.round(self.matrix, 6).tolist(),
            'meta': self.meta,
        }
        if self.head == 'bilinear':
            payload['query'] = np.round(self.query, 6).tolist()
        else:
            payload['head_params'] = {name: np.round(value, 6).tolist()
                                      for name, value in self.head_params.items()}
        if self.head == 'role':
            payload['roles'] = np.round(self.roles, 3).tolist()
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> 'LatentPolicy':
        if payload.get('format') not in COMPATIBLE_FORMATS:
            raise ValueError(f"策略格式不匹配：{payload.get('format')!r}")
        return cls(payload['names'], payload['vectors'], payload.get('query'),
                   payload['matrix'], head=payload.get('head', 'bilinear'),
                   head_params=payload.get('head_params'),
                   roles=payload.get('roles'), meta=payload.get('meta'))

    def save(self, path: Path | str = POLICY_PATH) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.to_dict(), ensure_ascii=False, separators=(',', ':'))
        path.write_text(text + '\n', encoding='utf-8')
        return path

    @classmethod
    def load(cls, path: Path | str = POLICY_PATH) -> 'LatentPolicy':
        payload = json.loads(Path(path).read_text(encoding='utf-8'))
        return cls.from_dict(payload)


def load_policy(path: Path | str = POLICY_PATH) -> LatentPolicy | None:
    """读取策略文件；不存在时返回 None，调用方可回退旧规则策略。"""
    path = Path(path)
    if not path.exists():
        return None
    return LatentPolicy.load(path)
