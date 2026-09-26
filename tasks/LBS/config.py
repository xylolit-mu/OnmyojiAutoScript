# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from enum import Enum

from pydantic import BaseModel, Field
from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.config_scheduler import Scheduler
from tasks.Component.SwitchSoul.switch_soul_config import SwitchSoulConfig


class LBSMode(str, Enum):
    SOLO = '单人挑战'
    TEAM = '组队匹配'
    DRIVE = '开车模式'


class LBSConfig(BaseModel):
    # 限制时间
    limit_time: Time = Field(default=Time(minute=30), description='limit_time_help')
    # 限制次数（想打的次数/想开的次数，与活动剩余次数无关）
    limit_count: int = Field(default=30, description='limit_count_help')
    # 运行模式
    mode: LBSMode = Field(default=LBSMode.SOLO, description='lbs_mode_help')


class LBS(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    lbs_config: LBSConfig = Field(default_factory=LBSConfig)
    switch_soul: SwitchSoulConfig = Field(default_factory=SwitchSoulConfig)
    general_battle_config: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)
