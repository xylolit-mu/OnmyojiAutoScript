# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey

import zerorpc
import zmq
import re
import cv2
import time
import os
import inflection
import json
import random

from datetime import date
import threading
from module.device.device import Device
from typing import Any, Callable
from datetime import datetime, timedelta
from pathlib import Path
from cached_property import cached_property
from pydantic import BaseModel, ValidationError
from threading import Thread
from multiprocessing.queues import Queue
from module.config.utils import convert_to_underscore
from module.config.config import Config
from module.device.env import IS_WINDOWS
from module.base.utils import load_module
from module.base.decorator import del_cached_property
from module.atom.scatter import RuleScatter
from module.logger import logger
from module.exception import *
from module.server.i18n import I18n
from module.image.rpc import ensure_image_server_ready
from module.ocr.rpc import (
    ensure_ocr_server_ready,
    set_ocr_logging_enabled,
    set_ocr_model_size,
    get_ocr_client,
)
from module.script import ScriptRuntimeController, ScriptRuntimeDecision
from module.cooperation import Cooperation, BATTLE_TASKS, prepare_task_cooperation
from tasks.Restart.server_update import delay_pending_tasks_for_server_update, is_server_update_window
from module.server.log_service import build_error_log_dir_name

_log_switch_lock = threading.Lock()#线程锁

# 桌面客户端连续登录恢复的容忍次数：超过就交给重启任务（或按重启开关停下），
# 避免账号被顶号时「登录→任务失败→再登录」无限循环
DESKTOP_LOGIN_RETRY_LIMIT = 3


class Script:
    def __init__(self, config_name: str ='oas') -> None:
        logger.hr('Start', level=0)
        self.server = None
        self.state_queue: Queue = None
        self._emulator_down = False
        self.runtime = ScriptRuntimeController(self)
        self.gui_update_task: Callable = None  # 回调函数, gui进程注册当每次config更新任务的时候更新gui的信息
        self.config_name = config_name
        from module.config.config_updater import ConfigUpdater
        ConfigUpdater().update_config(config_name)
        # Skip first restart
        self.is_first_task = True
        # Failure count of tasks
        # Key: str, task name, value: int, failure count
        self.failure_record = {}
        self._error_restart_count = 0
        self._error_restart_pending = False
        # 桌面掉登录后直接登录恢复（不经过重启任务）的次数与「原任务待重跑」标记
        self._desktop_login_count = 0
        self._login_retry_pending = False
        self.last_task_runtime_outcome: dict[str, Any] | None = None
        # 连续任务休息只统计调度器未进入等待状态的墙钟时间。
        self._continuous_task_started_at: float | None = None
        self._continuous_task_limit_seconds: int | None = None
        self._continuous_task_interval_range: tuple[int, int] | None = None
        # 运行loop的线程
        self.loop_thread: Thread = None
        set_ocr_model_size('medium')
        self.resource_precache_enable = bool(
            self.config.script.device.resource_precache_enable
        )

    @cached_property
    def config(self) -> "Config":
        try:
            from module.config.config import Config
            config = Config(config_name=self.config_name)
            return config
        except RequestHumanTakeover:
            logger.critical('Request human takeover')
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    @cached_property
    def device(self) -> Device | None:
        try:
            from module.device.device import Device
            device = Device(config=self.config)
            return device
        except RequestHumanTakeover:
            logger.critical('Request human takeover')
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    @cached_property
    def checker(self):
        """
        占位函数，在alas中是检查服务器是否正常的
        :return:
        """
        return None

    def save_error_log(self):
        """
        保存错误现场到 ./log/error/<script_name>_<timestamp_ms>。

        保存内容包括:
        - 最近一段截图, 文件名为时间戳 PNG。
        - 当前脚本日志的截取内容, 文件名为 log.txt。

        说明:
        - 新错误目录名会带上脚本名, 便于前端区分不同脚本产生的错误。
        - 目录名和脚本名都会经过统一净化, 避免路径注入。
        """
        from module.base.utils import save_image
        from module.handler.sensitive_info import (handle_sensitive_image,
                                                   handle_sensitive_logs)
        if self.config.script.error.save_error:
            if not os.path.exists('./log/error'):
                os.mkdir('./log/error')
            # 用统一规则生成错误目录名, 目录格式为 <script_name>_<timestamp_ms>。
            folder_name = build_error_log_dir_name(self.config_name, int(time.time() * 1000))
            folder = f'./log/error/{folder_name}'
            logger.warning(f'Saving error: {folder}')
            os.mkdir(folder)
            for data in self.device.screenshot_deque:
                image_time = datetime.strftime(data['time'], '%Y-%m-%d_%H-%M-%S-%f')
                image = handle_sensitive_image(data['image'])
                save_image(image, f'{folder}/{image_time}.png')
            with open(logger.log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                start = 0
                for index, line in enumerate(lines):
                    line = line.strip(' \r\t\n')
                    if re.match('^═{15,}$', line):
                        start = index
                lines = lines[start - 2:]
                lines = handle_sensitive_logs(lines)
            with open(f'{folder}/log.txt', 'w', encoding='utf-8') as f:
                f.writelines(lines)

    def init_server(self, port: int) -> int:
        """
        初始化zerorpc服务，返回端口号
        :return:
        """
        self.server = zerorpc.Server(self)
        try:
            self.server.bind(f'tcp://127.0.0.1:{port}')
            return port
        except zmq.error.ZMQError:
            logger.error(f"Ocr server cannot bind on port {port}")
            return None

    def run_server(self) -> None:
        """
        启动zerorpc服务
        :return:
        """
        self.server.run()

    def gui_args(self, task: str) -> str:
        """
        获取给gui显示的参数
        :return:
        """
        return self.config.gui_args(task=task)

    def gui_menu(self) -> str:
        """
        获取给gui显示的菜单
        :return:
        """
        return self.config.gui_menu

    def gui_task(self, task: str) -> str:
        """
        获取给gui显示的任务 的参数的具体值
        :return:
        """
        return self.config.model.gui_task(task=task)

    def gui_set_task(self, task: str, group: str, argument: str, value) -> bool:
        """
        设置给gui显示的任务 的参数的具体值
        :return:
        """
        # 验证参数
        task = convert_to_underscore(task)
        group = convert_to_underscore(group)
        argument = convert_to_underscore(argument)
        # pandtic验证
        if isinstance(value, str):
            if len(value) == 8:
                try:
                    value = datetime.strptime(value, '%H:%M:%S').time()
                except ValueError:
                    pass


        path = f'{task}.{group}.{argument}'
        task_object = getattr(self.config.model, task, None)
        group_object = getattr(task_object, group, None)
        argument_object = getattr(group_object, argument, None)

        if argument_object is None:
            logger.error(f'Set arg {task}.{group}.{argument}.{value} failed')
            return False

        try:
            setattr(group_object, argument, value)
            argument_object = getattr(group_object, argument, None)
            logger.info(f'Set arg {task}.{group}.{argument}.{argument_object}')
            self.config.save()  # 我是没有想到什么方法可以使得属性改变自动保存的
            return True
        except ValidationError as e:
            logger.error(e)
            return False

    @zerorpc.stream
    def gui_mirror_image(self):
        """
        获取给gui显示的镜像
        :return: cv2的对象将 numpy 数组转换为字节串。接下来MsgPack 进行序列化发送方将图像数据转换为字节串
        """
        # return msgpack.packb(cv2.imencode('.jpg', self.device.screenshot())[1].tobytes())
        img = cv2.cvtColor(self.device.screenshot(), cv2.COLOR_RGB2BGR)
        self.device.stuck_record_clear()
        ret, buffer = cv2.imencode('.jpg', img)
        yield buffer.tobytes()

    def _gui_update_tasks(self) -> None:
        """
        获取更新任务后 pending waiting 的任务 和 当前的任务的数据。打包给gui显示
        :return:
        """
        data = {}
        pending = []
        waiting = []
        task = {}
        if self.config.task is not None and self.config.task.next_run < datetime.now():
            task["name"] = self.config.task.command
            task["next_run"] = str(self.config.task.next_run)
        data["task"] = task

        for p in self.config.pending_task[1:]:
            item = {"name": p.command, "next_run": str(p.next_run)}
            pending.append(item)

        for w in self.config.waiting_task:
            item = {"name": w.command, "next_run": str(w.next_run)}
            waiting.append(item)


        data["pending"] = pending
        data["waiting"] = waiting

        if self.gui_update_task is not None:
            self.gui_update_task(data)

    def _gui_set_status(self, status: str) -> None:
        """
        设置给gui显示的状态
        :param status: 可以在gui中显示的状态 有 "Init", "Empty"(不显示), "Run"(运行中), "Error", "Free"(空闲)
        :return:
        """
        data = {"status": status}
        if self.gui_update_task is not None:
            self.gui_update_task(data)

    def gui_task_list(self) -> str:
        """
        获取给gui显示的任务列表
        :return:
        """
        result = {}
        for key, value in self.config.model.dict().items():
            if isinstance(value, str):
                continue
            if key == "restart":
                continue
            if "scheduler" not in value:
                continue

            scheduler = value["scheduler"]
            item = {"enable": scheduler["enable"],
                    "next_run": str(scheduler["next_run"])}
            key = self.config.model.type(key)
            result[key] = item
        return json.dumps(result)

    def wait_until(self, future):
        """
        Wait until a specific time.

        Args:
            future (datetime):

        Returns:
            bool: True if wait finished, False if config changed.
        """
        future = future + timedelta(seconds=1)
        self.config.start_watching()
        while 1:
            if datetime.now() > future:
                return True
            # if self.stop_event is not None:
            #     if self.stop_event.is_set():
            #         logger.info("Update event detected")
            #         logger.info(f"[{self.config_name}] exited. Reason: Update")
            #         exit(0)

            time.sleep(5)

            cooperation = self.__dict__.get('_cooperation')
            if cooperation is not None and cooperation.requested():
                return False
            if self.config.should_reload():
                return False

    @staticmethod
    def _in_sleep_window(t, start, end) -> bool:
        if start == end:
            return False
        if start < end:
            return start <= t < end
        return t >= start or t < end

    @staticmethod
    def _next_time_point(now: datetime, end) -> datetime:
        candidate = now.replace(hour=end.hour, minute=end.minute, second=end.second, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    @staticmethod
    def _parse_continuous_task_interval(value: str) -> tuple[int, int]:
        """解析连续任务休息间隔，格式为“最小分钟,最大分钟”。"""
        matched = re.fullmatch(r'\s*(\d+)\s*[,，]\s*(\d+)\s*', str(value))
        if matched is None:
            raise ValueError(f'Invalid continuous task rest interval: {value!r}')
        lower, upper = (int(item) for item in matched.groups())
        if lower <= 0 or upper <= 0 or lower > upper:
            raise ValueError(f'Invalid continuous task rest interval: {value!r}')
        return lower, upper

    @staticmethod
    def _sample_continuous_task_rest_seconds() -> tuple[int, str]:
        """休息时长：90% 落在2-8分钟的对称三角分布，其余10% 在8-20分钟内逐渐降低概率。"""
        if random.random() < 0.9:
            minutes = random.triangular(2, 8, 5)
            branch = 'short'
        else:
            minutes = random.triangular(8, 20, 8)
            branch = 'long_tail'
        return max(120, min(1200, round(minutes * 60))), branch

    def _reset_continuous_task_rest(self, *, log_idle: bool = False) -> None:
        if log_idle and self._continuous_task_started_at is not None:
            logger.info('Scheduler entered task waiting state; reset continuous task timer')
        self._continuous_task_started_at = None
        self._continuous_task_limit_seconds = None
        self._continuous_task_interval_range = None

    def _handle_continuous_task_rest(self) -> bool:
        """在新任务开始前处理连续运行休息，已休息时返回 True 要求重新调度。"""
        device_config = self.config.script.device
        if not device_config.continuous_task_rest_enable:
            self._reset_continuous_task_rest()
            return False

        try:
            interval_range = self._parse_continuous_task_interval(
                device_config.continuous_task_rest_interval
            )
        except ValueError as exc:
            logger.warning(f'{exc}; fallback to 60,120 minutes')
            interval_range = (60, 120)

        now = time.monotonic()
        if (
            self._continuous_task_started_at is None
            or self._continuous_task_interval_range != interval_range
        ):
            limit_minutes = random.randint(*interval_range)
            self._continuous_task_started_at = now
            self._continuous_task_limit_seconds = limit_minutes * 60
            self._continuous_task_interval_range = interval_range
            logger.info(
                'Continuous task timer started: '
                f'limit={limit_minutes}m, range={interval_range[0]}-{interval_range[1]}m'
            )
            return False

        elapsed = now - self._continuous_task_started_at
        if elapsed < self._continuous_task_limit_seconds:
            return False

        rest_seconds, branch = self._sample_continuous_task_rest_seconds()
        logger.info(
            'Continuous task limit reached: '
            f'elapsed={elapsed / 60:.1f}m, '
            f'rest={rest_seconds / 60:.1f}m, distribution={branch}'
        )
        time.sleep(rest_seconds)
        self._reset_continuous_task_rest()
        logger.info('Continuous task rest finished; reschedule pending tasks')
        return True

    def _antiban_wake_time(self, now: datetime):
        ab = self.config.script.anti_ban
        if not ab.enable:
            return None
        wake = None
        if self._in_sleep_window(now.time(), ab.sleep_start, ab.sleep_end):
            wake = self._next_time_point(now, ab.sleep_end)
        limit = ab.daily_active_limit.total_seconds()
        if limit > 0:
            if getattr(self, '_active_date', None) != now.date():
                self._active_date = now.date()
                self._active_seconds_today = 0
            rest_until = getattr(self, '_rest_until', None)
            if rest_until and now < rest_until:
                wake = max(wake, rest_until) if wake else rest_until
            elif getattr(self, '_active_seconds_today', 0) >= limit:
                self._rest_until = now + ab.long_rest_duration
                self._active_seconds_today = 0
                wake = max(wake, self._rest_until) if wake else self._rest_until
        return wake

    def get_next_task(self) -> str:
        """
        获取下一个任务的名字, 大驼峰。
        :return:
        """
        while True:
            cooperation = self.__dict__.get('_cooperation')
            wake_task = cooperation.wake_next(self.config) if cooperation is not None else None
            task = self.config.get_next()
            if wake_task and task.command != 'Restart':
                task = next(t for t in self.config.pending_task if t.command == wake_task)
            self.config.task = task
            if self.state_queue:
                self.state_queue.put({"schedule": self.config.get_schedule_data()})
            now = datetime.now()
            antiban_wake = self._antiban_wake_time(now)
            if antiban_wake is not None:
                task.next_run = max(task.next_run, antiban_wake)
            # 任务时间到了返回任务名称
            if task.next_run <= now:
                if self._handle_continuous_task_rest():
                    continue
                return task.command
            self._reset_continuous_task_rest(log_idle=True)
            # 根据策略执行等待逻辑
            decision = self.runtime.handle_wait_during_idle(task.next_run)
            if decision == ScriptRuntimeDecision.RESCHEDULE:
                logger.info('Idle wait requested scheduler refresh, reload config and reschedule')
                del_cached_property(self, "config")
            elif decision == ScriptRuntimeDecision.FAILED:
                logger.warning('Idle wait preparation failed, reload config and retry scheduling')
                del_cached_property(self, "config")

    def exception_handler(self, e: Exception, command: str) -> None:
        # 处理御魂溢出
        from tasks.Utils.post_diagnotor import PostDiagnotor, AnalyzeType
        image = getattr(self.device, 'image', None)
        # image为None则不做处理
        if image is None:
            return
        analyse_type = PostDiagnotor().handle(e=e, command=command, image=image)
        if analyse_type == AnalyzeType.SoulOverflow:
            self.config.task_call('SoulsTidy')
            time.sleep(1)

    def _reset_task_runtime_outcome(self) -> None:
        self.last_task_runtime_outcome = None
        self._login_retry_pending = False
        if 'config' in self.__dict__:
            self.config.task_runtime_outcome = None

    @property
    def recovery_pending(self) -> bool:
        """是否有排队中的恢复动作（异常重启，或登录后需重跑原任务）。"""
        return self._error_restart_pending or self._login_retry_pending

    def _set_task_runtime_outcome(self, task: str, status: str, wait_until: datetime | None = None) -> None:
        outcome = {
            'task': task,
            'status': status,
        }
        if wait_until is not None:
            outcome['wait_until'] = wait_until
        self.last_task_runtime_outcome = outcome
        if 'config' in self.__dict__:
            self.config.task_runtime_outcome = outcome

    def _capture_task_runtime_outcome(self, command: str) -> None:
        outcome = getattr(self.config, 'task_runtime_outcome', None)
        self.last_task_runtime_outcome = outcome if isinstance(outcome, dict) else None
        if self.last_task_runtime_outcome is None:
            return
        status = self.last_task_runtime_outcome.get('status')
        if status == 'server_update_delayed':
            wait_until = self.last_task_runtime_outcome.get('wait_until')
            logger.info(f'{command} runtime outcome: server_update_delayed (wait_until={wait_until})')
            if isinstance(wait_until, datetime):
                self.runtime.server_update_wait_until = wait_until
                self.runtime.server_update_wait_log_until = None
            return
        if command != 'Restart':
            return
        if status == 'recovered':
            logger.info('Restart runtime outcome: recovered')
            return
        logger.info(f'Restart runtime outcome: {status}')

    def _delay_tasks_for_server_update(self, task: str, reason: str) -> bool:
        if not is_server_update_window():
            return False

        delay_target = delay_pending_tasks_for_server_update(self.config, reason=reason)
        self._set_task_runtime_outcome(task=task, status='server_update_delayed', wait_until=delay_target)
        return True

    def run(self, command: str) -> bool:
        """
        :param command:  大写驼峰命名的任务名字
        :return:
        """
        if command == 'start' or command == 'goto_main':
            logger.error(f'Invalid command `{command}`')

        if command == 'Restart':
            self._ensure_restart_enabled()
        self._reset_task_runtime_outcome()
        from module.atom.click import RuleClick
        RuleClick.reset_task_points()
        set_ocr_logging_enabled(self.config.global_game.ocr.save_ocr_log)
        run_config = self.config
        previous_navigation_policy = getattr(run_config, 'defer_task_courtyard_return', False)
        run_config.defer_task_courtyard_return = bool(run_config.model.running_task) and command != 'GotoMain'
        try:
            cooperation = self.__dict__.get('_cooperation')
            self.config.cooperation_runtime = cooperation
            if command in BATTLE_TASKS:
                prepare_task_cooperation(self.config, command)
            self.device.screenshot()
            module_name = 'script_task'
            module_path = str(Path.cwd() / 'tasks' / command / (module_name + '.py'))
            logger.info(f'module_path: {module_path}, module_name: {module_name}')
            task_module = load_module(module_name, module_path)
            task_runner = task_module.ScriptTask(config=self.config, device=self.device)
            if command in BATTLE_TASKS:
                task_runner._battle_cooperation_prepared = True
            task_runner.run()
        except Exception as e:
            return self._handle_task_exception(e, command)
        finally:
            run_config.defer_task_courtyard_return = previous_navigation_policy
        return False

    def loop(self):
        """
        Main loop of scheduler.
        :return:
        """
        with _log_switch_lock:
            logger.set_file_logger(self.config_name, do_cleanup=True)
        start_day = date.today()
        logger.info(f'Start scheduler loop: {self.config_name}')
        self._cooperation = Cooperation(self.config_name)
        self._cooperation.start()
        if self.resource_precache_enable:
            logger.info('Resource precache enabled: warming up OCR model')
            try:
                get_ocr_client().warmup()
            except Exception as exc:
                logger.exception(exc)
                raise ScriptError('OCR model resource precache failed') from exc
            logger.info('Resource precache completed; scheduler tasks can start')
        self.config.model.running_task = ''
        self._active_seconds_today = 0
        self._active_date = date.today()
        self._rest_until = None

        # Update GUI 防呆, 读取设置并立刻显示后台模拟器到前台
        if not self.config.script.device.run_background_only and IS_WINDOWS:
            from module.device.platform2.platform_windows import minimize_by_name, show_window_by_name
            target_window_name = self.config.script.device.handle  # 在这里输入你的具体窗口名称
            if self.config.script.device.emulator_window_minimize:
                minimize_by_name(target_window_name)
                logger.info(f'重新显示: {target_window_name}')
            else:
                show_window_by_name(target_window_name)
                
        while 1:
            if date.today() > start_day:
                with _log_switch_lock:
                    logger.set_file_logger(self.config_name, do_cleanup=True)
                start_day = date.today()

            task = ""
            try:
                # Get task
                task = 'Restart' if self._error_restart_pending else self.get_next_task()
                # Skip first restart
                if self.is_first_task and task == 'Restart' and not self._error_restart_pending:
                    logger.info('Skip task `Restart` at scheduler start')
                    self.config.task_delay(task='Restart', success=True, server=True)
                    del_cached_property(self, 'config')
                    continue
                decision = self.runtime.prepare_task_execution(task)
            except Exception as e:
                self._handle_task_exception(e, task)
                # 本轮 prepare 失败,重新调度
                del_cached_property(self, 'config')
                continue

            if decision == ScriptRuntimeDecision.RESCHEDULE:
                logger.info(f'Runtime preparation for `{task}` requested reschedule, reload config and retry scheduling')
                del_cached_property(self, 'config')
                continue
            if decision == ScriptRuntimeDecision.FAILED:
                logger.warning(f'Runtime preparation for `{task}` failed, reload config and retry scheduling')
                del_cached_property(self, 'config')
                continue

            # Run
            logger.info(f'Scheduler: Start task `{task}`')
            self.device.stuck_record_clear()
            self.device.click_record_clear()
            logger.hr(task, level=0)
            self.config.model.running_task = task
            self._cooperation.publish(task)
            _task_start = datetime.now()
            RuleScatter.begin_task(task)
            try:
                success = self.run(inflection.camelize(task))
            finally:
                RuleScatter.end_task(task)
                self._cooperation.publish('')
                self._cooperation.finish(task)
                peers = getattr(self.config, '_cooperation_peers', None) or {}
                peers.pop(task, None)
            self.config.model.running_task = ''
            logger.info(f'Scheduler: End task `{task}`')
            self.is_first_task = False
            if task == 'Restart' and success:
                self._error_restart_pending = False
            if self._active_date != date.today():
                self._active_date = date.today()
                self._active_seconds_today = 0
            self._active_seconds_today += (datetime.now() - _task_start).total_seconds()

            outcome = self.last_task_runtime_outcome or {}
            if outcome.get('status') in ('cooperation_waiting', 'cooperation_failed'):
                # A missing/busy partner is a scheduling outcome, not a game recovery failure.
                del_cached_property(self, 'config')
                continue

            # Check failures
            # failed = deep_get(self.failure_record, keys=task, default=0)
            failed = self.failure_record[task] if task in self.failure_record else 0
            failed = 0 if success else failed + 1
            # deep_set(self.failure_record, keys=task, value=failed)
            self.failure_record[task] = failed
            if failed > self.config.model.restart.restart_config.error_restart_limit:
                logger.critical(f"Task `{task}` failed again after recovery.")
                logger.critical("Possible reason #1: You haven't used it correctly. "
                                "Please read the help text of the options.")
                logger.critical("Possible reason #2: There is a problem with this task. "
                                "Please contact developers or try to fix it yourself.")
                logger.critical('Request human takeover')
                # 恢复后再次失败时停止脚本。
                self.config.notifier.push(
                    title=f'{I18n.trans_zh_cn(task)}{task}',
                    content=f"<{self.config_name}> 任务恢复后再次失败，脚本已停止，请上线查看"
                )
                # 关闭模拟器
                if self.config.script.error.error_repeated:
                    self.device.emulator_stop()
                exit(1)

            if success:
                del_cached_property(self, 'config')
                continue
            elif self.config.script.error.handle_error:
                # self.config.task_delay(success=False)
                del_cached_property(self, 'config')
                # self.checker.check_now()
                continue
            else:
                break

        self._cooperation.close()

    def _ensure_restart_enabled(self) -> None:
        """所有重启入口均尊重任务启用开关，禁止绕过用户配置。"""
        if self.config.model.restart.scheduler.enable:
            return
        self._error_restart_pending = False
        logger.critical('Restart task is disabled; stop script instead of restarting game')
        self.config.notifier.push(
            title='Restart',
            content=f'<{self.config_name}> 重启任务未启用，无法自动恢复，脚本已停止，请手动处理',
        )
        raise SystemExit(1)

    def _request_error_restart(self, command: str) -> None:
        """按重启任务配置限制本次脚本运行的异常重启次数，强制优先调度。"""
        self._ensure_restart_enabled()
        limit = self.config.model.restart.restart_config.error_restart_limit
        if self._error_restart_count >= limit:
            logger.critical(f'Error restart limit reached ({self._error_restart_count}/{limit}): {command}; stop script')
            self.config.notifier.push(
                title=f'{I18n.trans_zh_cn(command)}{command}',
                content=f'<{self.config_name}> 异常重启次数已达上限 {limit} 次，脚本已停止，请上线查看',
            )
            raise SystemExit(1)
        self._error_restart_count += 1
        logger.warning(f'Error recovery restart {self._error_restart_count}/{limit}: {command}')
        self._error_restart_pending = True
        self.config.task_call('Restart', force_call=False)

    def desktop_login_recovery(self, command: str = '') -> bool:
        """桌面客户端停在登录页时直接登录，不占用重启任务。

        MPay 登录窗是独立顶层窗口，未登录时游戏画面只有开屏画、识别不到任何页面，
        登录动作只存在于此。它并不重启客户端，与「重启」开关无关；把它算成异常重启，
        关掉重启的用户每次冷启动都会直接停摆。进程/窗口真的没了才需要 Restart。
        """
        if not self.device.is_desktop:
            return False
        if not self.device.desktop_window_exists():
            return False
        if self.device.app_is_running() and not self.device.find_desktop_login_popup():
            return False
        limit = DESKTOP_LOGIN_RETRY_LIMIT
        if self._desktop_login_count >= limit:
            logger.warning(f'Desktop login recovery reached limit ({limit}), escalate to Restart')
            return False
        self._desktop_login_count += 1
        logger.info(f'Desktop client is not logged in, login directly '
                    f'({self._desktop_login_count}/{limit})')
        from tasks.Component.Login.service import LoginService
        try:
            LoginService(config=self.config, device=self.device).app_handle_login()
        except RequestHumanTakeover as e:
            self._request_human_takeover(e, command)
        except GameNotRunningError as e:
            # 登录过程中窗口被关掉：登录无从继续，退回重启路径由 Restart 重拉客户端
            logger.warning(f'Desktop client window lost during login: {e}')
            return False
        return True

    def _request_human_takeover(self, e: Exception, command: str) -> None:
        logger.critical(e)
        self.exception_handler(e=e, command=command)
        logger.critical('Request human takeover')
        self.config.notifier.push(
            title=f'{I18n.trans_zh_cn(command)}{command}',
            content=f"<{self.config_name}> RequestHumanTakeover",
        )
        exit(1)

    def _handle_task_exception(self, e: Exception, command: str) -> bool:
        """
        统一处理任务执行 / 准备阶段抛出的异常。
        Returns:
            True  -> 视为正常结束或已自动恢复 (例如已 task_call('Restart')),
                     调度器继续推进
            False -> 视为失败,脚本继续运行
        对致命异常 (ScriptError / RequestHumanTakeover / 未识别 Exception)
        在内部直接 exit(1)。
        """
        if isinstance(e, NetworkRecoveryRequired):
            # Unwind the interrupted task so no stale page state survives recovery.
            self.device.stuck_record_clear()
            self.device.click_record_clear()
            if e.reconnect:
                from tasks.Component.Login.service import LoginService
                from tasks.Restart.assets import RestartAssets
                login = LoginService(config=self.config, device=self.device)
                deadline = time.monotonic() + 30
                try:
                    while True:
                        self.device.screenshot()
                        if login.appear(RestartAssets.I_LOGIN_8):
                            break
                        if time.monotonic() >= deadline:
                            raise GameStuckError('Network reconnect did not reach login page within 30 seconds')
                        login.appear_then_click(login.I_CLIENT_CLEAR, interval=1)
                        self.device.sleep(0.25)
                    login.app_handle_login()
                except Exception as recovery_error:
                    logger.warning(f'Network reconnect failed: {recovery_error}')
                    self._request_error_restart(command)
            else:
                self._request_error_restart(command)
            if command != 'Restart':
                self.config.task_call(command)
            return True

        if isinstance(e, TaskEnd):
            self._capture_task_runtime_outcome(command)
            outcome = self.last_task_runtime_outcome or {}
            if outcome.get('status') != 'cooperation_failed':
                # 任务正常收尾，说明登录态是稳的，桌面登录恢复的容忍次数重新计数
                self._desktop_login_count = 0
            return outcome.get('status') != 'cooperation_failed'

        if isinstance(e, GameNotRunningError):
            logger.warning(e)
            self.exception_handler(e=e, command=command)
            if self.desktop_login_recovery(command):
                # 登录完成，回调度器重跑原任务（GotoMain 由 require_main 重新驱动）
                self._login_retry_pending = True
                return True
            self._request_error_restart(command)
            return True

        if isinstance(e, (GameStuckError, GameTooManyClickError)):
            logger.error(e)
            self.save_error_log()
            self.exception_handler(e=e, command=command)
            logger.warning(f'Game stuck, {self.device.package} will be restarted in 10 seconds')
            logger.warning('If you are playing by hand, please stop Alas')
            self.config.notifier.push(title=f'{I18n.trans_zh_cn(command)}{command}',
                                      content=f"<{self.config_name}> GameStuckError or GameTooManyClickError")
            self._request_error_restart(command)
            self.device.sleep(10)
            return False

        if isinstance(e, GameBugError):
            logger.warning(e)
            self.save_error_log()
            self.exception_handler(e=e, command=command)
            logger.warning('An error has occurred in Azur Lane game client, Alas is unable to handle')
            logger.warning(f'Restarting {self.device.package} to fix it')
            self._request_error_restart(command)
            self.device.sleep(10)
            return False

        if isinstance(e, GamePageUnknownError):
            logger.info('Game server may be under maintenance or network may be broken, check server status now')
            if command == 'GotoMain' and self._delay_tasks_for_server_update(
                    task=command,
                    reason='failed to goto main during morning server update window',
            ):
                logger.info('GotoMain failed during server update window, delayed pending tasks and reschedule')
                return False
            logger.critical('Game page unknown')
            self.save_error_log()
            self.exception_handler(e=e, command=command)
            self.config.notifier.push(
                title=f'{I18n.trans_zh_cn(command)}{command}',
                content=f"<{self.config_name}> GamePageUnknownError",
            )
            self._request_error_restart(command)
            self.device.sleep(10)
            return False

        if isinstance(e, ScriptError):
            logger.critical(e)
            self.exception_handler(e=e, command=command)
            logger.critical('This is likely to be a mistake of developers, but sometimes just random issues')
            self.config.notifier.push(
                title=f'{I18n.trans_zh_cn(command)}{command}',
                content=f"<{self.config_name}> ScriptError",
            )
            exit(1)

        if isinstance(e, RequestHumanTakeover):
            self._request_human_takeover(e, command)

        # generic
        logger.exception(e)
        self.exception_handler(e=e, command=command)
        self.save_error_log()
        self.config.notifier.push(
            title=f'{I18n.trans_zh_cn(command)}{command}',
            content=f"<{self.config_name}> Exception occured",
        )
        exit(1)
        return False

    def start_loop(self) -> None:
        """
        创建一个线程，运行loop
        :return:
        """
        if self.loop_thread is None:
            self.loop_thread = Thread(target=self.loop, name='Script_loop')
            self.loop_thread.start()


if __name__ == "__main__":
    ensure_image_server_ready()
    ensure_ocr_server_ready()
    script = Script("oas1")
    script.loop()
