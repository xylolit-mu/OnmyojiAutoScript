"""协同对弈：上阵（从已选式神里挑 3 张）的十六维隐向量策略。

选人阶段决定"拿哪 5 张"，上阵阶段决定"上哪 3 张"。旧实现是
``lineup.plan_lineup(own, team_size=3)`` 的规则搜索；这里用同一套隐向量思路量化：

    每张卡 c 有 16 维上阵向量 u_c；
    单卡价值  b·u_c
    两张般配  u_cᵀ N u_d   （N 取对称矩阵）
    一套 3 人的分数 = Σ 单卡价值 + Σ 两两般配

对 5 张卡的 10 种 3 人组合各打一次分，取最高的一组。参数 (U, b, N) 由
``dev_tools/draft_duel/fit_latent_field16.py`` 用旧上阵规则的选择做标签、
梯度下降 + 反向传播拟合得到，落盘为 ``data/DraftDuel/latent16/field16.json``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Sequence

import numpy as np

FIELD_DIM = 16
FIELD_SIZE = 3
FIELD_FORMAT = 'draft-duel-field16/1'
FIELD_PATH = (Path(__file__).resolve().parents[3]
              / 'data' / 'DraftDuel' / 'latent16' / 'field16.json')
UNKNOWN_REASON = '未收录：按中性分 0 处理'


@dataclass(frozen=True)
class FieldPick:
    names: tuple[str, ...]
    score: float
    reasons: tuple[str, ...] = ()


class FieldPolicy:
    """不可变的上阵策略：集合打分 + 组合枚举，只做前向计算。"""

    def __init__(self, names: Sequence[str], vectors, query, matrix, *, meta=None):
        self.names = tuple(str(name) for name in names)
        if len(set(self.names)) != len(self.names):
            raise ValueError('式神名单含重复项')
        vectors = np.array(vectors, dtype=np.float64, copy=True)
        if vectors.ndim != 2 or vectors.shape[0] != len(self.names):
            raise ValueError(f'上阵向量形状应为 ({len(self.names)}, dim)，实际 {vectors.shape}')
        dim = vectors.shape[1]
        query = np.array(query, dtype=np.float64, copy=True).reshape(-1)
        matrix = np.array(matrix, dtype=np.float64, copy=True)
        if query.shape != (dim,):
            raise ValueError(f'单卡价值向量长度应为 {dim}，实际 {query.shape}')
        if matrix.shape != (dim, dim):
            raise ValueError(f'般配矩阵形状应为 ({dim}, {dim})，实际 {matrix.shape}')
        if not (np.isfinite(vectors).all() and np.isfinite(query).all()
                and np.isfinite(matrix).all()):
            raise ValueError('参数含非有限值')
        self.vectors = vectors
        self.query = query
        # 对称化后使用，保证"哪张卡在前"不影响分数
        self.matrix = 0.5 * (matrix + matrix.T)
        self.meta = dict(meta or {})
        self._index = {name: row for row, name in enumerate(self.names)}

    @property
    def dim(self) -> int:
        return self.vectors.shape[1]

    def __len__(self) -> int:
        return len(self.names)

    def param_count(self) -> int:
        return int(self.vectors.size + self.query.size + self.matrix.size)

    def vector(self, name: str) -> np.ndarray:
        try:
            return self.vectors[self._index[name]]
        except KeyError:
            raise KeyError(f'策略中没有式神 {name!r}') from None

    def _stack(self, cards: Sequence[str]) -> tuple[np.ndarray, list[str]]:
        rows, missing = [], []
        for card in cards:
            row = self._index.get(card)
            if row is None:
                missing.append(card)
                rows.append(None)
            else:
                rows.append(row)
        stacked = np.zeros((len(rows), self.dim))
        for position, row in enumerate(rows):
            if row is not None:
                stacked[position] = self.vectors[row]
        return stacked, missing

    def score_set(self, cards: Sequence[str]) -> float:
        """一套上阵名单的分数；未收录的卡按 0 向量处理。"""
        vectors, _ = self._stack(cards)
        if not len(vectors):
            return 0.0
        total = float((vectors @ self.query).sum())
        if len(vectors) > 1:
            pairs = vectors @ self.matrix @ vectors.T
            total += float(np.triu(pairs, 1).sum())
        return total

    def choose(self, available: Sequence[str], size: int = FIELD_SIZE) -> FieldPick:
        available = list(dict.fromkeys(str(name) for name in available))
        if len(available) < size:
            raise ValueError(f'候选不足 {size} 张')
        best = None
        for combo in combinations(range(len(available)), size):
            cards = [available[index] for index in combo]
            value = self.score_set(cards)
            if best is None or value > best[0]:
                best = (value, cards)
        score, cards = best
        return FieldPick(tuple(cards), float(score), self.explain(cards))

    def explain(self, cards: Sequence[str]) -> tuple[str, ...]:
        reasons = []
        for card in cards:
            if card not in self._index:
                reasons.append(f'{card} {UNKNOWN_REASON}')
                continue
            reasons.append(f'{card} 单卡 {float(self.vector(card) @ self.query):+.2f}')
        for first, second in combinations(cards, 2):
            if first in self._index and second in self._index:
                value = float(self.vector(first) @ self.matrix @ self.vector(second))
                reasons.append(f'{first}+{second} 般配 {value:+.2f}')
        return tuple(reasons)

    def field_values(self, limit: int = 10) -> list[tuple[str, float]]:
        """单卡价值 b·u 最高的式神。"""
        values = self.vectors @ self.query
        order = np.argsort(-values)
        return [(self.names[row], float(values[row])) for row in order[:max(0, limit)]]

    def to_dict(self) -> dict:
        return {
            'format': FIELD_FORMAT,
            'dim': self.dim,
            'names': list(self.names),
            'vectors': np.round(self.vectors, 6).tolist(),
            'query': np.round(self.query, 6).tolist(),
            'matrix': np.round(self.matrix, 6).tolist(),
            'meta': self.meta,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> 'FieldPolicy':
        if payload.get('format') != FIELD_FORMAT:
            raise ValueError(f"上阵策略格式不匹配：{payload.get('format')!r}")
        return cls(payload['names'], payload['vectors'], payload['query'],
                   payload['matrix'], meta=payload.get('meta'))

    def save(self, path: Path | str = FIELD_PATH) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False,
                                   separators=(',', ':')) + '\n', encoding='utf-8')
        return path

    @classmethod
    def load(cls, path: Path | str = FIELD_PATH) -> 'FieldPolicy':
        return cls.from_dict(json.loads(Path(path).read_text(encoding='utf-8')))


def load_field_policy(path: Path | str = FIELD_PATH) -> FieldPolicy | None:
    """读取上阵策略；文件不存在时返回 None，调用方可回退旧规则。"""
    path = Path(path)
    if not path.exists():
        return None
    return FieldPolicy.load(path)
