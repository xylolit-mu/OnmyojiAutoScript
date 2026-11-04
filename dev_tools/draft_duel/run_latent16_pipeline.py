"""协同对弈两阶段策略的自动训练流水线（选人 + 上阵）。

一次运行会：
1. 用旧规则生成 N 局数据（默认 1000 局，每局 5 轮选人 + 1 次上阵）；
2. 拟合并导出选人策略（双线性 + 浅层网络）与上阵策略；
3. 按配置的验收条件检查结果，全部满足才返回 0；
4. 条件不满足且开了 ``--auto-scale`` 时，自动把局数翻倍重训，最多到 ``--max-episodes``。

验收条件（都可用参数覆盖）：
- 选人测试集 top-1 ≥ ``--target-draft``（默认 0.65）
- 选人自回归整局一致率 ≥ ``--target-rollout``（默认 0.55）
- 上阵 top-1 ≥ ``--target-field``（默认 0.55），平均重合 ≥ ``--min-overlap``（默认 2.3）
- 两种策略单次推理都 < ``--latency-limit`` ms（默认 100）
- 交叉验证标准差 ≤ ``--max-std``（默认 0.10，folds≥2 时才检查）

用法
----
    python -m dev_tools.draft_duel.run_latent16_pipeline
    python -m dev_tools.draft_duel.run_latent16_pipeline --episodes 1000 --folds 5
    python -m dev_tools.draft_duel.run_latent16_pipeline --auto-scale --max-episodes 4000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dev_tools.draft_duel import fit_latent_field16, fit_latent16_policy  # noqa: E402
from dev_tools.draft_duel.fit_latent16_policy import (  # noqa: E402
    DEFAULT_OUTPUT, DEFAULT_SEED, build_dataset)

DEFAULT_EPISODES = 1000


def _check(conditions: list[dict]) -> bool:
    return all(condition['passed'] for condition in conditions)


def _condition(name: str, target: float, actual: float, *, higher_is_better: bool = True,
               unit: str = '') -> dict:
    passed = (actual >= target) if higher_is_better else (actual <= target)
    return {'name': name, 'target': target, 'actual': actual, 'passed': passed,
            'unit': unit}


def _markdown_table(headers: list[str], rows: list[list]) -> str:
    lines = ['| ' + ' | '.join(headers) + ' |',
             '| ' + ' | '.join('---' for _ in headers) + ' |']
    for row in rows:
        lines.append('| ' + ' | '.join(str(cell) for cell in row) + ' |')
    return '\n'.join(lines)


def run_pipeline(*, episodes: int = DEFAULT_EPISODES, seed: int = DEFAULT_SEED,
                 output: Path = DEFAULT_OUTPUT, folds: int = 5,
                 epochs: int = 250, field_epochs: int = 600,
                 target_draft: float = 0.65, target_rollout: float = 0.55,
                 target_field: float = 0.55, min_overlap: float = 2.3,
                 target_role: float = 0.75, target_context: float = 0.35,
                 latency_limit: float = 100.0, max_std: float = 0.10,
                 auto_scale: bool = False, max_episodes: int = 4000,
                 time_budget: float = 1800.0, verbose: bool = True) -> dict:
    started = time.perf_counter()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    log_lines: list[str] = []

    def log(message: str) -> None:
        stamp = datetime.now().strftime('%H:%M:%S')
        line = f'[{stamp}] {message}'
        log_lines.append(line)
        if verbose:
            print(line, flush=True)

    attempts = []
    current = episodes
    conditions: list[dict] = []
    while True:
        log(f'=== 开始训练：{current} 局（第 {len(attempts) + 1} 次）===')
        dataset = build_dataset(episodes=current, rounds=5, seed=seed)
        draft = fit_latent16_policy.run(
            episodes=current, epochs=epochs, folds=folds, seed=seed, output=output,
            dataset=dataset, train_mlp=False)
        field = fit_latent_field16.run(
            episodes=current, epochs=field_epochs, folds=folds, seed=seed,
            output=output, dataset=dataset)

        bilinear = next(row for row in draft['comparison'] if row['head'] == 'bilinear')
        role = next((row for row in draft['comparison'] if row['head'] == 'role'), None)
        conditions = [
            _condition('选人测试 top-1', target_draft, draft['test_accuracy'], unit='%'),
            _condition('选人自回归整局一致率', target_rollout,
                       bilinear['rollout_accuracy'], unit='%'),
            _condition('上阵 top-1', target_field, field['test_accuracy'], unit='%'),
            _condition('上阵平均重合', min_overlap, field['test_mean_overlap'],
                       unit='张/3'),
            _condition('选人推理耗时', latency_limit, draft['latency_ms'],
                       higher_is_better=False, unit='ms'),
            _condition('上阵推理耗时', latency_limit, field['latency_ms'],
                       higher_is_better=False, unit='ms'),
        ]
        if folds >= 2 and draft.get('cross_validation'):
            std = float(draft['cross_validation']['bilinear']['std'])
            conditions.append(_condition(f'选人 {folds} 折标准差', max_std, std,
                                         higher_is_better=False, unit='%'))
        if role is not None:
            conditions.append(_condition('角色计数头测试 top-1', target_role,
                                         role['test_accuracy'], unit='%'))
            conditions.append(_condition('受队伍影响决策的一致率', target_context,
                                         role['context_sensitive'], unit='%'))
        passed = _check(conditions)
        attempts.append({'episodes': current, 'draft': draft, 'field': field,
                         'conditions': conditions, 'passed': passed})
        log(f'验收：{"全部通过" if passed else "未通过"}；'
            + '，'.join(f"{c['name']} {c['actual']:.3f}" for c in conditions))
        elapsed = time.perf_counter() - started
        if passed:
            break
        if not auto_scale or current >= max_episodes:
            break
        if elapsed > time_budget:
            log(f'已用 {elapsed:.0f}s，超过时间预算 {time_budget:.0f}s，停止自动扩量')
            break
        current = min(max_episodes, current * 2)
        log(f'条件未满足，自动扩量到 {current} 局重训')

    elapsed = time.perf_counter() - started
    summary = {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'seed': seed,
        'attempts': [{'episodes': attempt['episodes'], 'passed': attempt['passed']}
                     for attempt in attempts],
        'conditions': conditions,
        'final': attempts[-1],
        'elapsed': elapsed,
    }
    (output / 'pipeline_summary.json').write_text(
        json.dumps({key: value for key, value in summary.items() if key != 'final'},
                   ensure_ascii=False, indent=1), encoding='utf-8')

    report = build_pipeline_report(attempts=attempts, conditions=conditions,
                                   elapsed=elapsed, output=output)
    (output / 'pipeline_report.md').write_text(report, encoding='utf-8')
    (output / 'pipeline.log').write_text('\n'.join(log_lines) + '\n', encoding='utf-8')

    if verbose:
        print(f'流水线{"通过" if _check(conditions) else "未通过"}验收，'
              f'耗时 {elapsed:.0f}s，报告：{output / "pipeline_report.md"}', flush=True)
    summary['passed'] = _check(conditions)
    return summary


def build_pipeline_report(*, attempts: list[dict], conditions: list[dict],
                          elapsed: float, output: Path) -> str:
    final = attempts[-1]
    draft = final['draft']
    field = final['field']
    bilinear = next(row for row in draft['comparison'] if row['head'] == 'bilinear')
    mlp = next((row for row in draft['comparison'] if row['head'] == 'mlp'), None)
    role = next((row for row in draft['comparison'] if row['head'] == 'role'), None)
    rows = []
    for condition in conditions:
        mark = '✅' if condition['passed'] else '❌'
        target = (f"≥ {condition['target']:.3f}" if '推理耗时' not in condition['name']
                  and '标准差' not in condition['name'] else f"≤ {condition['target']:.3f}")
        rows.append([condition['name'], target, f"{condition['actual']:.3f}", mark])
    lines = [
        '# 协同对弈策略自动训练结果',
        '',
        f"生成时间：{datetime.now().isoformat(timespec='seconds')}　|　"
        f"总耗时：{elapsed:.0f}s　|　最终采样：{final['episodes']} 局",
        '',
        '## 验收条件',
        '',
        _markdown_table(['条件', '阈值', '实际', '结果'], rows),
        '',
        f"最终判定：{'全部通过' if all(c['passed'] for c in conditions) else '存在未通过项'}",
        '',
        '## 选人阶段',
        '',
        _markdown_table(['指标', '训练集', '测试集'], [
            ['top-1 一致率', f"{draft['train_accuracy']:.1%}",
             f"{draft['test_accuracy']:.1%}"],
            ['只按静态自身强度的基线', '-', f"{draft['baseline_test_accuracy']:.1%}"],
        ]),
        '',
        f"- 整局 5/5 完全一致率：{bilinear['episode_exact']:.1%}；"
        f"自回归整局一致率 {bilinear['rollout_accuracy']:.1%}"
        + (f"（浅层网络 {mlp['rollout_accuracy']:.1%}）" if mlp else ''),
        f"- 单次推理：双线性 {bilinear['latency_ms']:.3f} ms"
        + (f"，浅层网络 {mlp['latency_ms']:.3f} ms" if mlp else ''),
    ]
    if role is not None:
        lines += [
            '',
            '角色计数头（推荐）：',
            '',
            _markdown_table(['指标', '值'], [
                ['测试 top-1', f"{role['test_accuracy']:.1%}"],
                ['自回归整局一致率', f"{role['rollout_accuracy']:.1%}"],
                ['受队伍影响的决策一致率', f"{role['context_sensitive']:.0%}"
                 f"（双线性 {bilinear['context_sensitive']:.0%}）"],
                ['不受影响的决策一致率', f"{role['context_free']:.0%}"
                 f"（双线性 {bilinear['context_free']:.0%}）"],
                ['单次推理', f"{role['latency_ms']:.3f} ms"],
            ]),
        ]
    if draft.get('cross_validation'):
        values = draft['cross_validation']['bilinear']
        lines.append(f"- 交叉验证：平均 {values['mean']:.1%}（±{values['std']:.1%}）")
    lines += [
        '',
        '## 上阵阶段',
        '',
        _markdown_table(['指标', '值'], [
            ['队伍数', field['teams']],
            ['top-1（整套 3 张一致）', f"{field['test_accuracy']:.1%}"],
            ['平均重合张数', f"{field['test_mean_overlap']:.2f} / 3"],
            ['交叉验证', (f"{field['cv_accuracy']:.1%}"
                          if field.get('cv_accuracy') is not None else '未做')],
            ['单次推理', f"{field['latency_ms']:.3f} ms"],
        ]),
        '',
        '## 产物',
        '',
        f"- 选人策略：`{output / 'latent16.json'}`（双线性基线）、"
        f"`{output / 'latent16_role.json'}`（角色计数头，推荐）",
        f"- 上阵策略：`{output / 'field16.json'}`",
        f"- 详细报告：`{output / 'report.md'}`、`{output / 'field_report.md'}`",
        f"- 本次流水线日志：`{output / 'pipeline.log'}`",
        '',
    ]
    return '\n'.join(lines) + '\n'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='协同对弈选人 + 上阵策略自动训练')
    parser.add_argument('--episodes', type=int, default=DEFAULT_EPISODES)
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--folds', type=int, default=5, help='交叉验证折数，0 关闭')
    parser.add_argument('--epochs', type=int, default=250, help='选人阶段 epoch')
    parser.add_argument('--field-epochs', type=int, default=600, help='上阵阶段 epoch')
    parser.add_argument('--target-draft', type=float, default=0.65)
    parser.add_argument('--target-rollout', type=float, default=0.55)
    parser.add_argument('--target-field', type=float, default=0.55)
    parser.add_argument('--min-overlap', type=float, default=2.3)
    parser.add_argument('--target-role', type=float, default=0.75)
    parser.add_argument('--target-context', type=float, default=0.35)
    parser.add_argument('--latency-limit', type=float, default=100.0)
    parser.add_argument('--max-std', type=float, default=0.10)
    parser.add_argument('--auto-scale', action='store_true',
                        help='条件不满足时自动把局数翻倍重训')
    parser.add_argument('--max-episodes', type=int, default=4000)
    parser.add_argument('--time-budget', type=float, default=1800.0)
    args = parser.parse_args(argv)
    summary = run_pipeline(
        episodes=args.episodes, seed=args.seed, output=args.output, folds=args.folds,
        epochs=args.epochs, field_epochs=args.field_epochs,
        target_draft=args.target_draft, target_rollout=args.target_rollout,
        target_field=args.target_field, min_overlap=args.min_overlap,
        target_role=args.target_role, target_context=args.target_context,
        latency_limit=args.latency_limit, max_std=args.max_std,
        auto_scale=args.auto_scale, max_episodes=args.max_episodes,
        time_budget=args.time_budget)
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
