from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
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


class AutostartConfigTests(unittest.TestCase):
    def test_loads_autostart_app_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            with open(path, "w", encoding="utf-8") as fp:
                json.dump({"autostart_app_id": "  demo-app  "}, fp)

            config = resolve_runtime_config(
                SimpleNamespace(settings_path=path, apps_dir=temp_dir)
            )

            self.assertEqual(config["autostart_app_id"], "demo-app")
            with open(path, "r", encoding="utf-8") as fp:
                self.assertEqual(json.load(fp)["autostart_app_id"], "demo-app")

    def test_missing_autostart_app_id_is_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            config = resolve_runtime_config(
                SimpleNamespace(settings_path=path, apps_dir=temp_dir)
            )
            self.assertEqual(config["autostart_app_id"], "")


class AutostartLaunchTests(unittest.TestCase):
    def make_daemon(self, autostart_app_id="demo-app"):
        daemon = WhisplayDaemon.__new__(WhisplayDaemon)
        daemon.autostart_app_id = autostart_app_id
        daemon.state_lock = threading.RLock()
        daemon.selected_app_index = 0
        first = SimpleNamespace(app_id="first-app")
        target = SimpleNamespace(app_id="demo-app")
        daemon.apps = {first.app_id: first, target.app_id: target}
        daemon._app_list = lambda: [first, target]
        daemon.launched = []
        daemon._launch_app = daemon.launched.append
        return daemon

    def test_launches_and_selects_configured_app(self):
        daemon = self.make_daemon()
        daemon._autostart_configured_app()
        self.assertEqual([app.app_id for app in daemon.launched], ["demo-app"])
        self.assertEqual(daemon.selected_app_index, 1)

    def test_empty_autostart_does_nothing(self):
        daemon = self.make_daemon("")
        daemon._autostart_configured_app()
        self.assertEqual(daemon.launched, [])


class AutostartCommandTests(unittest.TestCase):
    def make_daemon(self, settings_path):
        daemon = WhisplayDaemon.__new__(WhisplayDaemon)
        daemon.state_lock = threading.RLock()
        daemon.settings_path = settings_path
        daemon.autostart_app_id = ""
        daemon.apps = {"demo-app": SimpleNamespace(app_id="demo-app")}
        return daemon

    def test_command_persists_autostart_app(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            with open(path, "w", encoding="utf-8") as fp:
                json.dump({"model": "whisplay"}, fp)
            daemon = self.make_daemon(path)

            response, keep_open = daemon.handle_command(
                {
                    "version": 1,
                    "cmd": "app.autostart.set",
                    "payload": {"app_id": "demo-app"},
                },
                None,
            )

            self.assertTrue(response["ok"])
            self.assertFalse(keep_open)
            self.assertEqual(daemon.autostart_app_id, "demo-app")
            with open(path, "r", encoding="utf-8") as fp:
                settings = json.load(fp)
            self.assertEqual(settings["model"], "whisplay")
            self.assertEqual(settings["autostart_app_id"], "demo-app")

    def test_command_rejects_unknown_app(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            daemon = self.make_daemon(os.path.join(temp_dir, "settings.json"))
            with self.assertRaisesRegex(RuntimeError, "unknown app"):
                daemon.handle_command(
                    {
                        "version": 1,
                        "cmd": "app.autostart.set",
                        "payload": {"app_id": "missing-app"},
                    },
                    None,
                )


if __name__ == "__main__":
    unittest.main()
