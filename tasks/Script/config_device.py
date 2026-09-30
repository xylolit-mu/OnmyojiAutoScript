# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from enum import Enum
from typing import Union
from pydantic import BaseModel, ValidationError, Field

from module.logger import logger


class PackageName(str, Enum):
    AUTO = 'auto'
    NETEASE_ONMYOJI = 'com.netease.onmyoji.wyzymnqsd_cps'  # 网易官方扫码版
    NETEASE_MI = 'com.netease.onmyoji.mi'  # 小米
    NETEASE = 'com.netease.onmyoji'     # 网易官方非扫码版
    NETEASE_HUAWEI = 'com.netease.onmyoji.huawei'
    NETEASE_BILIBILI = 'com.netease.onmyoji.bili' #哔哩哔哩渠道服
    NETEASE_VIVO = 'com.netease.onmyoji.vivo'  # vivo渠道服
    NETEASE_M4399 = 'com.netease.onmyoji.m4399'  # 4399渠道服
    NETEASE_NEARME = 'com.netease.onmyoji.nearme.gamecenter'  # OPPO渠道服
    TENCENT_YYS = 'com.tencent.tmgp.yys.zqb'  # 腾讯应用宝渠道阴阳师


class ScreenshotMethod(str, Enum):
    AUTO = 'auto'
    ADB = 'ADB'
    ADB_NC = 'ADB_nc'
    UIAUTOMATOR2 = 'uiautomator2'
    DROIDCAST = 'DroidCast'
    DROIDCAST_RAW = 'DroidCast_raw'
    SCRCPY = 'scrcpy'
    WINDOW_BACKGROUND = 'window_background'
    NEMU_IPC = 'nemu_ipc'


class ControlMethod(str, Enum):
    ADB = 'adb'
    UIAUTOMATOR2 = 'uiautomator2'
    MINITOUCH = 'minitouch'
    WINDOW_MESSAGE = 'window_message'


class EmulatorInfoType(str, Enum):
    # module.device.platform2.emulator_base.EmulatorBase
    AUTO = 'auto'
    NoxPlayer = 'NoxPlayer'
    NoxPlayer64 = 'NoxPlayer64'
    BlueStacks4 = 'BlueStacks4'
    BlueStacks5 = 'BlueStacks5'
    BlueStacks4HyperV = 'BlueStacks4HyperV'
    BlueStacks5HyperV = 'BlueStacks5HyperV'
    LDPlayer3 = 'LDPlayer3'
    LDPlayer4 = 'LDPlayer4'
    LDPlayer9 = 'LDPlayer9'
    MuMuPlayer = 'MuMuPlayer'
    MuMuPlayerX = 'MuMuPlayerX'
    MuMuPlayer12 = 'MuMuPlayer12'
    MEmuPlayer = 'MEmuPlayer'


class Device(BaseModel):
    serial: str = Field(default="auto", description='serial_help')
    handle: str = Field(default='', description='handle_help')
    # 桌面客户端自动启动用：游戏安装目录（含 bin\onmyoji.exe 或 Launch.exe 的根目录），
    # 留空时按 %ProgramFiles%\Onmyoji 与注册表 InstallLocation 自动发现；仅桌面模式使用
    desktop_game_path: str = Field(default='', description='desktop_game_path_help')
    package_name: PackageName = Field(title='Package Name', default=PackageName.AUTO, description='package_name_help')
    screenshot_method: ScreenshotMethod = Field(default=ScreenshotMethod.AUTO, description='screenshot_method_help')
    control_method: ControlMethod = Field(default=ControlMethod.MINITOUCH, description='control_method_help')
    adb_restart: bool = Field(default=False, description='adb_restart_help')
    emulatorinfo_type: EmulatorInfoType = Field(default=EmulatorInfoType.AUTO, description='emulatorinfo_type_help')
    emulatorinfo_name: str = Field(default='', description='emulatorinfo_name_help')
    emulatorinfo_path: str = Field(default='', description='emulatorinfo_path_help')
    # 举例, E:\ProgramFiles\MuMuPlayer-12.0\shell\MuMuPlayer.exe
    # 模拟器启动时最小化
    emulator_window_minimize: bool = Field(default=False, description='模拟器静默启动并最小化')
    # 启动时纯后台运行模拟器，不显示窗口和任务栏
    run_background_only: bool = Field(default=False, description='模拟器无UI后台运行，关掉后重启脚本会重新显示（无需重启OAS）')
    continuous_task_rest_enable: bool = Field(
        default=False,
        description='continuous_task_rest_enable_help',
    )
    continuous_task_rest_interval: str = Field(
        default='60,120',
        description='continuous_task_rest_interval_help',
    )
    # 启动时先将当前使用的OCR模型加载到服务内存
    resource_precache_enable: bool = Field(
        default=False,
        description='resource_precache_enable_help',
    )


if __name__ == '__main__':
    d = Device()
    print(d.json())
    print(d.schema_json())
    try:
        d.control_method = 'adb'
    except ValidationError as e:
        print(e)
