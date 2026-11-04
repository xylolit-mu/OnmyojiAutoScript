"""Declarative settings sections consumed by compatible clients."""
from pydantic import Field
from tasks.Component.config_base import ConfigBase


class SettingsLayoutConfig(ConfigBase):
    merged_settings_enable: bool = Field(default=True, title='合并配置显示',
        description='启用后按接口声明的项目合并配置块；关闭后逐块显示全部配置。')


def settings_section(key, title, groups=None, variants=None):
    section = {'id': key, 'title': title}
    if groups is not None:
        section['groups'] = groups
    if variants is not None:
        section['variants'] = variants
    return section
