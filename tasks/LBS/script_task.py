# This Python file uses the following encoding: utf-8
import random
from datetime import time, timedelta
from time import sleep

from module.base.timer import Timer
from module.exception import TaskEnd
from module.logger import logger

from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.GeneralInvite.config_invite import InviteConfig
from tasks.Component.GeneralInvite.general_invite import GeneralInvite, RoomType
from tasks.Component.GeneralRoom.general_room import GeneralRoom
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_shikigami_records
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.LBS.assets import LBSAssets
from tasks.LBS.config import LBSMode
from tasks.LBS.page import page_lbs


class ScriptTask(GameUi, GeneralBattle, GeneralRoom, GeneralInvite, SwitchSoul, LBSAssets):

    def _exit_matcher(self):
        # 战斗结束的标志：回到活动界面（组队挑战按钮重新可见）
        return self.I_LBS_TEAM_CHALLENGE

    def _handle_result(self, context, config):
        # 活动的奖励转换确认弹窗，出现在结算界面
        self.appear_then_click(self.I_LBS_SETTLE_CONFIRM, interval=0.8)
        return super()._handle_result(context, config)

    def _handle_reward(self, context, config):
        self.appear_then_click(self.I_LBS_SETTLE_CONFIRM, interval=0.8)
        return super()._handle_reward(context, config)

    def run(self):
        limit = self.config.lbs.lbs_config.limit_time
        limit = timedelta(hours=limit.hour, minutes=limit.minute, seconds=limit.second)
        timeout = Timer(limit.total_seconds()).start()
        limit_count = self.config.lbs.lbs_config.limit_count

        if self.config.lbs.switch_soul.enable:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul(self.config.lbs.switch_soul.switch_group_team)
        if self.config.lbs.switch_soul.enable_switch_by_name:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul_by_name(self.config.lbs.switch_soul.group_name,
                                         self.config.lbs.switch_soul.team_name)

        # 导航直达活动：庭院右栏入口、轮播切换、加载等待全在 main->lbs 边上
        self.goto_page(page_lbs)

        mode = self.config.lbs.lbs_config.mode
        if mode == LBSMode.TEAM:
            self._run_team_match(timeout, limit_count)
        elif mode == LBSMode.DRIVE:
            self._run_drive_mode(timeout, limit_count)
        else:
            self._run_solo(timeout, limit_count)

        self.goto_page(page_main)
        self.set_next_run(task='LBS', success=True)
        raise TaskEnd('LBS')

    def _reach_exit(self, limit_count: int) -> bool:
        """两种模式共用的循环退出判断：想打次数达到 或 活动剩余次数耗尽。"""
        self.screenshot()
        current, remain, total = self.O_LBS_COUNT.ocr(self.device.image)
        if total > 0 and remain <= 0:
            logger.info(f'Challenge count exhausted: {current}/{total}')
            return True
        if total == 0:
            logger.warning('Challenge count ocr failed, continue by time limit')
        if self.current_count >= limit_count:
            logger.info(f'Reach limit count: {self.current_count}/{limit_count}')
            return True
        return False

    def _run_solo(self, timeout: Timer, limit_count: int):
        """单人模式：创建不公开房间直接挑战。"""
        fail_count = 0
        while not timeout.reached():
            if self._reach_exit(limit_count):
                break
            if not self.appear(self.I_GI_IN_ROOM):
                # 点组队挑战直到权限弹窗出现（弹窗里有创建按钮）；
                # 3 次点击无反应=次数实际耗尽（OCR 失败/误读的兜底）
                if not self.create_room(create_room_rule=self.I_LBS_TEAM_CHALLENGE,
                                        ensure_rules=[self.I_LBS_CREATE_ENSURE]):
                    fail_count += 1
                    if fail_count >= 3:
                        logger.warning('Create room failed 3 times in a row, abort')
                        break
                    continue
                self.ensure_private(room_mark=self.I_GI_IN_ROOM,
                                    private_rules=[self.I_LBS_ENSURE_PRIVATE],
                                    private_false_rules=[self.I_LBS_ENSURE_PRIVATE_FALSE])
                self.create_ensure(ensure_rules=[self.I_LBS_CREATE_ENSURE])
            fail_count = 0
            self.click_fire()
            self.run_general_battle(config=self.config.lbs.general_battle_config)

    def _run_team_match(self, timeout: Timer, limit_count: int):
        """组队模式：寻找队伍→自动匹配→等进房开战（年兽流程）。"""
        battle_config = self.config.lbs.general_battle_config
        match_fail = 0
        while not timeout.reached():
            if self._reach_exit(limit_count):
                break
            if self.appear(self.I_LBS_MATCH_CANCEL):
                # 排队横幅的X还在 = 还在排队：等进房开战
                if self._wait_team_battle(battle_config):
                    match_fail = 0
                continue
            # 未排队：寻找队伍进组队界面，点自动匹配直到排队横幅出现
            if self._start_team_match():
                match_fail = 0
            else:
                match_fail += 1
                if match_fail >= 3:
                    logger.warning('Team match failed 3 times in a row, abort')
                    break

    def _start_team_match(self) -> bool:
        if not self.ui_click_until_appear_or_timeout(
                self.I_LBS_FIND_TEAM, self.I_GR_AUTO_MATCH, interval=2, timeout=15):
            logger.warning('Team ui not reached')
            return False
        # 中间可能有消耗确认弹窗
        timer = Timer(15).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_LBS_MATCH_CANCEL):
                return True
            if self.appear_then_click(GlobalGameAssets.I_UI_CONFIRM, interval=2):
                continue
            if self.appear_then_click(self.I_GR_AUTO_MATCH, interval=3):
                continue
        logger.warning('Auto match not started')
        return False

    def _run_drive_mode(self, timeout: Timer, limit_count: int):
        """开车模式：自己不打，纯开公开房等人，有人进来随机延迟后退房，继续开。"""
        # LBS 是三人房（房主+2），手动指定 room_type，绕开过渡帧识别出 None 被缓存的问题
        self.room_type = RoomType.NORMAL_3
        # len=1 → 盯左槽 I_ADD_1：第一人上车就退（名字不参与匹配，仅占位凑长度）
        invite_config = InviteConfig(friend_list='drive')
        drives = 0
        fail_count = 0
        while not timeout.reached():
            if drives >= limit_count:
                logger.info(f'Reach drive limit: {drives}/{limit_count}')
                break
            if not self.is_in_room():
                # 建公开房：点未勾选的「所有人」单选项，直到「所有人」呈勾选态
                if not self.create_room(create_room_rule=self.I_LBS_TEAM_CHALLENGE,
                                        ensure_rules=[self.I_LBS_CREATE_ENSURE]):
                    fail_count += 1
                    if fail_count >= 3:
                        logger.warning('Create room failed 3 times in a row, abort')
                        break
                    continue
                self.ensure_public(room_mark=self.I_GI_IN_ROOM,
                                   public_rules=[self.I_LBS_ENSURE_PUBLIC],
                                   public_false_rules=[self.I_LBS_ENSURE_PUBLIC_FALSE])
                self.create_ensure(ensure_rules=[self.I_LBS_CREATE_ENSURE])
            fail_count = 0
            # 房内等乘客：通用 room_check_can_fire 判定（三人房第一人上车 = 左槽「+」消失）
            while 1:
                if not self.is_in_room():
                    # 房间没了（倒计时自动开战/解散），回外层重建
                    break
                if self.room_check_can_fire(invite_config):
                    # 有人上车：随机延迟后退房，算开了一趟
                    sleep(random.uniform(3, 6))
                    self.exit_room()
                    drives += 1
                    logger.info(f'LBS drive count: {drives}/{limit_count}')
                    break

    def _wait_team_battle(self, battle_config: GeneralBattleConfig) -> bool:
        """排队等待：进房等房主开战（房主跑了自己变房主点挑战）。战斗开始返回True。

        准备按钮在房主点挑战、进入战斗后才出现，由 run_general_battle 自己点
        （_prepare_click_ready），房内等待阶段不需要也不存在准备操作。
        """
        self.device.stuck_record_add('LOGIN_CHECK')
        match_timer = Timer(480).start()
        battled = False
        blank = 0
        while 1:
            self.screenshot()
            # 被秒开直接进战斗（check_take_over_battle 是待删旧入口，内联等价逻辑）
            if self.is_in_battle(False):
                logger.info('LBS take over battle')
                self.run_general_battle(config=battle_config)
                battled = True
                break
            # 挑战按钮可见=自己已是房主（原房主跑路），随机延迟后点击开打
            if self.appear(self.I_FIRE, threshold=0.7):
                logger.info('LBS become host, click fire')
                sleep(random.uniform(3, 6))
                self.appear_then_click(self.I_FIRE, interval=2)
                # 等战斗界面出现再交给通用战斗
                timer = Timer(30).start()
                while not timer.reached():
                    self.screenshot()
                    if self.is_in_battle(False):
                        break
                self.run_general_battle(config=battle_config)
                battled = True
                break
            if self.is_in_room():
                # 队员干等房主开战
                if match_timer.reached():
                    logger.warning('LBS match timeout, exit room')
                    self.exit_room()
                    break
                blank = 0
                continue
            if self.appear(self.I_LBS_MATCH_CANCEL):
                # 还在排队
                if match_timer.reached():
                    logger.warning('LBS match timeout, cancel queue')
                    cancel_timer = Timer(15).start()
                    while not cancel_timer.reached() and self.appear(self.I_LBS_MATCH_CANCEL):
                        self.appear_then_click(self.I_LBS_MATCH_CANCEL, interval=2)
                        self.appear_then_click(GlobalGameAssets.I_UI_CONFIRM, interval=2)
                        self.screenshot()
                    break
                blank = 0
                continue
            # 战斗/房间/横幅都不在：进房过渡帧或房主开战后的加载。
            # 进房瞬间横幅已消失而房间 UI 未渲染，单帧会误判，连续多帧才算
            blank += 1
            if blank >= 4:
                # 房间解散=房主已开战，等战斗界面出现后接管
                sleep(random.uniform(3, 6))
                self.run_general_battle(config=battle_config)
                battled = True
                logger.info('Room dismissed, battle should be starting')
                break
        self.device.stuck_record_clear()
        return battled
