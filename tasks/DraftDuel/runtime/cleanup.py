"""协同对弈：cleanup。"""

import time
from module.exception import TaskEnd
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.page import page_town
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.GameUi.draft_duel_navigation import handle_draft_duel_exit


class DraftDuelCleanupMixin:
    def _finish_match_timeout(self):
        self._leave_draft_team('三次匹配超时')

    def _leave_draft_team(self, reason: str, *, success=True):
        """明确选择离队，确认返回町中后完成任务。"""
        logger.info(f'DraftDuel {reason}，离队返回町中')
        self._publish_draft_abort(reason)
        self._finalize_round_stats(completed=False, reason=reason)
        deadline = time.monotonic() + 30
        while True:
            self.device.stuck_record_clear()
            self.screenshot()
            if self.appear(GameUiAssets.I_CHECK_TOWN):
                break
            if time.monotonic() >= deadline:
                raise TaskEnd(f'协同对弈{reason}，离队返回町中未确认')
            handle_draft_duel_exit(self)
            time.sleep(0.3)
        self.set_next_run(task='DraftDuel', finish=success, success=success)
        self._clear_draft_checkpoint()
        self.config.notifier.push(title='协同对弈', content=f'{reason}，已离队返回町中并结束任务。')
        raise TaskEnd(f'协同对弈{reason}，已离队返回町中')

    def _finish_member_wait_timeout(self):
        self._finish_wait_timeout('等待开始过长', '600秒内队长未开始')

    def _finish_invite_wait_timeout(self):
        timeout = self.config.draft_duel.invite_config.wait_time_v.total_seconds()
        self._finish_wait_timeout('等待邀请过久', f'{timeout:g}秒内队友未接受邀请')

    def _finish_wait_timeout(self, message: str, detail: str):
        """队长/队员等待超时统一退出大厅，并按任务完成收尾。"""
        logger.warning(f'DraftDuel {message}：{detail}，返回町中并结束任务')
        # 复用已注册的黄色返回按钮路线；结束后停在町中。
        self.goto_page(page_town)
        self._clear_draft_checkpoint()
        self.set_next_run(task='DraftDuel', finish=True, success=True)
        self.config.notifier.push(
            title='协同对弈', content=f'{message}：{detail}，已返回町中并结束任务。',
        )
        raise TaskEnd(message)
