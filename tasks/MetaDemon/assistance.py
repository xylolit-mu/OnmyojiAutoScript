"""Share six-star bosses and challenge bosses shared by friends."""
from base64 import b64decode, b64encode
import re
import time
from datetime import datetime
from uuid import uuid4

import cv2
import numpy as np

from module.atom.click import RuleClick
from module.atom.swipe import RuleSwipe
from module.cooperation import Cooperation
from module.exception import RequestHumanTakeover, ScriptError, TaskEnd
from module.image.rpc import get_image_client
from module.logger import logger
from tasks.MetaDemon.config import AssistanceRole, BossType, MetaDemonMode
import tasks.GameUi.task_pages as pages


class AssistanceLink:
    CHANNEL = 'MetaDemonAssistance'

    def __init__(self, task):
        self.task = task
        settings = task.conf.assistance_config
        self.peer = settings.cooperation_script
        self.runtime = getattr(task.config, 'cooperation_runtime', None) or Cooperation(task.config.config_name)
        self.session = uuid4().hex
        other_role = AssistanceRole.ASSIST if settings.role == AssistanceRole.OFFERING else AssistanceRole.OFFERING
        if not self.peer:
            peers = []
            for path in (self.runtime.root / 'config').glob('*.json'):
                if path.stem == self.runtime.name:
                    continue
                other = self.runtime.read(path.stem).get('meta_demon', {})
                other_settings = other.get('assistance_config', {})
                if (other.get('scheduler', {}).get('enable')
                        and other_settings.get('role') == other_role
                        and other_settings.get('cooperation_script') == self.runtime.name):
                    peers.append(path.stem)
            if len(peers) > 1:
                raise ScriptError('有多个鬼王协助脚本指向当前实例，请明确填写协同脚本')
            self.peer = next(iter(peers), '')
        if self.peer:
            other = self.runtime.read(self.peer).get('meta_demon', {})
            other_settings = other.get('assistance_config', {})
            if (self.peer == self.runtime.name or other_settings.get('role') != other_role
                    or MetaDemonMode.BOSS_ASSIST not in other.get('meta_demon_config', {}).get('mode', [])
                    or other_settings.get('cooperation_script', '') not in ('', self.runtime.name)):
                raise ScriptError('鬼王协同脚本应为匹配的上供和协助实例')

    def publish(self, portrait=''):
        if self.peer:
            if portrait:
                self.session = uuid4().hex
            remaining = (self.task.limit_time - (datetime.now() - self.task.start_time)).total_seconds()
            self.runtime.publish_battle_state(self.CHANNEL, self.peer, self.session, {
                'portrait': portrait,
                'expires': time.time() + max(0, remaining),
            })

    def target(self):
        state = self.runtime.peer_battle_state(self.CHANNEL, self.peer)
        if not state:
            return None
        payload = state['payload']
        portrait = payload.get('portrait')
        if portrait and payload.get('expires', 0) > time.time():
            return state['session'], state['updated'], portrait
        return None

    def confirm(self, target, *, settled=False):
        self.runtime.publish_battle_state(self.CHANNEL, self.peer, target[0], {
            'matched': True, 'settled': settled,
        })

    def confirmed(self):
        state = self.runtime.peer_battle_state(self.CHANNEL, self.peer)
        return bool(state and state['session'] == self.session and state['payload'].get('matched'))

    def peer_settled(self):
        state = self.runtime.peer_battle_state(self.CHANNEL, self.peer)
        return bool(state and state['session'] == self.session and state['payload'].get('settled'))

    def peer_in_task(self):
        return self.runtime.runner_task(self.peer) == 'MetaDemon'

    def wait_for_peer(self):
        task = 'MetaDemon'
        if not self.runtime.prepare(self.task.config, task, self.peer, defer=False):
            return False
        deadline = time.monotonic() + min(30, self.task.limit_time.total_seconds())
        while time.monotonic() < deadline:
            running = self.runtime.runner_task(self.peer)
            if running == task:
                return True
            if running != '':
                break
            time.sleep(0.5)
        self.runtime.skip_without_wake(task, self.peer, '对端未进入超鬼王协同任务')
        logger.info(f'超鬼王协同跳过：{self.peer} 未进入同一任务')
        return False


class GhostOffering:
    INVITE_RETRY_SECONDS = 20

    def __init__(self, task, link=None):
        self.task = task
        self.link = link
        self.settings = task.conf.assistance_config
        self.shared_generation = None
        self.waiting = False
        self.settled = False
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
        return len(t.I_MD_FRIEND_SELECTED.match_all_any(t.device.image, frame_id=t.device.image_frame_id))

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
        if not wanted:
            return False
        t.screenshot()
        if not t.appear(t.I_MD_INVITE_FRIENDS) or self.selected_count():
            logger.warning('分享列表存在已有选择或已关闭，请清空好友选择后重试')
            return False
        rows = self.friend_rows()
        # All recipients are already visible. Avoid moving away from their rows.
        if wanted.keys() <= rows.keys():
            for name in wanted:
                rows = self.friend_rows()
                if len(rows.get(name, ())) != 1 or not self.select_friend(name, rows[name][0]):
                    return False
            return True
        if not self.list_to_top():
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
        portrait_sent = False
        select_recipients = True
        while self.within_limit():
            if not self.link.peer_in_task():
                logger.info('协助端已离开超鬼王，停止重试邀请')
                return False
            if portrait_sent and self.link.confirmed():
                logger.info('协助端已识别分享鬼王，等待好友击杀')
                return True
            if select_recipients and (not self.open_share_panel() or not self.select_friends()):
                return False
            t.screenshot()
            if portrait_sent and self.link.confirmed():
                t.appear_then_click(t.I_MD_CLOSE_POPUP, interval=0.8)
                logger.info('协助端已识别分享鬼王，等待好友击杀')
                return True
            if not select_recipients and self.selected_count() != len(self.settings.friends):
                logger.warning('邀请重试前好友选中状态已变化，停止本次上供')
                return False
            if not t.appear_then_click(t.I_MD_INVITE_FRIENDS, interval=self.INVITE_RETRY_SECONDS):
                return False
            deadline = time.monotonic() + self.INVITE_RETRY_SECONDS
            select_recipients = False
            while self.within_limit() and time.monotonic() < deadline:
                if portrait_sent and self.link.confirmed():
                    logger.info('协助端已识别分享鬼王，等待好友击杀')
                    return True
                t.screenshot()
                if t.appear(t.I_MD_INVITE_FRIENDS):
                    continue
                select_recipients = True
                if t.appear(t.I_MD_SUMMON_BOSS) and not portrait_sent:
                    return False
                if not portrait_sent and t.appear(t.I_MD_FIRE):
                    portrait_sent = self.send_portrait()
                    if not portrait_sent:
                        return False
            if self.within_limit():
                logger.info('邀请已等待 20 秒，未收到协助端确认，准备重试')
        logger.warning('运行时间内未收到协助端确认，停止本次上供')
        return False

    def send_portrait(self):
        t = self.task
        x, y, w, h = t.I_SHARE_DEMON.roi_back
        portrait = t.device.image[y:y + h, x:x + w]
        if portrait.shape[:2] != (h, w):
            logger.warning('上供鬼王截图区域无效')
            return False
        encoded, png = cv2.imencode('.png', cv2.cvtColor(portrait, cv2.COLOR_RGB2BGR))
        if not encoded:
            logger.warning('上供鬼王截图编码失败')
            return False
        self.link.publish(b64encode(png).decode('ascii'))
        logger.info('已同步上供鬼王截图，等待协助端确认')
        return True

    def tick(self):
        """Return False only when this run should stop; never enter a battle."""
        t = self.task
        if t.appear(t.I_UI_REWARD):
            t.click(t.C_UI_REWARD, interval=0.8)
            return True
        settling = t.appear(t.I_MD_SETTLING)
        if self.shared_generation is not None and not self.settled and (settling or self.link.peer_settled()):
            self.settled = True
            self.link.publish()
            logger.info('上供鬼王进入结算，等待召唤标志后再开始下一轮')
        if settling:
            self.empty_frames = 0
            return True
        if not self.settled and t.appear(t.I_MD_FIRE):
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
        if self.shared_generation is not None and not self.settled:
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
            self.settled = False
            self.shared_generation = None
            t.cur_boss_type = None
            self.unknown_reported = False
            if not self.within_limit():
                return True
        self.empty_frames = 0
        return t.check_and_switch_ticket(sequence=(BossType.SIX_STARS,)) or t.State.done


class GhostAssistant:
    def __init__(self, task, link):
        self.task = task
        self.link = link
        self.active_target = None
        self.settled_target = None
        swipe = task.S_MD_SWIPE_UP
        self.reverse = RuleSwipe(roi_front=swipe.roi_back, roi_back=swipe.roi_front,
                                 mode='default', name='metademon_assist_list_to_top')

    def within_limit(self):
        t = self.task
        t.update_global_state()
        return not t.State.done

    def match_portrait(self, template):
        t = self.task
        rule = t.I_FIGHT_SHARE_DEMON
        result = get_image_client().match_dynamic_template(
            template=template, image=t.device.image, frame_id=t.device.image_frame_id,
            roi_back=rule.roi_back, threshold=rule.threshold, name=rule.name,
        )
        return tuple(result['roi_front']) if result['matched'] else None

    def open_row(self, area):
        t = self.task
        t.click(RuleClick(roi_front=area, roi_back=area, name='metademon_assist_row'))
        time.sleep(0.6)
        deadline = time.monotonic() + 5
        while self.within_limit() and time.monotonic() < deadline:
            t.screenshot()
            if t.appear(t.I_MD_SETTLING):
                return False
            if t.appear(t.I_MD_FIRE):
                return True
        return False

    def return_to_list(self):
        t = self.task
        t.screenshot()
        if not t.match_page_once(pages.page_meta_demon_boss):
            t.appear_then_click(t.I_MD_CLOSE_POPUP, interval=0.8)
            t.goto_page(pages.page_meta_demon_boss, skip_first_screenshot=False)

    def fight(self, target):
        t = self.task
        if self.active_target != target:
            t.cur_boss_type = BossType.SIX_STARS
            t._boss_generation += 1
            t._current_boss_attempts = 0
            self.active_target = target
        while self.within_limit():
            t.screenshot()
            if self.observe_settlement(target) or not t.appear(t.I_MD_FIRE) or self.link.target() != target:
                break
            if not (t.check_fatigue() and t.check_and_switch_soul()
                    and t.check_and_switch_powerful()):
                break
            if self.link.target() != target:
                break
            if not t.start_battle(within_limit=self.within_limit):
                self.observe_settlement(target)
                break
            if self.observe_settlement(target):
                break

    def observe_settlement(self, target):
        if self.settled_target == target:
            return True
        if not self.task.appear(self.task.I_MD_SETTLING):
            return False
        self.settled_target = target
        if self.link.target() == target:
            self.link.confirm(target, settled=True)
        logger.info('协助鬼王进入结算，等待上供端重新召唤')
        return True

    def scan(self, target):
        t = self.task
        try:
            data = np.frombuffer(b64decode(target[2], validate=True), dtype=np.uint8)
            portrait = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
        except (ValueError, cv2.error):
            portrait = None
        if portrait is None:
            logger.warning('协助收到的鬼王截图无效')
            return
        template = cv2.cvtColor(portrait, cv2.COLOR_BGR2RGB)
        for swipe in (t.S_MD_SWIPE_UP, self.reverse):
            for _ in range(12):
                if not self.within_limit() or self.link.target() != target:
                    return
                t.screenshot()
                area = self.match_portrait(template)
                if area and self.link.target() == target:
                    logger.info(f'协助匹配到鬼王头像：{area}')
                    self.link.confirm(target)
                    opened = self.open_row(area)
                    if not self.observe_settlement(target) and opened and self.link.target() == target:
                        self.fight(target)
                    self.return_to_list()
                    return
                t.swipe(swipe)
                time.sleep(0.5)

    def run(self):
        self.task.goto_page(pages.page_meta_demon_boss)
        waiting_logged = False
        while self.within_limit():
            if not self.link.peer_in_task():
                logger.info('上供端已离开超鬼王，结束本轮协助')
                return False
            self.task.device.stuck_record_clear()
            target = self.link.target()
            if target and target != self.settled_target:
                waiting_logged = False
                self.scan(target)
            elif not waiting_logged:
                logger.info('协助已进入鬼王挑战页，等待上供者同步鬼王截图')
                waiting_logged = True
            if self.within_limit():
                time.sleep(self.task.conf.assistance_config.poll_interval)
        return True


def run_offering(task, link):
    task.goto_page(pages.page_meta_demon_boss)
    offering = GhostOffering(task, link)
    success = True
    while offering.within_limit():
        if not link.peer_in_task():
            logger.info('协助端已离开超鬼王，结束本轮上供')
            return False
        # Waiting for someone else to kill a boss is intentional inactivity.
        task.device.stuck_record_clear()
        task.screenshot()
        if not offering.tick():
            success = False
            logger.warning('鬼王上供流程未完成，结束本次运行')
            break
        remaining = max(0, (task.limit_time - (datetime.now() - task.start_time)).total_seconds())
        interval = 0.5 if offering.waiting and not offering.settled else offering.settings.poll_interval
        time.sleep(min(interval, remaining))
    return success


def run_assistance(task):
    settings = task.config.meta_demon.assistance_config
    offering = settings.role == AssistanceRole.OFFERING
    if offering and not settings.friends:
        raise RequestHumanTakeover('鬼王上供需要填写分享好友名字')
    task.before_run()
    try:
        link = AssistanceLink(task)
        if not link.peer and not offering:
            raise RequestHumanTakeover('鬼王协助需要关联对端脚本，以传送截图和确认分享')
        ready = bool(link.peer and link.wait_for_peer())
    except ScriptError as exc:
        if not offering:
            raise
        logger.info(f'鬼王上供协同未匹配：{exc}')
        ready = False
    if not ready:
        task.set_next_run(task='MetaDemon', success=False)
        if offering:
            logger.info('未匹配到可用的鬼王协助脚本，跳过本次上供')
            raise TaskEnd
        task.finish_task()
        return
    if settings.role == AssistanceRole.ASSIST:
        success = GhostAssistant(task, link).run()
    else:
        link.publish()
        try:
            success = run_offering(task, link)
        finally:
            link.publish()
    task.set_next_run(task='MetaDemon', success=success)
    task.finish_task()
