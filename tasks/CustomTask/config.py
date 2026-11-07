from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from tasks.ActivityShikigami.config import ActivityShikigami
from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.Exploration.config import Exploration
from tasks.MetaDemon.config import MetaDemon
from tasks.MetaDemonTicket.config import MetaDemonTicket
from tasks.RealmRaid.config import RealmRaid


class CustomTaskAccountInfo(AccountInfo):
    character: str = Field(
        default='',
        description='选填；留空时按目标服务器匹配，同服有多个角色时按默认登录流程进入。',
    )


class AccountSwitch(ConfigBase):
    account: CustomTaskAccountInfo = Field(default_factory=CustomTaskAccountInfo)


TASK_TYPES = {
    'Exploration': ('探索', Exploration),
    'RealmRaid': ('个人突破', RealmRaid),
    'MetaDemonTicket': ('鬼王门票', MetaDemonTicket),
    'MetaDemon': ('超鬼王', MetaDemon),
    'ActivityShikigami': ('式神活动', ActivityShikigami),
    'AccountSwitch': ('账号切换', AccountSwitch),
}
ENTRY_PREFIX = 'CustomTaskEntry-'


class CustomTaskEntry(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, pattern=r'^[a-zA-Z0-9_-]+$')
    type: str
    settings: dict = Field(default_factory=dict)

    @field_validator('type')
    @classmethod
    def validate_type(cls, value):
        if value not in TASK_TYPES:
            raise ValueError('不支持此任务类型')
        return value

    def task_model(self):
        return TASK_TYPES[self.type][1].model_validate(self.settings)


class CustomTask(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    entries: list[CustomTaskEntry] = Field(default_factory=list)

    @field_validator('entries')
    @classmethod
    def validate_entries(cls, entries):
        if len({entry.id for entry in entries}) != len(entries):
            raise ValueError('自定义任务条目 ID 不能重复')
        for entry in entries:
            entry.settings = entry.task_model().model_dump(exclude={'scheduler'})
        return entries

    def entry(self, entry_id: str):
        return next((entry for entry in self.entries if entry.id == entry_id), None)

    def list_payload(self):
        return {
            'entries': [
                {'id': entry.id, 'type': entry.type, 'settings': entry.settings, 'title': TASK_TYPES[entry.type][0], 'editor': ENTRY_PREFIX + entry.id}
                for entry in self.entries
            ],
            'choices': [{'type': key, 'title': label} for key, (label, _) in TASK_TYPES.items()],
        }

    def add(self, task_type: str):
        if task_type not in TASK_TYPES:
            raise ValueError('不支持此任务类型')
        task = TASK_TYPES[task_type][1]()
        self.entries.append(CustomTaskEntry(type=task_type, settings=task.model_dump(exclude={'scheduler'})))

    def reorder(self, ids: list[str]):
        entries = {entry.id: entry for entry in self.entries}
        if len(ids) != len(entries) or set(ids) != entries.keys():
            raise ValueError('任务列表已变化，请刷新后重试')
        self.entries = [entries[entry_id] for entry_id in ids]

    def remove(self, entry_id: str):
        entry = self.entry(entry_id)
        if entry is None:
            raise ValueError('此任务条目已删除')
        self.entries.remove(entry)
