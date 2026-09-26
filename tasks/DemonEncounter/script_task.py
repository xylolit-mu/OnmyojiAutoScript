# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
from time import sleep

from enum import Enum
from cached_property import cached_property
from datetime import datetime, timedelta

from module.logger import logger
from module.exception import GameStuckError, TaskEnd
from module.base.timer import Timer

from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.DemonEncounter.config import BossType, DemonEncounter, convert_to_general_battle_config
from tasks.DemonEncounter.page import page_rwt
from tasks.GameUi.default_pages import page_demon_encounter, page_main
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_shikigami_records
from tasks.DemonEncounter.assets import DemonEncounterAssets
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.DemonEncounter.data.answer import Answer


class LanternClass(Enum):
    BATTLE = 0  # 打怪  --> 无法判断因为怪的图片不一样，用排除法
    BOX = 1  # 开宝箱
    MAIL = 2  # 邮件答题
    REALM = 3  # 打结界
    EMPTY = 4  # 空
    MYSTERY = 5  # 神秘任务
    BOSS = 6  # 大鬼王


class ScriptTask(GameUi, GeneralBattle, DemonEncounterAssets, SwitchSoul):
    conf: DemonEncounter = None

    def screenshot(self):
        """逢魔长战斗超时时，确认仍在战斗则重置等待计时。"""
        try:
            return super().screenshot()
        except GameStuckError:
            if not self.exist_image() or not self.is_in_real_battle(False):
                raise
            logger.warning(
                'Demon encounter wait timeout, but battle is still active; '
                'reset stuck timer'
            )
            self.device.stuck_record_clear()
            self.device.stuck_record_add('BATTLE_STATUS_S')
            return super().screenshot()

    def run(self):
        self.conf = self.config.demon_encounter
        if not self.check_time():
            logger.warning('Time is not right')
            raise TaskEnd('DemonEncounter')
        # 切换御魂
        soul_config = self.config.demon_encounter.demon_soul_config
        best_soul_config = self.config.demon_encounter.best_demon_soul_config
        if soul_config.enable or best_soul_config.enable:
            self.goto_page(page_shikigami_records)
            self.checkout_soul()
        self.goto_page(page_rwt)
        if not self._check_challenge_count():
            logger.info('No challenge count left today, finish DemonEncounter')
            self.goto_page(page_main)
            self.set_next_run(task='DemonEncounter', success=False, finish=True)
            raise TaskEnd('DemonEncounter')
        self.execute_lantern()
        self.execute_boss()
        self.goto_page(page_main)
        self.set_next_run(task='DemonEncounter', success=True, finish=False)
        raise TaskEnd('DemonEncounter')

    def checkout_soul(self):
        """
        切换御魂
        """
        select_best_demon = getattr(self.conf.best_demon_boss_config, f'{self.boss_type}_select', False)
        if select_best_demon:
            group, team = getattr(self.conf.best_demon_soul_config, self.boss_type).split(",")
        else:
            group, team = getattr(self.conf.demon_soul_config, self.boss_type).split(",")
        if group and team:
            self.run_switch_soul_by_name(group, team)
            return
        logger.error(f'Unknown switch soul conf: group[{group}], team[{team}]')

    def _check_challenge_count(self) -> bool:
        """
        读地图顶部的「今日挑战次数」，返回 True 表示还有挑战次数。
        显示为 剩余/总: 1/1=还能打一次, 0/1=没次数了;
        DigitCounter 解析出的 current 即左侧的剩余次数。
        """
        for _ in range(3):
            self.screenshot()
            current, _remain, total = self.O_DE_CHALLENGE.ocr(self.device.image)
            if total > 0:
                logger.info(f'Demon encounter challenge count: {current}/{total}')
                return current > 0
            sleep(1)
        logger.warning('Challenge count not recognized, assume attempts remain')
        return True

    def execute_boss(self):
        """
        打boss
        :return:
        """
        logger.hr('Start boss battle', 1)

        def find_boss():
            search_button = self.I_DE_BOSS_BEST if self.best_demon_enable else self.I_DE_BOSS
            boss_name = 'best boss' if self.best_demon_enable else 'normal boss'

            # OCR 已确认仍有挑战次数, 搜不到多半是上一场战斗未结束/地图状态卡住。
            # 每轮搜索前若中央有未购买的宝箱展示, 先点左下角定位(小指针)把它挪开;
            # 一整轮(2次搜索)都没找到则重进逢魔之时清状态, 最多3轮。
            for reenter_round in range(1, 4):
                self.screenshot()
                if self.appear(self.I_DE_BOX_CENTER):
                    logger.info(
                        f'Box display at map center, click location to reset view '
                        f'(round {reenter_round}/3)'
                    )
                    self.appear_then_click(self.I_DE_LOCATION, interval=0)
                    time.sleep(1)

                # 最多重新执行两轮“逢魔/极逢魔 -> 地图中央首领”的完整流程。
                for search_attempt in range(1, 3):
                    self.device.click_record_clear()
                    self.screenshot()
                    if self.appear(self.I_BOSS_FIRE) or self.appear(self.I_BEST_BOSS_FIRE):
                        return True
                    if not self.appear_then_click(search_button, interval=0):
                        raise GameStuckError(f'Cannot find {boss_name} search button')
                    logger.info(
                        f'Finding {boss_name}, attempt {search_attempt}/2 '
                        f'(re-enter round {reenter_round}/3)...'
                    )
                    time.sleep(1)

                    # 每轮点击地图中央框选的红色“集结”区域至多两次，
                    # 每次等待集结挑战标志5秒。
                    for center_attempt in range(1, 3):
                        self.click(self.C_DM_BOSS_CLICK, interval=0)
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            self.screenshot()
                            if self.appear(self.I_BOSS_FIRE) or self.appear(self.I_BEST_BOSS_FIRE):
                                logger.info(
                                    f'{boss_name} gather appeared after center click '
                                    f'{center_attempt}/2'
                                )
                                return True
                            time.sleep(0.2)
                        logger.warning(
                            f'{boss_name} gather did not appear after center click '
                            f'{center_attempt}/2'
                        )

                    # 本轮失败，返回逢魔地图，重新点击逢魔/极逢魔进行下一轮搜寻。
                    self.screenshot()
                    if self.appear(self.I_UI_BACK_RED):
                        self.appear_then_click(self.I_UI_BACK_RED, interval=0)
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            self.screenshot()
                            if self.appear(search_button):
                                break
                            time.sleep(0.2)

                # 2次搜索都没找到: 复检次数(被用掉则优雅结束), 再重进地图清状态
                if not self._check_challenge_count():
                    logger.warning('Challenge count becomes 0 during boss search')
                    self.set_next_run(task='DemonEncounter', success=False, finish=True, server=True)
                    raise TaskEnd('DemonEncounter')
                logger.info(f'Boss not found, re-enter demon encounter map (round {reenter_round}/3)')
                self.goto_page(page_demon_encounter)
                self.goto_page(page_rwt)

            raise GameStuckError(
                f'Cannot enter {boss_name} after 3 re-enter rounds '
                f'(challenge count confirmed by OCR)'
            )

        def enter_boss():
            logger.info('trying to enter boss...')
            # 点击集结挑战
            boss_fire_count = 0  # 三次没点到就意味着今天已经挑战过了
            ocr_people_item = self.O_DE_BEST_BOSS_PEOPLE if self.best_demon_enable else self.O_DE_BOSS_PEOPLE
            while 1:
                self.screenshot()

                if self.appear(self.I_BOSS_FIRE) or self.appear(self.I_BEST_BOSS_FIRE):
                    current, remain, total = ocr_people_item.ocr(self.device.image)
                    if total == 300 and current >= 290:
                        logger.info('Boss battle people is full')
                        if not self.appear(self.I_UI_BACK_RED):
                            logger.warning('Boss battle people is full but no red back')
                            continue
                        self.ui_click_until_disappear(self.I_UI_BACK_RED)
                        # 退出重新选一个没人慢的boss
                        logger.info('Exit and reselect')
                        return False

                logger.info('Boss battle people is not full')

                if self.appear(self.I_BOSS_CONFIRM):
                    self.ui_click(self.I_BOSS_NO_SELECT, self.I_BOSS_SELECTED)
                    self.ui_click(self.I_BOSS_CONFIRM, self.I_BOSS_GATHER)
                    break
                if self.appear(self.I_BOSS_GATHER):
                    break
                if boss_fire_count >= 3:
                    logger.warning('Boss battle already done')
                    self.set_next_run(task='DemonEncounter', success=False, finish=True, server=True)
                    self.ui_click_until_disappear(self.I_UI_BACK_RED)
                    raise TaskEnd('DemonEncounter')

                if (self.appear_then_click(self.I_BOSS_FIRE, interval=3)
                        or self.appear_then_click(self.I_BEST_BOSS_FIRE, interval=3)):
                    boss_fire_count += 1
                    continue
            return True

        fail_count = 0
        while True:
            if fail_count >= 5:
                return
            if not find_boss():
                continue
            if enter_boss():
                break
            fail_count += 1

        logger.info('Boss battle confirm and enter')
        self.device.stuck_record_clear()
        # 等待挑战, 5秒也是等
        time.sleep(5)
        refresh_timer = Timer(280)
        while True:
            self.screenshot()
            if self.appear(self.I_BOSS_DONE_CHECK):
                break
            if self.appear(self.I_BOSS_GATHER):
                if not refresh_timer.started() or refresh_timer.reached():
                    self.device.stuck_record_clear()
                    self.device.stuck_record_add('BATTLE_STATUS_S')
                    logger.info('Boss Gathering...')
                    refresh_timer.reset()
                sleep(2)
                continue
            if self.appear(self.I_BOSS_WAIT):
                logger.info('Boss battle failed, waiting for 2 seconds...')
                self.device.stuck_record_clear()
                self.device.stuck_record_add('BATTLE_STATUS_S')
                refresh_timer.reset()
                sleep(2)
                continue
            if self.appear(self.I_PREPARE_HIGHLIGHT):
                if self.best_demon_enable:
                    general_battle_config = convert_to_general_battle_config(self.boss_type,
                                                                             best_demon_battle_conf=self.conf.best_demon_battle_config)
                else:
                    general_battle_config = convert_to_general_battle_config(self.boss_type,
                                                                             demon_battle_conf=self.conf.demon_battle_config)
                self.run_general_battle(config=general_battle_config, battle_key=self.boss_type)
                continue
            logger.info('Unknown scene Or Boss fight failed.waiting for Prepare_Button appear...')
            self.wait_until_appear(self.I_PREPARE_HIGHLIGHT, wait_time=2)

        # 等待回到挑战boss主界面
        self.wait_until_appear(self.I_BOSS_GATHER)
        while 1:
            self.screenshot()
            if self.appear(self.I_DE_LOCATION):
                break
            if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1):
                continue
            if self.appear_then_click(self.I_BOSS_BACK_WHITE, interval=1):
                continue
        # 返回到封魔主界面

    def execute_lantern(self):
        """
        点灯笼 四次
        :return:
        """
        # 先点四次
        ocr_timer = Timer(0.8)
        ocr_timer.start()
        while 1:
            self.screenshot()
            if not ocr_timer.reached():
                continue
            else:
                ocr_timer.reset()
            cu, re, total = self.O_DE_COUNTER.ocr(self.device.image)
            if cu + re != total:
                logger.warning('Lantern count error')
                continue
            if cu == 0 and re == 4:
                break

            if self.appear_then_click(self.I_DE_FIND, interval=2.5):
                continue
        logger.info('Lantern count success')
        # 然后领取红色达摩
        self.screenshot()
        if not self.appear(self.I_DE_AWARD):
            self.ui_get_reward(self.I_DE_RED_DHARMA)
        self.wait_until_appear(self.I_DE_AWARD)
        # 然后到四个灯笼
        match_click = {
            1: self.C_DE_1,
            2: self.C_DE_2,
            3: self.C_DE_3,
            4: self.C_DE_4,
        }
        lantern_types = self.scan_lantern_types()
        logger.info(
            'Lantern map: '
            + ', '.join(f'{index}={lantern_type.name}'
                        for index, lantern_type in lantern_types.items())
        )
        for i in range(1, 5):
            logger.hr(f'Check lantern {i}', 3)
            # 前面的事件可能改变右侧灯笼状态，处理前用当前画面复核。
            self.screenshot()
            current_type = self.check_lantern(i, screenshot=False)
            if current_type == LanternClass.EMPTY:
                lantern_type = LanternClass.EMPTY
            else:
                lantern_type = current_type
                if current_type != lantern_types[i]:
                    logger.info(
                        f'Lantern {i} changed: '
                        f'{lantern_types[i].name} -> {current_type.name}'
                    )
            self.device.click_record_clear()
            match lantern_type:
                case LanternClass.BOX:
                    self._box(match_click[i])
                case LanternClass.MAIL:
                    self._mail(match_click[i])
                case LanternClass.REALM:
                    self._realm(match_click[i])
                case LanternClass.EMPTY:
                    logger.warning(f'Lantern {i} is empty')
                case LanternClass.BATTLE:
                    self._battle(match_click[i])
                case LanternClass.MYSTERY:
                    self._mystery(match_click[i])
                case LanternClass.BOSS:
                    self._boss(match_click[i])
            time.sleep(1)

    def scan_lantern_types(self):
        """在同一帧确认四个固定位置，并返回各位置当前类型。"""
        self.screenshot()
        return {
            index: self.check_lantern(index, screenshot=False)
            for index in range(1, 5)
        }

    def check_lantern(self, index: int = 1, screenshot: bool = True):
        """
        检查灯笼的类型
        :param index: 四个灯笼，从1开始
        :return:
        """
        # 分类模板的搜索区(须容纳完整灯笼图案), 与点击区 C_DE_* 分离:
        # 点击区只包住内部图形, 模板放不进搜索区会全部误判成 battle
        match_roi = {
            1: self.C_DE_MATCH_1.roi_front,
            2: self.C_DE_MATCH_2.roi_front,
            3: self.C_DE_MATCH_3.roi_front,
            4: self.C_DE_MATCH_4.roi_front,
        }
        match_empty = {
            1: self.I_DE_DEFEAT_1,
            2: self.I_DE_DEFEAT_2,
            3: self.I_DE_DEFEAT_3,
            4: self.I_DE_DEFEAT_4,
        }
        self.I_DE_BOX.roi_back = match_roi[index]
        self.I_DE_LETTER.roi_back = match_roi[index]
        self.I_DE_MYSTERY.roi_back = match_roi[index]
        self.I_DE_REALM.roi_back = match_roi[index]
        self.I_DE_FIND_BOSS.roi_back = match_roi[index]
        target_box = self.I_DE_BOX
        target_letter = self.I_DE_LETTER
        target_mystery = self.I_DE_MYSTERY
        target_realm = self.I_DE_REALM
        target_find_boss = self.I_DE_FIND_BOSS
        target_empty = match_empty[index]

        # 使用同一截图依次分析四个固定位置。空灯笼优先，避免已完成位置
        # 因其他模板未命中而落入“战斗”兜底。
        if screenshot:
            self.screenshot()
        if self.appear(target_empty):
            logger.info(f'Lantern {index} is empty')
            return LanternClass.EMPTY
        elif self.appear(target_box):
            logger.info(f'Lantern {index} is box')
            return LanternClass.BOX
        elif self.appear(target_letter):
            logger.info(f'Lantern {index} is letter')
            return LanternClass.MAIL
        elif self.appear(target_mystery):
            logger.info(f'Lantern {index} is mystery task')
            return LanternClass.MYSTERY
        elif self.appear(target_realm):
            logger.info(f'Lantern {index} is realm')
            return LanternClass.REALM
        elif self.appear(target_find_boss):
            logger.info(f'Lantern {index} is boss')
            return LanternClass.BOSS
        else:
            # 无法判断是否是战斗的还是结界的
            logger.info(f'Lantern {index} is battle')
            return LanternClass.BATTLE

    def _box(self, target_click):
        box_buy_config = self.config.demon_encounter.box_buy_config
        if not self._enter_lantern_event(
                target_click,
                lambda: self.appear(self.I_JADE_50),
                'box purchase'):
            return
        while 1:
            self.screenshot()
            if not self.appear(self.I_MYSTERY_AMULET) and not (box_buy_config.box_buy_sushi and self.appear(self.I_SUSHI)):
                if self.appear_then_click(self.I_DE_FIND, interval=2.5):
                    break
            # 默认购买蓝票
            if self.appear(self.I_MYSTERY_AMULET):
                logger.info('Buy a mystery amulet for 50 jade')
                self.click(self.I_JADE_50)
                continue
            # 可选购买体力
            if box_buy_config.box_buy_sushi and self.appear(self.I_SUSHI):
                logger.info('Buy one hundred sushi for 50 jade')
                self.click(self.I_JADE_50)
                continue
        # 跳过购买后必须确认弹窗已关闭，残留弹窗会挡住后面的逢魔极/集结入口
        self._close_box_popup()

    def _close_box_popup(self, timeout=5):
        """确认宝箱购买弹窗已关闭；未关闭则继续点弹窗外侧直到关掉或超时。"""
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            if not self.appear(self.I_JADE_50):
                logger.info('Box purchase popup closed')
                return
            if timer.reached():
                logger.warning(f'Box purchase popup still visible after {timeout}s')
                return
            logger.info('Box purchase popup still visible, click outside to close')
            self.click(self.I_DE_FIND, interval=1)

    def _mail(self, target_click):
        # 答题
        def answer():
            click_match = {
                1: self.C_ANSWER_1,
                2: self.C_ANSWER_2,
                3: self.C_ANSWER_3,
            }
            index = None
            self.screenshot()
            question = self.O_LETTER_QUESTION.detect_text(self.device.image)
            question = question.replace('?', '').replace('？', '')
            answer_1 = self.O_LETTER_ANSWER_1.detect_text(self.device.image)
            answer_2 = self.O_LETTER_ANSWER_2.detect_text(self.device.image)
            answer_3 = self.O_LETTER_ANSWER_3.detect_text(self.device.image)
            if answer_1 == '其余选项皆对':
                index = 1
            elif answer_2 == '其余选项皆对':
                index = 2
            elif answer_3 == '其余选项皆对':
                index = 3
            if not index:
                index = Answer().answer_one(question=question, options=[answer_1, answer_2, answer_3])
            if index is None:
                index = 1
            logger.info(f'Question: {question}, Answer: {index}')
            return click_match[index]

        if not self._enter_lantern_event(
                target_click,
                lambda: self.appear(self.I_LETTER_CLOSE),
                'letter'):
            return
        logger.info('Question answering Start')
        for i in range(1, 4):
            # 还未测试题库无法识别的情况
            logger.hr(f'Answer {i}', 3)
            answer_click = answer()
            while 1:
                self.screenshot()
                if self.ui_reward_appear_click():
                    time.sleep(0.5)
                    while 1:
                        self.screenshot()
                        # 等待动画结束
                        if not self.appear(self.I_UI_REWARD, threshold=0.6):
                            logger.info('Get reward success')
                            break
                        # 一直点击
                        if self.ui_reward_appear_click():
                            continue
                    break
                # 如果没有出现红色关闭按钮，说明答题结束
                if not self.appear(self.I_LETTER_CLOSE):
                    time.sleep(2.5)
                    self.screenshot()
                    if not self.appear(self.I_LETTER_CLOSE):
                        self.ui_reward_appear_click()
                        logger.warning('Answer finish')
                        return

                # 一直点击
                self.click(answer_click, interval=1.5)
            time.sleep(0.5)

    def _enter_lantern_event(self, target_click, predicate, event_name):
        """灯笼入口仅点击一次；3秒未进入目标界面则跳过当前位置。"""
        self.click(target_click, interval=0)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.screenshot()
            if predicate():
                logger.info(f'Lantern entered {event_name}')
                return True
            time.sleep(0.2)
        logger.warning(f'Lantern did not enter {event_name} in 5s; mark handled and skip')
        return False

    def _battle(self, target_click):
        if not self._enter_lantern_event(
                target_click,
                lambda: not self.appear(self.I_DE_LOCATION)
                or self.appear(self.I_DE_SMALL_FIRE),
                'battle'):
            return
        if self.appear(self.I_DE_SMALL_FIRE):
            # 小鬼王
            logger.info('Small Boss')
            while 1:
                self.screenshot()
                if not self.appear(self.I_DE_SMALL_FIRE):
                    break
                if self.appear_then_click(self.I_DE_SMALL_FIRE, interval=1):
                    continue
        else:
            logger.info('Battle Start')
        self.current_count = 0
        if self.run_general_battle():
            logger.info('Battle End')

    def _realm(self, target_click):
        # 结界
        if not self._enter_lantern_event(
                target_click,
                lambda: self.appear(self.I_DE_REALM_FIRE)
                or not self.appear(self.I_DE_LOCATION),
                'realm'):
            return
        while 1:
            self.screenshot()
            if not self.appear(self.I_DE_LOCATION):
                logger.info('Battle Start')
                break
            if self.appear_then_click(self.I_DE_REALM_FIRE, interval=0.7):
                continue

        self.current_count = 0
        if self.run_general_battle():
            logger.info('Battle End')

    def _mystery(self, target_click):
        # 神秘任务， 不做
        pass

    def _boss(self, target_click):
        # 运气爆表，点灯笼出现大鬼王
        while 1:
            self.screenshot()
            if self.appear(self.I_BOSS_KILLED):
                # 这个大鬼王已经击败
                logger.warning('Boss already killed')
                self.ui_click_until_disappear(self.I_UI_BACK_RED)
                break
            if self.appear(self.I_BOSS_FIRE):
                self.execute_boss()
                break
            if self.click(target_click, interval=2.3):
                continue

    def check_time(self):
        """
        检查时间是否正确，
        如果正确就继续
        如果不在17:00到22:00之间,就推迟到下一个 17:30
        :return:
        """
        now = datetime.now()
        if now.hour < 17:
            # 17点之前，推迟到当天的17点半
            logger.info('Before 17:00, wait to 17:30')
            target_time = datetime(now.year, now.month, now.day, 17, 30, 0)
            self.set_next_run(task='DemonEncounter', success=False, finish=False, target=target_time)
            return False
        elif now.hour >= 23:
            # 23点之后，推迟到第二天的17:30
            logger.info('After 23:00, wait to 17:30')
            target_time = datetime(now.year, now.month, now.day, 17, 30, 0) + timedelta(days=1)
            self.set_next_run(task='DemonEncounter', success=False, finish=False, target=target_time)
            return False
        else:
            return True

    @property
    def boss_type(self) -> str:
        boss_name = BossType(datetime.now().weekday()).name
        if self.best_demon_enable:
            return f'best_demon_{boss_name}'
        return f'demon_{boss_name}'

    @property
    def best_demon_enable(self) -> bool:
        boss_name = BossType(datetime.now().weekday()).name
        return getattr(self.conf.best_demon_boss_config, f'best_demon_{boss_name}_select', False)


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('du')
    d = Device(c)
    t = ScriptTask(c, d)

    t.run()
