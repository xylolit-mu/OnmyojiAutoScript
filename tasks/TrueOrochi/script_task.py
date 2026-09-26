# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from time import sleep
from datetime import datetime, timedelta

from module.logger import logger
from module.exception import TaskEnd, GameStuckError
from module.base.timer import Timer
from tasks.Orochi.page import page_orochi
from tasks.Orochi.script_task import ScriptTask as OrochiScriptTask
from tasks.Orochi.config import Layer
from tasks.GameUi.page import page_main, page_soul_zones, page_shikigami_records
from tasks.Exploration.page import page_gameplay
from tasks.TrueOrochi.assets import TrueOrochiAssets


class ScriptTask(OrochiScriptTask, TrueOrochiAssets):

    def _default_detect_categories(self) -> set[str]:
        categories = super()._default_detect_categories()
        categories.add("orochi")
        return categories

    def run(self):
        conf = self.config.true_orochi.true_orochi_config
        # 玩法tab的八岐大蛇模块在场说明本周还有次数，消失说明本周已打完
        self.goto_page(page_gameplay)
        if not self.appear(self.I_ST_MODULE):
            logger.warning('True orochi module absent, no attempts left this week')
            self.check_times(True)
            raise TaskEnd('TrueOrochi')
        self.goto_page(page_orochi)
        battle = self.check_true_orochi(True)
        if not battle:
            logger.warning('Not find true orochi')
            logger.warning('Try to battle orochi for ten times')
            # 判断是否需要挑战十层触发真蛇
            if not conf.find_true_orochi:
                logger.info('Not find_true_orochi_help')
                self.check_times(False)
                raise TaskEnd('TrueOrochi')
            battle = self.get_true_orochi()
        if not battle:
            # 如果还没有真蛇，那么就退出
            self.check_times(battle)
            raise TaskEnd('TrueOrochi')
        # 如果有真蛇，那么就开始战斗
        self.run_true_orochi_battle()
        self.check_times(True)

        self.goto_page(page_orochi)
        if self.check_true_orochi(True):
            logger.info('Find another true orochi entry, continue')
            self.run_true_orochi_battle()
            self.check_times(True)

        self.goto_page(page_main)
        raise TaskEnd('TrueOrochi')

    def run_true_orochi_battle(self):
        """执行一次真蛇战斗"""
        conf = self.config.true_orochi.true_orochi_config
        logger.hr('True Orochi Battle')
        # 御魂切换方式一
        if self.config.true_orochi.switch_soul.enable:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul(self.config.true_orochi.switch_soul.switch_group_team)
        # 御魂切换方式二
        if self.config.true_orochi.switch_soul.enable_switch_by_name:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul_by_name(self.config.true_orochi.switch_soul.group_name,
                                         self.config.true_orochi.switch_soul.team_name)
        self.goto_page(page_orochi)
        while 1:
            self.screenshot()
            if self.appear(self.I_ST_CREATE_ROOM):
                break
            if self.appear_then_click(self.I_UI_CONFIRM, interval=1):
                continue
            if self.appear_then_click(self.I_ST_FIRE, interval=4):
                continue
            if self.appear_then_click(self.I_FIND_TS, interval=1):
                continue
        self.ensure_private()
        while 1:
            self.screenshot()
            if self.appear(self.I_ST_FIRE_PREPARE):
                break
            if self.appear_then_click(self.I_FIRE, interval=3, threshold=0.7):
                continue
            if self.appear_then_click(self.I_UI_CONFIRM, interval=1):
                continue
            if self.appear_then_click(self.I_ST_CREATE_ROOM, interval=1):
                continue
        # 战斗准备
        logger.info('Battle prepare')
        self.ui_click(self.I_ST_FIRE_PREPARE, self.I_BUFF)
        while 1:
            self.screenshot()
            if not self.appear(self.I_BUFF):
                break
            if self.appear_then_click(self.I_ST_AUTO_FALSE, interval=1.8):
                continue

            # 下面代码 "点击准备" 会造成，点击了准备打第一层 循环 if not self.appear(self.I_BUFF): 时候跳出while循环，导致真蛇卡在第二层
            # if self.appear_then_click(self.I_PREPARE_HIGHLIGHT, interval=1.8):
            #     continue
        self.device.stuck_record_add('BATTLE_STATUS_S')
        self.device.click_record_clear()
        logger.info("Start battle process")
        check_timer = Timer(280)
        check_timer.start()
        check_count = 0
        while 1:
            self.screenshot()
            if self.appear(self.I_GREED_GHOST):
                sleep(0.7)
                self.screenshot()
                if not self.appear(self.I_GREED_GHOST):
                    continue
                # 左上角的贪吃鬼
                while 1:
                    self.screenshot()
                    if not self.appear(self.I_GREED_GHOST):
                        break
                    if self.appear_then_click(self.I_GREED_GHOST, interval=1):
                        continue
                    if self.appear_then_click(self.I_ST_FRAME, interval=1):
                        continue
                break
            if self.appear_then_click(self.I_ST_FRAME, interval=1):
                continue
            if check_timer.reached():
                if check_count > 3:
                    raise GameStuckError
                check_count += 1
                logger.warning('Battle timeout')
                check_timer.reset()
                self.device.stuck_record_clear()
                self.device.stuck_record_add('BATTLE_STATUS_S')
            sleep(0.5)

        logger.info("Battle process end")

    def get_true_orochi(self) -> bool:
        """获取真蛇(攻打十层10次, 发现真蛇就退出, 会更改limit_count)"""
        self.config.orochi.orochi_config.layer = Layer.TEN
        self.limit_count = 0
        self.limit_time = timedelta(hours=10)
        # 切换十层御魂
        if self.config.true_orochi.switch_soul.enable_switch_layer_soul:
            self.switch_orochi_souls()
            self.goto_page(page_orochi)
        while True:
            self.screenshot()
            if self.check_true_orochi(False):
                logger.info('Find true orochi')
                return True
            if self.limit_count >= 10:
                logger.warning('Not find true orochi')
                break
            self.limit_count += 1
            self.run_alone()
        return False

    def check_true_orochi(self, screenshot=False) -> bool:
        """
        检查是否出现真蛇（要求当前的界面必须是在御魂挑战的界面）
        :return:
        """
        if screenshot:
            self.screenshot()
        return self.appear(self.I_FIND_TS)

    def check_times(self, battle: bool):
        """
        根据是否有战斗结果设置下次运行时间
        :param battle:
        :return:
        """
        interval = self.config.true_orochi.scheduler.success_interval if battle \
            else self.config.true_orochi.scheduler.failure_interval
        self.set_next_run(task='TrueOrochi', target=datetime.now() + interval)


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()

    t.run()