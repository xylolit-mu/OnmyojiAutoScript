"""Local, two-instance battle coordination. Never write another instance's config."""
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from module.exception import ScriptError, TaskEnd
from module.logger import logger
from module.config.utils import convert_to_underscore


BATTLE_TASKS = frozenset(('Orochi', 'EvoZone', 'Exploration', 'FallenSun',
                         'EternitySea', 'BondlingFairyland', 'OtherWorldTwilight', 'DraftDuel'))


def instance_name(value):
    value = str(value).strip()
    if value and (value in ('.', '..') or any(c in value for c in '\\/:*?"<>|\r\n,，;；')):
        raise ValueError('协同脚本只能填写一个本地实例配置名')
    return value


class Cooperation:
    def __init__(self, name, root=None):
        self.name = instance_name(name)
        self.root = Path(root or Path.cwd())
        folder = self.root / 'data' / 'cooperation'
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / 'state.sqlite3'
        self.active_task = ''
        self._stop = threading.Event()
        self._thread = None
        self.peers = {}
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS runners
                  (name TEXT PRIMARY KEY, task TEXT, heartbeat REAL);
                CREATE TABLE IF NOT EXISTS wakes
                  (target TEXT, task TEXT, source TEXT, created REAL,
                   PRIMARY KEY(target, task));
                CREATE TABLE IF NOT EXISTS waiting
                  (name TEXT, task TEXT, peer TEXT, since REAL,
                   PRIMARY KEY(name, task));
                CREATE TABLE IF NOT EXISTS assignments
                  (name TEXT, task TEXT, peer TEXT, PRIMARY KEY(name, task));
                CREATE TABLE IF NOT EXISTS room_checks
                  (source TEXT PRIMARY KEY, target TEXT, task TEXT, token TEXT,
                   created REAL, ready INTEGER, answered REAL);
                CREATE TABLE IF NOT EXISTS task_completions
                  (source TEXT, target TEXT, task TEXT, created REAL, reason TEXT,
                   PRIMARY KEY(source, target, task));
                CREATE TABLE IF NOT EXISTS battle_states
                  (source TEXT, target TEXT, task TEXT, session TEXT, payload TEXT, updated REAL,
                   PRIMARY KEY(source, target, task));
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def publish(self, task=None):
        changed = task is not None and task != self.active_task
        if task is not None:
            self.active_task = task
        with self.connect() as db:
            if changed:
                db.execute('DELETE FROM room_checks WHERE source=? OR target=?',
                           (self.name, self.name))
            db.execute('INSERT OR REPLACE INTO runners VALUES (?, ?, ?)',
                       (self.name, self.active_task, time.time()))

    def start(self):
        self.publish('')
        self._owner_thread = threading.current_thread()
        self._thread = threading.Thread(target=self._heartbeat, daemon=True,
                                        name='battle-cooperation')
        self._thread.start()

    def _heartbeat(self):
        while not self._stop.wait(5):
            try:
                if not self._owner_thread.is_alive():
                    with self.connect() as db:
                        db.execute('DELETE FROM runners WHERE name=?', (self.name,))
                    return
                self.publish()
            except sqlite3.Error as exc:
                logger.warning(f'Cooperation heartbeat: {exc}')

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=6)
        with self.connect() as db:
            db.execute('DELETE FROM room_checks WHERE source=? OR target=?',
                           (self.name, self.name))
            db.execute('DELETE FROM runners WHERE name=?', (self.name,))

    def request_room_check(self, task, peer):
        token = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO room_checks VALUES (?, ?, ?, ?, ?, NULL, NULL)',
                       (self.name, peer, task, token, time.time()))
        return token

    def publish_battle_state(self, task, peer, session, payload):
        """Exchange round-scoped JSON using the existing cooperation database."""
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO battle_states VALUES (?, ?, ?, ?, ?, ?)',
                       (self.name, peer, task, session, json.dumps(payload, ensure_ascii=False), time.time()))

    def peer_battle_state(self, task, peer):
        with self.connect() as db:
            row = db.execute('SELECT session, payload, updated FROM battle_states '
                             'WHERE source=? AND target=? AND task=?',
                             (peer, self.name, task)).fetchone()
        return {'session': row[0], 'payload': json.loads(row[1]), 'updated': row[2]} if row else None

    def begin_completion_phase(self, task, peer):
        """Invalidate this instance's previous outgoing completion on a new role phase."""
        with self.connect() as db:
            db.execute('DELETE FROM task_completions WHERE source=? AND target=? AND task=?',
                       (self.name, peer, task))
        return time.time()

    def notify_task_completion(self, task, peer, reason):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO task_completions VALUES (?, ?, ?, ?, ?)',
                       (self.name, peer, task, time.time(), reason))
        logger.info(f'Cooperation completed: {self.name} -> {peer}/{task}: {reason}')

    def peer_task_completion(self, task, peer, since):
        with self.connect() as db:
            row = db.execute('SELECT created, reason FROM task_completions '
                             'WHERE source=? AND target=? AND task=?',
                             (peer, self.name, task)).fetchone()
        return row[1] if row and row[0] >= since else None

    def pending_room_check(self, task, peer):
        with self.connect() as db:
            row = db.execute('SELECT token, created FROM room_checks WHERE source=? '
                             'AND target=? AND task=? AND answered IS NULL',
                             (peer, self.name, task)).fetchone()
        return row[0] if row and 0 <= time.time() - row[1] <= 5 else None

    def answer_room_check(self, task, peer, token, ready):
        with self.connect() as db:
            db.execute('UPDATE room_checks SET ready=?, answered=? WHERE source=? '
                       'AND target=? AND task=? AND token=? AND answered IS NULL',
                       (bool(ready), time.time(), peer, self.name, task, token))

    def room_check_result(self, task, peer, token):
        now = time.time()
        with self.connect() as db:
            row = db.execute('SELECT ready, answered, created FROM room_checks '
                             'WHERE source=? AND target=? AND task=? AND token=?',
                             (self.name, peer, task, token)).fetchone()
            runner = db.execute('SELECT task, heartbeat FROM runners WHERE name=?',
                                (peer,)).fetchone()
        if not row or not runner or runner[0] != task or now - runner[1] >= 30:
            return False
        if now - row[2] > 5:
            return False
        if row[1] is None:
            return None
        return bool(row[0]) and 0 <= now - row[1] <= 3

    def read(self, name):
        name = instance_name(name)
        path = self.root / 'config' / f'{name}.json'
        if not path.is_file():
            raise ScriptError(f'协同脚本不存在：{name}')
        with path.open(encoding='utf-8-sig') as stream:
            return json.load(stream)

    def validate(self, task, peer):
        peer = instance_name(peer)
        key = convert_to_underscore(task)
        if task not in BATTLE_TASKS or peer == self.name:
            raise ScriptError('组队身份有误')
        own, other = self.read(self.name).get(key, {}), self.read(peer).get(key, {})
        for name, item in ((self.name, own), (peer, other)):
            if not item.get('scheduler', {}).get('enable', False):
                raise ScriptError(f'协同任务未启用：{name}/{task}')
        def role(item):
            if task == 'DraftDuel':
                settings = item.get('draft_duel_config', {})
                mode = settings.get('match_mode', '单人')
                if mode == '组队':
                    mode = settings.get('role', '队长')
                return {'队长': 'leader', '队员': 'member', '单人': 'alone'}.get(mode)
            status = next((v.get('user_status') for v in item.values()
                           if isinstance(v, dict) and 'user_status' in v), None)
            if task == 'BondlingFairyland':
                return {'handoff1': 'leader', 'handoff2': 'member'}.get(status, status)
            return status
        if {role(own), role(other)} != {'leader', 'member'}:
            raise ScriptError('组队身份有误')
        # A configured pair cannot also be the target of a third enabled instance.
        pair = {self.name, peer}
        for path in (self.root / 'config').glob('*.json'):
            item = self.read(path.stem).get(key, {})
            if not item.get('scheduler', {}).get('enable', False):
                continue
            target = item.get('invite_config', {}).get('cooperation_script', '').strip()
            if target and ((path.stem in pair and (target not in pair or target == path.stem))
                           or (path.stem not in pair and target in pair)):
                raise ScriptError('组队身份有误')
        for name, item in ((self.name, own), (peer, other)):
            logger.info(f'Cooperation {name}/{task}: next_run={item["scheduler"].get("next_run")}')
        return other

    def accepted_peer(self, task):
        with self.connect() as db:
            row = db.execute('SELECT peer FROM assignments WHERE name=? AND task=?',
                             (self.name, task)).fetchone()
        return row[0] if row else ''

    def finish(self, task):
        with self.connect() as db:
            waiting = db.execute('SELECT 1 FROM waiting WHERE name=? AND task=?',
                                 (self.name, task)).fetchone()
            if not waiting:
                db.execute('DELETE FROM assignments WHERE name=? AND task=?', (self.name, task))
                self.peers.pop(task, None)

    def requested(self):
        with self.connect() as db:
            return db.execute('SELECT task, source FROM wakes WHERE target=? ORDER BY created',
                              (self.name,)).fetchall()

    def wake_next(self, config):
        """Only called at a scheduler boundary, so an active task is never interrupted."""
        for task, source in self.requested():
            key = convert_to_underscore(task)
            item = getattr(config.model, key, None)
            if task == 'DraftDuel':
                from module.draft_duel_schedule import skip_draft_duel_outside_hours
                if skip_draft_duel_outside_hours(config, runtime=self):
                    self.ack(task)
                    continue
            configured = getattr(getattr(item, 'invite_config', None), 'cooperation_script', '')
            if task == 'DraftDuel' and (not configured or item.draft_duel_config.match_mode == '单人'):
                # 协斗显式留空时，不因对方单向填写而被动接入协同。
                self.ack(task)
                continue
            # Ignore obsolete requests when either side changed the pairing or disabled it.
            if not (self.root / 'config' / f'{source}.json').is_file():
                self.ack(task)
                continue
            sender = self.read(source).get(key, {})
            sender_peer = sender.get('invite_config', {}).get('cooperation_script', '').strip()
            if (item is None or not item.scheduler.enable
                    or (configured != source and sender_peer != self.name)):
                self.ack(task)
                continue
            self.validate(task, source)
            config.reload()
            config.task_call(task, force_call=False)
            peers = getattr(config, '_cooperation_peers', None) or {}
            peers[task] = source
            config._cooperation_peers = peers
            self.peers[task] = source
            with self.connect() as db:
                db.execute('INSERT OR REPLACE INTO assignments VALUES (?, ?, ?)',
                           (self.name, task, source))
            self.ack(task)
            logger.info(f'Cooperation wake: {self.name}/{task} requested by {source}')
            return task
        return None

    def ack(self, task):
        with self.connect() as db:
            db.execute('DELETE FROM wakes WHERE target=? AND task=?', (self.name, task))

    def skip_without_wake(self, task, peer, reason):
        """清除双方旧唤醒；仅向心跳有效的实例发布跳过通知，不创建唤醒。"""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM wakes WHERE task=? AND '
                       '((target=? AND source=?) OR (target=? AND source=?))',
                       (task, peer, self.name, self.name, peer))
            db.execute('DELETE FROM waiting WHERE task=? AND '
                       '((name=? AND peer=?) OR (name=? AND peer=?))',
                       (task, self.name, peer, peer, self.name))
            runner = db.execute('SELECT heartbeat FROM runners WHERE name=?', (peer,)).fetchone()
            if runner and 0 <= time.time()-runner[0] < 30:
                db.execute('INSERT OR REPLACE INTO task_completions VALUES (?, ?, ?, ?, ?)',
                           (self.name, peer, task, time.time(), reason))
                logger.info(f'Cooperation skip: {self.name} -> {peer}/{task}: {reason}')

    def prepare(self, config, task, peer):
        if task == 'DraftDuel':
            from module.draft_duel_schedule import require_draft_duel_hours
            require_draft_duel_hours(config)
        self.validate(task, peer)
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM waiting WHERE name=? AND task=?', (self.name, task))
            runner = db.execute('SELECT task, heartbeat FROM runners WHERE name=?', (peer,)).fetchone()
            peer_waiting = db.execute('SELECT 1 FROM waiting WHERE name=? AND task=?',
                                      (peer, task)).fetchone()
            available = runner is not None and now - runner[1] < 30
            if available and runner[0] == task and not peer_waiting:
                db.execute('DELETE FROM waiting WHERE task=? AND name IN (?, ?)',
                           (task, self.name, peer))
                db.execute('DELETE FROM wakes WHERE task=? AND target IN (?, ?)',
                           (task, self.name, peer))
                logger.info(f'Cooperation ready: {self.name} + {peer}/{task}')
                return
            db.execute('INSERT OR REPLACE INTO wakes VALUES (?, ?, ?, ?)',
                       (peer, task, self.name, now))
            busy = not available or bool(runner[0])
            if busy:
                db.execute('INSERT OR REPLACE INTO waiting VALUES (?, ?, ?, ?)',
                           (self.name, task, peer, now))
        if busy:
            reason = runner[0] if available else '未运行'
            config.task_delay(task=task, success=False, server=False)
            target = getattr(config.model, convert_to_underscore(task)).scheduler.next_run
            config.task_runtime_outcome = {
                'task': task,
                'status': 'cooperation_waiting' if available else 'cooperation_failed',
                'success': False,
                'reason': reason if available else '目标实例未运行',
                'peer': peer,
                'wait_until': target,
            }
            if not available:
                logger.error(f'目标实例未运行：{peer}；{task} 同步失败，按任务失败间隔推迟至 {target}')
            logger.info(f'Cooperation waiting: {peer} ({reason}); {task} retry at {target}, may be woken earlier')
            raise TaskEnd
        logger.info(f'Cooperation requested: wake {peer}/{task}')


def prepare_task_cooperation(config, task):
    """Check and wake the partner as soon as the scheduler activates this task."""
    if task == 'DraftDuel':
        from module.draft_duel_schedule import require_draft_duel_hours
        require_draft_duel_hours(config)
    item = getattr(config.model, convert_to_underscore(task))
    if task == 'DraftDuel' and (item.draft_duel_config.match_mode == '单人'
                                or not item.invite_config.cooperation_script):
        return
    runtime = getattr(config, 'cooperation_runtime', None)
    peer = item.invite_config.cooperation_script
    if not peer:
        peer = (runtime.peers.get(task, '') if runtime is not None else '')
        peer = peer or (runtime.accepted_peer(task) if runtime is not None else '')
        peer = peer or (getattr(config, '_cooperation_peers', None) or {}).get(task, '')
    if peer:
        if runtime is None:
            runtime = Cooperation(config.config_name)
            config.cooperation_runtime = runtime
        runtime.peers[task] = peer
        runtime.prepare(config, task, peer)


def prepare_battle_cooperation(owner, task):
    """Fallback for direct task execution outside the scheduler."""
    if getattr(owner, '_battle_cooperation_prepared', False):
        return
    prepare_task_cooperation(owner.config, task)
    owner._battle_cooperation_task = task
    owner._battle_cooperation_prepared = True
