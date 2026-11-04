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


def serialize_draft_rounds(rows):
    """保留候选原始位置；未识别名字写 null，旧的不足三项记录不补猜。"""
    result = []
    for position, row in enumerate(rows or ()):
        if not isinstance(row, dict):
            continue
        number = row.get('round', position + 1)
        if type(number) is not int or not 1 <= number <= 5:
            continue
        offers = [str(name) if name else None for name in row.get('offers', ())]
        pick = str(row.get('pick') or '')
        entry = dict(round=number, offers=offers, pick=pick,
                     team=[str(name) for name in row.get('team', ())])
        slot = row.get('pick_slot')
        if (type(slot) is int and 1 <= slot <= len(offers) and offers[slot - 1] == pick):
            entry['pick_slot'] = slot
        elif len(offers) == 3 and pick and offers.count(pick) == 1:
            entry['pick_slot'] = offers.index(pick) + 1
        if isinstance(row.get('raw_offers'), (list, tuple)):
            entry['raw_offers'] = [str(name or '') for name in row['raw_offers']]
        result.append(entry)
    return result


def confirmed_draft_rounds(rows, picks):
    """只关联与已确认五张备选及当轮前缀一致的选择，排除待确认点击。"""
    valid = {}
    for row in serialize_draft_rounds(rows):
        number = row['round']
        if (number <= len(picks) and row['pick'] == picks[number - 1]
                and row['team'] == list(picks[:number - 1])):
            valid[number] = row
    return [valid[number] for number in sorted(valid)]


def load() -> dict:
    # 本地文件存在时始终优先使用；远端模板仅为首次记录提供空结构。
    try:
        return json.loads(STATS_PATH.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return json.loads(STATS_TEMPLATE_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'battles': [], 'rounds': []}


def _refresh_round_summary(data, entry):
    rows = [row for row in data.get('battles', [])
            if row.get('round_id') == entry.get('round_id')
            and row.get('instance', '') == entry.get('instance', '')]
    # 大轮也直接保存双方选人轨迹，旧小局已有轨迹时可据实补入。
    for key, roster in (('draft_rounds', 'ours'), ('peer_draft_rounds', 'mate')):
        candidates = [r for row in rows for r in (row.get(key) or ())]
        candidates.extend(entry.get(key) or ())
        transcript = confirmed_draft_rounds(candidates, entry.get(roster) or ())
        if transcript:
            entry[key] = transcript
    # Event IDs are idempotent in the writer. Never treat unknown as loss.
    total = max(entry.get('total_games', 0), len(rows))
    wins = sum(row.get('result') == 'win' for row in rows)
    losses = sum(row.get('result') == 'loss' for row in rows)
    unknown = max(0, total - wins - losses)
    entry.update(total_games=total, known_wins=wins, known_losses=losses,
                 unknown_games=unknown,
                 observed_win_rate=wins/(wins+losses) if wins+losses else None)
    final = entry.get('final_victory_count')
    valid_final = (entry.get('final_count_verified') and type(final) is int
                   and wins <= final <= total-losses)
    entry['wins'] = final if valid_final else wins
    entry['losses'] = total-final if valid_final else losses
    entry['win_rate'] = (entry['wins']/total if total and (valid_final or not unknown) else None)
    entry['win_rate_source'] = 'final_counter' if valid_final else 'recognized_results'
    groups = {}
    for row in rows:
        own, mate = row.get('own_fielded', []), row.get('mate_fielded', [])
        key = json.dumps([sorted(own), sorted(mate)], ensure_ascii=False)
        group = groups.setdefault(key, dict(own_fielded=own, mate_fielded=mate,
            games=0, wins=0, losses=0, unknown=0, verified=True, screenshots=[]))
        group['roster_source'] = row.get('roster_source', 'observed')
        group['games'] += 1
        result = row.get('result')
        group['wins' if result == 'win' else 'losses' if result == 'loss' else 'unknown'] += 1
        group['verified'] &= not row.get('roster_source', 'observed').startswith('planned') and not bool(row.get('unresolved_slots')) and len(own) == 3 and (not row.get('mate') or len(mate) == 3)
        if row.get('screenshot'):
            group['screenshots'].append(row['screenshot'])
    for group in groups.values():
        group['win_rate'] = group['wins']/group['games'] if not group['unknown'] else None
    entry['lineups'] = list(groups.values())


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
            if existing.get('result') in ('win', 'loss'):
                entry = {k: v for k, v in entry.items() if k not in ('result', 'victory_after')}
            existing.update(entry)
            entry = existing
        else:
            rows.append(entry)
        data[collection] = rows
        for round_entry in data.get('rounds', []):
            _refresh_round_summary(data, round_entry)
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
           unresolved_slots=(), draft_rounds=None, peer_draft_rounds=None,
           own_fielded=None, mate_fielded=None, policy_ids=None, screenshot=None, roster_source='observed'):
    """记录小局；胜负尚不能确定时写 unknown，不参与胜率加权。

    ``draft_rounds`` 是这一局的选人轨迹（每轮的三选一和当时的队伍），供离线把
    真实对局接入训练；旧记录没有这个字段，读取方按缺失处理。
    """
    entry = {'ts': round(time.time()), 'result': result, 'ours': list(ours),
             'fielded': list(fielded), 'mate': list(mate), 'opponent': list(opponent)}
    if draft_rounds:
        entry['draft_rounds'] = serialize_draft_rounds(draft_rounds)
    if peer_draft_rounds:
        entry['peer_draft_rounds'] = serialize_draft_rounds(peer_draft_rounds)
    if own_fielded is not None:
        entry['own_fielded'] = list(own_fielded)
    if mate_fielded is not None:
        entry['mate_fielded'] = list(mate_fielded)
    entry['roster_source'] = roster_source
    if screenshot:
        entry['screenshot'] = str(screenshot)
    if policy_ids:
        entry['policy_ids'] = dict(policy_ids)
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
    entry = _write_update('battles', {'event_id': event_id, 'result': result,
                                     'victory_after': victory_after}, event_id)
    return entry


def record_round(round_id, instance, final_victory_count, ours=(), *, completed, reason='',
                 total_games=0, final_count_verified=False, mate=(), planned_lineup=None,
                 policy_ids=None, draft_rounds=None, peer_draft_rounds=None):
    """大轮最终胜场严格取最后有效读数，不累加、不外推最后一局。"""
    event_id = f'{instance}:{round_id}'
    entry = {'event_id': event_id, 'round_id': round_id,
                                  'instance': instance, 'ts': round(time.time()),
                                  'final_victory_count': final_victory_count,
                                  'ours': list(ours), 'mate': list(mate), 'total_games': total_games,
                                  'final_count_verified': final_count_verified,
                                  'planned_lineup': planned_lineup, 'policy_ids': policy_ids,
                                  'completed': bool(completed),
                                  'reason': reason}
    for key, transcript in (('draft_rounds', draft_rounds), ('peer_draft_rounds', peer_draft_rounds)):
        if transcript:
            entry[key] = serialize_draft_rounds(transcript)
    return _write_update('rounds', entry, event_id)


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
