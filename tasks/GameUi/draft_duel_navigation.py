"""协斗返回路线必须明确点离队，不能反复点击弹窗后面的返回按钮。"""
from tasks.DraftDuel.assets import DraftDuelAssets
from tasks.GlobalGame.assets import GlobalGameAssets


def handle_draft_duel_exit(task):
    if task.appear(DraftDuelAssets.I_EXIT):
        return task.appear_then_click(DraftDuelAssets.I_EXIT, interval=2)
    if (task.appear(DraftDuelAssets.I_EXIT_MOMENT) or
            task.appear(DraftDuelAssets.I_EXIT_CANCEL)):
        # 弹窗已打开，离队按钮单帧未识别时等待下一帧，不点暂离或背后的返回。
        return False
    return task.appear_then_click(GlobalGameAssets.I_UI_BACK_YELLOW, interval=3)
