from __future__ import annotations

import os
import sys
import threading
import unittest


DAEMON_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if DAEMON_DIR not in sys.path:
    sys.path.insert(0, DAEMON_DIR)

from internal_apps.manager import InternalAppManager
from internal_apps.volume_app import VOLUME_APP_ID
from internal_apps.wifi_app import SCREEN_KEYBOARD_ROWS, WIFI_APP_ID, WifiInternalApp


class WifiScreenKeyboardTests(unittest.TestCase):
    def make_app(self):
        self.dirty_count = 0
        self.workers = []

        def mark_dirty():
            self.dirty_count += 1

        app = WifiInternalApp(
            threading.RLock(),
            mark_dirty,
            lambda *_args, **_kwargs: None,
            lambda name, target: self.workers.append((name, target)),
            lambda: None,
        )
        app.state.mode = "input"
        app.state.password_target_ssid = "Test WiFi"
        app._reset_screen_keyboard()
        return app

    def test_moves_and_selects_lowercase_character(self):
        app = self.make_app()
        app.handle_keyboard_action("keyboard_right")
        app.handle_keyboard_action("keyboard_select")
        self.assertEqual(app.state.password_buffer, "w")
        self.assertEqual(app.view_model()["selected_key"], "w")

    def test_shift_enters_uppercase_and_number_symbol(self):
        app = self.make_app()
        app.state.keyboard_row = 3
        app.state.keyboard_col = 0
        app.handle_keyboard_action("keyboard_select")
        self.assertTrue(app.state.keyboard_shifted)

        app.state.keyboard_row = 1
        app.state.keyboard_col = 0
        app.handle_keyboard_action("keyboard_select")
        app.state.keyboard_row = 0
        app.state.keyboard_col = 1
        app.handle_keyboard_action("keyboard_select")
        self.assertEqual(app.state.password_buffer, "Q!")

    def test_backspace_join_and_cancel_actions(self):
        app = self.make_app()
        app.state.password_buffer = "secretx"
        app.state.keyboard_row = 3
        app.state.keyboard_col = len(SCREEN_KEYBOARD_ROWS[3]) - 1
        app.handle_keyboard_action("keyboard_select")
        self.assertEqual(app.state.password_buffer, "secret")

        app.state.keyboard_row = 4
        app.state.keyboard_col = 1
        app.handle_keyboard_action("keyboard_select")
        self.assertEqual(app.state.mode, "list")
        self.assertTrue(app.state.busy)
        self.assertEqual([name for name, _target in self.workers], ["wifi-action"])

        app.state.mode = "input"
        app.state.password_buffer = "discard"
        app.handle_keyboard_action("keyboard_cancel")
        self.assertEqual(app.state.mode, "list")
        self.assertEqual(app.state.password_buffer, "")

    def test_view_model_contains_miniature_keyboard(self):
        app = self.make_app()
        view_model = app.view_model()
        self.assertTrue(view_model["screen_keyboard"])
        self.assertEqual(len(view_model["keyboard_rows"]), 5)
        selected = [
            key
            for row in view_model["keyboard_rows"]
            for key in row
            if key["selected"]
        ]
        self.assertEqual(selected, [{"label": "q", "width": 1.0, "selected": True}])

        app.state.keyboard_row = 4
        app.state.keyboard_col = 0
        self.assertEqual(app.view_model()["selected_key"], "SPACE")

    def test_only_foreground_app_keyboard_state_controls_input_routing(self):
        manager = InternalAppManager()
        manager.wifi.state.mode = "input"

        self.assertTrue(manager.text_input_active(WIFI_APP_ID))
        self.assertFalse(manager.text_input_active(VOLUME_APP_ID))

        manager.wifi.state.mode = "list"
        self.assertFalse(manager.text_input_active(WIFI_APP_ID))


if __name__ == "__main__":
    unittest.main()
