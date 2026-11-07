from pydantic import BaseModel, ConfigDict, Field, model_validator

from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.GeneralInvite.config_invite import InviteBaseConfig
from tasks.Component.SwitchSoul.switch_soul_config import SwitchSoulConfig
from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.config_scheduler import Scheduler
from tasks.Exploration.config import AutoRotate, ChooseRarity, ExplorationDifficulty, ExplorationLevel


class ExplorationFightConfig(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    limit_time: Time = Field(title='运行时间', default=Time(minute=30), description='limit_time_help')
    difficulty: ExplorationDifficulty = Field(title='探索难度', default=ExplorationDifficulty.HARD)
    start_chapter: ExplorationLevel = Field(title='开始章节', default=ExplorationLevel.EXPLORATION_1)
    end_chapter: ExplorationLevel = Field(title='结束章节', default=ExplorationLevel.EXPLORATION_28)
    collect_paper_reward: bool = Field(title='收取小纸人奖励', default=True)
    auto_rotate: AutoRotate = Field(title='自动添加候补式神', default=AutoRotate.no)
    choose_rarity: ChooseRarity = Field(title='选择狗粮稀有度', default=ChooseRarity.N)
    discover_monsters: bool = Field(
        title='发现妖怪模式', default=False,
        description='在指定范围内重复挑战，直到对应难度的章节入口不再显示未知式神。超过第十八章的设置会调整为第十八章。',
    )

    @property
    def chapter_options(self) -> list[ExplorationLevel]:
        chapters = list(ExplorationLevel)
        if self.discover_monsters:
            return chapters[:ExplorationLevel.EXPLORATION_18.get_index() + 1]
        return chapters

    @model_validator(mode='after')
    def normalize_chapters(self):
        chapters = self.chapter_options
        last = len(chapters) - 1
        start = min(self.start_chapter.get_index(), last)
        end = max(start, min(self.end_chapter.get_index(), last))
        object.__setattr__(self, 'start_chapter', chapters[start])
        object.__setattr__(self, 'end_chapter', chapters[end])
        return self

    @property
    def chapters(self) -> list[ExplorationLevel]:
        return self.chapter_options[self.start_chapter.get_index():self.end_chapter.get_index() + 1]


class ExplorationFight(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    exploration_fight_config: ExplorationFightConfig = Field(default_factory=ExplorationFightConfig)
    invite_config: InviteBaseConfig = Field(default_factory=InviteBaseConfig)
    general_battle_config: GeneralBattleConfig = Field(
        default_factory=lambda: GeneralBattleConfig(lock_team_enable=True),
    )
    switch_soul_config: SwitchSoulConfig = Field(default_factory=SwitchSoulConfig)

    @property
    def settings_options(self) -> dict[str, dict[str, list[str]]]:
        settings = self.exploration_fight_config
        options = [chapter.value for chapter in settings.chapter_options]
        return {'exploration_fight_config': {
            'start_chapter': options,
            'end_chapter': options[settings.start_chapter.get_index():],
        }}
