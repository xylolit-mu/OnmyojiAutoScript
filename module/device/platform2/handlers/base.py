import os
import typing as t
from abc import ABC, abstractmethod


class EmulatorHandler(ABC):
    """
    模拟器行为策略的抽象基类。
    每种模拟器（家族）实现一个具体子类，聚合该模拟器的所有行为。
    """

    # ------------------------------------------------------------------
    # 类型识别
    # ------------------------------------------------------------------

    @staticmethod
    @abstractmethod
    def type_names() -> list[str]:
        """返回此 Handler 管理的所有模拟器类型名。"""
        ...

    @staticmethod
    @abstractmethod
    def path_to_type(path: str, exe: str, dir1: str, dir2: str) -> str:
        """
        根据 exe 路径判断模拟器类型。
        Args:
            path: 完整路径
            exe:  可执行文件名 (小写)
            dir1: 上一级目录名 (小写)
            dir2: 上两级目录名 (小写)
        Returns:
            模拟器类型字符串，不匹配返回 ''
        """
        ...

    # ------------------------------------------------------------------
    # exe 路径转换
    # ------------------------------------------------------------------

    @staticmethod
    @abstractmethod
    def multi_to_single(exe: str) -> list[str]:
        """多开管理器 exe -> 单实例 exe 列表，不匹配返回空列表。"""
        ...

    @staticmethod
    @abstractmethod
    def single_to_console(exe: str) -> t.Optional[str]:
        """单实例 exe -> 控制台/管理器 exe，不匹配返回 None。"""
        ...

    # ------------------------------------------------------------------
    # 实例枚举
    # ------------------------------------------------------------------

    @abstractmethod
    def iter_instances(self, emulator) -> t.Iterable:
        """枚举此模拟器的所有实例。"""
        ...

    @abstractmethod
    def iter_adb_binaries(self, emulator) -> t.Iterable[str]:
        """返回此模拟器自带的 adb 可执行文件路径。"""
        ...

    # ------------------------------------------------------------------
    # 实例 ID
    # ------------------------------------------------------------------

    def get_instance_id(self, instance) -> t.Optional[int]:
        """获取实例 ID（如 MuMu12 的 id、LDPlayer 的 index），默认返回 None。"""
        return None

    # ------------------------------------------------------------------
    # 启动 / 停止
    # ------------------------------------------------------------------

    @abstractmethod
    def build_start_command(self, instance) -> t.Optional[str]:
        """构建启动命令字符串，返回 None 表示不支持。"""
        ...

    def build_stop_command(self, instance) -> t.Optional[str]:
        """构建停止命令字符串，返回 None 表示使用 kill 方式停止。"""
        return None

    def stop_by_kill(self, instance) -> t.Optional[str]:
        """返回需要 kill 的进程正则，返回 None 表示使用命令方式停止。"""
        return None

    def start_show_window(self) -> bool:
        """启动时命令本身是否默认需要窗口（如 MuMu12 通过 manager 启动，不需要窗口）。"""
        return True

    def start_instance(self, instance, platform, command: str, show_window: bool) -> t.Optional[bool]:
        """Return None to use the generic asynchronous launcher."""
        return None

    # ------------------------------------------------------------------
    # 启动监视扩展点（特殊模拟器可覆盖）
    # ------------------------------------------------------------------

    def query_player_info(self, instance, platform) -> dict:
        """查询模拟器实例运行信息，默认返回空 dict。"""
        return {}

    def try_hide_window(self, instance, platform, info=None) -> bool:
        """尝试隐藏模拟器窗口，默认不支持，返回 False。"""
        return False

    def check_launch_state(self, instance, state) -> tuple:
        """
        检查启动流程是否已经真正拉起。
        Returns:
            (result, player_info): result 为 'ready'/'wait'/'fail'/'unknown'；
                'unknown' 表示交由通用就绪检查继续确认
        """
        return 'ready', None

    def build_launch_confirm_timer(self, instance):
        """返回启动确认 Timer，默认返回 None（无需额外确认）。"""
        return None

    def build_stop_confirm_timer(self, instance) -> t.Any:
        """返回关闭确认 Timer，默认返回 None（提交命令后立即完成）。"""
        return None

    def check_stop_state(self, instance, platform) -> str:
        """返回 'stopped'/'running'/'unknown'，仅供启用关闭确认的 Handler 使用。"""
        return 'unknown'

    # ------------------------------------------------------------------
    # 通用 adb 兜底
    # ------------------------------------------------------------------

    @staticmethod
    def _iter_common_adb(emulator) -> t.Iterable[str]:
        """所有模拟器通用：检查目录下 adb.exe"""
        exe = emulator.abspath('./adb.exe')
        if os.path.exists(exe):
            yield exe
