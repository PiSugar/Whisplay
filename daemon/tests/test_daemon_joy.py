from __future__ import annotations

import json
import os
import sys
import tempfile
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

from daemon_shared import resolve_runtime_config
from whisplay_daemon import WhisplayDaemon
from whisplay import WhisplayBoard


class JoyConfigTests(unittest.TestCase):
    def test_physical_switch_direction_mapping(self):
        self.assertEqual(
            WhisplayBoard.JOYSTICK_PINS,
            {"up": 32, "right": 33, "down": 31, "left": 29, "center": 37},
        )

    def test_loads_whisplay_joy_model(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            with open(path, "w", encoding="utf-8") as fp:
                json.dump({"model": "whisplay-joy"}, fp)
            config = resolve_runtime_config(
                SimpleNamespace(settings_path=path, apps_dir=temp_dir)
            )
            self.assertEqual(config["model"], "whisplay-joy")

    def test_unknown_model_falls_back_to_whisplay(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            with open(path, "w", encoding="utf-8") as fp:
                json.dump({"model": "unknown"}, fp)
            config = resolve_runtime_config(
                SimpleNamespace(settings_path=path, apps_dir=temp_dir)
            )
            self.assertEqual(config["model"], "whisplay")


class JoyInteractionTests(unittest.TestCase):
    def make_daemon(self):
        daemon = WhisplayDaemon.__new__(WhisplayDaemon)
        daemon.state_lock = threading.RLock()
        daemon._screen_locked = False
        daemon.foreground_app_id = None
        daemon.internal_apps = SimpleNamespace(is_internal_app=lambda _app_id: False)
        daemon.apps = {}
        daemon.event_broadcaster = SimpleNamespace(broadcast=lambda *_args, **_kwargs: None)
        daemon.actions = []
        daemon._handle_keyboard_action = daemon.actions.append
        return daemon

    def test_directions_follow_screen_axes(self):
        daemon = self.make_daemon()
        for direction in ("up", "right", "down", "left"):
            daemon._on_joystick_pressed(direction)
        self.assertEqual(daemon.actions, ["up", "submit", "down"])

    def test_center_selects_like_right(self):
        daemon = self.make_daemon()
        daemon._on_joystick_pressed("center")
        daemon._on_joystick_released("center")
        self.assertEqual(daemon.actions, ["submit"])

    def test_external_app_pauses_joy_input(self):
        daemon = self.make_daemon()
        daemon.foreground_app_id = "external-app"
        for direction in ("up", "right", "down", "left", "center"):
            daemon._on_joystick_pressed(direction)
        self.assertEqual(daemon.actions, [])

    def test_all_directions_navigate_wifi_screen_keyboard(self):
        daemon = self.make_daemon()
        daemon.foreground_app_id = "whisplay-wifi"
        daemon.internal_apps = SimpleNamespace(
            is_internal_app=lambda _app_id: True,
            text_input_active=lambda: True,
        )
        for direction in ("up", "right", "down", "left", "center"):
            daemon._on_joystick_pressed(direction)
        self.assertEqual(
            daemon.actions,
            [
                "keyboard_up",
                "keyboard_right",
                "keyboard_down",
                "keyboard_left",
                "keyboard_select",
            ],
        )

    def test_hold_cancels_keyboard_before_button_release_and_flashes_red(self):
        daemon = self.make_daemon()
        daemon.joy_mode = True
        daemon.foreground_app_id = "whisplay-wifi"
        app = SimpleNamespace(app_id="whisplay-wifi", exit_gesture="")
        daemon.apps = {app.app_id: app}
        handled = []
        text_input_active = [True]

        def handle_keyboard_action(_app_id, action):
            handled.append(action)
            if action == "keyboard_cancel":
                text_input_active[0] = False

        daemon.internal_apps = SimpleNamespace(
            is_internal_app=lambda _app_id: True,
            text_input_active=lambda: text_input_active[0],
            handle_keyboard_action=handle_keyboard_action,
            exit_requested=False,
            consume_dirty=lambda: True,
        )
        rgb = []
        daemon.board = SimpleNamespace(
            set_rgb=lambda *color: rgb.append(color),
            button_pressed=lambda: True,
        )
        daemon.pisugar = SimpleNamespace(poll_home_trigger=lambda: False)
        daemon._recent_release_times = []
        daemon._foreground_long_press_fired = False
        daemon._keyboard_hold_cancel_fired = False
        daemon._render_internal_app = lambda: None

        daemon._button_press_started_at = time.time() - 0.1
        daemon._on_button_released()

        text_input_active[0] = True
        daemon._button_press_started_at = 1.0
        daemon._process_button_hold(now=2.0)
        self.assertEqual(handled, ["keyboard_select", "keyboard_cancel"])
        self.assertEqual(rgb[-1], (255, 0, 0))

        # Releasing after the hold action must not select a list item.
        daemon._on_button_released()
        self.assertEqual(handled, ["keyboard_select", "keyboard_cancel"])

    def test_left_returns_from_internal_app(self):
        daemon = self.make_daemon()
        daemon.foreground_app_id = "internal-app"
        daemon.internal_apps = SimpleNamespace(
            is_internal_app=lambda _app_id: True,
            text_input_active=lambda: False,
        )
        app = SimpleNamespace(app_id="internal-app")
        daemon.apps = {app.app_id: app}
        released = []
        daemon._release_focus = lambda target, reason: released.append((target, reason))
        daemon._on_joystick_pressed("left")
        self.assertEqual(released, [(app, "joy_left_back")])


if __name__ == "__main__":
    unittest.main()
