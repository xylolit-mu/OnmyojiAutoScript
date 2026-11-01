import ctypes
import os
import re
import subprocess
from dataclasses import dataclass, field

import psutil
from adbutils import AdbDevice, AdbClient
from filelock import FileLock

from deploy.utils import DataProcessInfo
from module.base.timer import Timer
from module.device.handle import Handle
from module.device.platform2.platform_base import PlatformBase
from module.device.platform2.emulator_windows import EmulatorInstance, EmulatorManager
from module.logger import logger

from ctypes import wintypes


class EmulatorUnknown(Exception):
    pass


def minimize_by_name(window_name, convert_hidden=True):
    """
    按名称处理窗口状态
    Args:
        window_name (str): 窗口名称（支持部分匹配）
        convert_hidden (bool): 是否将隐藏窗口改为最小化
    """
    def callback(hwnd, lParam):
        title = get_window_title(hwnd)
        if window_name.lower() in title.lower():
            # 检查窗口当前状态
            is_visible = ctypes.windll.user32.IsWindowVisible(hwnd)
            
            if is_visible:
                # 可见窗口 → 最小化
                minimize_window(hwnd)
                logger.info(f'最小化可见窗口: {title}')
            elif convert_hidden:
                # 隐藏窗口 → 改为最小化不激活
                ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_SHOWMINNOACTIVE
                logger.info(f'隐藏窗口改为最小化: {title}')
        return True
    
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, ctypes.POINTER(ctypes.c_int))
    ctypes.windll.user32.EnumWindows(WNDENUMPROC(callback), None)


def find_hwnd_by_name(window_name):
    """
    枚举所有窗口，返回第一个匹配名称的 hwnd
    """
    target = None
    def callback(hwnd, lParam):
        title = get_window_title(hwnd)
        if window_name.lower() in title.lower():
            nonlocal target
            target = hwnd
            return False  # 停止枚举
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, ctypes.POINTER(ctypes.c_int))
    ctypes.windll.user32.EnumWindows(WNDENUMPROC(callback), None)
    return target


def show_window_by_name(window_name):
    """
    显示指定名称的窗口
    Args:
        window_name (str): 窗口名称（支持部分匹配）
    """
    hwnd = find_hwnd_by_name(window_name)
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
        set_focus_window(hwnd)
        logger.info(f'显示窗口: {window_name}')
    else:
        logger.info(f'没有找到窗口: {window_name}')


def get_focused_window():
    return ctypes.windll.user32.GetForegroundWindow()


def set_focus_window(hwnd):
    ctypes.windll.user32.SetForegroundWindow(hwnd)


def minimize_window(hwnd):
    ctypes.windll.user32.ShowWindow(hwnd, 6)


def hide_window(hwnd):
    ctypes.windll.user32.ShowWindow(hwnd, 0)


def get_window_title(hwnd):
    """Returns the window title as a string."""
    text_len_in_characters = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    string_buffer = ctypes.create_unicode_buffer(
        text_len_in_characters + 1)  # +1 for the \0 at the end of the null-terminated string.
    ctypes.windll.user32.GetWindowTextW(hwnd, string_buffer, text_len_in_characters + 1)
    return string_buffer.value


def flash_window(hwnd, flash=True):
    ctypes.windll.user32.FlashWindow(hwnd, flash)


class AdbDeviceWithStatus(AdbDevice):
    def __init__(self, client: AdbClient, serial: str, status: str):
        self.status = status
        super().__init__(client, serial)

    def __str__(self):
        return f'AdbDevice({self.serial}, {self.status})'

    __repr__ = __str__

    def __bool__(self):
        return True


@dataclass
class EmulatorStartWatchState:
    serial: str
    current_window: int
    interval: Timer
    timeout: Timer
    struct_window: Timer
    launch_confirm: Timer | None
    window_hidden: bool = False
    new_window: int = 0
    logged_events: set[str] = field(default_factory=set)


class PlatformWindows(PlatformBase, EmulatorManager):
    LIFECYCLE_LOCK_FILE = os.path.abspath('./log/emulator_lifecycle.lock')

    @classmethod
    def build_startupinfo(cls, show_window=True):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        if not show_window:
            startupinfo.wShowWindow = 0
        else:
            startupinfo.wShowWindow = 1

        return startupinfo

    @classmethod
    def normalize_command(cls, command: str) -> str:
        return command.replace(r"\\", "/").replace("\\", "/").replace('"', '"')

    @classmethod
    def execute(cls, command, show_window=True):
        """
        Args:
            command (str):

        Returns:
            subprocess.Popen:
        """
        startupinfo = cls.build_startupinfo(show_window=show_window)
        command = cls.normalize_command(command)
        logger.info(f'Execute: {command}')
        return subprocess.Popen(
            command,
            close_fds=True,
            startupinfo=startupinfo
        )

    @classmethod
    def execute_output(
            cls,
            command: str,
            timeout: int = 15,
            show_window: bool = False,
            encoding: str | None = None,
    ) -> subprocess.CompletedProcess:
        startupinfo = cls.build_startupinfo(show_window=show_window)
        command = cls.normalize_command(command)
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding=encoding,
            errors='replace' if encoding else None,
            timeout=timeout,
            startupinfo=startupinfo,
            close_fds=True,
        )

    @classmethod
    def kill_process_by_regex(cls, regex: str) -> int:
        """
        Kill processes with cmdline match the given regex.

        Args:
            regex:

        Returns:
            int: Number of processes killed
        """
        count = 0

        for proc in psutil.process_iter():
            cmdline = DataProcessInfo(proc=proc, pid=proc.pid).cmdline
            if re.search(regex, cmdline):
                logger.info(f'Kill emulator: {cmdline}')
                proc.kill()
                count += 1

        return count

    @classmethod
    def emulator_lifecycle_lock(cls) -> FileLock:
        os.makedirs(os.path.dirname(cls.LIFECYCLE_LOCK_FILE), exist_ok=True)
        return FileLock(cls.LIFECYCLE_LOCK_FILE)

    @classmethod
    def list_adb_device_status(cls) -> dict[str, str]:
        """
        读取当前 ADB 服务中的设备状态列表。

        Returns:
            dict[str, str]: 键为设备 serial，值为 adb 状态。
        """
        host = '127.0.0.1'
        port = 5037
        env = os.environ.get('ANDROID_ADB_SERVER_PORT')
        if env is not None:
            try:
                port = int(env)
            except ValueError:
                logger.warning(f'Invalid environ variable ANDROID_ADB_SERVER_PORT={env}, using default port')

        devices = {}
        try:
            client = AdbClient(host, port)
            with client._connect() as c:
                c.send_command('host:devices')
                c.check_okay()
                output = c.read_string_block()
                for line in output.splitlines():
                    parts = line.strip().split('\t')
                    if len(parts) != 2:
                        continue
                    devices[parts[0]] = parts[1]
        except Exception as e:
            logger.info(f'Probe adb devices failed: {e}')

        return devices

    def refresh_target_instance(self, reason: str = ''):
        instance = self.refresh_emulator_instance(reason=reason)
        if instance is None:
            logger.error('[emu-instance] target instance not found')
        return instance

    def probe_target_instance_online(self) -> bool | None:
        """
        轻量探测当前目标模拟器是否已经在线，避免为判断状态初始化完整设备对象。

        Returns:
            bool | None:
                True 表示目标模拟器在线；
                False 表示目标模拟器不在线；
                None 表示当前无法准确判断。
        """
        from module.device.method.utils import get_serial_pair

        instance = self.emulator_instance
        if instance is None:
            logger.warning('Probe target emulator failed: target instance not found')
            return None

        devices = self.list_adb_device_status()
        status = devices.get(instance.serial)
        if status == 'device':
            return True

        port_serial, emu_serial = get_serial_pair(instance.serial)
        if port_serial and devices.get(port_serial) == 'device':
            return True
        if emu_serial and devices.get(emu_serial) == 'device':
            return True

        return False

    def is_instance_online(self, instance: EmulatorInstance, log_prefix: str = '[emu-start]') -> bool:
        try:
            devices = self.list_device().select(serial=instance.serial)
        except Exception as e:
            logger.warning(f'{log_prefix} online check failed: serial={instance.serial}, error={e}')
            return False

        if not devices:
            logger.info(f'{log_prefix} serial not in adb: serial={instance.serial}')
            return False

        device: AdbDeviceWithStatus = devices.first_or_none()
        logger.info(f'{log_prefix} adb status: serial={instance.serial}, status={device.status}')
        return device.status == 'device'

    def _get_handler(self, instance: EmulatorInstance):
        from module.device.platform2.handlers import get_handler
        return get_handler(instance.type)

    def _build_emulator_watch_state(self, instance: EmulatorInstance) -> EmulatorStartWatchState:
        """
        构建模拟器启动监视所需的运行状态。
        """
        handler = self._get_handler(instance)
        launch_confirm = handler.build_launch_confirm_timer(instance) if handler else None
        state = EmulatorStartWatchState(
            serial=instance.serial,
            current_window=get_focused_window(),
            interval=Timer(1).start(),
            timeout=Timer(120).start(),
            struct_window=Timer(10),
            launch_confirm=launch_confirm,
        )
        state._platform = self
        state._handler = handler
        return state

    def _log_emulator_watch_once(
        self,
        state: EmulatorStartWatchState,
        key: str,
        level: str,
        message: str
    ) -> None:
        """
        在单次启动监视流程中只打印一次日志。

        Args:
            state (EmulatorStartWatchState): 启动监视状态对象。
            key (str): 日志去重标识。
            level (str): logger 对应的日志级别方法名。
            message (str): 要输出的日志内容。

        Returns:
            None: 该方法仅负责日志输出，不返回有效结果。
        """
        if key in state.logged_events:
            return
        state.logged_events.add(key)
        getattr(logger, level)(message)

    def _wait_emulator_watch_tick(self, state: EmulatorStartWatchState) -> bool:
        """
        等待下一轮启动监视轮询，并处理整体超时。

        Args:
            state (EmulatorStartWatchState): 启动监视状态对象。

        Returns:
            bool: 未超时返回 True，达到总超时返回 False。
        """
        state.interval.wait()
        state.interval.reset()
        if state.timeout.reached():
            logger.warning('Emulator start timeout')
            return False
        return True

    def _check_handler_launch_state(
        self,
        instance: EmulatorInstance,
        state: EmulatorStartWatchState
    ) -> tuple[str, dict | None]:
        """
        通过 Handler 检查启动流程是否已经真正拉起。
        """
        handler = getattr(state, '_handler', None)
        if handler is None:
            return 'ready', None
        return handler.check_launch_state(instance, state)

    def _hide_emulator_window_if_needed(
        self,
        instance: EmulatorInstance,
        state: EmulatorStartWatchState,
        player_info: dict | None
    ) -> None:
        """
        在仅后台运行模式下尝试隐藏模拟器窗口（委托给 Handler）。
        """
        if not self.config.script.device.run_background_only or state.window_hidden:
            return
        handler = getattr(state, '_handler', None)
        if handler is None:
            return
        if player_info is None:
            player_info = handler.query_player_info(instance, self)
        if not handler.try_hide_window(instance, self, info=player_info):
            return

        self._log_emulator_watch_once(
            state,
            'hidden_window',
            'info',
            f'[emu-start] hide instance window: serial={state.serial}'
        )
        state.window_hidden = True

    def _track_emulator_focus_window(self, state: EmulatorStartWatchState) -> None:
        """
        跟踪新弹出的模拟器窗口，并在需要时恢复原焦点。

        Args:
            state (EmulatorStartWatchState): 启动监视状态对象。

        Returns:
            None: 该方法仅更新窗口句柄状态，不返回有效结果。
        """
        if state.current_window == 0 or state.new_window != 0:
            return

        state.new_window = get_focused_window()
        if state.current_window == state.new_window:
            state.new_window = 0
            return
        if self.config.script.device.emulator_window_minimize or self.config.script.device.run_background_only:
            state.new_window = 0
            return

        logger.info(f'New window showing up: {state.new_window}, focus back')
        set_focus_window(state.current_window)

    def _try_connect_emulator_adb(self, serial: str) -> bool:
        """
        尝试主动连接模拟器对应的 ADB 端口。

        Args:
            serial (str): 模拟器实例的 ADB 序列号。

        Returns:
            bool: 返回 ADB connect 是否给出了非预期提示信息。
        """
        message = self.adb_client.connect(serial)
        if 'connected' in message:
            return False
        if '(10061)' in message:
            return False
        return True

    def _ensure_emulator_device_ready(self, state: EmulatorStartWatchState) -> bool:
        """
        检查模拟器是否已经在 ADB 设备列表中可用。

        Args:
            state (EmulatorStartWatchState): 启动监视状态对象。

        Returns:
            bool: 设备可继续后续检查时返回 True，否则返回 False。
        """
        logger.info(f'Try to connect emulator, remain[{state.timeout.remain():.1f}s]')
        try:
            devices = self.list_device().select(serial=state.serial)
            if not devices:
                self._try_connect_emulator_adb(state.serial)
                return False

            device: AdbDeviceWithStatus = devices.first_or_none()
            if device.status == 'offline':
                self.adb_client.disconnect(state.serial)
                self._try_connect_emulator_adb(state.serial)
                return False

            self._log_emulator_watch_once(state, 'online', 'info', f'Emulator online: {device}')
            return True
        except Exception as e:
            self._log_emulator_watch_once(
                state,
                'adb_transient',
                'warning',
                f'[emu-start] transient adb error, keep waiting: serial={state.serial}, error={e}'
            )
            return False

    def _ensure_emulator_command_ready(self, state: EmulatorStartWatchState) -> bool:
        """
        检查模拟器 ADB shell 命令是否已经可用。

        Args:
            state (EmulatorStartWatchState): 启动监视状态对象。

        Returns:
            bool: 命令执行成功返回 True，否则返回 False。
        """
        try:
            pong = self.adb_shell(['echo', 'pong'])
        except Exception as e:
            logger.info(e)
            return False

        self._log_emulator_watch_once(state, 'ping', 'info', f'Command ping: {pong}')
        return True

    def _ensure_emulator_package_ready(self, state: EmulatorStartWatchState) -> bool:
        """
        检查目标游戏包是否已经可以从模拟器中查询到。

        Args:
            state (EmulatorStartWatchState): 启动监视状态对象。

        Returns:
            bool: 查到包列表返回 True，否则返回 False。
        """
        packages = self.list_app_packages(show_log=False)
        if not packages:
            return False

        self._log_emulator_watch_once(state, 'package', 'info', f'Found azurlane packages: {packages}')
        return True

    def _is_emulator_window_ready(self, state: EmulatorStartWatchState) -> bool:
        """
        检查模拟器窗口结构是否已经稳定到可继续执行。

        Args:
            state (EmulatorStartWatchState): 启动监视状态对象。

        Returns:
            bool: 窗口结构满足要求时返回 True，否则返回 False。
        """
        if not state.struct_window.started():
            state.struct_window.start()
            return False
        if state.struct_window.reached():
            return True
        if state.new_window == 0:
            return False
        if not Handle.handle_has_children(hwnd=state.new_window):
            return False
        return True

    def _finalize_emulator_window(self, state: EmulatorStartWatchState) -> None:
        """
        在启动成功后处理窗口最小化和后台运行提示。

        Args:
            state (EmulatorStartWatchState): 启动监视状态对象。

        Returns:
            None: 该方法仅执行启动后的窗口处理，不返回有效结果。
        """
        emulator_window_minimize = self.config.script.device.emulator_window_minimize
        if emulator_window_minimize:
            logger.info(f'Minimize new emulator window: {emulator_window_minimize}')
        if self.config.script.device.run_background_only:
            logger.info(f'run background only: {self.config.script.device.run_background_only}')
            logger.warning('run_background_only will not show any UI, emulator will run background only')
            return
        if not emulator_window_minimize:
            return

        sleep_time = 3
        logger.info(f'Waiting {sleep_time} seconds before minimizing window')
        Timer(sleep_time).wait()
        target_window_name = self.config.script.device.handle
        minimize_by_name(target_window_name)
        logger.info(f'最小化窗口: {target_window_name}')

    def _emulator_start(self, instance: EmulatorInstance):
        """
        Start a emulator without error handling (delegates to Handler)
        """
        handler = self._get_handler(instance)
        if handler is None:
            raise EmulatorUnknown(f'Cannot start an unknown emulator instance: {instance}')

        cmd = handler.build_start_command(instance)
        if cmd is None:
            raise EmulatorUnknown(f'Handler returned no start command for: {instance}')

        show_window = (
            not self.config.script.device.emulator_window_minimize
            and not self.config.script.device.run_background_only
            and handler.start_show_window()
        )
        result = handler.start_instance(instance, self, cmd, show_window=show_window)
        if result is not None:
            return result
        self.execute(cmd, show_window=show_window)

    def _wait_emulator_stop(self, instance: EmulatorInstance, handler) -> bool:
        """等待选择了关闭确认能力的 Handler 报告目标实例已停止。"""
        confirm_timer = handler.build_stop_confirm_timer(instance)
        if confirm_timer is None:
            return True

        logger.info(f'[emu-stop] wait confirmation: serial={instance.serial}')
        last_state = None
        while True:
            current_state = handler.check_stop_state(instance, self)
            if current_state != last_state:
                logger.info(f'[emu-stop] state: serial={instance.serial}, state={current_state}')
                last_state = current_state
            if current_state == 'stopped':
                logger.info(f'[emu-stop] stop completed: serial={instance.serial}')
                return True
            if confirm_timer.reached():
                logger.warning(
                    f'[emu-stop] confirmation timeout: serial={instance.serial}, state={current_state}'
                )
                return False
            Timer(1).wait()

    def _emulator_stop(self, instance: EmulatorInstance):
        """
        Stop a emulator without error handling (delegates to Handler)
        """
        handler = self._get_handler(instance)
        if handler is None:
            raise EmulatorUnknown(f'Cannot stop an unknown emulator instance: {instance}')

        kill_regex = handler.stop_by_kill(instance)
        if kill_regex:
            self.kill_process_by_regex(kill_regex)
            return True

        cmd = handler.build_stop_command(instance)
        if cmd is None:
            raise EmulatorUnknown(f'Handler returned no stop command for: {instance}')
        self.execute(cmd)
        return self._wait_emulator_stop(instance, handler)

    def _emulator_function_wrapper(self, func: callable, instance: EmulatorInstance = None):
        """
        Args:
            func (callable): _emulator_start or _emulator_stop

        Returns:
            bool: If success
        """
        if instance is None:
            instance = self.emulator_instance
        if instance is None:
            logger.error(f'Emulator function {func.__name__}() failed because no target instance was found')
            return False
        try:
            result = func(instance)
            return result is not False
        except OSError as e:
            msg = str(e)
            # OSError: [WinError 740] 请求的操作需要提升。
            if 'WinError 740' in msg:
                logger.error('To start/stop MumuAppPlayer, ALAS needs to be run as administrator')
        except EmulatorUnknown as e:
            logger.error(e)
        except Exception as e:
            logger.exception(e)

        logger.error(f'Emulator function {func.__name__}() failed')
        return False

    def emulator_start_watch(self, instance: EmulatorInstance = None):
        """
        Returns:
            bool: True if startup completed
                False if timeout, unexpected stop, adb preemptive
        """
        logger.hr('Emulator start', level=2)
        if instance is None:
            instance = self.emulator_instance
        if instance is None:
            logger.error('[emu-start] watch failed: target instance not found')
            return False
        state = self._build_emulator_watch_state(instance)
        logger.info(f'Current window: {state.current_window}')
        while 1:
            if not self._wait_emulator_watch_tick(state):
                return False

            launch_state, player_info = self._check_handler_launch_state(instance, state)
            if launch_state == 'fail':
                return False
            if launch_state == 'wait':
                continue
                if launch_state == 'unknown':
                    self._log_emulator_watch_once(
                        state,
                        'launch_state_unknown',
                        'warning',
                        f'[emu-start] launch state unknown, fallback to readiness checks: serial={state.serial}'
                    )

            self._hide_emulator_window_if_needed(instance, state, player_info)
            self._track_emulator_focus_window(state)
            if not self._ensure_emulator_device_ready(state):
                continue
            if not self._ensure_emulator_command_ready(state):
                continue
            if not self._ensure_emulator_package_ready(state):
                continue
            if self._is_emulator_window_ready(state):
                break

        self._finalize_emulator_window(state)
        logger.info('Emulator start completed')
        return True

    def emulator_start(self):
        logger.hr('Emulator start', level=1)
        max_attempts = 3
        for i in range(max_attempts):
            attempt = i + 1
            logger.info(f'[emu-start] lock wait: attempt={attempt}/{max_attempts}')
            with self.emulator_lifecycle_lock():
                logger.info(f'[emu-start] lock acquired: attempt={attempt}/{max_attempts}')
                instance = self.refresh_target_instance(reason=f'pre-start refresh attempt={attempt}')
                if instance is None:
                    logger.info('[emu-start] lock release: target instance not found')
                    return False

                if self.is_instance_online(instance):
                    logger.info(f'[emu-start] already online: serial={instance.serial}')
                    logger.info('[emu-start] lock release: already online')
                    return True

                if attempt > 1:
                    logger.warning(
                        f'[emu-start] retry with stop: serial={instance.serial}, '
                        f'attempt={attempt}/{max_attempts}'
                    )
                    if not self._emulator_function_wrapper(self._emulator_stop, instance):
                        logger.info('[emu-start] lock release: stop failed')
                        return False
                    instance = self.refresh_target_instance(reason=f'post-stop refresh attempt={attempt}')
                    if instance is None:
                        logger.info('[emu-start] lock release: target missing after stop')
                        return False

                logger.info(
                    f'[emu-start] start target: serial={instance.serial}, name={instance.name or "<default>"}, '
                    f'type={instance.type}, attempt={attempt}/{max_attempts}'
                )
                if not self._emulator_function_wrapper(self._emulator_start, instance):
                    logger.warning(
                        f'[emu-start] start command failed: serial={instance.serial}, '
                        f'attempt={attempt}/{max_attempts}'
                    )
                    logger.info('[emu-start] lock release: start failed')
                    if attempt >= max_attempts:
                        logger.warning(
                            f'[emu-start] final attempt failed, skip recovery stop: serial={instance.serial}'
                        )
                        break
                    continue
                logger.info(
                    f'[emu-start] start submitted: serial={instance.serial}, '
                    f'attempt={attempt}/{max_attempts}'
                )
                logger.info('[emu-start] lock release: start submitted')

            if self.emulator_start_watch(instance):
                return True
            if attempt >= max_attempts:
                logger.warning(
                    f'[emu-start] final attempt failed, skip recovery stop: serial={instance.serial}'
                )
                break
            logger.attr(max_attempts - attempt, 'Failed to connect or start, try again')

        logger.error(f'Failed to start emulator after {max_attempts} attempts')
        return False

    def emulator_stop(self):
        # 桌面客户端无模拟器生命周期，空闲关闭改走关闭客户端。
        # desktop_stop_client 内部已做「窗口消失 且 进程退出」验证并多轮重杀，直接用其结论
        if getattr(self, 'is_desktop', False):
            return self.desktop_stop_client()
        logger.hr('Emulator stop', level=1)
        max_attempts = 3
        for i in range(max_attempts):
            attempt = i + 1
            logger.info(f'[emu-stop] lock wait: attempt={attempt}/{max_attempts}')
            with self.emulator_lifecycle_lock():
                logger.info(f'[emu-stop] lock acquired: attempt={attempt}/{max_attempts}')
                instance = self.refresh_target_instance(reason='pre-stop refresh')
                if instance is None:
                    logger.info('[emu-stop] lock release: target instance not found')
                    return False
                # Stop
                if self._emulator_function_wrapper(self._emulator_stop, instance):
                    logger.info(f'[emu-stop] stop submitted: serial={instance.serial}')
                    logger.info('[emu-stop] lock release: stop ok')
                    return True
                if attempt >= max_attempts:
                    logger.warning(
                        f'[emu-stop] final attempt failed, skip recovery start: serial={instance.serial}'
                    )
                    logger.info('[emu-stop] lock release: final attempt failed')
                    break
                # Failed to stop, start and stop again
                if self._emulator_function_wrapper(self._emulator_start, instance):
                    logger.warning(
                        f'[emu-stop] stop failed, retry after start: serial={instance.serial}, '
                        f'next_attempt={attempt + 1}/{max_attempts}'
                    )
                    logger.info('[emu-stop] lock release: retry')
                    continue
                logger.info('[emu-stop] lock release: stop/start failed')
                return False

        logger.error(f'Failed to stop emulator after {max_attempts} attempts')
        return False


if __name__ == '__main__':
    self = PlatformWindows()
    d = self.emulator_instance
    print(d)
