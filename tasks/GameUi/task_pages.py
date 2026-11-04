from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.activity_handlers import handle_activity_close
"""普通任务的页面与导航集中定义；专用页面标志归 GameUi，业务按钮保留任务位置。"""

# tasks/AbyssShadows/page.py
from tasks.AbyssShadows.assets import AbyssShadowsAssets
from tasks.GameUi.page import Page, page_shirin, page_shikigami_records
from tasks.GlobalGame.assets import GlobalGameAssets

page_abyss = Page(GameUiAssets.I_PAGE_ABYSSSHADOWS_CHECK_ABYSS, key='page_abyss', name='page_abyss', category='abyss_shadows')
page_abyss_map = Page(GameUiAssets.I_PAGE_ABYSSSHADOWS_ABYSS_NAVIGATION, key='page_abyss_map', name='page_abyss_map', category='abyss_shadows')
page_shirin.connect(page_abyss, AbyssShadowsAssets.L_SHENSHE_TO_ABYSS, key="page_shirin->page_abyss")
page_abyss.connect(page_shirin, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_abyss->page_shirin")
page_abyss_map.connect(page_abyss, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_abyss_map->page_abyss")
page_abyss_map.connect(page_shikigami_records, AbyssShadowsAssets.I_ABYSS_SHIKI, key="page_abyss_map->page_shikigami_records", cost=4)
page_shikigami_records.connect(page_abyss_map, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_shikigami_records->page_abyss_map", cost=4)


# tasks/ActivityShikigami/page.py
"""式神活动统一页面定义。"""

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
        if task.appear(GameUiAssets.I_MAIN_GOTO_ACTIVITY):
            return True
        if task.appear(RightActivityAssets.I_TOGGLE_BUTTON):
            _settle_activity_auxiliary(task)
            if task.appear_then_click(RightActivityAssets.I_TOGGLE_BUTTON, interval=0):
                switched += 1
        time.sleep(0.5)

    task.screenshot()
    if task.appear(GameUiAssets.I_MAIN_GOTO_ACTIVITY):
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
    logger.info('Clear activity reward page via ui_reward')
    task.click(GlobalGameAssets.C_UI_REWARD, interval=0)
    task.device.click_record_clear()
    return True


# 保留旧导出名，跳过/确定/红叉统一走活动全局附属处理。
handle_activity_story = handle_activity_close


def handle_activity_overlay(task) -> bool:
    """清理活动奖励、签到等附属页面，并等待活动主页稳定。"""
    timer = Timer(15).start()
    award_clicked = False
    while not timer.reached():
        task.screenshot()

        if task.appear(GlobalGameAssets.I_UI_REWARD):
            if not award_clicked:
                _settle_activity_auxiliary(task)
                if task.appear(GlobalGameAssets.I_UI_REWARD):
                    logger.info('Clear activity award overlay via ui_reward')
                    task.click(GlobalGameAssets.C_UI_REWARD, interval=0)
                    task.device.click_record_clear()
                    award_clicked = True
            time.sleep(0.2)
            continue

        if handle_activity_close(task):
            continue

        if task.appear(GameUiAssets.I_PAGE_CHECK_ACTIVITY):
            # 主界面标志可能先于延迟弹窗出现，等待一秒后再确认稳定。
            _settle_activity_auxiliary(task)
            if task.appear(GlobalGameAssets.I_UI_REWARD) or \
                    task.appear(GameUiAssets.I_ACTIVITY_GENERAL_CLOSE_RED):
                continue
            if task.appear(GameUiAssets.I_PAGE_CHECK_ACTIVITY):
                logger.info('ActivityShikigami main page ready')
                return True

        time.sleep(0.2)

    logger.warning('ActivityShikigami overlays did not finish within 15s')
    return False


# 活动页面、入口及附属弹窗恢复统一由 GameUi 管理。
page_act = Page(any_of(GameUiAssets.I_PAGE_CHECK_ACTIVITY, GlobalGameAssets.I_UI_REWARD), priority=70, key='page_act', name='page_act', category='activity_shikigami')
page_act.add_enter_success_hooks(handle_activity_overlay)
page_act.add_enter_failure_hooks(
    find_activity_entry,
    handle_activity_reward,
    handle_activity_close,
)
page_act.connect(page_main, GlobalGameAssets.I_UI_BACK_YELLOW, key='activity->main')
page_main.connect(page_act, GameUiAssets.I_MAIN_GOTO_ACTIVITY, key='main->activity')

# 当期爬塔入口中间层。
PAGE_ACT_GENERAL_1 = Page(GameUiAssets.I_PAGE_CHECK_ACT_GENERAL_1, key='PAGE_ACT_GENERAL_1', name='PAGE_ACT_GENERAL_1', category='activity_shikigami')
# 兼容旧页面引用；同一个页面对象，不重复注册。
page_activity_exploration = PAGE_ACT_GENERAL_1
page_act.connect(PAGE_ACT_GENERAL_1, ActivityShikigamiAssets.I_TO_GENERAL_1,
                 key='activity->general_1')
PAGE_ACT_GENERAL_1.connect(page_act, GlobalGameAssets.I_UI_BACK_GENERAL_1,
                                  key='exploration->activity')

PAGE_ACT_GENERAL_2 = Page(GameUiAssets.I_PAGE_CHECK_ACT_GENERAL_2, key='PAGE_ACT_GENERAL_2', name='PAGE_ACT_GENERAL_2', category='activity_shikigami')
page_climb_main = PAGE_ACT_GENERAL_2
page_climb_main.connect(page_act, GlobalGameAssets.I_UI_BACK_YELLOW, key='climb_main->activity')

# 当期爬塔四种战斗页面。
page_climb_ap = Page(all_of(GameUiAssets.I_PAGE_CHECK_BATTLE_AP, ActivityShikigamiAssets.I_CLIMB_MODE_AP), key='page_climb_ap', name='page_climb_ap', category='activity_shikigami')
page_climb_ap.connect(page_act, GlobalGameAssets.I_UI_BACK_YELLOW, key='climb_ap->activity')

page_climb_pass = Page(all_of(GameUiAssets.I_PAGE_CHECK_BATTLE_PASS, ActivityShikigamiAssets.I_CLIMB_MODE_PASS), key='page_climb_pass', name='page_climb_pass', category='activity_shikigami')
page_climb_pass.connect(PAGE_ACT_GENERAL_1, GlobalGameAssets.I_UI_BACK_YELLOW, key='climb_pass->general_1')

page_climb_ap100 = Page(ActivityShikigamiAssets.I_CLIMB_MODE_AP100, key='page_climb_ap100', name='page_climb_ap100', category='activity_shikigami')
page_climb_ap100.add_enter_failure_hooks(handle_activity_close)
page_climb_ap100.connect(page_act, GlobalGameAssets.I_UI_BACK_YELLOW, key='climb_ap100->activity')

page_climb_boss = Page(ActivityShikigamiAssets.I_AS_BOSS_FIRE, key='page_climb_boss', name='page_climb_boss', category='activity_shikigami')
page_climb_boss.connect(page_act, GlobalGameAssets.I_UI_BACK_YELLOW, key='climb_boss->activity')

# 当前版本：主页面直达体力、百体、首领和通用页面 1；门票从通用页面 1 进入。
page_act.connect(page_climb_ap, ActivityShikigamiAssets.I_TO_BATTLE_AP,
                 key='activity->climb_ap')
page_act.connect(page_climb_ap100, ActivityShikigamiAssets.I_TO_BATTLE_AP100,
                 key='activity->climb_ap100')
page_act.connect(page_climb_boss, ActivityShikigamiAssets.I_TO_BATTLE_BOSS,
                 key='activity->climb_boss')
PAGE_ACT_GENERAL_1.connect(page_climb_pass, ActivityShikigamiAssets.I_GENERAL_1_TO_CLIMB,
                          key='general_1->climb_pass')


# 大富翁棋盘。
page_rich_man = Page(GameUiAssets.I_PAGE_CHECK_RICHMAN, key='page_rich_man', name='page_rich_man', category='activity_shikigami')
page_rich_man.connect(page_act, GlobalGameAssets.I_UI_BACK_YELLOW, key='rich_man->activity')

# 伪神降临沿用旧素材作为占位；下次复刻时整体替换 fakegod 子目录。
page_fakegod_action = Page(ActivityShikigamiAssets.I_FG_CLIMB_MODE_PASS, key='page_fakegod_action', name='page_fakegod_action', category='activity_shikigami')
page_fakegod_action.connect(page_act, GlobalGameAssets.I_UI_BACK_YELLOW, key='fakegod_action->activity')


# 每次到达活动子页面先清理同类红叉；失败恢复也复用同一资源。
for _activity_page in (page_activity_exploration, page_climb_main, page_climb_ap,
                       page_climb_pass, page_climb_ap100, page_climb_boss,
                       page_rich_man, page_fakegod_action):
    _activity_page.add_enter_success_hooks(handle_activity_close)
    if handle_activity_close not in _activity_page.on_enter_failure:
        _activity_page.add_enter_failure_hooks(handle_activity_close)


# tasks/CollectiveMissions/page.py
from tasks.CollectiveMissions.assets import CollectiveMissionsAssets
from tasks.GameUi.default_pages import page_shirin
from tasks.GameUi.matcher import all_of
from tasks.GameUi.page_definition import Page
from tasks.GlobalGame.assets import GlobalGameAssets

page_collective_missions = Page(all_of(CollectiveMissionsAssets.I_CM_RECORDS, CollectiveMissionsAssets.I_CM_SWITCH, GlobalGameAssets.I_UI_BACK_RED), key='page_collective_missions', name='page_collective_missions', category='collective_missions')
page_collective_missions.connect(page_shirin, GlobalGameAssets.I_UI_BACK_RED, key="page_collective_missions->page_shirin")
page_shirin.connect(page_collective_missions, CollectiveMissionsAssets.I_CM_CM, key="page_shirin->page_collective_missions")


# tasks/DailyTrifles/page.py
from tasks.DailyTrifles.assets import DailyTriflesAssets
from tasks.GameUi.default_pages import page_friends, page_guild
from tasks.GameUi.page import Page, page_mall, page_mall_recommend
from tasks.GlobalGame.assets import GlobalGameAssets

# 商店礼包屋页面
page_store_gift_room = Page(DailyTriflesAssets.I_GIFT_RECOMMEND, key='page_store_gift_room', name='page_store_gift_room', category='daily_trifles')
page_store_gift_room.connect(page_mall, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_store_gift_room->page_mall")
page_mall.connect(page_store_gift_room, DailyTriflesAssets.I_ROOM_GIFT, key="page_mall->page_store_gift_room")
page_mall_recommend.connect(page_store_gift_room, DailyTriflesAssets.I_ROOM_GIFT,
                            key="page_mall_recommend->page_store_gift_room")
# 好友吉闻页面
page_friends_luck = Page(GameUiAssets.I_PAGE_DAILYTRIFLES_LUCK_TITLE, priority=75, key='page_friends_luck', name='page_friends_luck', category='daily_trifles')
page_friends_luck.connect(page_friends, DailyTriflesAssets.I_CLOSE_LUCK_RED, key="page_friends_luck->page_friends")
page_friends.connect(page_friends_luck, DailyTriflesAssets.O_LUCK_MSG, key="page_friends->page_friends_luck")
page_friends_luck.add_enter_failure_hooks(DailyTriflesAssets.I_FRIENDSHIP_UP)
# 寮祈愿页面
page_guild_wish = Page(GameUiAssets.I_PAGE_DAILYTRIFLES_DT_CHECK_GUILD_WISH, key='page_guild_wish', name='page_guild_wish', category='daily_trifles')
page_guild_wish.connect(page_guild, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_guild_wish->page_guild")
page_guild.connect(page_guild_wish, DailyTriflesAssets.I_GUILD_TO_WISH, key="page_guild->page_guild_wish")


# tasks/DemonEncounter/page.py
from tasks.DemonEncounter.assets import DemonEncounterAssets
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.default_pages import page_demon_encounter, page_town
from tasks.GameUi.page_definition import Page
from tasks.GlobalGame.assets import GlobalGameAssets

# 现世逢魔页面
page_rwt = Page(GameUiAssets.I_CHECK_DEMON_ENCOUNTER, key='page_rwt', name='page_rwt', category='demon_encounter')
page_rwt.connect(page_town, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_rwt->page_town")
# 町中直接到现世逢魔
page_town.connect(page_rwt, GameUiAssets.I_TOWN_GOTO_DEMON_ENCOUNTER, key="page_town->page_rwt")
# 逢魔之时到现世逢魔
page_demon_encounter.connect(page_rwt, DemonEncounterAssets.I_DE_TO_REAL_WORLD, key="page_demon_encounter->page_rwt")


# tasks/Dokan/page.py
from tasks.Dokan.assets import DokanAssets
from tasks.GameUi.action import sequence
from tasks.GameUi.default_pages import (page_shirin, page_shikigami_records, page_battle, page_battle_result,
                                        page_battle_prepare, page_reward, random_click, page_main)
from tasks.GameUi.matcher import any_of
from tasks.GameUi.page_definition import Page
from tasks.GlobalGame.assets import GlobalGameAssets

# 道馆地图页面
page_dokan_map = Page(any_of(GameUiAssets.I_PAGE_DOKAN_RYOU_DOKAN_FOUND_DOKAN, GameUiAssets.I_PAGE_DOKAN_RYOU_DOKAN_FINDING_DOKAN), key='page_dokan_map', name='page_dokan_map', category='dokan')
page_shirin.connect(page_dokan_map, DokanAssets.I_SCENE_SHENSHE, key="page_shirin->page_dokan_map")
page_dokan_map.connect(page_shirin, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_dokan_map->page_shirin")


def map_enter_dokan(task) -> bool:
    try_count = 0
    while try_count < 5:
        task.screenshot()
        if task.appear(GameUiAssets.I_PAGE_DOKAN_RYOU_DOKAN_CHECK):
            return True
        pos = task.O_DOKAN_MAP.ocr_full(task.device.image)
        if pos != (0, 0, 0, 0):
            x = pos[0] + pos[2] / 2  # 取中间
            y = pos[1] - 20  # 往上偏移20
            task.device.click(x=x, y=y, control_name='dokan_map_goto_dokan')
        try_count += 1
    return False


# 道馆页面(必须道馆已经开启才可以调用)
page_dokan = Page(GameUiAssets.I_PAGE_DOKAN_RYOU_DOKAN_CHECK, key='page_dokan', name='page_dokan', category='dokan')
page_dokan.connect(page_dokan_map,
                   sequence(DokanAssets.I_RYOU_DOKAN_DOKAN_QUIT, DokanAssets.I_RYOU_DOKAN_EXIT_ENSURE,
                            success_index=1),
                   key="page_dokan->page_dokan_map")
page_dokan_map.connect(page_dokan, action=map_enter_dokan, key="page_dokan_map->page_dokan")
# 道馆->道馆地图->神社->寮->庭院  导航器会误认为(道馆->式神录->庭院)返回庭院最快, 增加花费可以减少惩罚纠错过程
page_dokan.connect(page_shikigami_records, DokanAssets.I_RYOU_DOKAN_SHIKIGAMI, key="page_dokan->page_shikigami_records", cost=4)
# 庭院->寮->神社->道馆地图->道馆  导航器会误认为(庭院->式神录->道馆)进入道馆最快, 同理需要增加花费
page_shikigami_records.connect(page_dokan, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_shikigami_records->page_dokan", cost=4)


def priority_enter_dokan(task) -> bool:
    priority_list = [DokanAssets.I_RYOU_DOKAN_ATTACK_PRIORITY_0,
                     DokanAssets.I_RYOU_DOKAN_ATTACK_PRIORITY_1,
                     DokanAssets.I_RYOU_DOKAN_ATTACK_PRIORITY_2,
                     DokanAssets.I_RYOU_DOKAN_ATTACK_PRIORITY_3,
                     DokanAssets.I_RYOU_DOKAN_ATTACK_PRIORITY_4]
    target_priority = priority_list[task.config.dokan.dokan_config.dokan_attack_priority]
    return task.appear_then_click(target_priority, interval=1.2)


# 道馆攻击优先级页面
page_dokan_priority = Page(DokanAssets.I_RYOU_DOKAN_ATTACK_PRIORITY_0, priority=75, key='page_dokan_priority', name='page_dokan_priority', category='dokan')
page_dokan.connect(page_dokan_priority, DokanAssets.I_RYOU_DOKAN_ATTACK_PRIORITY, key="page_dokan->page_dokan_priority")
page_dokan_priority.connect(page_dokan, priority_enter_dokan, key="page_dokan_priority->page_dokan")


# tasks/Exploration/page.py
from tasks.Exploration.assets import ExplorationAssets
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.page import (all_of, any_of, page_battle, page_battle_prepare, page_battle_team, page_exploration,
                               page_main, page_shikigami_records, page_reward, page_battle_team_exit, random_click,
                               reward_random_click, page_battle_result)
from tasks.GameUi.page_definition import Page
from tasks.GlobalGame.assets import GlobalGameAssets

# 探索大地图 · 主线 tab（"章"字锚点，优先级高于 page_exploration 的兜底识别）
page_mainline = Page(all_of(GameUiAssets.I_CHECK_EXPLORATION, GameUiAssets.I_PAGE_EXPLORATION_CHECK_MAIN_TITLE), priority=60, key='page_mainline', name='page_mainline', category='exploration')
# 探索大地图 · 玩法 tab（"御魂"标题锚点）
page_gameplay = Page(all_of(GameUiAssets.I_CHECK_EXPLORATION, GameUiAssets.I_PAGE_EXPLORATION_CHECK_PLAY_TITLE), priority=60, key='page_gameplay', name='page_gameplay', category='exploration')


def inherit_transitions(source: Page, *targets: Page) -> None:
    """把 source 的全部出边复制给 targets（tab 页继承大地图出口，动作与 tab 状态无关）。"""
    for target in targets:
        for transition in source.transitions:
            if transition.destination == target:
                continue
            if any(item.destination == transition.destination for item in target.transitions):
                continue
            target.connect(
                transition.destination,
                transition.action,
                cost=transition.cost,
                on_enter_success=transition.on_enter_success,
                on_enter_failure=transition.on_enter_failure,
                on_leave_success=transition.on_leave_success,
                on_leave_failure=transition.on_leave_failure,
            )


# 探索副本入口
page_exp_entrance = Page(ExplorationAssets.I_E_EXPLORATION_CLICK, key='page_exp_entrance', name='page_exp_entrance', category='exploration')
page_exp_entrance.connect(page_exploration, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_exp_entrance->page_exploration")
page_mainline.connect(page_exp_entrance, action=lambda task: task.open_expect_level,
                      key="page_mainline->page_exp_entrance")

# 探索退出弹窗
page_exp_exit = Page(all_of(GameUiAssets.I_PAGE_EXPLORATION_E_CHECK_EXIT, ExplorationAssets.I_E_EXIT_CONFIRM, ExplorationAssets.I_E_EXIT_CANCEL), priority=88, key='page_exp_exit', name='page_exp_exit', category='exploration')
page_exp_exit.connect(page_exp_entrance, ExplorationAssets.I_E_EXIT_CONFIRM, key="page_exp_exit->page_exp_entrance")

# 探索副本主界面
page_exp_main = Page(any_of(ExplorationAssets.I_E_SETTINGS_BUTTON, ExplorationAssets.I_E_AUTO_ROTATE_ON, ExplorationAssets.I_E_AUTO_ROTATE_OFF), key='page_exp_main', name='page_exp_main', category='exploration')
page_exp_main.connect(page_exp_exit, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_exp_main->page_exp_exit")
page_exp_entrance.connect(page_exp_main, ExplorationAssets.I_E_EXPLORATION_CLICK, key="page_exp_entrance->page_exp_main")
page_exp_exit.connect(page_exp_main, ExplorationAssets.I_E_EXIT_CANCEL, key="page_exp_exit->page_exp_main")

# 探索设置界面
page_exp_settings = Page(ExplorationAssets.I_E_OPEN_SETTINGS, priority=75, key='page_exp_settings', name='page_exp_settings', category='exploration')
page_exp_settings.connect(page_exp_main, ExplorationAssets.I_E_SURE_BUTTON, key="page_exp_settings->page_exp_main")
page_exp_main.connect(page_exp_settings, ExplorationAssets.C_CLICK_SETTINGS, key="page_exp_main->page_exp_settings")

# 大地图 tab 互切（固定坐标点击，幂等）
page_mainline.connect(page_gameplay, ExplorationAssets.C_CLICK_PALY_TITLE, key="page_mainline->page_gameplay")
page_gameplay.connect(page_mainline, ExplorationAssets.C_CLICK_MAIN_TITLE, key="page_gameplay->page_mainline")

# 大地图通用态 -> tab 桥接边：page_main->page_exploration 的到达确认与 tab 无关，
# 导航缓存也会停在 page_exploration，tab 页必须从这里可达才能从庭院方向进入
page_exploration.connect(page_mainline, ExplorationAssets.C_CLICK_MAIN_TITLE, key="page_exploration->page_mainline")
page_exploration.connect(page_gameplay, ExplorationAssets.C_CLICK_PALY_TITLE, key="page_exploration->page_gameplay")

# tab 页继承大地图全部出口（回庭院、底部栏玩法入口等），此后 page_exploration 新增出边自动跟随
inherit_transitions(page_exploration, page_mainline, page_gameplay)


# tasks/FloatParade/page.py
from tasks.FloatParade.assets import FloatParadeAssets
from tasks.GameUi.default_pages import page_main
from tasks.GameUi.page_definition import Page
from tasks.GlobalGame.assets import GlobalGameAssets

# 花车主界面
page_fp_main = Page(FloatParadeAssets.I_FP_TASKS, key='page_fp_main', name='page_fp_main', category='float_parade')
page_fp_main.connect(page_main, GlobalGameAssets.I_UI_BACK_YELLOW, key='page_fp_main->page_main')
page_main.connect(page_fp_main, FloatParadeAssets.I_FP_ACCESS, key='page_main->page_fp_main')
# 花车任务界面
page_fp_task = Page(FloatParadeAssets.I_FP_UPGRADE, key='page_fp_task', name='page_fp_task', category='float_parade')
page_fp_task.connect(page_fp_main, GlobalGameAssets.I_UI_BACK_YELLOW, key='page_fp_task->page_fp_main')
page_fp_main.connect(page_fp_task, FloatParadeAssets.I_FP_TASKS, key='page_fp_main->page_fp_task')
# 花车放置界面
page_fp_placement = Page(GameUiAssets.I_PAGE_FLOATPARADE_FP_PR_CHECK, key='page_fp_placement', name='page_fp_placement', category='float_parade')
page_fp_placement.connect(page_fp_main, GlobalGameAssets.I_UI_BACK_RED, key='page_fp_placement->page_fp_main')
page_fp_main.connect(page_fp_placement, FloatParadeAssets.I_FP_PLACEMENT_REWARD_ENTER, key='page_fp_main->page_fp_placement')


# tasks/FrogChallenge/page.py
from tasks.Component.RightActivity.assets import RightActivityAssets
from tasks.FrogChallenge.assets import FrogChallengeAssets
from tasks.GameUi.page import Page, any_of, page_main, page_shikigami_records
from tasks.GlobalGame.assets import GlobalGameAssets

# 青蛙瓷器挑战赛挑战页(点击庭院右侧图标直接进入, 没有中间页面)
page_frog_challenge = Page(any_of(GameUiAssets.I_PAGE_FROGCHALLENGE_CHECK_FROG_CHALLENGE, FrogChallengeAssets.I_FC_FIRE), key='page_frog_challenge', name='page_frog_challenge', category='frog_challenge')
page_frog_challenge.add_enter_failure_hooks(RightActivityAssets.I_TOGGLE_BUTTON)
page_frog_challenge.connect(
    page_main, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_frog_challenge->page_main"
)
page_main.connect(
    page_frog_challenge,
    FrogChallengeAssets.I_GOTO_FROG_CHALLENGE,
    key="page_main->page_frog_challenge",
)


# tasks/GuguArtStudio/page.py
from tasks.Component.RightActivity.assets import RightActivityAssets
from tasks.GameUi.default_pages import page_main, page_shikigami_records
from tasks.GameUi.matcher import any_of
from tasks.GameUi.page_definition import Page
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.GuguArtStudio.assets import GuguArtStudioAssets

# 呱呱画室主页面
page_gugu = Page(any_of(GameUiAssets.I_PAGE_GUGUARTSTUDIO_CHECK_GUGU_ACT, GuguArtStudioAssets.I_SUBMIT_PAINT, GuguArtStudioAssets.I_OBTAIN_PAINT), key='page_gugu', name='page_gugu', category='gugu_art_studio')
page_gugu.add_enter_failure_hooks(RightActivityAssets.I_TOGGLE_BUTTON)
page_gugu.connect(page_main, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_gugu->page_main")
page_main.connect(page_gugu, GuguArtStudioAssets.I_GAS_MAIN_TO_GUGU, key="page_main->page_gugu")

# 呱呱画室挑战页面
page_gugu_fire = Page(any_of(GuguArtStudioAssets.I_GAS_CAN_FIRE, GuguArtStudioAssets.I_GAS_CANNOT_FIRE), key='page_gugu_fire', name='page_gugu_fire', category='gugu_art_studio')
page_gugu_fire.connect(page_gugu, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_gugu_fire->page_gugu")
page_gugu.connect(page_gugu_fire, GuguArtStudioAssets.I_OBTAIN_PAINT, key="page_gugu->page_gugu_fire")


# tasks/GuildBanquet/page.py
from tasks.GameUi.default_pages import page_guild, page_main
from tasks.GameUi.matcher import any_of
from tasks.GameUi.page_definition import Page
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.GuildBanquet.assets import GuildBanquetAssets

# 宴会式神界面
page_banquet_shikigami = Page(GuildBanquetAssets.I_BANQUET_SWITCH, priority=75, key='page_banquet_shikigami', name='page_banquet_shikigami', category='guild_banquet')
page_banquet_shikigami.connect(page_guild, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_banquet_shikigami->page_guild")
page_guild.connect(page_banquet_shikigami, GuildBanquetAssets.I_BANQUET_EXP_FULL, key="page_guild->page_banquet_shikigami")

# 宴会切换式神界面
page_banquet_switch_shikigami = Page(any_of(GuildBanquetAssets.I_BANQUET_CONFIRM, GuildBanquetAssets.I_BANQUET_CLEAR_ALL, GuildBanquetAssets.I_BANQUET_CLEAR_ALL), priority=75, key='page_banquet_switch_shikigami', name='page_banquet_switch_shikigami', category='guild_banquet')
page_banquet_switch_shikigami.connect(page_banquet_shikigami, GlobalGameAssets.I_UI_BACK_RED,
                                      key="page_banquet_switch_shikigami->page_banquet_shikigami")
page_banquet_shikigami.connect(page_banquet_switch_shikigami, GuildBanquetAssets.I_BANQUET_SWITCH,
                               key="page_banquet_shikigami->page_banquet_switch_shikigami")



# tasks/IbukiArena/page.py
from tasks.Component.RightActivity.assets import RightActivityAssets
from tasks.GameUi.page import Page, any_of, page_main, page_shikigami_records
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.IbukiArena.assets import IbukiArenaAssets

# 伊吹之擂主界面
page_ibuki = Page(any_of(GameUiAssets.I_PAGE_IBUKIARENA_CHECK_IBUKI, IbukiArenaAssets.I_GOTO_ARENA), key='page_ibuki', name='page_ibuki', category='ibuki_arena')
page_ibuki.add_enter_failure_hooks(RightActivityAssets.I_TOGGLE_BUTTON)
page_ibuki.connect(
    page_main, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_ibuki->page_main"
)
page_main.connect(
    page_ibuki, IbukiArenaAssets.I_MAIN_TO_IBUKI, key="page_main->page_ibuki"
)

# 狭间幻境挑战界面
page_ibuki_arena = Page(GameUiAssets.I_PAGE_IBUKIARENA_CHECK_ARENA, key='page_ibuki_arena', name='page_ibuki_arena', category='ibuki_arena')
page_ibuki_arena.connect(
    page_ibuki, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_ibuki_arena->page_ibuki"
)
page_ibuki.connect(
    page_ibuki_arena, IbukiArenaAssets.I_GOTO_ARENA, key="page_ibuki->page_ibuki_arena"
)


# tasks/KekkaiUtilize/page.py
from tasks.Component.ReplaceShikigami.assets import ReplaceShikigamiAssets
from tasks.GameUi.matcher import any_of
from tasks.GameUi.page import Page, page_guild, random_click
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.KekkaiUtilize.assets import KekkaiUtilizeAssets

# 寮结界主界面
page_guild_realm = Page(KekkaiUtilizeAssets.I_REALM_SHIN, key='page_guild_realm', name='page_guild_realm', category='kekkai_utilize')
page_guild_realm.connect(page_guild, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_guild_realm->page_guild")
page_guild.connect(page_guild_realm, KekkaiUtilizeAssets.I_GUILD_REALM, key="page_guild->page_guild_realm")
# 放置结界卡界面
page_guild_card = Page(GameUiAssets.I_PAGE_KEKKAIUTILIZE_CHECK_GUILD_CARD, key='page_guild_card', name='page_guild_card', category='kekkai_utilize')
page_guild_card.connect(page_guild_realm, GlobalGameAssets.I_UI_BACK_RED, key="page_guild_card->page_guild_realm")
page_guild_realm.connect(page_guild_card, KekkaiUtilizeAssets.O_R_REALM, key="page_guild_realm->page_guild_card")
# 结界育成界面
page_guild_realm_growth = Page(ReplaceShikigamiAssets.I_RS_RECORDS_SHIKI, key='page_guild_realm_growth', name='page_guild_realm_growth', category='kekkai_utilize')
page_guild_realm_growth.connect(page_guild_realm, GlobalGameAssets.I_UI_BACK_BLUE, key="page_guild_realm_growth->page_guild_realm")
page_guild_realm.connect(page_guild_realm_growth, KekkaiUtilizeAssets.O_R_SHIKIGAMI, key="page_guild_realm->page_guild_realm_growth")
# 结界育成寄养页面
page_guild_realm_utilize = Page(KekkaiUtilizeAssets.I_U_ENTER_REALM, key='page_guild_realm_utilize', name='page_guild_realm_utilize', category='kekkai_utilize')
page_guild_realm_utilize.connect(page_guild_realm_growth, random_click, key="page_guild_realm_utilize->page_guild_realm_growth")
page_guild_realm_growth.connect(page_guild_realm_utilize, KekkaiUtilizeAssets.I_UTILIZE_ADD, key="page_guild_realm_growth->page_guild_realm_utilize")
# 好友结界页面
page_friend_realm = Page(KekkaiUtilizeAssets.O_R_FRIEND_REALM, key='page_friend_realm', name='page_friend_realm', category='kekkai_utilize')
page_friend_realm.connect(page_guild_realm, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_friend_realm->page_guild_realm")
# 好友寄养页面
page_friend_utilize = Page(recognizer=any_of(KekkaiUtilizeAssets.I_CHECK_FRIEND_REALM_1, KekkaiUtilizeAssets.I_CHECK_FRIEND_REALM_2, KekkaiUtilizeAssets.I_CHECK_FRIEND_REALM_3), key='page_friend_utilize', name='page_friend_utilize', category='kekkai_utilize')
page_friend_utilize.connect(page_friend_realm, GlobalGameAssets.I_UI_BACK_BLUE, key="page_friend_utilize->page_friend_realm")
page_guild_realm_utilize.connect(page_friend_utilize, KekkaiUtilizeAssets.I_U_ENTER_REALM, key="page_guild_realm_utilize->page_friend_utilize")
page_friend_realm.connect(page_friend_utilize, KekkaiUtilizeAssets.O_R_SHIKIGAMI, key="page_friend_realm->page_friend_utilize")
# 体力食盒
page_gr_ap_box = Page(KekkaiUtilizeAssets.I_AP_EXTRACT, priority=75, key='page_gr_ap_box', name='page_gr_ap_box', category='kekkai_utilize')
page_gr_ap_box.connect(page_guild_realm, GlobalGameAssets.I_UI_BACK_RED, key="page_gr_ap_box->page_guild_realm")
page_guild_realm.connect(page_gr_ap_box, KekkaiUtilizeAssets.I_BOX_AP, key="page_guild_realm->page_gr_ap_box")
# 经验酒壶
page_gr_exp_jug = Page(KekkaiUtilizeAssets.I_EXP_EXTRACT, priority=75, key='page_gr_exp_jug', name='page_gr_exp_jug', category='kekkai_utilize')
page_gr_exp_jug.connect(page_guild_realm, GlobalGameAssets.I_UI_BACK_RED, key="page_gr_exp_jug->page_guild_realm")
page_guild_realm.connect(page_gr_exp_jug, action=lambda task: \
    task.appear_then_click(KekkaiUtilizeAssets.I_BOX_EXP, interval=0.8) or \
    task.appear_then_click(KekkaiUtilizeAssets.I_BOX_EXP_MAX, interval=0.8), key="page_guild_realm->page_gr_exp_jug")


# tasks/LBS/page.py
"""LBS鬼王（限时活动）页面定义。

层级：庭院 -> 活动主界面（组队挑战）--红色返回--> 活动地图（中转层）--返回--> 庭院。
地图右下角 LBS策划鼠 图标可回到活动主界面。
"""

from tasks.Component.RightActivity.assets import RightActivityAssets
from tasks.GameUi.page import Page, conditional_action, page_main
from tasks.GameUi.assets import GameUiAssets
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.LBS.assets import LBSAssets

# 活动主界面；奖励弹窗遮住组队挑战按钮时由 enter_failure hook 清理
page_lbs = Page(LBSAssets.I_LBS_TEAM_CHALLENGE, priority=70, key='page_lbs', name='page_lbs', category='lbs')
page_lbs.add_enter_failure_hooks(GlobalGameAssets.I_UI_REWARD)
# 庭院右栏轮播没刷出入口时切一栏，导航重试 main->lbs 边
page_lbs.add_enter_failure_hooks(conditional_action(
    condition=GameUiAssets.I_CHECK_MAIN,
    action=RightActivityAssets.I_TOGGLE_BUTTON,
))

# 活动点红色返回后的地图中转层；左上角返回回庭院
page_lbs_map = Page(LBSAssets.I_LBS_MAP_ENTRY, priority=60, key='page_lbs_map', name='page_lbs_map', category='lbs')

# 庭院右栏点 LBS 活动入口直达活动主界面
page_main.connect(page_lbs, LBSAssets.I_LBS_ENTRY, key='main->lbs')
page_lbs.connect(page_lbs_map, GlobalGameAssets.I_UI_BACK_RED, key='lbs->map')
page_lbs_map.connect(page_main, GlobalGameAssets.I_UI_BACK_YELLOW, key='lbs_map->main')
page_lbs_map.connect(page_lbs, LBSAssets.I_LBS_MAP_ENTRY, key='lbs_map->lbs')


# tasks/MartialArts/page.py
"""武道大会页面图：庭院 -> 武道大会主页 -> 日常训练。"""

import random
import time

from module.base.timer import Timer
from module.exception import GamePageUnknownError
from module.logger import logger
from tasks.Component.RightActivity.assets import RightActivityAssets
from tasks.GameUi.action import conditional_action
from tasks.GameUi.default_pages import random_click
from tasks.GameUi.page import Page, any_of, page_main, page_shikigami_records
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.MartialArts.assets import MartialArtsAssets


ACTIVITY_COLUMN_SWITCH_MAX_TRIES = 8


def find_martial_arts_entry(task) -> bool:
    """循环切换庭院右侧活动栏目，直到武道大会入口出现。"""
    switched = 0
    for _ in range(ACTIVITY_COLUMN_SWITCH_MAX_TRIES):
        task.screenshot()
        if task.appear(MartialArtsAssets.I_MAIN_GOTO_MAR):
            return True
        if task.appear_then_click(RightActivityAssets.I_TOGGLE_BUTTON, interval=0.5):
            switched += 1
        time.sleep(0.5)

    task.screenshot()
    if task.appear(MartialArtsAssets.I_MAIN_GOTO_MAR):
        return True

    logger.warning(
        f'MartialArts entry not found after switching columns '
        f'{switched} times ({ACTIVITY_COLUMN_SWITCH_MAX_TRIES} checks)'
    )
    raise GamePageUnknownError(
        f'Cannot find MartialArts entry after '
        f'{ACTIVITY_COLUMN_SWITCH_MAX_TRIES} column switches'
    )


def handle_martial_arts_overlay(task) -> bool:
    """清理武道大会奖励页和签到弹窗，直到稳定显示活动主页。"""
    timer = Timer(task.CLICK_WAIT_TIMEOUT).start()
    award_clicked = False
    while not timer.reached():
        task.screenshot()

        if task.appear_then_click(MartialArtsAssets.I_MR_REWARD_MAIN, interval=0):
            logger.info('Claim MartialArts main-page reward')
            task.device.click_record_clear()
            time.sleep(0.2)
            continue

        if task.appear(GlobalGameAssets.I_UI_REWARD):
            if not award_clicked:
                click = random.choice([
                    MartialArtsAssets.C_RANDOM_TOP,
                    MartialArtsAssets.C_RANDOM_DOWN,
                    MartialArtsAssets.C_RANDOM_LEFT,
                    MartialArtsAssets.C_RANDOM_RIGHT,
                ])
                logger.info(f'Clear MartialArts award overlay via {click.name}')
                task.click(click, interval=0)
                task.device.click_record_clear()
                award_clicked = True
            time.sleep(0.2)
            continue

        if task.appear_then_click(MartialArtsAssets.I_MR_MAIN_SIGHIN_CLOSE, interval=0):
            logger.info('Close MartialArts sign-in overlay')
            task.device.click_record_clear()
            time.sleep(0.2)
            continue

        if task.appear(GameUiAssets.I_PAGE_MARTIALARTS_CHECK_MAIN_MAR):
            logger.info('MartialArts main page ready')
            return True

        time.sleep(0.2)

    logger.warning(
        f'MartialArts main page overlays did not finish within '
        f'{task.CLICK_WAIT_TIMEOUT}s'
    )
    return False


# 武道大会主页
page_martial_arts = Page(any_of(GameUiAssets.I_PAGE_MARTIALARTS_CHECK_MAIN_MAR, MartialArtsAssets.I_MR_REWARD_MAIN, GlobalGameAssets.I_UI_REWARD, MartialArtsAssets.I_MR_MAIN_SIGHIN_CLOSE), key='page_martial_arts', name='page_martial_arts', category='martial_arts')
page_martial_arts.add_enter_success_hooks(handle_martial_arts_overlay)
page_martial_arts.add_enter_failure_hooks(
    find_martial_arts_entry,
    conditional_action(GlobalGameAssets.I_UI_REWARD, random_click),
    GlobalGameAssets.I_UI_BACK_RED,
)
page_martial_arts.connect(page_main, GlobalGameAssets.I_UI_BACK_YELLOW,
                          key="page_martial_arts->page_main")
page_main.connect(page_martial_arts, MartialArtsAssets.I_MAIN_GOTO_MAR,
                  key="page_main->page_martial_arts")

# 日常训练（体力战斗）页面
page_martial_arts_ap = Page(GameUiAssets.I_PAGE_MARTIALARTS_CHECK_BATTLE_AP, key='page_martial_arts_ap', name='page_martial_arts_ap', category='martial_arts')
page_martial_arts_ap.connect(page_martial_arts, GlobalGameAssets.I_UI_BACK_YELLOW,
                             key="page_martial_arts_ap->page_martial_arts")
page_martial_arts.connect(page_martial_arts_ap, MartialArtsAssets.I_TO_BATTLE_AP,
                          key="page_martial_arts->page_martial_arts_ap")

# 修行合训（首领地图）页面
page_martial_arts_boss = Page(GameUiAssets.I_PAGE_MARTIALARTS_CHECK_BATTLE_BOSS, key='page_martial_arts_boss', name='page_martial_arts_boss', category='martial_arts')
page_martial_arts_boss.connect(page_martial_arts, GlobalGameAssets.I_UI_BACK_YELLOW,
                               key="page_martial_arts_boss->page_martial_arts")
page_martial_arts.connect(page_martial_arts_boss, MartialArtsAssets.I_TO_BATTLE_BOSS,
                          key="page_martial_arts->page_martial_arts_boss")

# 搜寻首领后的战斗小界面
page_martial_arts_boss_main = Page(GameUiAssets.I_PAGE_MARTIALARTS_CHECK_BATTLE_BOSS_MAIN, key='page_martial_arts_boss_main', name='page_martial_arts_boss_main', category='martial_arts')
page_martial_arts_boss_main.connect(
    page_martial_arts_boss,
    MartialArtsAssets.I_BOSS_MAIN_GOTO_BOSS,
    key="page_martial_arts_boss_main->page_martial_arts_boss",
)

# 战斗小界面进入式神录切换御魂，返回键回到战斗小界面。
page_martial_arts_boss_main.connect(
    page_shikigami_records,
    MartialArtsAssets.I_BATTLE_BOSS_TO_RECORDS,
    key="page_martial_arts_boss_main->page_shikigami_records",
    cost=2,
)
page_shikigami_records.connect(
    page_martial_arts_boss_main,
    GlobalGameAssets.I_UI_BACK_YELLOW,
    key="page_shikigami_records->page_martial_arts_boss_main",
    cost=2,
)


# tasks/MetaDemon/page.py
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.page import (
    Page,
    page_act_list,
    page_battle,
    page_battle_result,
    page_main,
    page_reward,
    page_shikigami_records,
    random_click,
    reward_random_click,
)
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.MetaDemon.assets import MetaDemonAssets

page_act_list_meta_demon = Page(GameUiAssets.I_PAGE_METADEMON_CHECK_ACT_LIST_METADEMON_ACT, key='page_act_list_meta_demon', name='page_act_list_meta_demon', category='meta_demon')
page_act_list_meta_demon.connect(page_main, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_act_list_meta_demon->page_main")

page_meta_demon = Page(GameUiAssets.I_PAGE_METADEMON_MD_CHECK_MAIN_PAGE, key='page_meta_demon', name='page_meta_demon', category='meta_demon')
page_meta_demon.add_enter_success_hooks(MetaDemonAssets.I_MD_GET_YESTERDAY_REWARD, MetaDemonAssets.I_MD_CLOSE_POPUP)
page_meta_demon.add_enter_failure_hooks(find_activity_entry, handle_activity_reward, handle_activity_close)
# 与式神活动共用庭院侧栏入口及栏目切换恢复逻辑，直接进入超鬼王主页。
page_main.connect(page_meta_demon, GameUiAssets.I_MAIN_GOTO_ACTIVITY, key="page_main->page_meta_demon")
page_meta_demon.connect(page_main, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_meta_demon->page_main")

page_meta_demon_boss = Page(GameUiAssets.I_PAGE_METADEMON_CHECK_BOSS_PAGE, key='page_meta_demon_boss', name='page_meta_demon_boss', category='meta_demon')
page_meta_demon_boss.add_enter_success_hooks(MetaDemonAssets.I_MD_CLOSE_POPUP)
page_meta_demon_boss.connect(page_meta_demon, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_meta_demon_boss->page_meta_demon")
page_meta_demon.connect(page_meta_demon_boss, GameUiAssets.I_PAGE_METADEMON_MD_CHECK_MAIN_PAGE, key="page_meta_demon->page_meta_demon_boss")

page_reward.connect(
    page_meta_demon_boss,
    reward_random_click(),
    key="page_reward->page_meta_demon_boss",
)
page_battle_result.connect(page_meta_demon_boss, random_click(), key="page_battle_result->page_meta_demon_boss")


# tasks/Orochi/page.py
from tasks.GameUi.default_pages import page_exploration, page_soul_zones, page_shikigami_records
from tasks.GameUi.matcher import any_of
from tasks.GameUi.page_definition import Page
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.Orochi.assets import OrochiAssets

# 御魂页面(魂1-魂10,魂11,12,13检查标志...)
page_orochi = Page(any_of(GameUiAssets.I_PAGE_OROCHI_OROCHI_CHECK_10, GameUiAssets.I_PAGE_OROCHI_OROCHI_CHECK_11, GameUiAssets.I_PAGE_OROCHI_OROCHI_CHECK_12, GameUiAssets.I_PAGE_OROCHI_OROCHI_CHECK_13), key='page_orochi', name='page_orochi', category='orochi')
page_orochi.connect(page_exploration, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_orochi->page_exploration")
page_soul_zones.connect(page_orochi, OrochiAssets.I_OROCHI, key="page_soul_zones->page_orochi")

page_orochi.connect(page_shikigami_records, OrochiAssets.I_SHI_RECORDS,
                    key="page_orochi->page_shikigami_records", cost=2)
page_shikigami_records.connect(page_orochi, GlobalGameAssets.I_UI_BACK_YELLOW,
                               key="page_shikigami_records->page_orochi", cost=2)


# tasks/OtherWorldTwilight/page.py
from tasks.GameUi.default_pages import page_demon_encounter
from tasks.GameUi.matcher import any_of
from tasks.GameUi.page_definition import Page
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.OtherWorldTwilight.assets import OtherWorldTwilightAssets

page_owt = Page(any_of(OtherWorldTwilightAssets.I_OWT_FIRE, OtherWorldTwilightAssets.I_OWT_TEAM), key='page_owt', name='page_owt', category='other_world_twilight')
page_owt.connect(page_demon_encounter, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_owt->page_demon_encounter")
page_demon_encounter.connect(page_owt, OtherWorldTwilightAssets.I_DE_TO_OTHER_WORLD, key="page_demon_encounter->page_owt")


# tasks/RealmRaid/page.py
from tasks.GameUi.page import page_shikigami_records, page_realm_raid
from tasks.GlobalGame.assets import GlobalGameAssets

page_shikigami_records.connect(page_realm_raid, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_shikigami_records->page_realm_raid", cost=2)


# SixRealms 保留原实现，只纳入统一加载顺序。
import tasks.SixRealms.page

# tasks/WeeklyPurchase/page.py
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.default_pages import (
    page_entertainment,
    page_guild,
    page_shirin,
    random_click,
)
from tasks.GameUi.page_definition import Page
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.WeeklyPurchase.assets import WeeklyPurchaseAssets


def handle_weekly_purchase_shrine_unknown(task) -> bool:
    """功勋商店导航遇到未知中间页时只补一次安全随机点击。"""

    if not getattr(task, '_weekly_purchase_guild_navigation', False):
        return False
    if getattr(task, '_weekly_purchase_shrine_random_clicked', False):
        return False

    task.screenshot()
    if task.appear(GameUiAssets.I_CHECK_GUILD):
        return False
    if task.appear(GameUiAssets.I_CHECK_SHRIN):
        return False

    task._weekly_purchase_shrine_random_clicked = True
    logger.warning(
        'Unknown page appeared while entering shrine; '
        'perform one safe random click'
    )
    task.click(random_click())
    return True


# 仅在 WeeklyPurchase 的功勋商店导航标志开启时执行上述保底；其他任务
# 仍沿用原本的公用神社跳转逻辑。
page_guild.connect(
    page_shirin,
    GameUiAssets.I_GUILD_TO_SHRIN,
    key="page_guild->page_shirin",
    on_enter_failure=[handle_weekly_purchase_shrine_unknown],
)

page_guild_store = Page(GameUiAssets.I_PAGE_WEEKLYPURCHASE_RM_CHECK_GUILD_STORE, priority=75, category='guild', key='page_guild_store', name='page_guild_store')
page_guild_store.connect(page_shirin, GlobalGameAssets.I_UI_BACK_RED, key="page_guild_store->page_shirin")
page_shirin.connect(page_guild_store, WeeklyPurchaseAssets.I_GUILD_STORE, key="page_shirin->page_guild_store")

page_itachi_shop = Page(GameUiAssets.I_PAGE_WEEKLYPURCHASE_ITACHI_SHOP_CHECK, priority=75, category='weekly_purchase', key='page_itachi_shop', name='page_itachi_shop')
page_entertainment.connect(
    page_itachi_shop,
    WeeklyPurchaseAssets.I_ITACHI_SHOP_ENTRY,
    key="page_entertainment->page_itachi_shop",
)
page_itachi_shop.connect(
    page_entertainment,
    GlobalGameAssets.I_UI_BACK_RED,
    key="page_itachi_shop->page_entertainment",
)


# tasks/WeeklyTrifles/page.py
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.default_pages import page_collection, page_guild, page_main
from tasks.GameUi.page_definition import Page
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.WeeklyTrifles.assets import WeeklyTriflesAssets

page_shikigami_collection = Page(WeeklyTriflesAssets.I_WT_SHARE, key='page_shikigami_collection', name='page_shikigami_collection', category='weekly_trifles')
page_shikigami_collection.add_enter_failure_hooks(GlobalGameAssets.I_UI_CANCEL_SAMLL)
page_collection.connect(
    page_shikigami_collection,
    GameUiAssets.I_CHECK_COLLECTION,
    key="page_collection->page_shikigami_collection",
)
page_shikigami_collection.connect(
    page_collection,
    GlobalGameAssets.I_UI_BACK_YELLOW,
    key="page_shikigami_collection->page_collection",
)

page_shikigami_share = Page(WeeklyTriflesAssets.I_WT_COLLECT_WECHAT, key='page_shikigami_share', name='page_shikigami_share', category='weekly_trifles')
page_shikigami_collection.connect(
    page_shikigami_share,
    WeeklyTriflesAssets.I_WT_SHARE,
    key="page_shikigami_collection->page_shikigami_share",
)
page_shikigami_share.connect(
    page_shikigami_collection,
    GlobalGameAssets.I_UI_BACK_RED,
    key="page_shikigami_share->page_shikigami_collection",
)
# 摸鱼行动页面
page_touch_fish = Page(GameUiAssets.I_PAGE_WEEKLYTRIFLES_CHECK_TOUCH_FISH, key='page_touch_fish', name='page_touch_fish', category='weekly_trifles')
page_guild.connect(
    page_touch_fish,
    WeeklyTriflesAssets.I_GUILD_GOTO_TF,
    key="page_guild->page_touch_fish",
)
page_touch_fish.connect(
    page_main,
    WeeklyTriflesAssets.I_WT_TF_GOTO_MAIN,
    key="page_touch_fish->page_main",
)

