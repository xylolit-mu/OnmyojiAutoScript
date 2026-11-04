# This Python file uses the following encoding: utf-8
"""式神活动统一配置。"""

from datetime import time, timedelta
from enum import Enum
from copy import deepcopy
import re

from pydantic import BaseModel, Field, model_validator, validator

from module.logger import logger
from module.config.multi_select import normalize_multi_select
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.config_scheduler import Scheduler


class ActivityTask(str, Enum):
    EXPLORATION = '探索'
    RICH_MAN = '大富翁'
    FAKE_GOD = '伪神'
    CLIMB = '爬塔'
    BOSS = '首领'


class ClimbTask(str, Enum):
    AP = '体力'
    EASY = '简单'
    HARD = '困难'
    AP100 = '百体'


ACTIVITY_EXECUTION_ORDER = ('探索', '大富翁', '伪神', '爬塔', '首领')
ACTIVITY_NAME_TO_FIELD = {
    '大富翁': 'rich_man',
    '伪神': 'fakegod',
    '爬塔': 'climb',
    '探索': 'exploration',
    '首领': 'boss',
}
ACTIVITY_NAME_ALIASES = {
    '探索': '探索',
    'exploration': '探索',
    'exp': '探索',
    'richman': '大富翁',
    'rich_man': '大富翁',
    '大富翁': '大富翁',
    'climb': '爬塔',
    'normal': '爬塔',
    '爬塔': '爬塔',
    'fakegod': '伪神',
    'fake_god': '伪神',
    '伪神': '伪神',
    '伪神降临': '伪神',
    '首领boss': '首领',
    '首领': '首领',
    'boss': '首领',
}
CLIMB_TYPES = ('pass', 'ap', 'ap100')
BATTLE_TYPES = ('rich_man', *CLIMB_TYPES, 'pass_hard', 'boss', 'fakegod')


class GeneralConfig(ConfigBase):
    task_sequence: list[ActivityTask] = Field(
        default=[
            ActivityTask.RICH_MAN,
            ActivityTask.CLIMB,
            ActivityTask.BOSS,
        ],
        title='Activity Task Sequence',
        description='activity_task_sequence_help',
    )
    throw_limit: int = Field(default=0, title='Throw Limit', ge=0)
    climb_task_sequence: list[ClimbTask] = Field(
        default=[ClimbTask.HARD, ClimbTask.EASY, ClimbTask.AP,
                 ClimbTask.AP100],
        title='Climb Task Sequence',
    )
    ap_limit: int = Field(default=0, title='Ap Limit', ge=0)
    pass_limit: str = Field(
        default='0',
        title='Pass Limit',
        description='pass_limit_help',
    )
    boss_limit: int = Field(default=0, title='Boss Limit', ge=0)
    ap100_limit: int = Field(default=0, title='Ap100 Limit', ge=0)
    fakegod_limit: int = Field(default=0, title='Fakegod Limit', ge=0)
    limit_time: Time = Field(
        default=Time(hour=1, minute=30),
        title='Activity Limit Time',
        description='activity_limit_time_help',
    )
    active_souls_clean: bool = Field(
        default=False,
        title='Active Souls Clean',
        description='active_souls_clean_help',
    )
    random_sleep: bool = Field(
        default=False,
        title='Activity Random Sleep',
        description='activity_random_sleep_help',
    )
    use_penta_pass: bool = Field(
        default=False,
        title='Use Penta Pass',
        description='use_penta_pass_help',
    )
    climb_drink_break: bool = Field(
        default=False,
        title='Climb Drink Break',
        description='climb_drink_break_help',
    )
    climb_drink_interval: str = Field(
        default='60,120',
        title='Climb Drink Interval',
        description='climb_drink_interval_help',
    )

    @model_validator(mode='before')
    @classmethod
    def split_legacy_climb_boss(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        climb = data.get('climb_task_sequence')
        legacy_boss = climb is None or '首领' in climb
        if climb is not None:
            data['climb_task_sequence'] = [item for item in climb if item != '首领']
        # Only migrate the previous structure; a saved four-mode list marks the split.
        if legacy_boss and data.get('task_sequence') is not None:
            sequence = normalize_multi_select(data['task_sequence'])
            names = [part.strip() for item in sequence
                     for part in re.split(r'[,，;；\n\r]+', str(item.value if isinstance(item, ActivityTask) else item))
                     if part.strip()]
            names = [ACTIVITY_NAME_ALIASES.get(name.lower(), name) for name in names]
            if '爬塔' in names and '首领' not in names:
                names.insert(names.index('爬塔') + 1, '首领')
            data['task_sequence'] = names
        return data

    @property
    def limit_time_v(self) -> timedelta:
        if isinstance(self.limit_time, time):
            return timedelta(
                hours=self.limit_time.hour,
                minutes=self.limit_time.minute,
                seconds=self.limit_time.second,
            )
        return self.limit_time

    @property
    def task_sequence_v(self) -> list[str]:
        selected = [
            item.value if isinstance(item, ActivityTask) else str(item)
            for item in self.task_sequence
        ]
        return [
            name for name in ACTIVITY_EXECUTION_ORDER if name in selected and self.activity_enabled(name)
        ]

    @property
    def climb_sequence_v(self) -> list[str]:
        """困难、简单、体力、百体固定优先级，跳过次数为零的项。"""
        actions = {'体力': 'ap', '简单': 'pass_easy', '困难': 'pass_hard',
                   '百体': 'ap100'}
        result = []
        for item in (ClimbTask.HARD, ClimbTask.EASY, ClimbTask.AP, ClimbTask.AP100):
            if item not in self.climb_task_sequence:
                continue
            name = actions[item.value if isinstance(item, ClimbTask) else item]
            limit = (self.pass_limit_for(name[5:]) if name.startswith('pass_')
                     else self.limit_for(name))
            if limit > 0 and name not in result:
                result.append(name)
        return result

    @property
    def pass_limits_v(self) -> tuple[int, int]:
        """返回门票简单、困难模式的次数限制。"""
        parts = [int(part) for part in self.pass_limit.split(',')]
        if len(parts) == 1:
            return parts[0], 0
        return parts[0], parts[1]

    def pass_limit_for(self, mode: str) -> int:
        easy_limit, hard_limit = self.pass_limits_v
        if mode == 'easy':
            return easy_limit
        if mode == 'hard':
            return hard_limit
        raise ValueError(f'Unsupported pass mode: {mode}')

    def activity_enabled(self, activity_name: str) -> bool:
        field = ACTIVITY_NAME_TO_FIELD[activity_name]
        if field == 'exploration':
            return True
        if field == 'rich_man':
            return self.throw_limit > 0
        if field == 'climb':
            return bool(self.climb_sequence_v)
        if field == 'boss':
            return self.boss_limit > 0
        return self.fakegod_limit > 0

    def limit_for(self, action_type: str) -> int:
        if action_type == 'pass':
            return sum(self.pass_limits_v)
        field = 'throw_limit' if action_type == 'rich_man' else f'{action_type}_limit'
        return getattr(self, field, 0)

    @validator('pass_limit', pre=True, always=True)
    def parse_pass_limit(cls, value):
        """兼容“简单次数”及“简单次数,困难次数”两种写法。"""
        raw_value = '0' if value is None else str(value).strip()
        parts = [part.strip() for part in raw_value.split(',')]
        if len(parts) not in (1, 2) or any(not part for part in parts):
            raise ValueError('门票战斗次数必须填写数字或“简单次数,困难次数”')
        if any(not part.isdigit() for part in parts):
            raise ValueError('门票战斗次数只能填写非负整数')
        return ','.join(str(int(part)) for part in parts)

    @validator('task_sequence', pre=True, always=True)
    def parse_task_sequence(cls, value):
        """兼容旧版填空字符串和多选列表，统一为现有活动选项。"""
        if value is None:
            values = list(ACTIVITY_EXECUTION_ORDER)
        else:
            values = normalize_multi_select(value)

        # 旧版文本框可能使用中文分隔符；部分界面会将整段文本包装成列表。
        names = []
        for item in values:
            if isinstance(item, ActivityTask):
                names.append(item)
            else:
                names.extend(re.split(r'[,，;；\n\r]+', str(item)))

        selected = []
        for raw_name in names:
            if isinstance(raw_name, ActivityTask):
                name = raw_name.value
            else:
                raw_name = str(raw_name).strip()
                if not raw_name:
                    continue
                name = ACTIVITY_NAME_ALIASES.get(raw_name.lower())
            if name is None:
                raise ValueError(
                    f'任务启用项仅支持 {", ".join(ACTIVITY_EXECUTION_ORDER)}，当前为 {raw_name}'
                )
            if name not in selected:
                selected.append(name)
        # 旧配置可能同时启用互斥活动，按固定优先级保留一个。
        exclusive = next((name for name in ACTIVITY_EXECUTION_ORDER[:3] if name in selected), None)
        return [name for name in ACTIVITY_EXECUTION_ORDER
                if name in selected and (name not in ACTIVITY_EXECUTION_ORDER[:3] or name == exclusive)]

    @validator('limit_time', pre=True, always=True)
    def parse_limit_time(cls, value):
        if isinstance(value, str):
            if value.isdigit():
                delta = timedelta(seconds=int(value))
                return time(
                    hour=delta.seconds // 3600,
                    minute=delta.seconds // 60 % 60,
                    second=delta.seconds % 60,
                )
            try:
                return time.fromisoformat(value)
            except ValueError:
                logger.warning('Invalid activity limit_time value. Expected format: HH:MM:SS')
                return time(hour=1, minute=30)
        return value

    @validator('climb_drink_interval', pre=True, always=True)
    def parse_climb_drink_interval(cls, value):
        matched = re.fullmatch(r'\s*(\d+)\s*[,，]\s*(\d+)\s*', str(value))
        if matched is None:
            raise ValueError('爬塔喝水间隔必须填写“最小分钟,最大分钟”')
        lower, upper = (int(item) for item in matched.groups())
        if lower <= 0 or upper <= 0 or lower > upper:
            raise ValueError('爬塔喝水间隔必须为有效的正整数范围')
        return f'{lower},{upper}'


def check_soul_by_number(enable_switch: bool, group_team: str, label: str):
    if not enable_switch:
        return
    parts = group_team.split(',') if group_team else []
    if len(parts) != 2 or not all(part.strip().isdigit() for part in parts):
        raise ValueError(f'[{label}]御魂预设必须为数字组号和队伍号，格式为 组号,队伍号')


def check_soul_by_ocr(enable_switch: bool, group_team_name: str, label: str):
    if not enable_switch:
        return
    parts = group_team_name.split(',') if group_team_name else []
    if len(parts) != 2 or not all(part.strip() for part in parts):
        raise ValueError(f'[{label}]御魂预设名称必须为 组名,队伍名')


class SwitchSoulConfig(BaseModel):
    enable_switch_exp_encounter: bool = Field(default=False)
    exp_encounter_group_team: str = Field(default='-1,-1', description='switch_group_team_help')
    enable_switch_exp_encounter_by_name: bool = Field(default=False, description='enable_switch_by_name_help')
    exp_encounter_group_team_name: str = Field(default='')

    enable_switch_rich_man: bool = Field(default=False)
    rich_man_group_team: str = Field(default='-1,-1', description='switch_group_team_help')
    enable_switch_rich_man_by_name: bool = Field(default=False, description='enable_switch_by_name_help')
    rich_man_group_team_name: str = Field(default='')

    enable_switch_ap: bool = Field(default=False)
    ap_group_team: str = Field(default='-1,-1', description='switch_group_team_help')
    enable_switch_ap_by_name: bool = Field(default=False, description='enable_switch_by_name_help')
    ap_group_team_name: str = Field(default='')

    enable_switch_pass: bool = Field(default=False)
    pass_group_team: str = Field(default='-1,-1', description='switch_group_team_help')
    enable_switch_pass_by_name: bool = Field(default=False, description='enable_switch_by_name_help')
    pass_group_team_name: str = Field(default='')

    enable_switch_pass_hard: bool = Field(default=False)
    pass_hard_group_team: str = Field(default='-1,-1', description='switch_group_team_help')
    enable_switch_pass_hard_by_name: bool = Field(default=False, description='enable_switch_by_name_help')
    pass_hard_group_team_name: str = Field(default='')

    enable_switch_boss: bool = Field(default=False)
    boss_group_team: str = Field(default='-1,-1', description='switch_group_team_help')
    enable_switch_boss_by_name: bool = Field(default=False, description='enable_switch_by_name_help')
    boss_group_team_name: str = Field(default='')

    enable_switch_ap100: bool = Field(default=False)
    ap100_group_team: str = Field(default='-1,-1', description='switch_group_team_help')
    enable_switch_ap100_by_name: bool = Field(default=False, description='enable_switch_by_name_help')
    ap100_group_team_name: str = Field(default='')

    enable_switch_fakegod: bool = Field(default=False)
    fakegod_group_team: str = Field(default='-1,-1', description='switch_group_team_help')
    enable_switch_fakegod_by_name: bool = Field(default=False, description='enable_switch_by_name_help')
    fakegod_group_team_name: str = Field(default='')

    def validate_switch_soul(self):
        for label in ('exp_encounter', *BATTLE_TYPES):
            check_soul_by_number(
                getattr(self, f'enable_switch_{label}'),
                getattr(self, f'{label}_group_team'),
                label.upper(),
            )
            check_soul_by_ocr(
                getattr(self, f'enable_switch_{label}_by_name'),
                getattr(self, f'{label}_group_team_name'),
                label.upper(),
            )
        return self


class ActivityShikigami(ConfigBase):
    # OASX 按字段顺序排版：任务调度、通用设置、切换御魂、六种战斗配置。
    scheduler: Scheduler = Field(default_factory=Scheduler)
    general_config: GeneralConfig = Field(default_factory=GeneralConfig)
    switch_soul_config: SwitchSoulConfig = Field(default_factory=SwitchSoulConfig)

    rich_man_battle_conf: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)
    ap_battle_conf: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)
    pass_battle_conf: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)
    pass_hard_battle_conf: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)
    boss_battle_conf: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)
    ap100_battle_conf: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)
    fakegod_battle_conf: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)
    exp_encounter_battle_conf: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)

    @model_validator(mode='before')
    @classmethod
    def migrate_legacy_configs(cls, data):
        """合并旧爬塔、大富翁和伪神降临配置。"""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        old_encounter = data.pop('exp_encounter_soul_config', None)
        if isinstance(old_encounter, dict):
            soul = dict(data.get('switch_soul_config') or {})
            for old_key, new_key in (
                ('enable', 'enable_switch_exp_encounter'),
                ('switch_group_team', 'exp_encounter_group_team'),
                ('enable_switch_by_name', 'enable_switch_exp_encounter_by_name'),
            ):
                if old_key in old_encounter:
                    soul.setdefault(new_key, old_encounter[old_key])
            soul.setdefault('exp_encounter_group_team_name',
                            f"{old_encounter.get('group_name', '')},{old_encounter.get('team_name', '')}")
            data['switch_soul_config'] = soul
        old_climb = data.get('general_climb')
        old_rich_man = data.pop('_legacy_rich_man', None)
        old_fakegod = data.pop('_legacy_fakegod', None)

        if 'general_config' not in data:
            general = {}
            if isinstance(old_climb, dict):
                for key in (
                    'limit_time', 'ap_limit', 'pass_limit', 'boss_limit', 'ap100_limit',
                    'active_souls_clean', 'random_sleep',
                ):
                    if key in old_climb:
                        general[key] = old_climb[key]
                general['task_sequence'] = '爬塔'

            if isinstance(old_rich_man, dict):
                run = old_rich_man.get('run_config', old_rich_man.get('general_climb', {}))
                if isinstance(run, dict):
                    general['throw_limit'] = run.get('throw_limit', run.get('pass_limit', 0))
                    general.setdefault('limit_time', run.get('limit_time', '01:30:00'))
                    general['active_souls_clean'] = bool(
                        general.get('active_souls_clean', False) or run.get('active_souls_clean', False)
                    )
                    general['random_sleep'] = bool(
                        general.get('random_sleep', False) or run.get('random_sleep', False)
                    )

            if isinstance(old_fakegod, dict):
                run = old_fakegod.get('general_climb', {})
                if isinstance(run, dict):
                    general['fakegod_limit'] = run.get('pass_limit', 0)
                    general.setdefault('limit_time', run.get('limit_time', '01:30:00'))

            enabled_sequence = []
            if isinstance(old_rich_man, dict) and old_rich_man.get('scheduler', {}).get('enable'):
                enabled_sequence.append('大富翁')
            if isinstance(old_climb, dict):
                enabled_sequence.append('爬塔')
            if isinstance(old_fakegod, dict) and old_fakegod.get('scheduler', {}).get('enable'):
                enabled_sequence.append('伪神降临')
            if enabled_sequence:
                general['task_sequence'] = ','.join(enabled_sequence)
            data['general_config'] = general

        soul = dict(data.get('switch_soul_config') or {})
        if isinstance(old_rich_man, dict):
            old_soul = old_rich_man.get('switch_soul', old_rich_man.get('common_switch_soul', {}))
            if isinstance(old_soul, dict):
                soul.setdefault('enable_switch_rich_man', old_soul.get('enable', False))
                soul.setdefault('rich_man_group_team', old_soul.get('switch_group_team', '-1,-1'))
                soul.setdefault(
                    'enable_switch_rich_man_by_name', old_soul.get('enable_switch_by_name', False),
                )
                combined = ','.join(filter(None, [old_soul.get('group_name', ''), old_soul.get('team_name', '')]))
                soul.setdefault('rich_man_group_team_name', combined)
        if isinstance(old_fakegod, dict):
            old_soul = old_fakegod.get('switch_soul_config', {})
            if isinstance(old_soul, dict):
                soul.setdefault('enable_switch_fakegod', old_soul.get('enable_switch_pass', False))
                soul.setdefault(
                    'enable_switch_fakegod_by_name', old_soul.get('enable_switch_pass_by_name', False),
                )
                soul.setdefault('fakegod_group_team', old_soul.get('pass_group_team', '-1,-1'))
                soul.setdefault('fakegod_group_team_name', old_soul.get('pass_group_team_name', ''))
        # Split the former shared preset while preserving existing hard-ticket choices.
        for old_key, new_key in (
            ('enable_switch_ap100', 'enable_switch_pass_hard'),
            ('ap100_group_team', 'pass_hard_group_team'),
            ('enable_switch_ap100_by_name', 'enable_switch_pass_hard_by_name'),
            ('ap100_group_team_name', 'pass_hard_group_team_name'),
        ):
            if old_key in soul:
                soul.setdefault(new_key, soul[old_key])
        data.setdefault('pass_hard_battle_conf', deepcopy(data.get('pass_battle_conf', {})))
        data['switch_soul_config'] = soul

        if isinstance(old_rich_man, dict):
            data.setdefault('rich_man_battle_conf', old_rich_man.get('general_battle', {}))
        if isinstance(old_fakegod, dict):
            data.setdefault('fakegod_battle_conf', old_fakegod.get('pass_battle_conf', {}))
        return data
