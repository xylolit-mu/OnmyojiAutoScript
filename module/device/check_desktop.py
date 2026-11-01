"""Desktop integration checks: mock all process/window actions; only use temporary files."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.atom.image import ROI_MATCH_PAD, roi_match_padding
from module.config.config_model import ConfigModel
from module.device import desktop_launch as jobs
from module.device.method import desktop_impl as desktop
from module.device.method import windows_impl as windows
from module.image.runtime import ImageRuntime


class DesktopChecks(unittest.TestCase):
    def test_desktop_is_off_for_existing_default_configuration(self):
        self.assertFalse(ConfigModel().script.desktop.enable)

    def test_reused_pid_is_rejected_before_window_lookup(self):
        owner = SimpleNamespace(desktop_profile_dir=lambda: Path('offline-profile'),
                                desktop_resolve_install_root=lambda: None)
        with patch.object(desktop, 'desktop_read_instance_state', return_value={'pid': 321, 'started': 100}), \
                patch.object(desktop, 'desktop_pid_alive', return_value=True), \
                patch.object(desktop, 'desktop_pid_create_time', return_value=200), \
                patch.object(desktop, 'desktop_clear_instance_state') as clear, \
                patch.object(desktop, 'find_desktop_window_by_pid') as lookup:
            self.assertEqual(desktop.DesktopWindow._desktop_locate_window(owner), (None, 0))
        lookup.assert_not_called()
        clear.assert_called_once()

    def test_terminate_checks_creation_time_on_the_process_handle(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 42
        def get_times(handle, creation, *args):
            creation._obj.dwLowDateTime = 200
            return True
        kernel.GetProcessTimes.side_effect = get_times
        with patch.object(desktop, 'desktop_pid_alive', return_value=True), \
                patch.object(desktop.ctypes, 'windll', SimpleNamespace(kernel32=kernel)):
            self.assertFalse(desktop._terminate_verified_pid(321, 100))
        kernel.TerminateProcess.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(42)

    def test_profile_launch_does_not_fall_back_when_job_creation_fails(self):
        with patch.object(desktop, 'list_desktop_windows', return_value=[]), \
                patch.object(desktop, 'desktop_profile_env', return_value={'USERPROFILE': 'fake'}), \
                patch.object(desktop.subprocess, 'Popen', return_value=SimpleNamespace(pid=123)), \
                patch.object(desktop, 'desktop_pid_create_time', return_value=100), \
                patch.object(desktop, '_desktop_attach_job', return_value=0):
            self.assertEqual(desktop.desktop_spawn_client('fake.exe', 'fake-root', Path('fake-profile')),
                             (0, {123: 100}))

    def test_atomic_instance_state_contains_creation_time(self):
        with TemporaryDirectory() as folder, \
                patch.object(desktop, 'desktop_pid_create_time', return_value=100):
            desktop.desktop_write_instance_state(Path(folder), 321, True)
            self.assertEqual(desktop.desktop_read_instance_state(Path(folder)),
                             {'pid': 321, 'started': 100, 'logged_in': True})
            self.assertEqual(list(Path(folder).glob('*.tmp')), [])

    def test_shared_profile_is_rejected_before_launch(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'config').mkdir()
            profile = root / 'profiles' / 'account'
            (root / 'config' / 'B.json').write_text(json.dumps({'script': {'desktop': {
                'enable': True, 'profile_dir': str(profile)}}}), encoding='utf-8')
            config = SimpleNamespace(config_name='A', script=SimpleNamespace(desktop=SimpleNamespace(
                game_path='fake', profile_dir=str(profile))))
            action = Mock(return_value=True)
            with patch.object(desktop, 'desktop_install_root', return_value=str(root / 'game')), \
                    patch.object(desktop.Path, 'cwd', return_value=root):
                self.assertFalse(desktop._serialized_launch(action)(config))
            action.assert_not_called()

    def test_launch_lock_serializes_competing_workers(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            config = SimpleNamespace(config_name='A', script=SimpleNamespace(desktop=SimpleNamespace(
                game_path='fake', profile_dir=str(root / 'profile'))))
            active = [0, 0]
            lock = Lock()
            @desktop._serialized_launch
            def action(config):
                with lock:
                    active[0] += 1
                    active[1] = max(active)
                time.sleep(.02)
                with lock:
                    active[0] -= 1
                return True
            with patch.object(desktop, 'desktop_install_root', return_value=str(root / 'game')), \
                    patch.object(desktop.Path, 'cwd', return_value=root), ThreadPoolExecutor(2) as pool:
                self.assertEqual(list(pool.map(action, [config, config])), [True, True])
            self.assertEqual(active[1], 1)

    def test_button_enqueues_once_without_running_launch_on_request_thread(self):
        config = SimpleNamespace(config_name='offline', script=SimpleNamespace(desktop=SimpleNamespace(enable=True)))
        with patch.dict(jobs._jobs, {}, clear=True), patch.object(jobs._executor, 'submit') as submit:
            first = jobs.schedule_desktop_launch(config)
            second = jobs.schedule_desktop_launch(config)
            self.assertEqual(first['id'], second['id'])
            self.assertEqual(first['state'], 'queued')
            submit.assert_called_once()

    def test_failed_launch_is_exposed_in_job_status(self):
        with patch.dict(jobs._jobs, {}, clear=True), \
                patch.object(desktop, 'desktop_launch_instance', return_value=False):
            jobs._launch(SimpleNamespace(), 'offline', 'job')
            self.assertEqual(jobs.desktop_launch_status('offline')['state'], 'failed')

    def test_screenshot_failure_releases_window_dc_and_bitmap(self):
        owner = SimpleNamespace(desktop_window_restore_if_minimized=Mock(),
            _desktop_assert_window_pos=Mock(), root_handle_num=1, screenshot_handle_num=1)
        memory = Mock()
        memory.BitBlt.side_effect = RuntimeError('fake capture failure')
        source = Mock()
        source.CreateCompatibleDC.return_value = memory
        bitmap = Mock()
        bitmap.GetHandle.return_value = 7
        with patch.object(windows, 'dpi_awareness', return_value=nullcontext()), \
                patch.object(windows, 'GetClientRect', return_value=(0, 0, 1280, 720)), \
                patch.object(windows, 'GetDC', return_value=42), \
                patch.object(windows, 'CreateDCFromHandle', return_value=source), \
                patch.object(windows, 'CreateBitmap', return_value=bitmap), \
                patch.object(windows, 'DeleteObject') as delete, \
                patch.object(windows, 'ReleaseDC') as release:
            with self.assertRaisesRegex(RuntimeError, 'fake capture'):
                windows.Window.screenshot_desktop_bitblt(owner)
            memory.DeleteDC.assert_called_once()
            delete.assert_called_once_with(7)
            release.assert_called_once_with(1, 42)

    def test_roi_padding_is_scoped_and_brightness_area_does_not_change(self):
        self.assertEqual(ROI_MATCH_PAD.get(), 0)
        with roi_match_padding(2):
            self.assertEqual(ROI_MATCH_PAD.get(), 2)
            with roi_match_padding(0):
                self.assertEqual(ROI_MATCH_PAD.get(), 0)
            self.assertEqual(ROI_MATCH_PAD.get(), 2)
        self.assertEqual(ROI_MATCH_PAD.get(), 0)
        runtime = object.__new__(ImageRuntime)
        common = dict(roi_front=[0, 0, 1, 1], roi_back=[1, 1, 20, 20], roi_pad=2)
        template = runtime._normalize_rule(dict(common, method='Template matching'))
        brightness = runtime._normalize_rule(dict(common, method='Brightness'))
        self.assertEqual(template['roi_back'], [0, 0, 23, 23])
        self.assertEqual(brightness['roi_back'], [1, 1, 20, 20])

    def test_empty_job_members_never_select_an_unrelated_new_window(self):
        bound = Mock()
        kernel = Mock()
        with patch.object(desktop, 'list_desktop_windows', side_effect=[[], [{'pid': 999, 'hwnd': 3}]]), \
                patch.object(desktop, 'desktop_profile_env', return_value={'USERPROFILE': 'fake'}), \
                patch.object(desktop.subprocess, 'Popen', return_value=SimpleNamespace(pid=123)), \
                patch.object(desktop, 'desktop_pid_create_time', return_value=100), \
                patch.object(desktop, '_desktop_attach_job', return_value=7), \
                patch.object(desktop, '_desktop_job_member_pids', return_value=set()), \
                patch.object(desktop, '_desktop_pid_under_root', return_value=True), \
                patch.object(desktop, 'time', SimpleNamespace(
                    time=Mock(side_effect=[0, 0, 2]), sleep=Mock())), \
                patch.object(desktop.ctypes, 'windll', SimpleNamespace(kernel32=kernel)):
            pid, _ = desktop.desktop_spawn_client('fake.exe', 'root', Path('profile'), timeout=1, on_bound=bound)
            self.assertEqual(pid, 0)
        bound.assert_not_called()

    def test_window_drag_releases_button_after_interruption(self):
        owner = SimpleNamespace(is_desktop_window=True, root_handle_num=1,
            desktop_window_restore_if_minimized=Mock(), move_desktop_window_message=Mock(),
            desktop_message_coord=lambda x, y: (x, y))
        with patch.object(windows, 'PostMessage') as post, \
                patch.object(windows.time, 'sleep', side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                windows.Window.press_and_drag_window_message(owner, (10, 20), (40, 50),
                    [((40, 50), .1)], hold_duration=.2, release_duration=.05)
        self.assertEqual(post.call_args_list[0].args[1], windows.WM_LBUTTONDOWN)
        self.assertEqual(post.call_args_list[-1].args[1], windows.WM_LBUTTONUP)

    def test_task_requests_do_not_share_roi_padding_between_devices(self):
        from tasks.base_task import BaseTask
        from module.atom.image import RuleImage
        rule = RuleImage(roi_front=(0, 0, 2, 2), roi_back=(0, 0, 5, 5), file='',
                         method='Template matching', threshold=.8)
        observed = []
        rule.match = Mock(side_effect=lambda *a, **kw: observed.append(ROI_MATCH_PAD.get()) or True)
        for pad in (2, 0, 2, 0):
            task = object.__new__(BaseTask)
            task.device = Mock(image_roi_padding=pad)
            task.device.get_image_batch_cache.return_value = None
            self.assertTrue(task.appear(rule))
        self.assertEqual(observed, [2, 0, 2, 0])
        self.assertEqual(ROI_MATCH_PAD.get(), 0)

    def test_login_recovery_does_not_schedule_a_restart(self):
        from module.script.runtime_controller import ScriptRuntimeController, ScriptRuntimeDecision
        script = Mock()
        script.desktop_login_recovery.return_value = True
        controller = ScriptRuntimeController(script)
        self.assertEqual(controller._run_game_recovery('offline-check'), ScriptRuntimeDecision.READY)
        script.run.assert_not_called()

    def test_desktop_restart_keeps_healthy_window_by_default(self):
        from tasks.Restart.script_task import ScriptTask
        task = object.__new__(ScriptTask)
        task.device = SimpleNamespace(is_desktop=True)
        task.config = SimpleNamespace(script=SimpleNamespace(desktop=SimpleNamespace(
            restart_when_window_exists=False)))
        task._desktop_start_and_login = Mock()
        task._desktop_restart_and_login = Mock()
        task.app_restart()
        task._desktop_start_and_login.assert_called_once()
        task._desktop_restart_and_login.assert_not_called()

    def test_emulator_background_recovery_keeps_existing_behavior(self):
        from tasks.Restart.script_task import ScriptTask
        task = object.__new__(ScriptTask)
        task.device = Mock(is_desktop=False)
        task.device.app_is_alive.return_value = True
        task.device.app_is_running.return_value = False
        task.app_start = Mock()
        task.app_restart = Mock()
        task.recover_app()
        task.app_start.assert_called_once()
        task.app_restart.assert_not_called()


if __name__ == '__main__':
    unittest.main()
