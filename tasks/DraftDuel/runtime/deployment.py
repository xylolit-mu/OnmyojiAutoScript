"""协同对弈：deployment。"""

import time
from module.logger import logger
from tasks.DraftDuel.config import DraftMatchMode
from tasks.DraftDuel.strategy import plan_lineup, plan_duel_lineup
from tasks.DraftDuel.runtime.portrait_match import (
    HAND_MIDDLE_FIGHT_SCALE, HAND_PORTRAIT_SCALE, HAND_PORTRAIT_THRESHOLDS,
    FIGHT_PORTRAIT_THRESHOLDS,
    identify_portrait, portrait_score, locate_portrait,
)


class DraftDuelDeploymentMixin:
    def _locate_deployment_portrait(self, head, *, scale=HAND_PORTRAIT_SCALE):
        """先以0.8阈值定位手牌，未命中时以0.7补测。"""
        for threshold in HAND_PORTRAIT_THRESHOLDS:
            located = locate_portrait(
                head, self.device.image, self.I_HAND_CARD_AREA.roi_back,
                scale=scale, threshold=threshold)
            if located is not None:
                if threshold < HAND_PORTRAIT_THRESHOLDS[0]:
                    logger.info(f'DraftDuel hand portrait matched at reduced threshold '
                                f'{threshold:.2f}, score={located[1]:.3f}')
                return located
        return None

    def _capture_deployment_hand_templates(self, countdown=None):
        """大轮首次进入上阵，按手牌从左到右绑定此前五次选人名单。"""
        if len(getattr(self, 'draft_hand_templates', ())) == 5:
            return True
        names = tuple(getattr(self, 'draft_selected', ()))
        if len(names) != 5 or len(set(names)) != 5:
            return False
        if type(countdown) is not int or not 1 <= countdown <= 60:
            return False
        epoch = getattr(self, '_draft_deploy_epoch', 0)
        baseline = getattr(self, '_draft_hand_capture_clock', None)
        if baseline is None or baseline[0] != epoch or countdown > baseline[1]:
            self._draft_hand_capture_clock = (epoch, countdown)
            return False
        if baseline[1] - countdown < 1:
            return False
        result = self.runframe(self.F_HAND_CARD)
        if not result or len(result) != 5 or any(
                getattr(crop, 'size', 0) == 0 for crop in result):
            return False
        self._draft_hand_capture_clock = None
        templates = [(name, crop.copy()) for name, crop in zip(names, result)]
        self.draft_hand_templates = tuple(templates)
        logger.info(f'DraftDuel saved five hand middles in selection order: {names}, '
                    f'top_scale={HAND_MIDDLE_FIGHT_SCALE}')
        self._save_draft_checkpoint('fight')
        return True

    def _release_deployment_templates(self):
        """首局结束且确认自动上阵后，释放图片和预规划，保留实际上阵记录。"""
        self._draft_hand_capture_clock = None
        if not any(getattr(self, key, ()) for key in
                   ('draft_hand_templates', 'draft_heads', 'draft_named_heads', 'draft_planned_lineup')):
            return
        self.draft_hand_templates = ()
        self.draft_heads = ()
        self.draft_named_heads = ()
        self.draft_planned_lineup = ()
        self._save_draft_checkpoint('rank')
        logger.info('DraftDuel automatic deployment confirmed; released portrait templates and planned lineup')

    def _update_deployment_clock(self):
        value = self.O_FIGHT_WAIT_TIME.ocr(self.device.image)
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if type(value) is not int or not 1 <= value <= 60:
            return
        previous = getattr(self, '_draft_deploy_time', None)
        observed = getattr(self, '_draft_deploy_clock_wall', None)
        now = time.time()
        expected = max(0, previous - max(0, now - observed)) \
            if previous is not None and observed is not None else previous
        if previous is not None and (value > previous or value - expected >= 3):
            if value - expected < 3:
                return
            candidate = getattr(self, '_draft_clock_candidate', None)
            self._draft_clock_candidate = value
            if candidate is None or not 0 <= candidate - value <= 2:
                return
            self._draft_deploy_epoch += 1
            logger.info(f'DraftDuel deployment countdown reset confirmed: {previous} -> {value}')
        self._draft_clock_candidate = None
        self._draft_deploy_time = value
        self._draft_deploy_clock_wall = now
        return value

    def _deployment_slots(self, portraits):
        mode = self.config.draft_duel.draft_duel_config.match_mode
        if mode == DraftMatchMode.CAPTAIN:
            return (0, 1, 2)
        if mode == DraftMatchMode.MEMBER:
            return (3, 4, 5)
        # 单排只在队友标志唯一落入一侧时确定身份，确定后本阶段不改变。
        self.I_TEAMATE_SELECT.load_image()
        sides = {i // 3 for i, region in enumerate(portraits)
                 if portrait_score(self.I_TEAMATE_SELECT.image, region) >= 0.8}
        if sides == {0}:
            return (3, 4, 5)
        if sides == {1}:
            return (0, 1, 2)
        return None

    def _deployment_plan(self, reserved=()):
        own_locked = tuple(dict.fromkeys((*getattr(self, 'draft_fielded', ()), *reserved)))
        mate = getattr(self, 'draft_peer_picks', ())
        if not mate:
            return plan_lineup(self.draft_selected, team_size=3, locked=own_locked)
        mate_locked = tuple(r['name'] for r in getattr(self, 'draft_peer_rounds', ()))
        # 两端统一以队长为第一参数，保证相同状态下规划一致。
        if self.config.draft_duel.draft_duel_config.match_mode == DraftMatchMode.CAPTAIN:
            return plan_duel_lineup(self.draft_selected, mate, locked=own_locked,
                                   mate_locked=mate_locked,
                                   field_weights=getattr(self, '_draft_field_weights', None))[0]
        return plan_duel_lineup(mate, self.draft_selected, locked=mate_locked,
                               mate_locked=own_locked,
                               field_weights=getattr(self, '_draft_field_weights', None))[1]

    def _record_deployed_portrait(self, pending, slots, actual):
        index = pending['index']
        if actual in self.draft_fielded:
            return
        self.draft_deployment_rounds.append(
            {'round': index + 1, 'slot': slots[index] + 1, 'name': actual})
        self.draft_deployment_rounds.sort(key=lambda row: row['round'])
        self.draft_fielded = tuple(row['name'] for row in self.draft_deployment_rounds)
        self.draft_unresolved_slots = [slot for slot in getattr(self, 'draft_unresolved_slots', ())
                                       if slot != slots[index] + 1]
        logger.info(f'DraftDuel round {index + 1} actual fielded: {actual}')

    def _recognize_deployment_portrait(self, region):
        for threshold in FIGHT_PORTRAIT_THRESHOLDS:
            actual, score = identify_portrait(
                region, self.draft_hand_templates, threshold=threshold,
                scale=HAND_MIDDLE_FIGHT_SCALE, return_score=True)
            if actual:
                if threshold < FIGHT_PORTRAIT_THRESHOLDS[0]:
                    logger.info(f'DraftDuel top portrait supplemental check: {actual}, '
                                f'threshold={threshold:.2f}, score={score:.3f}')
                return actual, score
        return None, score

    def _best_of_two_portraits(self, entry, region):
        """两次识别后，取满足阈值和候选分差要求的最高分结果。"""
        actual, score = self._recognize_deployment_portrait(region)
        previous = entry.pop('portrait_sample', None)
        if previous is None:
            entry['portrait_sample'] = [actual, score]
            return False, None
        candidates = [(name, value) for name, value in (previous, (actual, score)) if name]
        if not candidates:
            return True, None
        name, best_score = max(candidates, key=lambda sample: sample[1])
        logger.info(f'DraftDuel best of two portrait checks: {name}, score={best_score:.3f}')
        return True, name

    def _recheck_deferred_deployments(self, deferred, portraits, slots):
        """后续上卡期间回查未识别槽位，每两次取最高分补记。"""
        remaining = []
        for entry in deferred:
            complete, actual = self._best_of_two_portraits(entry, portraits[slots[entry['index']]])
            if complete and actual and actual not in self.draft_fielded:
                self._record_deployed_portrait(entry, slots, actual)
                logger.info(f"DraftDuel back-verified round {entry['index'] + 1}: {actual}")
                continue
            remaining.append(entry)
        return remaining

    def _finish_deployment_on_battle(self, pending, slots):
        if pending and pending['clicked'] and slots:
            if pending.get('preview_stable', 0) >= 2:
                self._record_deployed_portrait(pending, slots, pending['preview'])
        if slots:
            known = {r['slot'] for r in self.draft_deployment_rounds}
            self.draft_unresolved_slots = [slot + 1 for slot in slots if slot + 1 not in known]
            if self.draft_unresolved_slots:
                logger.warning(f'DraftDuel entered battle with unverified slots: {self.draft_unresolved_slots}')
        self._draft_deployment_state = None
        self._save_draft_checkpoint('battle')

    def _deploy_shikigami(self) -> bool:
        """首局手动上阵，当前大轮后续小局启用自动上阵。"""
        if getattr(self, 'draft_small_game', 0) > 1:
            return self._auto_deploy_shikigami()
        if len(getattr(self, 'draft_fielded', ())) == 3:
            return True
        state = getattr(self, '_draft_deployment_state', None)
        recovering = state is not None
        if state is None:
            self.draft_fielded = ()
            self.draft_deployment_rounds = []
            self._draft_deploy_time = None
            self._draft_deploy_clock_wall = None
            self._draft_deploy_epoch = 0
            state = {'next_epoch': 0, 'slots': None, 'pending': None}
        self._draft_deployment_wait_reason = None
        next_epoch, slots, pending = state['next_epoch'], state['slots'], state['pending']
        completed = state.get('completed', len(self.draft_fielded))
        deferred = state.get('deferred', [])
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            self._draft_deployment_state = {'next_epoch': next_epoch, 'slots': slots, 'pending': pending,
                                            'completed': completed, 'deferred': deferred}
            self._save_draft_checkpoint('fight')
            self.device.stuck_record_clear()
            self.screenshot()
            if self.appear(self._battle_info_rule()):
                self._draft_battle_seen = True
                self._draft_round_had_battle = True
                self._finish_deployment_on_battle(pending, slots)
                logger.info('DraftDuel battle_info detected; finish deployment immediately')
                return True
            self._observe_duel_lobby()
            if not self._sync_draft_state(require_small_game=True):
                time.sleep(0.3)
                continue
            # 点击第三次确定后可能已离开上阵页，仍先尝试读取顶部头像。
            if pending is None and not self.appear(self.I_POST_DRAFT_FIGHT):
                time.sleep(0.3)
                continue
            countdown = self._update_deployment_clock()
            if recovering and pending and not pending['clicked'] and (
                    self.appear(self.I_SELECT_ENSURE_READY) or
                    self._draft_deploy_epoch > pending['epoch']):
                # 恢复时确认点击可能已经生效；不把下一轮的确定按钮再点一次。
                pending['clicked'] = True
                pending['locked'] = True
            if pending and pending['clicked']:
                if self.appear(self.I_SELECT_ENSURE_READY):
                    pending['locked'] = True
                elif (self.appear(self.I_SELECT_ENSURE)
                      and self._draft_deploy_epoch > pending['epoch']):
                    # 自己最后确认时可能看不到 ready，倒计时重开证明前轮已结束。
                    pending['locked'] = True
            if (pending and pending['clicked'] and pending['locked']
                    and self._draft_deploy_epoch > pending['epoch']
                    and pending.get('preview_stable', 0) < 2):
                deferred.append(dict(index=pending['index'], name=pending['name'], stable=0))
                completed = pending['index'] + 1
                next_epoch = pending['epoch'] + 1
                self.draft_unresolved_slots = [slots[e['index']] + 1 for e in deferred]
                logger.warning(f"DraftDuel round {pending['index'] + 1} locked but unverified; "
                               'continue deployment and recheck previous slot')
                pending = None
                recovering = False
            if not self._capture_deployment_hand_templates(countdown=countdown):
                self._log_deployment_wait('等待上阵倒计时减少至少1秒后采集五张手牌中段')
                time.sleep(0.3)
                continue
            portraits = self.runframe(self.F_FIGHT_SHIKIGAMI)
            if not portraits or len(portraits) != 6:
                if pending and not pending['clicked']:
                    pending['preview'] = None
                    pending['preview_stable'] = 0
                    pending.pop('portrait_sample', None)
                    pending['failures'] = min(3, pending.get('failures', 0) + 1)
                    if pending['failures'] >= 3:
                        pending['clicked'] = bool(self.appear_then_click(self.I_SELECT_ENSURE, interval=1))
                        if pending['clicked']:
                            logger.warning('DraftDuel top portrait unavailable after three checks; confirm fallback')
                            self._save_draft_checkpoint('fight')
                    else:
                        self._log_deployment_wait(f"目标={pending['name']}，顶部栏未完整识别，等待重试")
                time.sleep(0.3)
                continue
            if slots is None:
                slots = self._deployment_slots(portraits)
                if slots is None:
                    time.sleep(0.3)
                    continue
                logger.info(f'DraftDuel own deployment slots: {tuple(i + 1 for i in slots)}')
            deferred = self._recheck_deferred_deployments(deferred, portraits, slots)
            if completed >= 3 and pending is None:
                if not deferred:
                    self._draft_deployment_state = None
                    self._save_draft_checkpoint('battle')
                    return True
                time.sleep(0.3)
                continue
            if pending is not None:
                index = pending['index']
                complete, actual = self._best_of_two_portraits(pending, portraits[slots[index]])
                if not complete:
                    time.sleep(0.3)
                    continue
                pending['preview'] = actual
                pending['preview_stable'] = 2 if actual else 0
                if not pending['clicked']:
                    if actual != pending['name']:
                        pending['failures'] = min(3, pending.get('failures', 0) + 1)
                    if (actual == pending['name'] and pending['preview_stable'] >= 2) or pending.get('failures', 0) >= 3:
                        self._draft_deployment_wait_reason = None
                        if actual != pending['name']:
                            logger.warning(f"DraftDuel target={pending['name']}, actual={actual}; confirm after three failed checks")
                        pending['clicked'] = bool(self.appear_then_click(self.I_SELECT_ENSURE, interval=1))
                        if pending['clicked']:
                            self._save_draft_checkpoint('fight')
                            logger.info(f"DraftDuel round {index + 1}: select_ensure clicked")
                    else:
                        self._log_deployment_wait(
                            f"目标={pending['name']}，顶部识别={actual}，两次识别最佳结果={actual}，等待重试")
                elif actual and actual not in self.draft_fielded:
                    pending['actual'], pending['stable'] = actual, 2
                    if pending['stable'] >= 2 and pending['locked']:
                        self._record_deployed_portrait(pending, slots, actual)
                        completed = pending['index'] + 1
                        next_epoch = pending['epoch'] + 1
                        pending = None
                        recovering = False
                        self._draft_deployment_state = {
                            'next_epoch': next_epoch, 'slots': slots, 'pending': None,
                            'completed': completed, 'deferred': deferred}
                        self._save_draft_checkpoint('fight')
                        self._sync_draft_state()
                        deadline = time.monotonic() + 120
                        if len(self.draft_fielded) == 3:
                            self._draft_deployment_state = None
                            self._save_draft_checkpoint('battle')
                            return True
                else:
                    pending['stable'] = 0
                time.sleep(0.3)
                continue
            if self._draft_deploy_epoch < next_epoch:
                time.sleep(0.3)
                continue
            try:
                reserved = tuple(e['name'] for e in deferred if e['name'] not in self.draft_fielded)
                lineup = self._deployment_plan(reserved=reserved)
            except ValueError as exc:
                logger.warning(f'DraftDuel lineup unavailable: {exc}')
                return False
            remaining_heads = tuple((n, h) for n, h in self.draft_hand_templates
                                    if n not in self.draft_fielded and n not in reserved)
            name = next((n for n in lineup if n not in self.draft_fielded and n not in reserved), None)
            if name is None:
                return False
            index = completed
            destinations = self.runframe(self.F_FIGHT_SHIKIGAMI_SET)
            if not destinations or len(destinations) != 6:
                self._log_deployment_wait('未识别到六个上阵目标位置')
                time.sleep(0.3)
                continue
            head = next(h for n, h in remaining_heads if n == name)
            located = self._locate_deployment_portrait(head, scale=1.0)
            if located is None:
                self._log_deployment_wait(f'第{index + 1}次上阵目标={name}，整个手牌back内未匹配到目标')
                time.sleep(0.3)
                continue
            if not self.appear(self.I_SELECT_ENSURE):
                self._log_deployment_wait(f'已识别手牌{name}，等待 select_ensure')
                time.sleep(0.3)
                continue
            self._draft_deployment_wait_reason = None
            source, score = located
            logger.info(f'DraftDuel located hand card: {name}, area={source}, score={score:.3f}')
            destination = destinations[slots[index]]
            center = lambda area: (int(area[0] + area[2] / 2), int(area[1] + area[3] / 2))
            self.device.press_and_drag(center(source), center(destination), hold_duration=0.2,
                                       swipe_duration=0.45, name=f'DraftDuel_deploy_{name}')
            pending = {'index': index, 'name': name, 'failures': 0, 'clicked': False,
                       'stable': 0, 'locked': False, 'epoch': self._draft_deploy_epoch}
            recovering = False
            time.sleep(0.3)
        logger.warning('DraftDuel deployment timed out without identifying actual lineup')
        return False

    def _auto_deploy_shikigami(self) -> bool:
        """后续小局只等待战斗；自动上阵已在等待匹配页面确认。"""
        self._draft_deployment_state = None
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            self._save_draft_checkpoint('fight')
            self.device.stuck_record_clear()
            self.screenshot()
            if self.appear(self._battle_info_rule()):
                self._draft_battle_seen = True
                self._draft_round_had_battle = True
                self._save_draft_checkpoint('battle')
                return True
            time.sleep(0.3)
        logger.warning('DraftDuel automatic deployment timed out waiting for battle')
        return False

    def _log_deployment_wait(self, reason):
        if getattr(self, '_draft_deployment_wait_reason', None) != reason:
            logger.info(f'DraftDuel deployment waiting: {reason}')
            self._draft_deployment_wait_reason = reason

    def _capture_draft_heads(self) -> bool:
        """大轮首次采集五张头像，后续小局复用，直到新大轮才清除。"""
        if getattr(self, 'draft_heads', ()):
            return True
        names = tuple(getattr(self, 'draft_selected', ()))
        if len(names) != 5:
            return False
        result = self.runframe(self.F_SHIKIGAMI_HEAD)
        if not result or len(result) != 5:
            return False
        if any(getattr(head, 'size', 0) == 0 for head in result):
            return False
        self.draft_heads = tuple(head.copy() for head in result)
        self.draft_named_heads = tuple(zip(names, self.draft_heads))
        logger.info(f'DraftDuel saved named heads in selection order: {names}')
        return True
