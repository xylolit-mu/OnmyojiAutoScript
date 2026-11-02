"""协同对弈开放时间；在设备操作和协同唤醒之前执行。"""
from datetime import datetime, timedelta, timezone

from module.exception import TaskEnd
from module.logger import logger

CHINA_TIME = timezone(timedelta(hours=8))


def draft_duel_now():
    return datetime.now(CHINA_TIME)


def skip_draft_duel_outside_hours(config, runtime=None):
    now = draft_duel_now()
    opening_hour = 10 if now.weekday() < 5 else 8
    if opening_hour <= now.hour < 23:
        return False
    opening_day = now + timedelta(days=1) if now.hour >= 23 else now
    next_hour = 10 if opening_day.weekday() < 5 else 8
    opening = opening_day.replace(hour=next_hour, minute=0, second=0, microsecond=0)
    # 调度器存储本机时区的无时区 datetime。
    target = opening.astimezone().replace(tzinfo=None)
    item = getattr(config.model, 'draft_duel', None)
    peer = getattr(getattr(item, 'invite_config', None), 'cooperation_script', '').strip()
    mode = getattr(getattr(item, 'draft_duel_config', None), 'match_mode', '单人')
    runtime = runtime or getattr(config, 'cooperation_runtime', None)
    reason = '协同对弈仅在北京时间周一至周五10:00～23:00、周六周日08:00～23:00开放，跳过本次任务'
    if peer and mode != '单人':
        if runtime is None:
            from module.cooperation import Cooperation
            runtime = Cooperation(config.config_name)
        runtime.skip_without_wake('DraftDuel', peer, reason)
    elif runtime is not None:
        runtime.ack('DraftDuel')
    config.task_delay(task='DraftDuel', target=target, server=False)
    config.task_runtime_outcome = dict(task='DraftDuel', status='time_window_skipped',
                                       success=True, reason=reason, wait_until=target)
    logger.info(f'{reason}；下次开放时间 {opening:%Y-%m-%d %H:%M}（北京时间）')
    return True


def require_draft_duel_hours(config):
    if skip_draft_duel_outside_hours(config):
        raise TaskEnd('协同对弈不在开放时间内（工作日10:00～23:00，周末08:00～23:00），已跳过')
