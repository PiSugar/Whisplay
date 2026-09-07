from __future__ import annotations

import os
import sys
import threading
import types
import unittest
from types import SimpleNamespace


DAEMON_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if DAEMON_DIR not in sys.path:
    sys.path.insert(0, DAEMON_DIR)

# Unit tests exercise daemon state only, so hardware and rendering modules can
# be represented by import-time stubs on development machines without them.
try:
    import PIL  # noqa: F401
except ImportError:
    pil_module = types.ModuleType("PIL")
    pil_module.Image = SimpleNamespace(Image=object)
    pil_module.ImageDraw = SimpleNamespace(ImageDraw=object)
    pil_module.ImageFont = SimpleNamespace()
    sys.modules["PIL"] = pil_module
sys.modules.setdefault("spidev", types.ModuleType("spidev"))
sys.modules.setdefault("gpiod", types.ModuleType("gpiod"))

from internal_apps.system_app import SystemInternalApp
from whisplay_daemon import WhisplayDaemon


class FakeBoard:
    def __init__(self):
        self.backlight = []
        self.rgb = []

    def set_backlight(self, value):
        self.backlight.append(value)

    def set_rgb(self, r, g, b):
        self.rgb.append((r, g, b))


class SystemInternalAppTests(unittest.TestCase):
    def make_app(self):
        calls = []
        locked = []

        def run_command(args, timeout):
            calls.append((args, timeout))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        app = SystemInternalApp(
            threading.RLock(),
            lambda: None,
            run_command,
            lambda _name, target: target(),
            lambda: None,
            lambda: locked.append(True),
        )
        app.activate()
        return app, calls, locked

    def test_lock_screen_runs_without_confirmation(self):
        app, _calls, locked = self.make_app()
        self.assertEqual(app.builtin_app().display_name, "Power")
        self.assertEqual(app.view_model()["title"], "Power Menu")
        app.state.selected_index = 1
        app.handle_button(True)
        self.assertEqual(locked, [True])

    def test_reboot_runs_immediately_with_fixed_systemctl_command(self):
        app, calls, _locked = self.make_app()
        app.state.selected_index = 2
        app.handle_button(True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][0:2], ["sudo", "-n"])
        self.assertTrue(calls[0][0][2].endswith("systemctl"))
        self.assertEqual(calls[0][0][3], "reboot")


class LockScreenStateTests(unittest.TestCase):
    def make_daemon(self):
        daemon = WhisplayDaemon.__new__(WhisplayDaemon)
        daemon.state_lock = threading.RLock()
        daemon.board = FakeBoard()
        daemon.apps = {"whisplay-system": SimpleNamespace(session_token=None)}
        daemon.foreground_app_id = "whisplay-system"
        daemon.pending_launch_app_id = None
        daemon.pending_launch_started_at = 0.0
        daemon.exit_request = None
        daemon._foreground_long_press_fired = False
        daemon._screen_locked = False
        daemon._lock_started_at = 0.0
        daemon._last_lock_led_level = -1
        daemon._button_press_started_at = 0.0
        daemon.last_frame = object()
        daemon._teardown_framebuffer = lambda _app: None
        daemon.rendered_desktop = False
        daemon._render_desktop = lambda: setattr(daemon, "rendered_desktop", True)
        daemon.event_broadcaster = SimpleNamespace(broadcast=lambda *_args, **_kwargs: None)
        return daemon

    def test_button_release_wakes_locked_screen_and_returns_to_desktop(self):
        daemon = self.make_daemon()
        daemon._lock_screen()
        self.assertTrue(daemon._screen_locked)
        self.assertEqual(daemon.board.backlight[-1], 0)

        daemon._update_lock_led()
        self.assertEqual(daemon.board.rgb[-1][0:2], (0, 0))
        self.assertGreater(daemon.board.rgb[-1][2], 0)

        daemon._on_button_released()
        self.assertFalse(daemon._screen_locked)
        self.assertEqual(daemon.board.backlight[-1], 100)
        self.assertEqual(daemon.board.rgb[-1], (0, 0, 0))
        self.assertTrue(daemon.rendered_desktop)


if __name__ == "__main__":
    unittest.main()
