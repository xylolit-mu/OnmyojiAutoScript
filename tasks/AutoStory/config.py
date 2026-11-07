from pydantic import Field

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class AutoStory(ConfigBase):
    """自动剧情仅提供通用任务调度配置。"""

    scheduler: Scheduler = Field(default_factory=Scheduler)
