# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
import random

from random import randint

from tasks.Component.GeneralRoom.assets import GeneralRoomAssets
from module.atom.ocr import RuleOcr
from module.atom.image import RuleImage
from tasks.base_task import BaseTask
from module.logger import logger
from module.base.timer import Timer


class GeneralRoom(BaseTask, GeneralRoomAssets):

    def create_room(self, create_room_rule: RuleImage = None) -> bool:
        """
        创建队伍  一般是下方的黄色按钮
        :return:
        """
        logger.info('Create room')
        create_room_rule = self.I_CREATE_ROOM if create_room_rule is None else create_room_rule
        if not self.appear(create_room_rule):
            logger.warning('No create room button')
            return False
        click_number = 0
        while 1:
            self.screenshot()
            if click_number > 3:
                logger.warning('Create room button do not take effect')
                logger.warning('The most possible reason is that there are not challenge tickets')
                return False
            if self.appear_then_click(create_room_rule, interval=2):
                click_number += 1
                continue
            if self.appear(self.I_CREATE_ENSURE):
                return True
            if self.appear(self.I_CREATE_ENSURE_2):
                return True
        return False

    def ensure_private(self, room_mark: RuleImage = None) -> bool:
        """
        确认私人房间, 不公开仅邀请

        正常情况下弹窗上的"不公开(仅邀请)"开关匹配到即返回。
        但存在建房过渡动画期间(弹窗已关闭、房间已建成)本方法仍被调用的时序,
        此时四个弹窗资产全部匹配不到会空转直至卡死检测。传入 room_mark 后,
        若检测到房间已建成则视为私人设置已生效, 直接返回, 避免空转。

        :param room_mark: 可选。房间已建成界面的特征图(如房间标题栏/协战队伍)。
                          为 None 时行为与旧版完全一致。
        :return:
        """
        logger.info('Ensure private')
        timeout = Timer(15).start()
        while 1:
            self.screenshot()
            if timeout.reached():
                logger.warning('Ensure private timeout')
                return False
            # 降阈值适配渲染偏差设备；小图标 0.8 太严
            if self.appear(self.I_ENSURE_PRIVATE, threshold=0.7):
                return True
            if self.appear(self.I_ENSURE_PRIVATE_2, threshold=0.7):
                return True
            if self.appear_then_click(self.I_ENSURE_PRIVATE_FALSE, interval=1, threshold=0.7):
                continue
            if self.appear_then_click(self.I_ENSURE_PRIVATE_FALSE_2, interval=1, threshold=0.7):
                continue
            # 弹窗上的开关匹配不到但房间已经建成: 私人设置此前已生效(或沿用上次设置), 直接放行
            if room_mark is not None and self.appear(room_mark):
                logger.info('Room already created, private ensured')
                return True
        return False

    def ensure_public(self) -> bool:
        logger.info('Ensure public')
        timeout = Timer(15).start()
        while 1:
            self.screenshot()
            if timeout.reached():
                logger.warning('Ensure public timeout')
                return False
            if self.appear(self.I_ENSURE_PUBLIC, threshold=0.7):
                return True
            if self.appear(self.I_ENSURE_PUBLIC_2, threshold=0.7):
                return True
            if self.appear_then_click(self.I_ENSURE_PUBLIC_FALSE, interval=1, threshold=0.7):
                continue
            if self.appear_then_click(self.I_ENSURE_PUBLIC_FALSE_2, interval=1, threshold=0.7):
                continue
        return False

    def create_ensure(self) -> bool:
        """
        创建确认
        :return:
        """
        logger.info('Create ensure')
        appear1 = self.I_CREATE_ENSURE.match(self.device.image, frame_id=self.device.image_frame_id)
        appear2 = self.I_CREATE_ENSURE_2.match(self.device.image, frame_id=self.device.image_frame_id)
        target = None
        if appear1:
            target = self.I_CREATE_ENSURE
        elif appear2:
            target = self.I_CREATE_ENSURE_2
        if not target:
            logger.warning('No create ensure button')
            return False

        while True:
            self.screenshot()
            if self.appear_then_click(target, interval=1.5):
                continue
            if not self.appear(target):
                return True
        return False

    def exit_team(self) -> bool:
        """
        在组队界面 退出组队的界面， 返回到庭院或者是你一开始进入的入口
        :return:
        """
        if self.appear(self.I_CHECK_TEAM):
            logger.info('Exit team ui')
            while 1:
                self.screenshot()
                if not self.appear(self.I_CHECK_TEAM):
                    return True
                if self.appear_then_click(self.I_GR_BACK_YELLOW, interval=0.5):
                    continue
        return False

    def check_zones(self, name: str) -> bool:
        """
        确认副本的名称，并选中
        :param name:
        :return:
        """
        pos = self.list_find(self.L_TEAM_LIST, name)
        if not pos:
            return False
        if name == '愤怒的石距' or name == '喷怒的石距':
            name = '价悠的石距'
        self.O_GR_ZONES_NAME.keyword = name
        click_timer = Timer(1.1)
        click_timer.start()
        while 1:
            self.screenshot()

            if self.ocr_appear(self.O_GR_ZONES_NAME):
                break
            # https://github.com/runhey/OnmyojiAutoScript/issues/488
            # 只能说朴实无华
            text_ocr = self.O_GR_ZONES_NAME.ocr(self.device.image)
            if name == '石距' and name in text_ocr:
                break
            if name == '金币妖怪' and "金币" in text_ocr:
                break
            if name == '经验妖怪' and '经验' in text_ocr:
                break
            if click_timer.reached():
                click_timer.reset()
                self.device.click(x=pos[0] + randint(-5, 5), y=pos[1] + randint(-5, 5))

        return True
