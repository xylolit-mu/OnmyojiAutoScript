# This Python file uses the following encoding: utf-8
from pydantic import BaseModel, Field


class QuickLoadoutConfig(BaseModel):
    """按分组名称和预设名称识别的一键配置。"""

    enable: bool = Field(default=False, description='quick_loadout_enable_help')
    group_name: str = Field(default='', description='quick_loadout_group_name_help')
    preset_name: str = Field(default='', description='quick_loadout_preset_name_help')

    def validate_target(self) -> None:
        if not self.group_name.strip():
            raise ValueError('Quick loadout group name cannot be empty in OCR mode')
        if not self.preset_name.strip():
            raise ValueError('Quick loadout preset name cannot be empty in OCR mode')


class NamedQuickLoadoutConfig(QuickLoadoutConfig):
    """支持按任务关卡名称选择不同预设的一键配置。"""

    custom_preset_enable: bool = Field(
        default=False,
        description='quick_loadout_custom_preset_enable_help',
    )
    custom_preset: str = Field(
        default='',
        description='quick_loadout_custom_preset_help',
    )
