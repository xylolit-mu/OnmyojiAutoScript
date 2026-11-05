"""Six-star offerings: summon, share with named friends, then wait for defeat."""
import re
import time
from datetime import datetime

from module.atom.click import RuleClick
from module.atom.swipe import RuleSwipe
from module.exception import RequestHumanTakeover, TaskEnd
from module.logger import logger
from tasks.MetaDemon.config import AssistanceRole, BossType
import tasks.GameUi.task_pages as pages


class GhostOffering:
    def __init__(self, task):
        self.task = task
        self.settings = task.conf.assistance_config
        self.shared_generation = None
        self.waiting = False
        self.empty_frames = 0
        self.unknown_reported = False

    def within_limit(self):
        t = self.task
        return (datetime.now() - t.start_time < t.limit_time and
                t.total_count < t.limit_count and not t.State.done)

    @staticmethod
    def normalize_name(name):
        return re.sub(r'\s+', '', str(name))

    def friend_rows(self):
        t = self.task
        x, y, _, _ = t.O_MD_FRIENDS.roi
        rows = {}
        for result in t.O_MD_FRIENDS.detect_and_ocr(t.device.image):
            name = self.normalize_name(result.ocr_text)
            box = result.box
            left, top = int(min(box[:, 0])) + x, int(min(box[:, 1])) + y
            width, height = max(1, int(max(box[:, 0]) - min(box[:, 0]))), max(1, int(max(box[:, 1]) - min(box[:, 1])))
            rows.setdefault(name, []).append((left, top, width, height))
        return rows

    def selected_count(self):
        t = self.task
        return len(t.I_MD_FRIEND_SELECTED.match_all(t.device.image, frame_id=t.device.image_frame_id))

    def list_to_top(self):
        t = self.task
        swipe = t.S_MD_SWIPE_FRIEND_UP
        reverse = RuleSwipe(roi_front=swipe.roi_back, roi_back=swipe.roi_front,
                            mode='default', name='metademon_friend_list_to_top')
        previous = None
        stable = 0
        for _ in range(20):
            if not self.within_limit():
                return False
            t.screenshot()
            if not t.appear(t.I_MD_INVITE_FRIENDS):
                return False
            signature = tuple(self.friend_rows())
            stable = stable + 1 if signature == previous else 0
            if stable >= 2:
                return True
            previous = signature
            t.swipe(reverse, interval=1.2)
        return False

    def open_share_panel(self):
        t = self.task
        deadline = time.monotonic() + 12
        while self.within_limit() and time.monotonic() < deadline:
            t.screenshot()
            if t.appear(t.I_MD_INVITE_FRIENDS):
                return True
            t.appear_then_click(t.I_MD_INVITE, interval=1.5)
        return False

    def select_friend(self, name, area):
        t = self.task
        before = self.selected_count()
        t.click(RuleClick(roi_front=area, roi_back=area, name='metademon_offering_friend'))
        deadline = time.monotonic() + 4
        while self.within_limit() and time.monotonic() < deadline:
            t.screenshot()
            if not t.appear(t.I_MD_INVITE_FRIENDS):
                return False
            if self.selected_count() == before + 1:
                logger.info(f'鬼王上供已选好友: {name}')
                return True
        logger.warning(f'好友 {name} 点击后未确认选中，停止本次分享')
        return False

    def select_friends(self):
        t = self.task
        wanted = {self.normalize_name(name): name for name in self.settings.friends}
        if not wanted or not self.list_to_top():
            return False
        # Inspect the whole list before selecting, so stale selections cannot
        # silently include an unconfigured recipient on an off-screen row.
        previous = None
        stable = 0
        available = set()
        for _ in range(30):
            if not self.within_limit():
                return False
            t.screenshot()
            if not t.appear(t.I_MD_INVITE_FRIENDS) or self.selected_count():
                logger.warning('分享列表存在已有选择或已关闭，请清空好友选择后重试')
                return False
            rows = self.friend_rows()
            if any(len(rows.get(name, [])) > 1 for name in wanted):
                logger.warning('好友名字有多个相同识别结果，无法唯一选择')
                return False
            available.update(rows)
            signature = tuple(rows)
            stable = stable + 1 if signature == previous else 0
            if stable >= 2:
                break
            previous = signature
            t.swipe(t.S_MD_SWIPE_FRIEND_UP, interval=1.2)
        else:
            logger.warning('好友列表扫描超过限制，停止分享')
            return False
        missing = wanted.keys() - available
        if missing:
            logger.warning('未找到指定好友: ' + '; '.join(wanted[name] for name in missing))
            return False
        if not self.list_to_top():
            return False
        remaining = dict(wanted)
        for _ in range(30):
            if not self.within_limit():
                return False
            t.screenshot()
            if not t.appear(t.I_MD_INVITE_FRIENDS):
                return False
            rows = self.friend_rows()
            for name in tuple(remaining):
                # Selection can reorder rows; resolve each subsequent name on
                # the latest screenshot instead of reusing an earlier box.
                rows = self.friend_rows()
                if name not in rows:
                    continue
                if len(rows[name]) != 1 or not self.select_friend(remaining[name], rows[name][0]):
                    return False
                del remaining[name]
            if not remaining:
                return True
            t.swipe(t.S_MD_SWIPE_FRIEND_UP, interval=1.2)
        return False

    def share(self):
        t = self.task
        if t.cur_boss_type != BossType.SIX_STARS:
            logger.warning('仅允许分享本次已确认召唤的六星鬼王')
            return False
        if not self.open_share_panel() or not self.select_friends():
            return False
        # Select -> fresh screenshot -> submit, never submit a partial selection.
        t.screenshot()
        if not t.appear_then_click(t.I_MD_INVITE_FRIENDS, interval=1.5):
            return False
        deadline = time.monotonic() + 10
        while self.within_limit() and time.monotonic() < deadline:
            t.screenshot()
            if not t.appear(t.I_MD_INVITE_FRIENDS) and (
                    t.appear(t.I_MD_FIRE) or t.appear(t.I_MD_SUMMON_BOSS) or t.appear(t.I_MD_SETTLING)):
                logger.info('六星鬼王分享流程完成，等待好友击杀')
                return True
        logger.warning('未确认分享面板关闭，停止本次上供，避免重复分享')
        return False

    def tick(self):
        """Return False only when this run should stop; never enter a battle."""
        t = self.task
        if t.appear(t.I_UI_REWARD):
            t.click(t.C_UI_REWARD, interval=0.8)
            return True
        if t.appear(t.I_MD_SETTLING):
            self.empty_frames = 0
            return True
        if t.appear(t.I_MD_FIRE):
            self.empty_frames = 0
            if t.cur_boss_type != BossType.SIX_STARS:
                if not self.unknown_reported:
                    logger.warning('已有鬼王星级未确认，不分享、不挑战；等待其消失后仅召唤六星')
                    self.unknown_reported = True
            elif self.shared_generation != t._boss_generation:
                if not self.share():
                    return False
                self.shared_generation = t._boss_generation
            self.waiting = True
            return True
        if not t.appear(t.I_MD_SUMMON_BOSS):
            self.empty_frames = 0
            return True
        self.empty_frames += 1
        if self.empty_frames < 2:
            return True
        if self.waiting:
            if self.shared_generation is not None:
                t.total_count += 1
                logger.info(f'上供鬼王已结束: {t.total_count}/{t.limit_count}')
            self.waiting = False
            self.shared_generation = None
            t.cur_boss_type = None
            self.unknown_reported = False
            if not self.within_limit():
                return True
        self.empty_frames = 0
        return t.check_and_switch_ticket(sequence=(BossType.SIX_STARS,)) or t.State.done


def run_assistance(task):
    settings = task.config.meta_demon.assistance_config
    if settings.role == AssistanceRole.ASSIST:
        logger.warning('鬼王协助身份尚未实现，本次不执行')
        task.set_next_run(task='MetaDemon', success=False)
        raise TaskEnd
    if not settings.friends:
        raise RequestHumanTakeover('鬼王上供需要填写分享好友名字')
    task.before_run()
    task.goto_page(pages.page_meta_demon_boss)
    offering = GhostOffering(task)
    success = True
    while offering.within_limit():
        # Waiting for someone else to kill a boss is intentional inactivity.
        task.device.stuck_record_clear()
        task.screenshot()
        if not offering.tick():
            success = False
            logger.warning('鬼王上供流程未完成，结束本次运行')
            break
        remaining = max(0, (task.limit_time - (datetime.now() - task.start_time)).total_seconds())
        time.sleep(min(settings.poll_interval, remaining))
    task.set_next_run(task='MetaDemon', success=success)
    task.finish_task()
