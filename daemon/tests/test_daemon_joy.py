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

from daemon_shared import AUTO_MODEL, resolve_runtime_config
from whisplay_daemon import WhisplayDaemon
from whisplay import WhisplayBoard
from whisplay_client import WhisplayDaemonProxy


class JoyConfigTests(unittest.TestCase):
    def test_physical_switch_direction_mapping(self):
        self.assertEqual(
            WhisplayBoard.JOYSTICK_PINS,
            {"up": 32, "right": 33, "down": 31, "left": 36, "center": 37},
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

    def test_unknown_model_falls_back_to_eeprom_detection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            with open(path, "w", encoding="utf-8") as fp:
                json.dump({"model": "unknown"}, fp)
            config = resolve_runtime_config(
                SimpleNamespace(settings_path=path, apps_dir=temp_dir)
            )
            self.assertEqual(config["configured_model"], AUTO_MODEL)
            self.assertEqual(config["model_source"], "eeprom")

    def test_concrete_model_forces_override(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            with open(path, "w", encoding="utf-8") as fp:
                json.dump({"model": "whisplay-plus"}, fp)
            config = resolve_runtime_config(
                SimpleNamespace(settings_path=path, apps_dir=temp_dir)
            )
            self.assertEqual(config["model"], "whisplay-plus")
            self.assertEqual(config["model_source"], "config")

    def test_direct_board_exposes_model_method(self):
        board = WhisplayBoard.__new__(WhisplayBoard)
        board.model = "whisplay-joy"
        self.assertEqual(board.get_model(), "whisplay-joy")
        self.assertEqual(
            board.get_capabilities(),
            {"five_way": True, "motion": True},
        )

    def test_direct_board_reads_five_way_state(self):
        board = WhisplayBoard.__new__(WhisplayBoard)
        board.enable_joystick = True
        pressed_pins = {32, 37}
        board._gpio_input = lambda pin: 0 if pin in pressed_pins else 1
        self.assertEqual(
            board.get_joystick_state(),
            {
                "up": True,
                "right": False,
                "down": False,
                "left": False,
                "center": True,
            },
        )

    def test_daemon_proxy_reads_current_model(self):
        proxy = WhisplayDaemonProxy.__new__(WhisplayDaemonProxy)
        proxy.model = "whisplay"
        proxy._send_request = lambda command: {
            "payload": {"model": "whisplay-joy"}
        }
        self.assertEqual(proxy.get_model(), "whisplay-joy")

    def test_hardware_info_command_returns_effective_model(self):
        daemon = WhisplayDaemon.__new__(WhisplayDaemon)
        daemon.state_lock = threading.RLock()
        daemon.model = "whisplay-joy"
        daemon.joy_mode = True
        response, keep_open = daemon.handle_command(
            {"version": 1, "cmd": "hardware.get_info", "payload": {}},
            None,
        )
        self.assertFalse(keep_open)
        self.assertEqual(response["payload"]["model"], "whisplay-joy")
        self.assertEqual(
            response["payload"]["capabilities"],
            {"five_way": True, "motion": True},
        )

    def test_hardware_input_commands_return_button_and_motion_data(self):
        daemon = WhisplayDaemon.__new__(WhisplayDaemon)
        daemon.state_lock = threading.RLock()
        sample = SimpleNamespace(
            acceleration_g=(0.1, 0.2, 0.9),
            angular_rate_dps=(1.0, 2.0, 3.0),
            timestamp=12.5,
        )
        daemon.board = SimpleNamespace(
            get_joystick_state=lambda: {"up": True, "center": False},
            read_motion=lambda: sample,
        )
        joystick, _ = daemon.handle_command(
            {"version": 1, "cmd": "joystick.get_state", "payload": {}},
            None,
        )
        motion, _ = daemon.handle_command(
            {"version": 1, "cmd": "motion.get_state", "payload": {}},
            None,
        )
        self.assertTrue(joystick["payload"]["state"]["up"])
        self.assertEqual(motion["payload"]["acceleration_g"], [0.1, 0.2, 0.9])
        self.assertEqual(motion["payload"]["angular_rate_dps"], [1.0, 2.0, 3.0])


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

    def test_external_app_receives_joy_events(self):
        daemon = self.make_daemon()
        daemon.foreground_app_id = "external-app"
        events = []
        daemon.event_broadcaster = SimpleNamespace(
            broadcast=lambda *args, **kwargs: events.append((args, kwargs))
        )
        daemon._on_joystick_pressed("left")
        daemon._on_joystick_released("left")
        self.assertEqual(
            [event[0][0] for event in events],
            ["joystick_pressed", "joystick_released"],
        )
        self.assertEqual(events[0][0][1]["direction"], "left")

    def test_all_directions_navigate_wifi_screen_keyboard(self):
        daemon = self.make_daemon()
        daemon.foreground_app_id = "whisplay-wifi"
        daemon.internal_apps = SimpleNamespace(
            is_internal_app=lambda _app_id: True,
            text_input_active=lambda _app_id: True,
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
            text_input_active=lambda _app_id: text_input_active[0],
            handle_keyboard_action=handle_keyboard_action,
            exit_requested=False,
            consume_dirty=lambda: True,
        )
        rgb = []
        button_pressed = [True]
        daemon.board = SimpleNamespace(
            set_rgb=lambda *color: rgb.append(color),
            button_pressed=lambda: button_pressed[0],
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

        # The polling loop may observe GPIO release before the release callback.
        # It must leave the suppression flag for that callback to consume.
        button_pressed[0] = False
        daemon._process_button_hold(now=2.1)

        # Releasing after the hold action must not select a list item.
        daemon._on_button_released()
        self.assertEqual(handled, ["keyboard_select", "keyboard_cancel"])

    def test_left_returns_from_internal_app(self):
        daemon = self.make_daemon()
        daemon.foreground_app_id = "internal-app"
        daemon.internal_apps = SimpleNamespace(
            is_internal_app=lambda _app_id: True,
            text_input_active=lambda _app_id: False,
        )
        app = SimpleNamespace(app_id="internal-app")
        daemon.apps = {app.app_id: app}
        released = []
        daemon._release_focus = lambda target, reason: released.append((target, reason))
        daemon._on_joystick_pressed("left")
        self.assertEqual(released, [(app, "joy_left_back")])


if __name__ == "__main__":
    unittest.main()
