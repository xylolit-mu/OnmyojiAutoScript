"""多个任务共用的按住并拖动操作，由 Device.press_and_drag 调用。"""

import time

import numpy as np

import module.device.method.scrcpy.const as scrcpy_const
from module.base.utils import (
    ensure_int,
    ensure_time,
    point2str,
    random_rectangle_point,
)
from module.logger import logger


def press_and_drag(
    device,
    p1,
    p2,
    hold_duration=None,
    point_random=(-2, -2, 2, 2),
    swipe_duration=None,
    name='Press_and_Drag',
    retry=False,
):
    """在起点按住后连续拖至终点，支持不同触摸通道；结果由调用任务确认。"""
    device.handle_control_check(name)
    p1, p2 = ensure_int(p1, p2)
    hold_duration = ensure_time(hold_duration if hold_duration is not None
                                else ((0.12, 0.18) if retry else (0.03, 0.05)))
    if swipe_duration is None:
        swipe_duration = 0.45 if retry else 0.30
    swipe_duration = ensure_time(
        (swipe_duration * 0.92, swipe_duration * 1.08)
    )
    release_duration = ensure_time((0.04, 0.06))
    action_log = 'Press_and_Drag %s -> %s' % (
        point2str(*p1),
        point2str(*p2),
    )
    method = device.config.script.device.control_method
    start = time.perf_counter()
    device._invalidate_image_batch_cache()

    if method == 'minitouch':
        _press_and_drag_minitouch(
            device,
            p1,
            p2,
            hold_duration=hold_duration,
            point_random=point_random,
            swipe_duration=swipe_duration,
            release_duration=release_duration,
        )
    elif method == 'uiautomator2':
        _press_and_drag_uiautomator2(
            device,
            p1,
            p2,
            hold_duration=hold_duration,
            point_random=point_random,
            swipe_duration=swipe_duration,
            release_duration=release_duration,
        )
    elif method == 'scrcpy':
        _press_and_drag_scrcpy(
            device,
            p1,
            p2,
            hold_duration=hold_duration,
            point_random=point_random,
            swipe_duration=swipe_duration,
            release_duration=release_duration,
        )
    elif method == 'window_message':
        _press_and_drag_window_message(
            device,
            p1,
            p2,
            hold_duration=hold_duration,
            point_random=point_random,
            swipe_duration=swipe_duration,
            release_duration=release_duration,
        )
    else:
        logger.warning(
            f'Control method {method} cannot hold before moving; '
            'falling back to ADB swipe'
        )
        device.swipe_adb(
            p1,
            p2,
            duration=ensure_time(hold_duration + swipe_duration),
        )

    elapsed = time.perf_counter() - start
    logger.info(f'{device._format_action_duration(elapsed)}{action_log} '
                f'profile={"retry" if retry else "fast"}, hold={hold_duration:.3f}s')


def _randomized_points(p1, p2, point_random):
    p1 = np.array(p1) - random_rectangle_point(point_random)
    p2 = np.array(p2) - random_rectangle_point(point_random)
    return (tuple(int(v) for v in np.clip(p1, (0, 0), (1279, 719))),
            tuple(int(v) for v in np.clip(p2, (0, 0), (1279, 719))))


def _motion_steps(p1, p2, swipe_duration):
    """An initial 4-8px slow move followed by a straight continuous drag.

    Each returned wait follows its MOVE; the finger stays down throughout.
    """
    p1, p2 = np.asarray(p1, dtype=float), np.asarray(p2, dtype=float)
    delta = p2 - p1
    distance = float(np.linalg.norm(delta))
    if distance < 1:
        return [(p2.astype(int).tolist(), swipe_duration)]
    start_distance = min(float(np.random.uniform(4, 8)), distance / 4)
    start = p1 + delta * (start_distance / distance)
    steps = []
    for fraction in (0.5, 1.0):
        point = np.rint(p1 + (start - p1) * fraction).astype(int).tolist()
        steps.append((point, 0.025))
    segments = int(np.clip(np.ceil((distance - start_distance) / 20), 8, 24))
    for index in range(1, segments + 1):
        t = index / segments
        progress = 3 * t*t - 2 * t*t*t
        point = np.rint(start + (p2 - start) * progress).astype(int).tolist()
        steps.append((point, swipe_duration / segments))
    return steps


def _best_effort_release(release):
    try:
        release()
    except Exception as exc:
        logger.warning(f'Press-and-drag emergency release failed: {exc}')


def _press_and_drag_minitouch(device, p1, p2, hold_duration, point_random,
                              swipe_duration, release_duration):
    p1, p2 = _randomized_points(p1, p2, point_random)
    steps = _motion_steps(p1, p2, swipe_duration)
    builder = device.minitouch_builder
    builder.clear()
    try:
        builder.down(*p1).commit().wait(round(hold_duration * 1000))
        for point, wait in steps:
            builder.move(*point).commit().wait(max(1, round(wait * 1000)))
        builder.wait(round(release_duration * 1000)).up().commit()
        # Device-side waits keep DOWN/MOVE/UP separate even in one packet.
        device.minitouch_send()
    except BaseException:
        # Drop unsent moves before attempting a release. Never replay a drag
        # after an uncertain send; business confirmation decides any retry.
        def release():
            builder.clear()
            builder.up().commit()
            try:
                device.minitouch_send()
            finally:
                builder.clear()
        _best_effort_release(release)
        raise
    finally:
        builder.clear()


def _press_and_drag_uiautomator2(device, p1, p2, hold_duration, point_random,
                                swipe_duration, release_duration):
    p1, p2 = _randomized_points(p1, p2, point_random)
    steps = _motion_steps(p1, p2, swipe_duration)
    last = p1
    completed = False
    try:
        # Use individual touch events: _drag_along retries the whole gesture
        # on transport errors, before the game result has been checked.
        device.u2.touch.down(*p1)
        device.sleep(hold_duration)
        for point, wait in steps:
            last = point
            device.u2.touch.move(*point)
            device.sleep(wait)
        device.sleep(release_duration)
        device.u2.touch.up(*p2)
        completed = True
    finally:
        if not completed:
            _best_effort_release(lambda: device.u2.touch.up(*last))


def _press_and_drag_scrcpy(device, p1, p2, hold_duration, point_random,
                          swipe_duration, release_duration):
    device.scrcpy_ensure_running()
    with device._scrcpy_control_socket_lock:
        p1, p2 = _randomized_points(p1, p2, point_random)
        steps = _motion_steps(p1, p2, swipe_duration)
        last = p1
        completed = False
        try:
            device._scrcpy_control.touch(*p1, scrcpy_const.ACTION_DOWN)
            device.sleep(hold_duration)
            for point, wait in steps:
                last = point
                device._scrcpy_control.touch(*point, scrcpy_const.ACTION_MOVE)
                device.sleep(wait)
            device.sleep(release_duration)
            device._scrcpy_control.touch(*p2, scrcpy_const.ACTION_UP)
            completed = True
        finally:
            if not completed:
                _best_effort_release(lambda: device._scrcpy_control.touch(*last, scrcpy_const.ACTION_UP))
    device.sleep(0.05)


def _press_and_drag_window_message(device, p1, p2, hold_duration, point_random,
                                   swipe_duration, release_duration):
    p1, p2 = _randomized_points(p1, p2, point_random)
    steps = _motion_steps(p1, p2, swipe_duration)
    device.press_and_drag_window_message(
        p1, p2, steps,
        hold_duration=hold_duration,
        release_duration=release_duration,
    )
