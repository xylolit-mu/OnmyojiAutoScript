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
page_lbs = Page(LBSAssets.I_LBS_TEAM_CHALLENGE, priority=70)
page_lbs.add_enter_failure_hooks(GlobalGameAssets.I_UI_REWARD)
# 庭院右栏轮播没刷出入口时切一栏，导航重试 main->lbs 边
page_lbs.add_enter_failure_hooks(conditional_action(
    condition=GameUiAssets.I_CHECK_MAIN,
    action=RightActivityAssets.I_TOGGLE_BUTTON,
))

# 活动点红色返回后的地图中转层；左上角返回回庭院
page_lbs_map = Page(LBSAssets.I_LBS_MAP_ENTRY, priority=60)

# 庭院右栏点 LBS 活动入口直达活动主界面
page_main.connect(page_lbs, LBSAssets.I_LBS_ENTRY, key='main->lbs')
page_lbs.connect(page_lbs_map, GlobalGameAssets.I_UI_BACK_RED, key='lbs->map')
page_lbs_map.connect(page_main, GlobalGameAssets.I_UI_BACK_YELLOW, key='lbs_map->main')
page_lbs_map.connect(page_lbs, LBSAssets.I_LBS_MAP_ENTRY, key='lbs_map->lbs')
