from typing import ClassVar
from tasks.Component.config_settings_layout import SettingsLayoutConfig, settings_section
# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from datetime import timedelta, time
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig

from tasks.Component.config_base import ConfigBase, TimeDelta, Time, dynamic_hide
from pydantic import Field, field_validator, model_validator
from module.config.multi_select import normalize_multi_select
from enum import IntEnum, Enum
from tasks.Component.config_scheduler import Scheduler


class BossType(IntEnum):
    ONE_STAR = 1
    TWO_STARS = 2
    THREE_STARS = 3
    FOUR_STARS = 4
    FIVE_STARS = 5
    SIX_STARS = 6
    MONEY = 7


class MetaDemonMode(str, Enum):
    BOSS_BATTLE = '鬼王战斗'
    BOSS_ASSIST = '鬼王协助'
    AWAKE = '门票刷取：觉醒七层'
    EXPLORATION = '门票刷取：探索'


class MetaDemonConfig(ConfigBase):
    mode: list[MetaDemonMode] = Field(default=[MetaDemonMode.BOSS_BATTLE], title='运行模式', json_schema_extra={'ui_exclusive_groups': [['门票刷取：觉醒七层', '门票刷取：探索'], ['鬼王战斗', '鬼王协助']]}, description='勾选执行；两种门票刷取至多选一种；鬼王战斗与鬼王协助至多选一种，门票刷取优先执行')

    @field_validator('mode', mode='before')
    @classmethod
    def parse_modes(cls, value):
        selected = [MetaDemonMode('门票刷取：探索' if item == '门票刷取：探索绘卷' else item)
                    for item in normalize_multi_select(value)]
        tickets = [item for item in (MetaDemonMode.AWAKE, MetaDemonMode.EXPLORATION) if item in selected]
        if len(tickets) > 1:
            raise ValueError('觉醒和探索门票刷取至多选择一种')
        bosses = [item for item in (MetaDemonMode.BOSS_BATTLE, MetaDemonMode.BOSS_ASSIST) if item in selected]
        if len(bosses) > 1:
            raise ValueError('鬼王战斗和鬼王协助至多选择一种')
        return tickets + bosses

    limit_time: Time = Field(default=Time(minute=45), description='limit_time_help')
    limit_count: int = Field(default=50, description='limit_count_help')
    auto_tea: bool = Field(default=False, description='疲劳度满时是否自动喝茶, 关闭则自动根据调度器等待间隔时间安排下一次调度')
    fire_sequence: str = Field(default='1 2 3 4', description='攻击顺序:从左到右,空格分隔(1:一星鬼王,2:二星鬼王...)\n例:3 2(先攻击三星鬼王然后二星鬼王)')
    powerful_list: str = Field(default='', description='开启强力鬼王列表(空格分隔),例:4 5(四星鬼王和五星鬼王开启强力追击)')
    synthesis_list: str = Field(default='1', description='鬼王合成列表(空格分隔),支持1-5星合成,例:1 2 3(合成一星,二星,三星鬼王)')

    @property
    def limit_time_v(self) -> timedelta:
        if isinstance(self.limit_time, time):
            return timedelta(hours=self.limit_time.hour, minutes=self.limit_time.minute, seconds=self.limit_time.second)
        return self.limit_time

    @property
    def fire_sequence_v(self) -> list[BossType]:
        sequence_text = self.fire_sequence.strip()
        if sequence_text == '':
            return []
        fire_num_list = sequence_text.split(' ')
        fire_boss_type_list = [BossType(int(num_text)) for num_text in fire_num_list]
        return fire_boss_type_list

    @property
    def powerful_list_v(self) -> list[BossType]:
        powerful_list_text = self.powerful_list.strip()
        if powerful_list_text == '':
            return []
        powerful_num_list = powerful_list_text.split(' ')
        powerful_boss_type_list = [BossType(int(num_text)) for num_text in powerful_num_list]
        return powerful_boss_type_list

    @property
    def synthesis_list_v(self) -> list[BossType]:
        synthesis_list_text = self.synthesis_list.strip()
        if synthesis_list_text == '':
            return []
        synthesis_num_list = synthesis_list_text.split(' ')
        synthesis_boss_type_list = [BossType(int(num_text)) for num_text in synthesis_num_list]
        return synthesis_boss_type_list


def loadout_group(boss_type: BossType | None) -> str:
    if boss_type is None:
        return 'low_star'
    return 'low_star' if int(boss_type) <= 4 else 'high_star'


class ExplorationTarget(str, Enum):
    ALL = '全部'
    COIN = '金币'
    EXP = '经验'
    DARUMA = '达摩'


class MetaDemonGeneralConfig(ConfigBase):
    high_star_quick_loadout_enable: bool = Field(default=False, title="高星鬼王启用快速上阵", description="仅五星、六星鬼王战斗生效；使用对应御魂配置中的分组名、预设名快速上阵，跳过式神录切魂与战斗准备预设。门票刷取仍按原配置执行。")
    exploration_rotate: bool = Field(default=False, title='探索自动添加候补式神', description='与探索任务的自动添加候补式神一致：启用轮换并自动补充候补式神；关闭时关闭轮换。')
    awake_buff_enable: bool = Field(default=False, title='觉醒启用加成')
    exploration_target: ExplorationTarget = Field(default=ExplorationTarget.ALL, title='探索目标')


class MetaDemonSwitchSoulConfig(ConfigBase):
    low_star_enable: bool = Field(default=False, title='按编号切换')
    low_star_group_team: str = Field(default='-1,-1', title='分组、队号', description='switch_group_team_help')
    low_star_by_name: bool = Field(default=False, title='按名称切换', description='enable_switch_by_name_help')
    low_star_group_team_name: str = Field(default='', title='分组名、预设名')
    high_star_enable: bool = Field(default=False, title='按编号切换')
    high_star_group_team: str = Field(default='-1,-1', title='分组、队号', description='switch_group_team_help')
    high_star_by_name: bool = Field(default=False, title='按名称切换', description='enable_switch_by_name_help')
    high_star_group_team_name: str = Field(default='', title='分组名、预设名')
    ticket_farming_enable: bool = Field(default=False, title='按编号切换')
    ticket_farming_group_team: str = Field(default='-1,-1', title='分组、队号', description='switch_group_team_help')
    ticket_farming_by_name: bool = Field(default=False, title='按名称切换', description='enable_switch_by_name_help')
    ticket_farming_group_team_name: str = Field(default='', title='分组名、预设名')

    def switch_for(self, category: str):
        from tasks.Component.SwitchSoul.switch_soul_config import SwitchSoulConfig
        names = getattr(self, f'{category}_group_team_name').split(',')
        by_name = getattr(self, f'{category}_by_name')
        group_name, team_name = (part.strip() for part in names) if len(names) == 2 else ('', '')
        return SwitchSoulConfig(
            enable=getattr(self, f'{category}_enable') and not by_name,
            switch_group_team=getattr(self, f'{category}_group_team'),
            enable_switch_by_name=by_name, group_name=group_name, team_name=team_name,
        )


class MetaDemonQuickPresetConfig(ConfigBase):
    high_star_custom_preset_enable: bool = Field(default=False, title='指定高星切换预设',
        description='每次挑战新首领时上阵对应预设并装配一次御魂')
    high_star_custom_preset: str = Field(default='ALL:(1,1)', title='自定义配置上阵预设',
        description='示例：boss_name:("预设组","预设名"); 多项以分号分隔；ALL 表示其他怪物。')


class AssistanceRole(str, Enum):
    ASSIST = '协助'
    OFFERING = '上供'


class MetaDemonAssistanceConfig(ConfigBase):
    role: AssistanceRole = Field(default=AssistanceRole.OFFERING, title='鬼王协助身份',
        description='上供：仅召唤六星鬼王，分享给指定好友后等待击杀；协助暂未实现。')
    friend_names: str = Field(default='', title='分享好友名字',
        description='填写游戏内好友完整名字；多个名字用分号或换行分隔，按名称精确匹配，不使用近似名字。')
    poll_interval: int = Field(default=5, ge=2, le=60, title='击杀等待检查间隔（秒）',
        description='分享后按此间隔检查鬼王是否已被击杀，不重复分享或自行挑战。')

    @property
    def friends(self) -> list[str]:
        import re
        return list(dict.fromkeys(name.strip() for name in re.split(r'[;；\n\r]+', self.friend_names) if name.strip()))


class MetaDemonScheduler(Scheduler):
    wait_interval: TimeDelta = Field(default=TimeDelta(hours=1, minutes=40), description='疲劳度满时等多长时间后再次运行本任务,建议设置等待完全恢复疲劳度所需的时间\n例:100分钟恢复100点疲劳度,再次运行必定可以继续战斗')

    hide_fields = dynamic_hide('server_update', 'delay_date')


class MetaDemon(ConfigBase):
    settings_layout_config: SettingsLayoutConfig = Field(default_factory=SettingsLayoutConfig)
    settings_layout: ClassVar[list[dict]] = [
        settings_section('scheduler', '任务调度', groups=[{'name': 'scheduler'}]),
        settings_section('general_config', '通用设置', groups=[
            {'name': 'meta_demon_config', 'fields': ['mode']},
            {'name': 'assistance_config', 'fields': ['friend_names']},
            {'name': 'meta_demon_config', 'fields': [key for key in MetaDemonConfig.model_fields if key != 'mode']},
            {'name': 'general_config'},
            {'name': 'assistance_config', 'fields': ['role']},
        ]),
        settings_section('switch_soul_config', '执行任务前切换御魂', variants=[
            {'id': key, 'label': label, 'groups': [{'name': 'switch_soul_config',
                'fields': [f'{key}_{suffix}' for suffix in ('enable', 'group_team', 'by_name', 'group_team_name')],
                'labels': {f'{key}_enable': '启用', f'{key}_group_team': '御魂装配分组设置',
                           f'{key}_by_name': '通过OCR来切换御魂预设', f'{key}_group_team_name': '分组名、预设名'}}]}
            for key, label in (('low_star', '低星鬼王'), ('high_star', '高星鬼王'), ('ticket_farming', '门票刷取'))]),
        settings_section('battle_config', '战斗配置', variants=[
            {'id': key, 'label': label, 'groups': [{'name': f'{key}_battle_conf'}]}
            for key, label in (('low_star', '低星鬼王'), ('high_star', '高星鬼王'), ('ticket_farming', '门票刷取'))]),
        settings_section('quick_preset_config', '快速预设详情', groups=[{'name': 'quick_preset_config'}]),
        settings_section('assistance_config', '鬼王协助', groups=[
            {'name': 'assistance_config', 'fields': [key for key in MetaDemonAssistanceConfig.model_fields if key not in ('friend_names', 'role')]},
        ]),
    ]

    scheduler: MetaDemonScheduler = Field(default_factory=MetaDemonScheduler)
    meta_demon_config: MetaDemonConfig = Field(default_factory=MetaDemonConfig)
    general_config: MetaDemonGeneralConfig = Field(default_factory=MetaDemonGeneralConfig)
    switch_soul_config: MetaDemonSwitchSoulConfig = Field(default_factory=MetaDemonSwitchSoulConfig)
    quick_preset_config: MetaDemonQuickPresetConfig = Field(default_factory=MetaDemonQuickPresetConfig)
    assistance_config: MetaDemonAssistanceConfig = Field(default_factory=MetaDemonAssistanceConfig)
    low_star_battle_conf: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)
    high_star_battle_conf: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)
    ticket_farming_battle_conf: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)

    def battle_config(self, category: str) -> GeneralBattleConfig:
        config = getattr(self, f'{category}_battle_conf')
        if category == 'high_star' and self.general_config.high_star_quick_loadout_enable:
            return config.model_copy(update={'preset_enable': False})
        return config

    @model_validator(mode='before')
    @classmethod
    def migrate_flat_presets(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        old = data.pop('preset_config', None)
        if isinstance(old, dict):
            for category in ('low_star', 'high_star', 'ticket_farming'):
                data.setdefault(f'{category}_battle_conf', {
                    'preset_enable': old.get(f'{category}_enable', False) if category != 'high_star' else False,
                    'preset_group': old.get(f'{category}_group', 1),
                    'preset_team': old.get(f'{category}_team', 1),
                })
            # Preserve the previously entered OCR target without enabling quick loadout implicitly.
            souls = dict(data.get('switch_soul_config', {}))
            if old.get('high_star_group_name') and old.get('high_star_preset_name'):
                souls.setdefault('high_star_group_team_name', f"{old['high_star_group_name']},{old['high_star_preset_name']}")
            data['switch_soul_config'] = souls
        return data

    @model_validator(mode='before')
    @classmethod
    def migrate_legacy_loadouts(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        old = data.pop('switch_soul', None)
        if not isinstance(old, dict) or 'switch_soul_config' in data:
            return data
        souls, presets = {}, {}
        for category, stars in (('low_star', ('one_star','two_stars','three_stars','four_stars')),
                                ('high_star', ('five_stars','six_stars'))):
            star = next((key for key in stars if str(old.get(key, '')).strip()), stars[0])
            text = str(old.get(star, '')).strip()
            if text == '-1,-1':
                text = ''
            parts = [part.strip() for part in text.split(',')]
            numbered = len(parts) == 2 and all(part.isdigit() for part in parts)
            souls[f'{category}_enable'] = bool(old.get('enable') and text and numbered)
            souls[f'{category}_by_name'] = bool(old.get('enable') and text and not numbered)
            souls[f'{category}_group_team' if numbered else f'{category}_group_team_name'] = text
            if category == 'low_star' and numbered:
                presets.update(low_star_enable=bool(old.get(f'enable_{star}_preset')),
                               low_star_group=int(parts[0]), low_star_team=int(parts[1]))
        data['switch_soul_config'] = souls
        data.setdefault('preset_config', presets)
        return data
