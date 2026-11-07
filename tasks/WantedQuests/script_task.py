# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import re
from datetime import timedelta, time, datetime
from time import sleep

from tasks.GameUi.default_pages import page_battle_prepare, page_battle, random_click
from tasks.GameUi.matcher import any_of
from typing import List, Callable, Optional

from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from module.base.timer import Timer
from module.exception import TaskEnd
from module.image.recipes import match_highlight_rule
from module.logger import logger
from tasks.Component.Costume.config import MainType
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.GameUi.page import page_main, page_exploration, page_shikigami_records
from tasks.Secret.script_task import ScriptTask as SecretScriptTask
from tasks.WantedQuests.assets import WantedQuestsAssets
from tasks.WantedQuests.config import CooperationType, CooperationSelectMask, WQInfo, WQType, WantedQuestsConfig
from tasks.WantedQuests.explore import WQExplore, ExploreWantedBoss


class ScriptTask(WQExplore, SecretScriptTask, WantedQuestsAssets):
    WQ_LIST_MAX_SCROLLS = 4
    WQ_LIST_EDGE_MARGIN = 80

    # 追踪界面(显示"前往"按钮的界面,左上角位置,神秘任务不好使)显示以下名称时,任务不再执行
    unwanted_boss_name_list: list = []
    # 已经执行过的悬赏封印(仅记录执行过的, 不一定成功运行)
    wq_executed_set: set[tuple[str, int, WQInfo]] = set()

    def run(self):
        con = self.config.model.wanted_quests
        self.unwanted_boss_name_list = []
        unwanted_boss_names = con.wanted_quests_config.unwanted_boss_names
        if unwanted_boss_names is not None and unwanted_boss_names != '':
            self.unwanted_boss_name_list = re.split(r"[，,]", unwanted_boss_names)

        self.wq_executed_set = set()

        # 使用悬赏封印独立配置的御魂预设。
        self.switch_mission_souls()

        preSuc = False
        if (self.get_config()).cooperation_only:
            preSuc = self.pre_work_cooperation_only()
        else:
            preSuc = self.pre_work()
        if not preSuc:
            # 无法完成预处理 很有可能你已经完成了悬赏任务
            logger.warning('Cannot pre-work')
            logger.warning('You may have completed the reward task')
            self.next_run()
            raise TaskEnd('WantedQuests')

        error_count = 0
        self._wq_skipped_progress_areas = set()
        while 1:
            self.screenshot()
            if not self.is_wq_remained():
                logger.info("no more wq remained")
                break
            if self.appear(self.I_WQ_BOX):
                logger.info("get reward")
                self.ui_get_reward(self.I_WQ_BOX)
                continue
            if self.appear(self.I_E_REWARD_BOX_BIG):
                logger.info("get treasure")
                self.ui_get_reward(self.I_E_REWARD_BOX_BIG)
                continue
            current, found, total, self.O_WQ_TEXT_ALL.area = self.find_wq(
                self.device.image,
                allow_edge_fallback=(
                    error_count >= self.WQ_LIST_MAX_SCROLLS
                ),
            )
            if found == -1:
                if error_count >= self.WQ_LIST_MAX_SCROLLS:
                    logger.warning(
                        'No unfinished wanted quest after scrolling '
                        f'{self.WQ_LIST_MAX_SCROLLS} times, exit'
                    )
                    break
                error_count += 1
                # 当前画面没有可靠候选，保留原逻辑：手指上滑以继续浏览列表下方。
                self.swipe(self.S_WQ_LIST_UP, interval=1)
                self._wq_skipped_progress_areas.clear()
                sleep(1)
                continue
            if not self.open_wq_info():
                error_count += 1
                if error_count <= self.WQ_LIST_MAX_SCROLLS:
                    self.swipe(self.S_WQ_LIST_UP, interval=1)
                    self._wq_skipped_progress_areas.clear()
                    sleep(1)
                continue
            result = self.execute_mission(total - current)
            if result == 'skip_mystery':
                # 只屏蔽当前列表视口中的这一条；翻页后坐标失效，重新识别。
                self._wq_skipped_progress_areas.add(tuple(self.O_WQ_TEXT_ALL.area))
            else:
                error_count = 0
                self._wq_skipped_progress_areas.clear()
            sleep(1.5)
        self.next_run()
        raise TaskEnd('WantedQuests')

    def switch_mission_souls(self) -> None:
        """装配悬赏封印自己的御魂预设。"""
        soul_config = self.config.model.wanted_quests.switch_soul
        if soul_config.enable:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul(soul_config.switch_group_team)
        if soul_config.enable_switch_by_name:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul_by_name(
                soul_config.group_name, soul_config.team_name,
            )

    def open_wq_info(self) -> bool:
        """打开对应怪物悬赏详情界面"""
        timeout_timer = Timer(5).start()
        while not timeout_timer.reached():
            self.screenshot()
            if self.appear(self.I_TRACE_DISABLE) or self.appear(self.I_WQ_TRACE_ONE_REALWORLD):
                return True
            # 未揭晓的神秘妖怪详情没有取消追踪按钮。
            if self.appear(self.I_UI_BACK_RED) and '神秘妖怪' in self.read_wq_target():
                return True
            self.click(self.O_WQ_TEXT_ALL, interval=1.2)
        return False

    def read_wq_target(self) -> str:
        return ''.join(self.O_WQ_MONSTER_TYPE.detect_text(self.device.image).split())

    def next_run(self):
        before_end: time = self.get_config().before_end
        if before_end == time(hour=0, minute=0, second=0):
            self.set_next_run(task='WantedQuests', success=True, finish=True)
            return
        time_delta = timedelta(hours=-before_end.hour, minutes=-before_end.minute, seconds=-before_end.second)
        now_datetime = datetime.now()
        now_time = now_datetime.time()
        if time(hour=5) <= now_time < time(hour=18):
            # 如果是在5点到18点之间，那就设定下一次运行的时间为第二天的5点 + before_end
            next_run_datetime = datetime.combine(now_datetime.date() + timedelta(days=1), time(hour=5))
            next_run_datetime = next_run_datetime + time_delta
        elif time(hour=18) <= now_time < time(hour=23, minute=59, second=59):
            # 如果是在18点到23点59分59秒之间，那就设定下一次运行的时间为第二天的18点 + before_end
            next_run_datetime = datetime.combine(now_datetime.date() + timedelta(days=1), time(hour=18))
            next_run_datetime = next_run_datetime + time_delta
        else:
            # 如果是在0点到5点之间，那就设定下一次运行的时间为今天的18点 + before_end
            next_run_datetime = datetime.combine(now_datetime.date(), time(hour=18))
            next_run_datetime = next_run_datetime + time_delta
        self.set_next_run(task='WantedQuests', target=next_run_datetime)

    def pre_work(self):
        """
        前置工作，
        :return:
        """
        self.goto_page(page_main)
        done_timer = Timer(5)
        while 1:
            self.screenshot()
            if self.appear(self.I_TRACE_DISABLE):
                break
            if self.appear_then_click(self.I_WQ_SEAL, interval=1):
                continue
            if self.appear_then_click(self.I_WQ_DONE, interval=1):
                continue
            if self.appear_then_click(self.I_TRACE_ENABLE, interval=1):
                continue
            if self.special_main and self.click(self.C_SPECIAL_MAIN, interval=3):
                logger.info('Click special main left to find wanted quests')
                continue
            if self.appear(self.I_UI_BACK_RED):
                if not done_timer.started():
                    done_timer.start()
            if done_timer.started() and done_timer.reached():
                self.ui_click_until_disappear(self.I_UI_BACK_RED)
                return False
        # 已追踪所有任务
        logger.info('All wanted quests are traced')

        # 存在协作任务则邀请
        self.screenshot()
        if self.appear(self.I_WQ_INVITE_1) or self.appear(self.I_WQ_INVITE_2) or self.appear(self.I_WQ_INVITE_3):
            if self.need_invite_vip():
                self.all_cooperation_invite()
            else:
                self.invite_five()
        self.ui_click_until_disappear(self.I_UI_BACK_RED)
        self.goto_page(page_exploration)
        return True

    def pre_work_cooperation_only(self):
        if self.get_current_page() != page_main:
            self.goto_page(page_main)
        # 打开悬赏封印 界面
        done_timer = Timer(5)
        while 1:
            self.screenshot()
            if self.appear(self.I_TRACE_ENABLE) or self.appear(self.I_TRACE_DISABLE):
                break
            if self.appear_then_click(self.I_WQ_SEAL, interval=1):
                continue
            if self.appear_then_click(self.I_WQ_DONE, interval=1):
                continue
            if self.special_main and self.click(self.C_SPECIAL_MAIN, interval=3):
                logger.info('Click special main left to find wanted quests')
                continue
            if self.appear(self.I_UI_BACK_RED):
                if not done_timer.started():
                    done_timer.start()
            if done_timer.started() and done_timer.reached():
                self.ui_click_until_disappear(self.I_UI_BACK_RED)
                return False

        if not (self.appear(self.I_WQ_INVITE_1) or self.appear(self.I_WQ_INVITE_2) or self.appear(self.I_WQ_INVITE_3)):
            logger.info("there is no cooperation quest")
            return False

        # 追踪任务 并邀请
        self.all_cooperation_invite()

        self.ui_click_until_disappear(self.I_UI_BACK_RED)
        self.goto_page(page_exploration)
        return True

    def trace_one(self, btn: RuleImage):
        """
            参数必须为邀请按钮(I_WQ_INVITE_n ),特定场景,就不做通用的函数了,怪麻烦的,若还有什么奇葩需求,再扩展吧
        @param btn: 邀请按钮,
        @type btn:
        """
        self.screenshot()
        if not self.appear(btn):
            return
        while 1:
            self.screenshot()
            if self.appear(self.I_WQ_TRACE_ONE_REALWORLD):
                logger.info('Skip real-world wanted quest')
                self.ui_click_until_disappear(self.I_UI_BACK_RED, interval=1.5)
                return False
            # 追踪成功。
            if self.appear(self.I_WQ_TRACE_ONE_ENABLE):
                break
            if self.appear(self.I_WQ_TRACE_ONE_DISABLE):
                self.click(self.I_WQ_TRACE_ONE_DISABLE, interval=1.5)
                continue
            # 根据邀请按钮位置生成 对应的点击位置 打开追踪界面
            # NOTE magic Number

            self.device.click(btn.roi_front[0], btn.roi_front[1] - 40, control_name=str(btn) + ' y-40')
            # 防止点击后界面来不及刷新
            sleep(1.5)
        # 关闭单个任务的追踪界面
        self.ui_click_until_disappear(self.I_UI_BACK_RED, interval=1.5)

    def execute_mission(self, num_want: int):
        """
        执行对应的悬赏任务
        :param num_want: 一共要打败的怪物数量
        :return:
        """
        logger.hr('Start wanted quests')
        if self.appear(self.I_WQ_TRACE_ONE_REALWORLD):
            logger.info('Skip real-world wanted quest')
            self.ui_click_until_disappear(self.I_UI_BACK_RED, interval=1.5)
            self.goto_page(page_exploration)
            return False
        target_text = self.read_wq_target()
        ignored_target = next((name.strip() for name in self.unwanted_boss_name_list
                               if name.strip() and name.strip() in target_text), None)
        self._wanted_target_text = target_text
        if '神秘妖怪' in target_text:
            # 未揭晓目标没有取消追踪按钮，由列表扫描跳过本条。
            logger.info(f'Skip mystery wanted target without untracking: {target_text}')
            self.ui_click_until_disappear(self.I_UI_BACK_RED, interval=1.5)
            self.goto_page(page_exploration)
            return 'skip_mystery'
        if '未知目标' in target_text or ignored_target:
            logger.info(f'Skip wanted target: {target_text}')
            self.ui_click(self.I_WQ_TRACE_ONE_ENABLE, self.I_WQ_TRACE_ONE_DISABLE)
            self.ui_click_until_disappear(self.I_UI_BACK_RED, interval=1.5)
            self.goto_page(page_exploration)
            return False
        # 获取排序后的悬赏信息列表
        ordered_wq_infos = self.get_ordered_wq_infos(num_want)
        if not ordered_wq_infos:
            logger.info('Current wanted quest skipped all, cancel it')
            self.ui_click(self.I_WQ_TRACE_ONE_ENABLE, self.I_WQ_TRACE_ONE_DISABLE)
            self.ui_click_until_disappear(self.I_UI_BACK_RED, interval=1.5)
            self.goto_page(page_exploration)
            return False
        wq_call_dict: dict[WQType, Callable] = {
            WQType.CHALLENGE: self.challenge,
            WQType.EXPLORE: self.explore,
            WQType.SECRET: self.secret
        }
        # 获取当前需要执行的悬赏策略
        wq_info = self.get_need_exec_wq(ordered_wq_infos)
        if not wq_info:
            logger.info('Current wanted quests can not be executed')
            self.ui_click(self.I_WQ_TRACE_ONE_ENABLE, self.I_WQ_TRACE_ONE_DISABLE)
            self.ui_click_until_disappear(self.I_UI_BACK_RED, interval=1.5)
            self.goto_page(page_exploration)
            return False
        try:
            logger.info(f'Choose wq: {wq_info}')
            do_num = (num_want + wq_info.number - 1) // wq_info.number
            wq_call_dict[wq_info.type](wq_info.goto_btn, do_num)
        except ExploreWantedBoss:
            logger.warning('Maybe only need attack boss')
        finally:
            self.wq_executed_set.add((self._wanted_target_text, num_want, wq_info))
            self.goto_page(page_exploration)

    def get_need_exec_wq(self, ordered_wq_infos: List[WQInfo]) -> Optional[WQInfo]:
        """获取需要执行的悬赏(按优先级选择最靠前的)"""
        for wq_info in ordered_wq_infos:
            if WQType.CHALLENGE == wq_info.type:
                number_challenge = self.O_WQ_NUMBER.ocr(self.device.image)
                if number_challenge < 5:  # 挑战卷5张都没有了, 省着点吧试试别的
                    logger.warning("Challenge ticket num < 5, skip")
                    continue
            return wq_info
        return None

    def get_ordered_wq_infos(self, num_want: int) -> List[WQInfo]:
        """获取当前悬赏妖怪页面排好序的信息列表, 会自动排除无法执行的悬赏"""
        wq_type_ocr = [self.O_WQ_TYPE_1, self.O_WQ_TYPE_2, self.O_WQ_TYPE_3, self.O_WQ_TYPE_4]
        goto_btn_list = [self.I_GOTO_1, self.I_GOTO_2, self.I_GOTO_3, self.I_GOTO_4]
        wq_info_list = []
        for i in range(4):
            wq_info = self.build_wq_info(wq_type_ocr[i], goto_btn_list[i])
            if not wq_info:
                continue
            # 只有剩余数量也未变化时，才判定这条路线未取得进展。
            if (self._wanted_target_text, num_want, wq_info) in self.wq_executed_set:
                logger.warning('This wanted quest has been executed, skip')
                continue
            wq_info_list.append(wq_info)
        # 排序
        type_ordered_list = self.get_config()._wq_type_ordered_list
        wq_info_list.sort(key=lambda x: type_ordered_list.index(x.type))
        return wq_info_list

    @staticmethod
    def parse_wq_row(text: str):
        text = ''.join(text.split()).replace('：', ':')
        type_match = re.match(r'^(式神碎片|式神|挑战|探索|秘闻|妖气)', text)
        number_match = re.search(r'[数教]量:([0-9]+)', text)
        if not type_match or not number_match:
            return None
        number = int(number_match.group(1))
        if number <= 0:
            return None
        kind = type_match.group(1)
        return WQType('挑战' if kind in ('式神碎片', '式神') else kind), number

    def build_wq_info(self, type_rule: RuleOcr, goto_btn_rule: RuleImage) -> Optional[WQInfo]:
        row_text = type_rule.ocr(self.device.image)
        parsed = self.parse_wq_row(row_text)
        if parsed is None:
            logger.warning(f'Unknown wanted quest row: {row_text}')
            return None
        wq_type, number = parsed
        if wq_type not in self.get_config()._wq_type_ordered_list:
            return None
        if wq_type not in (WQType.CHALLENGE, WQType.EXPLORE, WQType.SECRET):
            return None
        return WQInfo(wq_type, number, goto_btn_rule)

    def challenge(self, goto_btn, num):
        self.ui_click(goto_btn, self.I_WQC_FIRE)
        self.ui_click_until_appear(self.I_WQC_UNLOCK,
            page_check=self.I_WQC_FIRE,
            expected_present=False)
        self.ui_click_until_disappear(self.I_WQC_FIRE)
        # 锁定阵容进入战斗
        wq_config = GeneralBattleConfig(lock_team_enable=True)
        self.run_general_battle(config=wq_config, exit_matcher=self.I_WQC_FIRE)

    def secret(self, goto, num=1):
        self.ui_click(goto, self.I_WQSE_FIRE)
        for i in range(num):
            self.screenshot()
            # 若是当周特殊秘闻则禁止连续进攻, 战斗结束之后直接退到探索页面重新进入挑战(避免当周秘闻没打结果跳转到第一层)
            if self.appear(self.I_WQSE_SPECIAL_FIRE):
                logger.warning('Current is special secret, exit and retry')
                return 
            # 又臭又长的对话针的是服了这个网易
            click_count = 0
            while 1:
                self.screenshot()
                if self.get_current_page() in [page_battle_prepare, page_battle]:
                    self.run_general_battle(self.battle_config, exit_matcher=any_of(self.I_UI_BACK_RED, self.I_WQSE_SPECIAL_FIRE))
                    self._finish_secret_battle_reward()
                    break
                if self.appear_then_click(self.I_WQSE_FIRE, interval=1):
                    continue
                if self.appear(self.I_UI_BACK_RED, threshold=0.7) and not self.appear(self.I_WQSE_FIRE):
                    self.click(self.C_SECRET_CHAT, interval=0.8)
                    click_count += 1
                    if click_count >= 6:
                        logger.warning('Secret mission chat too long, force to close')
                        click_count = 0
                        self.device.click_record_clear()
        logger.info('Secret mission finished')

    def _finish_secret_battle_reward(self):
        """秘闻悬赏结算后点击空白区域，直到回到已知挑战界面。"""
        while True:
            self.screenshot()
            if (
                self.appear(self.I_WQSE_FIRE)
                or self.appear(self.I_WQSE_SPECIAL_FIRE)
            ):
                return
            self.click(
                random_click(ltrb=(True, False, True, True)),
                interval=0.8,
            )

    def invite_random(self, add_button: RuleImage):
        self.screenshot()
        if not self.appear(add_button):
            return False
        self.ui_click(add_button, self.I_WQ_INVITE_ENSURE, interval=2.5)
        logger.info('enter invite form')
        sleep(1)
        self.click(self.I_WQ_FRIEND_1)
        sleep(0.4)
        self.click(self.I_WQ_FRIEND_2)
        sleep(0.4)
        self.click(self.I_WQ_FRIEND_3)
        sleep(0.4)
        self.click(self.I_WQ_FRIEND_4)
        sleep(0.4)
        self.click(self.I_WQ_FRIEND_5)
        sleep(0.2)
        self.screenshot()
        if not self.appear(self.I_SELECTED):
            logger.warning('No friend selected')
            return False
        self.ui_click_until_disappear(self.I_INVITE_ENSURE)
        sleep(0.5)

    def invite_five(self):
        """
        邀请好友，默认点五个
        :return:
        """

        logger.hr('Invite friends')
        self.invite_random(self.I_WQ_INVITE_1)
        self.invite_random(self.I_WQ_INVITE_2)
        self.invite_random(self.I_WQ_INVITE_3)

    def all_cooperation_invite(self, name_all: str = None):
        """
            所有的协作任务依次邀请
            如果配置了只完成协作任务 还会将该任务设置为追踪
        @return:

        """
        self.screenshot()
        if not self.appear(self.I_WQ_INVITE_1):
            return False

        ret = self.get_cooperation_info()
        if len(ret) == 0:
            logger.info("no Cooperation found")
            return False
        typeMask = 15
        typeMask = CooperationSelectMask[(self.get_config()).cooperation_type.value]
        for item in ret:
            # 该任务是需要邀请的任务类型
            if not (item['type'] & typeMask):
                # BUG 存在多个协作任务时,邀请完第一个协作任务对方接受后,未邀请的任务位置无法确定(缺少信息)
                # 例如 按顺序存在 abc 3个协作任务,邀请完a,好友接受后,这三个任务在界面上的顺序变化,abc 还是bca
                # 如果顺序不变 则应该没有问题
                logger.info("cooperationType %s But needed Type %s ,Skipped", item['type'], typeMask)
                break
            '''
               尝试5次 如果邀请失败 等待20s 重新尝试
               阴阳师BUG: 好友明明在线 但邀请界面找不到该好友(好友未接受任何协作任务的情况下)
           '''
            index = 0
            item['inviteResult'] = False
            if name_all is None:
                name = self.get_invite_vip_name(item['type'])
            else:
                name = name_all
            logger.warning("find cooperationType %s ,start invite %s", item['type'], name)
            while index < 5:
                if self.cooperation_invite(item['inviteBtn'], name):
                    item['inviteResult'] = True
                    index = 5
                    continue
                logger.info("%s not found,Wait 20s,%d invitations left", name, 5 - index - 1)
                index += 1
                sleep(20) if index < 5 else sleep(0)
                # NOTE 等待过程如果出现协作邀请 将会卡住 为了防止卡住
                self.screenshot()
            # 邀请追踪一起吧,只有邀请成功才追踪
            if item['inviteResult']:
                self.invite_success_callback(item['type'], name)
                if (self.get_config()).cooperation_only:
                    logger.info("start trace_one")
                    self.trace_one(item['inviteBtn'])
        return ret

    def cooperation_invite(self, btn: RuleImage, name: str):
        """
            单个协作任务邀请
        @param btn:
        @param name:
        @return:
        """
        self.ui_click(btn, stop=self.I_WQ_INVITE_ENSURE, interval=2.5)

        # 选人
        self.O_WQ_INVITE_COLUMN_1.keyword = name
        self.O_WQ_INVITE_COLUMN_2.keyword = name

        find = False
        for i in range(2):
            self.wait_until_appear(self.I_WQ_INVITE_FRIEND_LIST_APPEAR, wait_time=4)
            self.screenshot()
            in_col_1 = self.ocr_appear_click(self.O_WQ_INVITE_COLUMN_1)
            in_col_2 = self.ocr_appear_click(self.O_WQ_INVITE_COLUMN_2)
            find = in_col_2 or in_col_1
            if find:
                self.wait_until_appear(self.I_WQ_INVITE_SELECTED, wait_time=2)
                self.screenshot()
                if self.appear(self.I_WQ_INVITE_SELECTED):
                    logger.info("friend found and selected")
                    break
                # TODO OCR识别到文字 但是没有选中 尝试重新选择  (选择好友时,弹出协作邀请导致选择好友失败)
            # 检测跨服好友按钮是否高亮
            while 1:
                self.screenshot()
                if not self.appear_highlight(self.I_WQ_INVITE_DIFF_SVR_HIGHLIGHT):
                    self.click(self.I_WQ_INVITE_DIFF_SVR)
                    continue
                break
            # 等待好友列表加载
            self.wait_until_appear(self.I_WQ_INVITE_DIFF_SVR_HIGHLIGHT, wait_time=4)
        # 没有找到需要邀请的人,点击取消 返回悬赏封印界面
        if not find:
            self.screenshot()
            self.ui_click_until_disappear(self.I_WQ_INVITE_CANCEL, interval=1.5)
            return False
        #
        self.ui_click_until_disappear(self.I_WQ_INVITE_ENSURE, interval=1)
        return True

    def get_cooperation_info(self) -> List:
        """
            获取协作任务详情
        @return: 协作任务类型与邀请按钮
        """
        self.screenshot()
        retList = []
        i = 0
        for index in range(3):
            btn = self.__getattribute__("I_WQ_INVITE_" + str(index + 1))
            if not self.appear(btn):
                break
            if self.appear(self.__getattribute__("I_WQ_COOPERATION_TYPE_JADE_" + str(index + 1))):
                retList.append({'type': CooperationType.Jade, 'inviteBtn': btn})
                continue
            if self.appear(self.__getattribute__("I_WQ_COOPERATION_TYPE_DOG_FOOD_" + str(index + 1))):
                retList.append({'type': CooperationType.Food, 'inviteBtn': btn})
                continue
            if self.appear(self.__getattribute__("I_WQ_COOPERATION_TYPE_CAT_FOOD_" + str(index + 1))):
                retList.append({'type': CooperationType.Food, 'inviteBtn': btn})
                continue
            if self.appear(self.__getattribute__("I_WQ_COOPERATION_TYPE_SUSHI_" + str(index + 1))):
                retList.append({'type': CooperationType.Sushi, 'inviteBtn': btn})
                continue
            # NOTE 因为食物协作里面也有金币奖励 ,所以判断金币协作放在最后面
            if self.appear(self.__getattribute__("I_WQ_COOPERATION_TYPE_GOLD_" + str(index + 1))):
                retList.append({'type': CooperationType.Gold, 'inviteBtn': btn})
                continue
        logger.info(f"get cooperation size {len(retList)}")
        return retList

    # 使用平均亮度检测是否一致
    def appear_highlight(self, rule_image: RuleImage):
        return match_highlight_rule(rule_image, self.device.image, frame_id=self.device.image_frame_id)

    @property
    def special_main(self) -> bool:
        # 特殊的庭院需要点一下，左边然后才能找到图标
        main_type = getattr(self, 'current_main_type', None)
        if main_type is None:
            configured = self.config.global_game.costume_config.costume_main_type
            if len(configured) == 1:
                main_type = configured[0]
        return main_type == MainType.COSTUME_MAIN_3

    def get_config(self) -> WantedQuestsConfig:
        return self.config.wanted_quests.wanted_quests_config

    def need_invite_vip(self):
        return bool(self.get_config().invite_friend_name)

    def get_invite_vip_name(self, ctype: CooperationType):
        return self.get_config().invite_friend_name

    def invite_success_callback(self, ctype: CooperationType, name):
        """
           邀请成功回调
        @param ctype:
        @type ctype:
        @param name:
        @type name:
        """

        return True

    def process_ocr(self, txt):
        def detect_spliter(txt):
            index = txt.find('/')
            if index != -1:
                return index
            # 由于斜杠'/'经常被误识别为'7',且悬赏封印悬赏怪物总数没有与‘7’相关的数字
            reg = re.compile(r'^(\d+)([7/])(\d+)$')
            match = reg.match(txt)
            if match:
                return match.start(2)
            return -1

        index = detect_spliter(txt)
        if index < 0:
            return 0, 0, 0
        return int(txt[:index]), 1, int(txt[index + 1:])

    def txt_ocr_appear(self, ocr_item: RuleOcr, reg, img):
        res = ocr_item.ocr(img)
        regex = re.compile(reg)
        ismatch = regex.match(res)
        return ismatch is not None

    @staticmethod
    def _wq_box_xywh(box) -> List[int]:
        """将 OCR 的四点坐标转换为相对 OCR 截图的 xywh。"""
        xs = [int(point[0]) for point in box]
        ys = [int(point[1]) for point in box]
        x1, x2 = min(xs), max(xs)
        y1, y2 = min(ys), max(ys)
        return [x1, y1, x2 - x1, y2 - y1]

    @staticmethod
    def _normalize_wq_list_text(text: str) -> str:
        """清理列表 OCR 文本，保留进度分隔符的容错字符 7。"""
        return ''.join(str(text).split()).replace('／', '/')

    @staticmethod
    def _has_wq_label_hint(text: str) -> bool:
        """完整或被底部裁切的“悬赏封印”都可作为局部归属证据。"""
        if '悬赏' in text or '封印' in text:
            return True
        return re.search(r'[封野][印卬]', text) is not None

    def find_wq(
        self,
        img,
        *,
        allow_edge_fallback: bool = False,
    ) -> tuple[int, int, int, List[int]]:
        """
        在探索页面定位未完成的悬赏卡位。

        新逻辑先找进度，再用同一卡位附近的“悬赏/封印”文字确认归属，
        避免将真八岐大蛇等其他左侧入口误当成悬赏。靠近底部的卡位先
        通过外层翻页移到完整区域；连续翻页仍无法完整显示时才启用边缘保底。

        :param img: 当前截图
        :param allow_edge_fallback: 已连续翻页到下方时，允许点击文字裁切的底部卡位
        :return: (已完成数量, 有效标记, 总数量, 进度文字点击 roi)
        """
        res_list = self.O_WQ_TEXT_ALL.detect_and_ocr(img)
        progress_pattern = re.compile(
            r'(?<!\d)(\d{1,2})([7/])(\d{1,2})(?!\d)'
        )
        roi_x, roi_y, _, roi_height = self.O_WQ_TEXT_ALL.roi
        safe_progress_bottom = (
            roi_y + roi_height - self.WQ_LIST_EDGE_MARGIN
        )

        ocr_items = []
        for result in res_list:
            relative_area = self._wq_box_xywh(result.box)
            absolute_area = [
                relative_area[0] + roi_x,
                relative_area[1] + roi_y,
                relative_area[2],
                relative_area[3],
            ]
            ocr_items.append((
                self._normalize_wq_list_text(result.ocr_text),
                absolute_area,
            ))

        confirmed_candidates = []
        edge_candidates = []
        for text, progress_area in ocr_items:
            if any(abs(progress_area[1] - area[1]) <= 12
                   for area in getattr(self, '_wq_skipped_progress_areas', set())):
                continue
            match = progress_pattern.search(text)
            if not match:
                continue
            current, total = int(match.group(1)), int(match.group(3))
            if total <= 0 or total > 14 or current > total:
                logger.warning(
                    'Ignore abnormal wanted quest progress: '
                    f'{text}'
                )
                continue
            if current == total:
                continue

            progress_top = progress_area[1]
            progress_bottom = progress_top + progress_area[3]
            label_confirmed = self._has_wq_label_hint(text)
            if not label_confirmed:
                for nearby_text, nearby_area in ocr_items:
                    if not self._has_wq_label_hint(nearby_text):
                        continue
                    nearby_top = nearby_area[1]
                    nearby_bottom = nearby_top + nearby_area[3]
                    if (
                        nearby_bottom >= progress_top - 12
                        and nearby_top <= progress_bottom + 82
                    ):
                        label_confirmed = True
                        break

            candidate = (
                progress_top,
                current,
                total,
                progress_area,
                text,
            )
            if progress_top >= safe_progress_bottom:
                # 最底一项即使识别到局部文字，也先翻页将其移入完整区域。
                edge_candidates.append(candidate)
            elif label_confirmed:
                confirmed_candidates.append(candidate)

        candidates = confirmed_candidates
        if not candidates and allow_edge_fallback:
            candidates = edge_candidates
            if candidates:
                logger.warning(
                    'Use cropped bottom wanted quest as fallback after '
                    'list scrolling'
                )
        if candidates:
            _, current, total, click_area, raw_text = min(
                candidates,
                key=lambda item: item[0],
            )
            logger.info(
                f'Find wanted quest progress {raw_text}: '
                f'{current}/{total}, click={click_area}'
            )
            return current, 1, total, click_area
        return -1, -1, -1, [0, 0, 0, 0]

    def is_wq_remained(self):
        """检测是否仍有悬赏；兼容列表底部仅剩裁切卡位。"""
        if self.appear(self.O_WQ_LIST_CHECK):
            return True
        progress_pattern = re.compile(
            r'(?<!\d)(\d{1,2})([7/])(\d{1,2})(?!\d)'
        )
        for result in self.O_WQ_TEXT_ALL.detect_and_ocr(
            self.device.image,
            logDisplay=False,
        ):
            text = self._normalize_wq_list_text(result.ocr_text)
            match = progress_pattern.search(text)
            if not match:
                continue
            current, total = int(match.group(1)), int(match.group(3))
            if 0 <= current < total <= 14:
                logger.info(
                    'Wanted quest remains by cropped progress fallback: '
                    f'{current}/{total}'
                )
                return True
        return False


if __name__ == '__main__':
    # 原始版本
    strict_pattern = re.compile(r"^(.+?)\(?[数教]量:\s*(\d+)\)?$")
    # OCR容错版本
    ocr_pattern = re.compile(r"(.+?)\s*\(?[数教]量[:：]\s*(\d+)")
    test_cases = [
        "第十章(数量:2)",
        '第十章(数量:2)"',
        '第十章(数量:2""',
        "第十章 数量:2",
        "第十章 数量：2",
        "第十章(数量：2)",
        "第十章 (数量: 2)",
        "第十章(教量:2)",
        "第十章(数量:2abc)",
        "第十章(数量:20)",
        "第十章数量:2",
        "第十章",
        "",
    ]
    test_cases.extend([
        "第十章（数量：2）",  # 中文括号
        "第十章(数最:2)",  # OCR错字
        "第十章(数量:Z)",  # OCR数字识别失败
        "第十章(数量:2 )",
        "第十章(数量: 2)",
        "第十章(数量:02)",
        "第十章(数量:2.”)",
        "第十章(数量:2。)",
    ])

    def test(pattern, text):
        match = pattern.search(text)
        if not match:
            return "❌ 不匹配"
        return (
            f"✅ 匹配 "
            f"dest=[{match.group(1).strip()}], "
            f"number=[{int(match.group(2))}]"
        )

    for text in test_cases:
        print("=" * 60)
        print(f"输入: {repr(text)}")
        print("\n【严格模式】")
        print(test(strict_pattern, text))
        print("\n【OCR容错模式】")
        print(test(ocr_pattern, text))
        print()
