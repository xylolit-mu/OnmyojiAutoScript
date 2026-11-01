import ctypes
import json
import os
import re
import typing as t

from module.device.platform2.handlers.base import EmulatorHandler
from module.logger import logger


class MuMu12Handler(EmulatorHandler):
    """现代 MuMu（Android 12/15）Handler，保留原配置类型以兼容旧配置。"""
    MuMuPlayer12 = 'MuMuPlayer12'
    INSTANCE_NAME_PATTERNS = (
        re.compile(r'MuMuPlayer(?:Global)?-\d+(?:\.\d+)*-(\d+)'),
        re.compile(r'YXArkNights-\d+(?:\.\d+)*-(\d+)'),
    )

    @staticmethod
    def type_names() -> list[str]:
        return ['MuMuPlayer12']

    @staticmethod
    def path_to_type(path: str, exe: str, dir1: str, dir2: str) -> str:
        if exe in ['mumuplayer.exe', 'mumunxmain.exe']:
            return 'MuMuPlayer12'
        return ''

    @staticmethod
    def multi_to_single(exe: str) -> list[str]:
        if 'MuMuMultiPlayer.exe' in exe:
            return [exe.replace('MuMuMultiPlayer.exe', 'MuMuPlayer.exe')]
        if 'MuMuManager.exe' in exe:
            return [exe.replace('MuMuManager.exe', 'MuMuPlayer.exe')]
        return []

    @staticmethod
    def single_to_console(exe: str) -> t.Optional[str]:
        if 'MuMuPlayer.exe' in exe:
            return exe.replace('MuMuPlayer.exe', 'MuMuManager.exe')
        if 'MuMuNxMain.exe' in exe:
            return exe.replace('MuMuNxMain.exe', 'MuMuManager.exe')
        return None

    def get_instance_id(self, instance) -> t.Optional[int]:
        name = getattr(instance, 'name', '')
        if not isinstance(name, str):
            return None
        for pattern in self.INSTANCE_NAME_PATTERNS:
            res = pattern.fullmatch(name)
            if res:
                return int(res.group(1))
        return None

    def _resolve_console(self, instance, log_warning: bool = True) -> t.Optional[str]:
        emulator = getattr(instance, 'emulator', None)
        exe = getattr(emulator, 'path', '')
        console = self.single_to_console(exe) if isinstance(exe, str) else None
        if not console:
            if log_warning:
                logger.warning(f'Cannot resolve MuMu control executable from path {exe}')
            return None
        if not os.path.isfile(console):
            if log_warning:
                logger.warning(f'MuMu control executable does not exist: {console}')
            return None
        return console

    def iter_instances(self, emulator) -> t.Iterable:
        from module.device.platform2.emulator_windows import EmulatorInstance, Emulator
        from module.device.platform2.utils import iter_folder

        # vms/MuMuPlayer-12.0-0 or vms/MuMuPlayer-15.0-0
        for folder in emulator.list_folder('../vms', is_dir=True):
            found_nemu = False
            for file in iter_folder(folder, ext='.nemu'):
                found_nemu = True
                serial = Emulator.vbox_file_to_serial(file)
                name = os.path.basename(folder)
                if serial:
                    yield EmulatorInstance(
                        serial=serial,
                        name=name,
                        path=emulator.path,
                    )
                else:
                    # Fix for MuMu12 v4.0.4
                    instance = EmulatorInstance(
                        serial=serial,
                        name=name,
                        path=emulator.path,
                    )
                    mumu_id = self.get_instance_id(instance)
                    if mumu_id is not None:
                        instance.serial = f'127.0.0.1:{16384 + 32 * mumu_id}'
                        yield instance

            # Newer MuMu versions may remove the .nemu descriptor after an
            # instance is shut down. Keep the stopped instance discoverable so
            # the manager command can launch it again.
            if not found_nemu:
                name = os.path.basename(folder)
                instance = EmulatorInstance(
                    serial=None,
                    name=name,
                    path=emulator.path,
                )
                mumu_id = self.get_instance_id(instance)
                if mumu_id is not None:
                    instance.serial = f'127.0.0.1:{16384 + 32 * mumu_id}'
                    logger.info(
                        f'MuMu instance {name} has no .nemu descriptor, '
                        f'use index-derived serial {instance.serial}'
                    )
                    yield instance

    def iter_adb_binaries(self, emulator) -> t.Iterable[str]:
        # MuMu12 特有: ../vmonitor/bin/adb_server.exe
        exe = emulator.abspath('../vmonitor/bin/adb_server.exe')
        if os.path.exists(exe):
            yield exe
        yield from self._iter_common_adb(emulator)

    def start_show_window(self) -> bool:
        # MuMu12 通过 MuMuManager 启动，命令本身不需要窗口
        return False

    def build_start_command(self, instance) -> t.Optional[str]:
        mumu_id = self.get_instance_id(instance)
        if mumu_id is None:
            logger.warning(f'Cannot get MuMu instance index from name {instance.name}')
            return None
        console = self._resolve_console(instance)
        if console is None:
            return None
        # MuMuManager.exe control -v 0 launch
        return f'"{console}" control -v {mumu_id} launch'

    def build_stop_command(self, instance) -> t.Optional[str]:
        mumu_id = self.get_instance_id(instance)
        if mumu_id is None:
            logger.warning(f'Cannot get MuMu instance index from name {instance.name}')
            return None
        console = self._resolve_console(instance)
        if console is None:
            return None
        # MuMuManager.exe control -v 1 shutdown
        return f'"{console}" control -v {mumu_id} shutdown'

    def start_instance(self, instance, platform, command: str, show_window: bool) -> bool:
        # Wait for the manager to acknowledge the request before starting the
        # launch timer or releasing the lifecycle lock. Popen alone only proves
        # that the CLI was created, not that it accepted the launch request.
        logger.info(f'Execute: {command}')
        result = platform.execute_output(
            command, timeout=30, show_window=show_window, encoding='utf-8'
        )
        stdout = result.stdout.strip() if isinstance(result.stdout, str) else ''
        stderr = result.stderr.strip() if isinstance(result.stderr, str) else ''
        logger.info(
            f'[emu-start] launch command result: serial={instance.serial}, '
            f'returncode={result.returncode}, stdout={stdout!r}, stderr={stderr!r}'
        )
        if result.returncode != 0:
            logger.error(f'[emu-start] MuMu manager exited with code {result.returncode}')
            return False
        for output in (stdout, stderr):
            try:
                response = json.loads(output.lstrip('\ufeff'))
            except json.JSONDecodeError:
                continue
            if not isinstance(response, dict):
                continue
            error = response.get('errcode', response.get('error_code', 0))
            if error not in (None, 0, '0'):
                logger.error(f'[emu-start] MuMu rejected launch: {response!r}')
                return False
        return True

    # ------------------------------------------------------------------
    # 现代 MuMu 专属扩展
    # ------------------------------------------------------------------

    def build_launch_confirm_timer(self, instance):
        from module.base.timer import Timer
        # A cold manager/VM startup can take longer than the previous 12 seconds.
        return Timer(30).start()

    def build_stop_confirm_timer(self, instance) -> t.Any:
        from module.base.timer import Timer
        return Timer(30).start()

    @staticmethod
    def _remember_info_result(
            instance,
            returncode: int | None = None,
            stdout: str = '',
            stderr: str = '',
            error: str = ''
    ) -> None:
        """缓存最近一次 info 查询结果，交由状态变化日志按需输出。"""
        instance._mumu_info_result = {
            'returncode': returncode,
            'stdout': stdout,
            'stderr': stderr,
            'error': error,
        }

    @staticmethod
    def _log_info_on_state_change(instance, current_state: str) -> None:
        """每轮监视初始打印一次，后续仅在状态发生变化时打印 info。"""
        if (
                getattr(instance, '_mumu_info_state_logged', False)
                and getattr(instance, '_mumu_info_logged_state', None) == current_state
        ):
            return

        result = getattr(instance, '_mumu_info_result', {})
        returncode = result.get('returncode')
        stdout = result.get('stdout', '')
        stderr = result.get('stderr', '')
        error = result.get('error', '')
        message = (
            f'[emu-state] info state: serial={instance.serial}, state={current_state}, '
            f'returncode={returncode}, stdout={stdout!r}, stderr={stderr!r}'
        )
        if error:
            message = f'{message}, error={error}'

        log = logger.warning if current_state == 'unknown' or returncode not in (None, 0) or error else logger.info
        log(message)
        instance._mumu_info_state_logged = True
        instance._mumu_info_logged_state = current_state

    def query_player_info(self, instance, platform) -> dict:
        mumu_id = self.get_instance_id(instance)
        if mumu_id is None:
            self._remember_info_result(instance, error=f'Cannot get instance index from name {instance.name}')
            return {}

        manager = self._resolve_console(instance, log_warning=False)
        if manager is None:
            self._remember_info_result(instance, error='Cannot resolve MuMu control executable')
            return {}
        command = f'"{manager}" info -v {mumu_id}'
        try:
            # MuMuManager JSON 使用 UTF-8；Windows 中文系统默认的 GBK
            # 无法解码实例名称等字段，会导致 main_wnd 一并丢失。
            result = platform.execute_output(command, timeout=10, encoding='utf-8')
        except Exception as e:
            self._remember_info_result(instance, error=f'Query failed: {e}')
            return {}

        stdout = result.stdout.strip() if isinstance(result.stdout, str) else ''
        stderr = result.stderr.strip() if isinstance(result.stderr, str) else ''
        self._remember_info_result(
            instance,
            returncode=result.returncode,
            stdout=stdout,
            stderr=stderr,
        )
        if not stdout and not stderr:
            self._remember_info_result(instance, returncode=result.returncode, error='Empty info output')
            return {}

        # Parse stdout and stderr independently. Some MuMu versions emit
        # diagnostics to stderr even when stdout contains valid JSON.
        parse_errors = []
        for source, output in (('stdout', stdout), ('stderr', stderr)):
            if not output:
                continue
            try:
                player_info = json.loads(output.lstrip('\ufeff'))
            except json.JSONDecodeError as e:
                parse_errors.append(f'Invalid {source} JSON: {e}')
                continue
            if not isinstance(player_info, dict):
                parse_errors.append(f'Invalid {source} type: {type(player_info).__name__}')
                continue
            return player_info

        self._remember_info_result(
            instance,
            returncode=result.returncode,
            stdout=stdout,
            stderr=stderr,
            error='; '.join(parse_errors) or 'No valid info object',
        )
        return {}

    @staticmethod
    def _parse_process_state(player_info: dict) -> str:
        if not player_info:
            return 'unknown'

        error_code = player_info.get('error_code')
        if error_code not in (None, 0):
            return 'unknown'

        if 'is_process_started' not in player_info:
            # Some MuMu12 versions omit process fields after a successful shutdown.
            return 'stopped' if error_code == 0 else 'unknown'

        process_started = player_info['is_process_started']
        if isinstance(process_started, bool):
            return 'running' if process_started else 'stopped'
        if isinstance(process_started, int) and process_started in (0, 1):
            return 'running' if process_started else 'stopped'
        return 'unknown'

    def check_stop_state(self, instance, platform) -> str:
        player_info = self.query_player_info(instance, platform)
        current_state = self._parse_process_state(player_info)
        self._log_info_on_state_change(instance, current_state)
        return current_state

    def try_hide_window(self, instance, platform, info=None) -> bool:
        mumu_id = self.get_instance_id(instance)
        if mumu_id is None:
            return False

        if info is None:
            info = self.query_player_info(instance, platform)
        if not info:
            return False

        hwnd = info.get('main_wnd')
        if isinstance(hwnd, str):
            try:
                hwnd = int(hwnd, 16)
            except ValueError:
                return False
        if not isinstance(hwnd, int) or hwnd <= 0:
            return False
        if not ctypes.windll.user32.IsWindow(hwnd):
            return False

        from module.device.platform2.platform_windows import hide_window
        hide_window(hwnd)
        return True

    def check_launch_state(self, instance, state) -> tuple:
        if state.launch_confirm is None:
            return 'ready', None

        player_info = self.query_player_info(instance, state._platform)
        if player_info.get('launch_err_code') not in (None, 0, '0'):
            logger.error(
                f'[emu-start] MuMu launch failed: serial={instance.serial}, '
                f'code={player_info["launch_err_code"]}, '
                f'message={player_info.get("launch_err_msg", "")!r}'
            )
            return 'fail', player_info
        current_state = 'unknown'
        process_state = self._parse_process_state(player_info)
        if process_state == 'stopped':
            current_state = 'stopped'
        elif process_state == 'running':
            current_state = player_info.get('player_state') or (
                'start_finished' if player_info.get('is_android_started', False) else 'starting'
            )
        else:
            # Keep polling info while allowing the generic ADB/window readiness
            # checks to confirm a successful startup independently.
            self._log_info_on_state_change(instance, current_state)
            return 'unknown', player_info
        self._log_info_on_state_change(instance, current_state)
        if current_state == 'stopped':
            if state.launch_confirm.reached():
                logger.warning(f'[emu-start] launch not started: serial={state.serial}')
                return 'fail', player_info
            return 'wait', player_info

        state.launch_confirm = None
        return 'ready', player_info
