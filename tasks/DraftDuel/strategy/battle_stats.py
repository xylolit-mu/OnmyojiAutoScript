"""协同对弈实战统计：小局阵容胜负加权，以及大轮最后一次胜场读数。

数据结构:
{
  "battles": [
    {"ts": 1730000000, "result": "win",
     "ours": [5个初选], "fielded": [3个上阵],
     "mate": [队友5个], "opponent": []}
  ],
  "rounds": [
    {"round_id": "...", "instance": "MUMU", "final_victory_count": 3,
     "completed": true}
  ]
}
未确认的小局记为 unknown，不参与加权；大轮最终胜场不由小局结果外推。
对手式神名暂时留空，避免凭画面猜测。
"""

import json
import time
from pathlib import Path

STATS_PATH = Path(__file__).resolve().parents[3] / 'data' / 'DraftDuel' / 'duel_stats.json'
STATS_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / 'duel_stats.template.json'
MIN_SAMPLES = 3
MAX_BONUS = 8.0


def load() -> dict:
    # 本地文件存在时始终优先使用；远端模板仅为首次记录提供空结构。
    try:
        return json.loads(STATS_PATH.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return json.loads(STATS_TEMPLATE_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'battles': [], 'rounds': []}


def _write_update(collection, entry, event_id=None):
    """两实例共享统计文件：跨进程锁、原子替换，并按事件标识防重。"""
    import os
    import tempfile
    from filelock import FileLock
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(STATS_PATH) + '.lock', timeout=10):
        data = load()
        rows = data.setdefault(collection, [])
        existing = next((r for r in rows if event_id and r.get('event_id') == event_id), None)
        if existing is not None:
            if existing.get('result') in ('win', 'loss') and entry.get('result') == 'unknown':
                entry = {k: v for k, v in entry.items() if k not in ('result', 'victory_after')}
            existing.update(entry)
            entry = existing
        else:
            rows.append(entry)
        data[collection] = rows[-500:]
        fd, temporary = tempfile.mkstemp(prefix=STATS_PATH.name + '.', suffix='.tmp', dir=STATS_PATH.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False, indent=1)
            os.replace(temporary, STATS_PATH)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return dict(entry)


def record(result, ours=(), fielded=(), mate=(), opponent=(), *, event_id=None,
           instance='', round_id='', small_game=None, victory_before=None, victory_after=None,
           unresolved_slots=()):
    """记录小局；胜负尚不能确定时写 unknown，不参与胜率加权。"""
    entry = {'ts': round(time.time()), 'result': result, 'ours': list(ours),
             'fielded': list(fielded), 'mate': list(mate), 'opponent': list(opponent)}
    if event_id:
        entry.update(event_id=event_id, instance=instance, round_id=round_id,
                     small_game=small_game, victory_before=victory_before, victory_after=victory_after)
    if unresolved_slots:
        entry['unresolved_slots'] = list(unresolved_slots)
    return _write_update('battles', entry, event_id)


def resolve_result(event_id, result, victory_after):
    """补齐同一小局的胜负，不追加第二条样本。"""
    if result not in ('win', 'loss', 'unknown'):
        raise ValueError('Unsupported battle result')
    return _write_update('battles', {'event_id': event_id, 'result': result,
                                    'victory_after': victory_after}, event_id)


def record_round(round_id, instance, final_victory_count, ours=(), *, completed, reason=''):
    """大轮最终胜场严格取最后有效读数，不累加、不外推最后一局。"""
    event_id = f'{instance}:{round_id}'
    return _write_update('rounds', {'event_id': event_id, 'round_id': round_id,
                                  'instance': instance, 'ts': round(time.time()),
                                  'final_victory_count': final_victory_count,
                                  'ours': list(ours), 'completed': bool(completed),
                                  'reason': reason}, event_id)


def _tally(key='ours'):
    table = {}
    for battle in load().get('battles', []):
        result = battle.get('result')
        if result not in ('win', 'loss'):
            continue
        for name in battle.get(key) or []:
            if not name:
                continue
            win, loss = table.get(name, (0, 0))
            table[name] = (win + (result == 'win'), loss + (result == 'loss'))
    return table


def pick_bonus(name: str, table=None) -> float:
    """按实战胜率给选人加分; 样本不足不加."""
    table = table if table is not None else _tally('ours')
    win, loss = table.get(name, (0, 0))
    if win + loss < MIN_SAMPLES:
        return 0.0
    rate = win / (win + loss)
    return max(-MAX_BONUS, min(MAX_BONUS, (rate - 0.5) * 2 * MAX_BONUS))


def field_bonus(name: str, table=None) -> float:
    """按"上阵时"的胜率给上阵加分; 样本不足不加."""
    table = table if table is not None else _tally('fielded')
    win, loss = table.get(name, (0, 0))
    if win + loss < MIN_SAMPLES:
        return 0.0
    rate = win / (win + loss)
    return max(-MAX_BONUS, min(MAX_BONUS, (rate - 0.5) * 2 * MAX_BONUS))


def summary_line(table) -> str:
    if not table:
        return '无实战样本'
    rows = sorted(((w + l, w, l, name) for name, (w, l) in table.items()), reverse=True)
    parts = [f'{name} {w}胜{l}负' for _, w, l, name in rows[:5]]
    return ', '.join(parts)
