from cached_property import cached_property

from module.base.timer import Timer
from module.exception import GameStuckError, TaskEnd
from module.logger import logger
from tasks.Component.GeneralInvite.config_invite import InviteConfig
from tasks.Exploration.config import (
    AutoRotate, Exploration, ExplorationConfig, ExplorationDifficulty, UserStatus,
)
from tasks.ExplorationFight.config import ExplorationFightConfig
from tasks.Exploration.script_task import InviteFailedException, ScriptTask as ExplorationTask
from tasks.GameUi import task_pages as pages


class RunTimeReached(Exception):
    """探索推进达到运行时间上限。"""


class ScriptTask(ExplorationTask):
    _run_timer: Timer | None = None
    _boss_reward_timer: Timer | None = None

    def _infer_task_category(self) -> str:
        return pages.page_exp_main.category

    @cached_property
    def _config(self) -> Exploration:
        task = self.config.model.exploration_fight
        settings = task.exploration_fight_config
        group = settings.difficulty == ExplorationDifficulty.HARD and bool(task.invite_config.friend_list_v)
        config = Exploration(
            exploration_config=ExplorationConfig(
                limit_time=settings.limit_time, difficulty=settings.difficulty,
                collect_paper_reward=settings.collect_paper_reward, auto_rotate=settings.auto_rotate,
                choose_rarity=settings.choose_rarity,
                user_status=UserStatus.LEADER if group else UserStatus.ALONE,
            ),
            invite_config=InviteConfig(**task.invite_config.model_dump()),
            general_battle_config=task.general_battle_config.model_copy(deep=True),
            switch_soul_config=task.switch_soul_config,
        )
        if settings.auto_rotate == AutoRotate.yes:
            config.general_battle_config.lock_team_enable = False
        return config

    def ensure_exploration_team_locked(self) -> bool:
        if not self._config.general_battle_config.lock_team_enable:
            return False
        return super().ensure_exploration_team_locked()

    def screenshot(self):
        self.check_runtime()
        result = super().screenshot()
        self.check_runtime()
        return result

    def check_runtime(self) -> None:
        if self._run_timer is not None and self._run_timer.reached():
            raise RunTimeReached

    def run(self) -> None:
        settings = self.config.model.exploration_fight.exploration_fight_config
        limit = settings.limit_time
        self._run_timer = Timer(limit.hour * 3600 + limit.minute * 60 + limit.second).start()
        success = False
        try:
            self.check_runtime()
            self.pre_process()
            team_exit = self.navigator.resolve_page(pages.page_battle_team_exit)
            team_exit.connect(pages.page_exp_entrance, self.I_UI_CONFIRM, key='page_battle_team_exit->page_exp_entrance')
            self.run_chapters(settings)
            success = True
        except RunTimeReached:
            logger.info('探索推进达到运行时间上限')
            success = True
        except InviteFailedException as exc:
            logger.warning(f'探索推进邀请失败：{exc}')
        finally:
            self._run_timer = None
        self.set_next_run('ExplorationFight', finish=True, success=success)
        raise TaskEnd

    def run_chapters(self, settings: ExplorationFightConfig) -> None:
        config = self._config.exploration_config
        discover = settings.discover_monsters
        for chapter in settings.chapters:
            config.exploration_level = chapter
            while True:
                self.check_runtime()
                self.goto_page(pages.page_mainline)
                self.goto_page(pages.page_exp_entrance)
                self.ensure_difficulty(config.difficulty)
                if discover and not self.has_unknown_monster():
                    logger.info(f'{chapter.value}已无未知妖怪')
                    break
                logger.info(f'探索推进：{chapter.value}（{config.difficulty.value}）')
                self.run_chapter()
                if not discover:
                    break
        self.goto_page(pages.page_mainline)

    def has_unknown_monster(self) -> bool:
        # 确认切换完成后的详情已经渲染，再据此决定是否跳章。
        timeout = Timer(5).start()
        clear_frames = 0
        while not timeout.reached():
            self.screenshot()
            if not self.appear(self.I_E_EXPLORATION_CLICK):
                clear_frames = 0
                continue
            if self.appear(self.I_UNKNOWN_SHIKIGAMI):
                return True
            clear_frames += 1
            if clear_frames >= 3:
                return False
        raise GameStuckError('无法确认探索章节入口的未知式神标记')

    def fire(self, button) -> bool:
        fired = super().fire(button)
        if not fired or self.match_page_once(pages.page_exp_exit):
            self.fire_monster_type = ''
        return fired

    def run_chapter(self) -> None:
        self.need_exit = False
        self.fire_monster_type = ''
        self.paper_reward_pending = False
        self.pre_page = None
        self._boss_reward_timer = None
        self._match_end.refresh()
        self.device.click_record_clear()
        if self.user_status == UserStatus.LEADER:
            if not self.enter_team() or not self.run_invite(self._config.invite_config, is_first=True):
                raise InviteFailedException('未能开启组队挑战')
        else:
            self.goto_page(pages.page_exp_main)

        handlers = self.exp_page_handle_dict
        battle_exit = pages.any_of(
            pages.page_exp_main.recognizer, pages.page_exp_entrance.recognizer, pages.page_battle_team.recognizer,
        )
        while True:
            self.screenshot()
            if self.close_chat_sidebar_first():
                continue
            page = self.get_current_page()
            if self.handle_paper_reward_page(page):
                continue
            if page in (pages.page_exp_entrance, pages.page_mainline, pages.page_exploration):
                return
            if page in (pages.page_battle_prepare, pages.page_battle, pages.page_battle_result):
                won = self.run_general_battle(self._config.general_battle_config, exit_matcher=battle_exit)
                self._match_end.refresh()
                if self.fire_monster_type == 'boss' or not won:
                    self._boss_reward_timer = Timer(3).start()
            elif page == pages.page_exp_main and self._boss_reward_timer is not None:
                if self.collect_reward():
                    continue
                if self._boss_reward_timer.reached():
                    self.quit_exp_main()
            elif page == pages.page_battle_team and self._boss_reward_timer is not None:
                self.goto_page(pages.page_exp_entrance)
                return
            elif page in handlers:
                handlers[page]()
            self.pre_page = page
