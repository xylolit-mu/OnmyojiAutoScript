from cached_property import cached_property
from module.atom.gif import RuleGif
from module.atom.image import RuleImage
from module.logger import logger
import tasks.GameUi.task_pages as pages
from tasks.Exploration.base import BaseExploration
from tasks.Exploration.config import UpType, UserStatus
from tasks.Exploration.script_task import ScriptTask as ExplorationScriptTask
from tasks.Exploration.version import HighLight
from typing import Optional


class ExploreWantedBoss(Exception):
    # 出现一种情况，要求的怪是仅仅最后的Boss，其他小怪不是
    pass


class WQExplore(ExplorationScriptTask, HighLight):
    _explor_cnt: int = 0  # 探索次数
    _max_cnt: int = 0  # 探索最大次数

    @cached_property
    def _config(self):
        config = super()._config.model_copy(deep=True)
        # 探索悬赏使用当前阵容，预设上阵仅保留给秘闻路线。
        config.general_battle_config.preset_enable = False
        return config

    def _default_detect_categories(self) -> set[str]:
        categories = super()._default_detect_categories()
        categories.add("exploration")
        return categories

    @property
    def solo_difficulty(self) -> None:
        # 悬赏的「前往」已经指定章节和难度。
        return None

    def switch_rotate(self) -> bool:
        if self.config.wanted_quests.wanted_quests_config.auto_rotate:
            if self.appear(self.I_E_AUTO_ROTATE_OFF):
                self.click(self.C_CLICK_SETTINGS, interval=2)
                return True
        elif self.appear_then_click(self.I_E_AUTO_ROTATE_ON, interval=0.8):
            return True
        return False

    def run_on_exp_settings(self):
        if not self.config.wanted_quests.wanted_quests_config.auto_rotate:
            self.goto_page(pages.page_exp_main)
            return
        self.fill_shikigami()
        self.appear_then_click(self.I_E_AUTO_ROTATE_OFF, interval=0.8)

    def search_up_fight(self, up_type: UpType = None) -> Optional[RuleImage | RuleGif]:
        if self.appear(self.TEMPLATE_GIF):
            self.fire_monster_type = 'wq_normal'
            return self.TEMPLATE_GIF
        return None

    def check_exit(self, current_page: pages.Page | None) -> bool:
        need_exit = self._explor_cnt >= self._max_cnt
        # 探索次数已经够了但是任何怪物都没打过
        if need_exit and self.fire_monster_type == '':
            raise ExploreWantedBoss
        return need_exit

    def arrive_end(self) -> bool:
        arrived_end = BaseExploration.arrive_end(self)
        self._explor_cnt += 1 if arrived_end else 0
        return arrived_end

    def fire(self, button) -> bool:
        fired = super().fire(button)
        self._explor_cnt += 1 if fired and self.fire_monster_type == 'boss' else 0
        return fired

    def explore(self, goto: RuleImage, num: int):
        logger.info(f'Start exploring with number: {num}')
        self._max_cnt = num
        self._explor_cnt = 0
        while True:
            self.screenshot()
            if pages.page_exp_entrance == self.get_current_page():
                break
            if self.appear_then_click(goto, interval=2):
                continue
        self.user_status = UserStatus.ALONE
        self.exec_exp_page()

if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    from tasks.WantedQuests.assets import WantedQuestsAssets

    config = Config('oas1')
    device = Device(config)
    t = WQExplore(config, device)
    t.explore(goto=WantedQuestsAssets.I_GOTO_1, num=2)
