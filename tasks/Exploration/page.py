from tasks.Component.GeneralInvite.general_invite import GeneralInvite
from tasks.Exploration.assets import ExplorationAssets
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.page import (all_of, any_of, page_battle, page_battle_prepare, page_battle_team, page_exploration,
                               page_main, page_shikigami_records, page_reward, page_battle_team_exit, random_click,
                               reward_random_click, page_battle_result)
from tasks.GameUi.page_definition import Page
from tasks.GlobalGame.assets import GlobalGameAssets

# 探索大地图 · 主线 tab（"章"字锚点，优先级高于 page_exploration 的兜底识别）
page_mainline = Page(all_of(GameUiAssets.I_CHECK_EXPLORATION, ExplorationAssets.I_CHECK_MAIN_TITLE), priority=60)
# 探索大地图 · 玩法 tab（"御魂"标题锚点）
page_gameplay = Page(all_of(GameUiAssets.I_CHECK_EXPLORATION, ExplorationAssets.I_CHECK_PLAY_TITLE), priority=60)


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
page_exp_entrance = Page(ExplorationAssets.I_E_EXPLORATION_CLICK)
page_exp_entrance.connect(page_exploration, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_exp_entrance->page_exploration")
page_mainline.connect(page_exp_entrance, action=lambda task: task.open_expect_level,
                      key="page_mainline->page_exp_entrance")

# 探索退出弹窗
page_exp_exit = Page(all_of(ExplorationAssets.I_E_CHECK_EXIT,
                            ExplorationAssets.I_E_EXIT_CONFIRM, ExplorationAssets.I_E_EXIT_CANCEL), priority=88)
page_exp_exit.connect(page_exp_entrance, ExplorationAssets.I_E_EXIT_CONFIRM, key="page_exp_exit->page_exp_entrance")

# 探索副本主界面
page_exp_main = Page(any_of(ExplorationAssets.I_E_SETTINGS_BUTTON, ExplorationAssets.I_E_AUTO_ROTATE_ON,
                            ExplorationAssets.I_E_AUTO_ROTATE_OFF))
page_exp_main.connect(page_exp_exit, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_exp_main->page_exp_exit")
page_exp_entrance.connect(page_exp_main, ExplorationAssets.I_E_EXPLORATION_CLICK, key="page_exp_entrance->page_exp_main")
page_exp_exit.connect(page_exp_main, ExplorationAssets.I_E_EXIT_CANCEL, key="page_exp_exit->page_exp_main")

# 探索设置界面
page_exp_settings = Page(ExplorationAssets.I_E_OPEN_SETTINGS, priority=75)
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
