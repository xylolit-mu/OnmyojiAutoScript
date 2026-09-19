"""式神活动统一页面定义。"""

import random
import time

from module.base.timer import Timer
from module.exception import GamePageUnknownError
from module.logger import logger
from tasks.ActivityShikigami.assets import ActivityShikigamiAssets
from tasks.Component.RightActivity.assets import RightActivityAssets
from tasks.GameUi.page import (
    Page,
    all_of,
    any_of,
    conditional_action,
    page_battle,
    page_battle_prepare,
    page_battle_result,
    page_main,
    page_reward,
    page_shikigami_records,
    random_click,
    reward_random_click,
)
from tasks.GlobalGame.assets import GlobalGameAssets


ACTIVITY_COLUMN_SWITCH_MAX_TRIES = 8
ACTIVITY_AUXILIARY_SETTLE_SECONDS = 1.0


def _settle_activity_auxiliary(task):
    """附属界面出现后等待一秒，再用新截图确认并处理。"""
    time.sleep(ACTIVITY_AUXILIARY_SETTLE_SECONDS)
    task.screenshot()


def find_activity_entry(task) -> bool:
    """在庭院右侧栏目中寻找本期式神活动入口。"""
    switched = 0
    for _ in range(ACTIVITY_COLUMN_SWITCH_MAX_TRIES):
        task.screenshot()
        if not task.appear(task.I_CHECK_MAIN):
            return False
        if task.appear(ActivityShikigamiAssets.I_MAIN_GOTO_ACT):
            return True
        if task.appear(RightActivityAssets.I_TOGGLE_BUTTON):
            _settle_activity_auxiliary(task)
            if task.appear_then_click(RightActivityAssets.I_TOGGLE_BUTTON, interval=0):
                switched += 1
        time.sleep(0.5)

    task.screenshot()
    if task.appear(ActivityShikigamiAssets.I_MAIN_GOTO_ACT):
        return True
    logger.warning(
        f'ActivityShikigami entry not found after switching columns {switched} times '
        f'({ACTIVITY_COLUMN_SWITCH_MAX_TRIES} checks)'
    )
    raise GamePageUnknownError('Cannot find ActivityShikigami entry')


def handle_activity_reward(task) -> bool:
    if not task.appear(GlobalGameAssets.I_UI_REWARD):
        return False
    _settle_activity_auxiliary(task)
    if not task.appear(GlobalGameAssets.I_UI_REWARD):
        return False
    click = random_click()
    logger.info(f'Clear activity reward page via {click.name}')
    task.click(click, interval=0)
    task.device.click_record_clear()
    return True


def handle_activity_close(task) -> bool:
    if not task.appear(GlobalGameAssets.I_UI_BACK_RED):
        return False
    _settle_activity_auxiliary(task)
    return task.appear_then_click(GlobalGameAssets.I_UI_BACK_RED, interval=0)


def handle_activity_story(task) -> bool:
    """处理剧情跳过按钮及其确认页面，两次操作前分别等待一秒。"""
    if task.appear(ActivityShikigamiAssets.I_SKIP_BUTTON):
        _settle_activity_auxiliary(task)
        if not task.appear_then_click(ActivityShikigamiAssets.I_SKIP_BUTTON, interval=0):
            return False
        task.device.click_record_clear()
        time.sleep(ACTIVITY_AUXILIARY_SETTLE_SECONDS)
        task.screenshot()
        if task.appear_then_click(ActivityShikigamiAssets.I_CONFIRM_SKIP, interval=0):
            task.device.click_record_clear()
        return True
    if task.appear(ActivityShikigamiAssets.I_CONFIRM_SKIP):
        _settle_activity_auxiliary(task)
        return task.appear_then_click(ActivityShikigamiAssets.I_CONFIRM_SKIP, interval=0)
    return False


def handle_activity_overlay(task) -> bool:
    """清理活动奖励、签到等附属页面，并等待活动主页稳定。"""
    timer = Timer(15).start()
    award_clicked = False
    while not timer.reached():
        task.screenshot()

        if task.appear(ActivityShikigamiAssets.I_ACTIVITY_AWARD):
            if not award_clicked:
                _settle_activity_auxiliary(task)
                if task.appear(ActivityShikigamiAssets.I_ACTIVITY_AWARD):
                    click = random_click()
                    logger.info(f'Clear activity award overlay via {click.name}')
                    task.click(click, interval=0)
                    task.device.click_record_clear()
                    award_clicked = True
            time.sleep(0.2)
            continue

        if task.appear(ActivityShikigamiAssets.I_ACTIVITY_SIGNIN_CLOSE):
            _settle_activity_auxiliary(task)
            if task.appear_then_click(ActivityShikigamiAssets.I_ACTIVITY_SIGNIN_CLOSE, interval=0):
                logger.info('Close activity sign-in overlay')
                task.device.click_record_clear()
            time.sleep(0.2)
            continue

        if task.appear(ActivityShikigamiAssets.I_CHECK_BATTLE_MAIN):
            # 主界面标志可能先于延迟弹窗出现，等待一秒后再确认稳定。
            _settle_activity_auxiliary(task)
            if task.appear(ActivityShikigamiAssets.I_ACTIVITY_AWARD) or \
                    task.appear(ActivityShikigamiAssets.I_ACTIVITY_SIGNIN_CLOSE):
                continue
            if task.appear(ActivityShikigamiAssets.I_CHECK_BATTLE_MAIN):
                logger.info('ActivityShikigami main page ready')
                return True

        time.sleep(0.2)

    logger.warning('ActivityShikigami overlays did not finish within 15s')
    return False


# 活动主页和附属弹窗均由本任务自行识别、进入和处理。
page_act = Page(
    any_of(
        ActivityShikigamiAssets.I_CHECK_BATTLE_MAIN,
        ActivityShikigamiAssets.I_ACTIVITY_AWARD,
        ActivityShikigamiAssets.I_ACTIVITY_SIGNIN_CLOSE,
    ),
    priority=70,
)
page_act.add_enter_success_hooks(handle_activity_overlay)
page_act.add_enter_failure_hooks(
    find_activity_entry,
    handle_activity_reward,
    handle_activity_close,
    handle_activity_story,
)
page_act.connect(page_main, GlobalGameAssets.I_UI_BACK_YELLOW, key='activity->main')
page_main.connect(page_act, ActivityShikigamiAssets.I_MAIN_GOTO_ACT, key='main->activity')

# 当期爬塔入口中间层。
page_activity_exploration = Page(ActivityShikigamiAssets.I_EXP_CHECK_EXPLORATION)
page_act.connect(page_activity_exploration, ActivityShikigamiAssets.I_TO_EXPLORATION,
                 key='activity->exploration')
page_activity_exploration.connect(page_act, ActivityShikigamiAssets.I_EXPLORATION_TO_MAIN,
                                  key='exploration->activity')

page_climb_main = Page(ActivityShikigamiAssets.I_CHECK_CLIMB_MAIN)
page_climb_main.connect(page_act, GlobalGameAssets.I_UI_BACK_YELLOW, key='climb_main->activity')

# 当期爬塔四种战斗页面。
page_climb_ap = Page(all_of(
    ActivityShikigamiAssets.I_CHECK_BATTLE_PASS,
    ActivityShikigamiAssets.I_CLIMB_MODE_AP,
))
page_climb_ap.connect(page_climb_main, GlobalGameAssets.I_UI_BACK_YELLOW, key='climb_ap->climb_main')

page_climb_pass = Page(all_of(
    ActivityShikigamiAssets.I_CHECK_BATTLE_PASS,
    ActivityShikigamiAssets.I_CLIMB_MODE_PASS,
))
page_climb_pass.connect(page_climb_main, GlobalGameAssets.I_UI_BACK_YELLOW, key='climb_pass->climb_main')

page_climb_ap100 = Page(ActivityShikigamiAssets.I_CLIMB_MODE_AP100)
page_climb_ap100.add_enter_failure_hooks(GlobalGameAssets.I_UI_BACK_RED)
page_climb_ap100.connect(page_climb_main, GlobalGameAssets.I_UI_BACK_YELLOW, key='climb_ap100->climb_main')

page_climb_boss = Page(ActivityShikigamiAssets.I_AS_BOSS_FIRE)
# 本期 Boss 入口挂在活动主界面，返回也回到活动主界面。
page_climb_boss.connect(page_act, GlobalGameAssets.I_UI_BACK_YELLOW, key='climb_boss->activity')

# 大富翁棋盘。
page_rich_man = Page(ActivityShikigamiAssets.I_CHECK_RM_RICHMAN)
page_rich_man.connect(page_act, GlobalGameAssets.I_UI_BACK_YELLOW, key='rich_man->activity')

# 伪神降临沿用旧素材作为占位；下次复刻时整体替换 fakegod 子目录。
page_fakegod_action = Page(ActivityShikigamiAssets.I_FG_CLIMB_MODE_PASS)
page_fakegod_action.connect(page_act, GlobalGameAssets.I_UI_BACK_YELLOW, key='fakegod_action->activity')
