"""协同对弈开放时间；在设备操作和协同唤醒之前执行。"""
from datetime import datetime, timedelta, timezone

from module.exception import TaskEnd
from module.logger import logger

CHINA_TIME = timezone(timedelta(hours=8))


def draft_duel_now():
    return datetime.now(CHINA_TIME)


def skip_draft_duel_outside_hours(config, runtime=None):
    now = draft_duel_now()
    if 8 <= now.hour < 23:
        return False
    opening = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if now.hour >= 23:
        opening += timedelta(days=1)
    # 调度器存储本机时区的无时区 datetime。
    target = opening.astimezone().replace(tzinfo=None)
    item = getattr(config.model, 'draft_duel', None)
    peer = getattr(getattr(item, 'invite_config', None), 'cooperation_script', '').strip()
    mode = getattr(getattr(item, 'draft_duel_config', None), 'match_mode', '单人')
    runtime = runtime or getattr(config, 'cooperation_runtime', None)
    reason = '协同对弈仅在北京时间08:00～23:00开放，跳过本次任务'
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
        raise TaskEnd('协同对弈不在08:00～23:00开放时间内，已跳过')
