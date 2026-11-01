"""Desktop launch jobs: keep the configuration API responsive; no launch at import time."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Lock
from types import SimpleNamespace
from uuid import uuid4

from module.logger import logger

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='desktop-launch')
_lock = Lock()
_jobs = {}


def desktop_launch_status(config_name):
    with _lock:
        return dict(_jobs.get(config_name, {'state': 'idle'}))


def _launch(snapshot, name, job_id):
    with _lock:
        _jobs[name] = {'id': job_id, 'state': 'running'}
    try:
        from module.device.method.desktop_impl import desktop_launch_instance
        success = desktop_launch_instance(snapshot)
        result = {'id': job_id, 'state': 'succeeded' if success else 'failed',
                  'message': '客户端已就绪' if success else '启动失败，请查看实例日志'}
    except Exception as error:
        logger.exception(f'Desktop launch failed: {name}')
        result = {'id': job_id, 'state': 'failed', 'message': str(error)}
    with _lock:
        _jobs[name] = result
    logger.info(f'Desktop launch job {name}: {result["state"]}')


def schedule_desktop_launch(config):
    name = str(config.config_name)
    snapshot = SimpleNamespace(config_name=name, script=deepcopy(config.script))
    with _lock:
        if _jobs.get(name, {}).get('state') in ('queued', 'running'):
            return dict(_jobs[name])
        job_id = uuid4().hex
        _jobs[name] = {'id': job_id, 'state': 'queued'}
    _executor.submit(_launch, snapshot, name, job_id)
    logger.info(f'Desktop launch queued: {name}, job={job_id}')
    return desktop_launch_status(name)
