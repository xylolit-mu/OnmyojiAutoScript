# This Python file uses the following encoding: utf-8
from module.logger import logger
from datetime import datetime
from tasks.MetaDemon.boss_battle import BossBattle
from tasks.MetaDemon.config import MetaDemonMode


class ScriptTask(BossBattle):
    """超鬼王任务入口：门票刷取或鬼王战斗。"""

    def run(self):
        modes = self.config.meta_demon.meta_demon_config.mode
        full = True
        for mode in modes:
            if mode == MetaDemonMode.BOSS_BATTLE:
                self.start_time = datetime.now()
                return super().run()
            from tasks.MetaDemon.ticket_farming import run_ticket_farming
            full = run_ticket_farming(self, mode)
            logger.info('门票已满' if full else '门票刷取达到时间限制')
        self.set_next_run(task='MetaDemon', success=full)
        self.finish_task()
