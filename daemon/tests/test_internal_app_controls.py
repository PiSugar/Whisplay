from __future__ import annotations

import os
import sys
import threading
import time
import types
import unittest
from types import SimpleNamespace


DAEMON_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if DAEMON_DIR not in sys.path:
    sys.path.insert(0, DAEMON_DIR)

sys.modules.setdefault("spidev", types.ModuleType("spidev"))
sys.modules.setdefault("gpiod", types.ModuleType("gpiod"))
try:
    import PIL  # noqa: F401
except ImportError:
    pil_module = types.ModuleType("PIL")
    pil_module.Image = SimpleNamespace(Image=object)
    pil_module.ImageDraw = SimpleNamespace(ImageDraw=object)
    pil_module.ImageFont = SimpleNamespace()
    sys.modules["PIL"] = pil_module

from internal_apps.bluetooth_app import BLUETOOTH_APP_ID
from internal_apps.manager import InternalAppManager
from internal_apps.system_app import SYSTEM_APP_ID
from internal_apps.volume_app import VOLUME_APP_ID
from internal_apps.wifi_app import WIFI_APP_ID
from whisplay_daemon import WhisplayDaemon


INTERNAL_APP_IDS = (
    BLUETOOTH_APP_ID,
    WIFI_APP_ID,
    VOLUME_APP_ID,
    SYSTEM_APP_ID,
)


class InternalAppControlTests(unittest.TestCase):
    def make_manager(self, app_id: str) -> InternalAppManager:
        manager = InternalAppManager()
        app = manager._apps[app_id]
        app.state.selected_index = 0
        app.state.busy = True
        if app_id == WIFI_APP_ID:
            app.state.mode = "list"
        if app_id == BLUETOOTH_APP_ID:
            app.state.pairing_active = False
            app.state.pairing_requires_confirmation = False
        return manager

    def make_daemon(self, app_id: str):
        manager = self.make_manager(app_id)
        daemon = WhisplayDaemon.__new__(WhisplayDaemon)
        daemon.state_lock = threading.RLock()
        daemon._screen_locked = False
        daemon.joy_mode = True
        daemon.foreground_app_id = app_id
        daemon.internal_apps = manager
        app_record = SimpleNamespace(app_id=app_id, exit_gesture="")
        daemon.apps = {app_id: app_record}
        daemon.event_broadcaster = SimpleNamespace(
            broadcast=lambda *_args, **_kwargs: None
        )
        daemon.board = SimpleNamespace(set_rgb=lambda *_color: None)
        daemon.pisugar = SimpleNamespace(poll_home_trigger=lambda: False)
        daemon._recent_release_times = []
        daemon._button_press_started_at = time.time() - 0.1
        daemon._keyboard_hold_cancel_fired = False
        released = []
        daemon._release_focus = lambda app, reason: released.append((app, reason))
        daemon._render_internal_app = lambda: None
        return daemon, released

    def test_up_and_down_navigate_every_internal_app(self):
        for app_id in INTERNAL_APP_IDS:
            with self.subTest(app_id=app_id):
                manager = self.make_manager(app_id)
                manager.handle_keyboard_action(app_id, "down")
                self.assertEqual(manager._apps[app_id].state.selected_index, 1)
                manager.handle_keyboard_action(app_id, "up")
                self.assertEqual(manager._apps[app_id].state.selected_index, 0)

    def test_joy_center_activates_back_on_every_internal_app(self):
        for app_id in INTERNAL_APP_IDS:
            with self.subTest(app_id=app_id):
                daemon, released = self.make_daemon(app_id)
                daemon._on_joystick_pressed("center")
                self.assertEqual(len(released), 1)

    def test_whisplay_click_activates_back_on_every_internal_app(self):
        for app_id in INTERNAL_APP_IDS:
            with self.subTest(app_id=app_id):
                daemon, released = self.make_daemon(app_id)
                daemon._on_button_released()
                self.assertEqual(len(released), 1)


if __name__ == "__main__":
    unittest.main()
