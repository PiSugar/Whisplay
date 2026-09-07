from __future__ import annotations

import shutil
import time
from dataclasses import dataclass

from daemon_models import AppRecord


SYSTEM_APP_ID = "whisplay-system"


@dataclass
class SystemViewState:
    selected_index: int = 0
    busy: bool = False
    status: str = "Long press to select"
    last_refresh_at: float = 0.0


class SystemInternalApp:
    ACTIONS = ("lock", "reboot", "poweroff")

    def __init__(self, lock, mark_dirty, run_command, spawn_worker, request_exit, lock_screen):
        self._lock = lock
        self._mark_dirty = mark_dirty
        self._run_command = run_command
        self._spawn_worker = spawn_worker
        self._request_exit = request_exit
        self._lock_screen = lock_screen
        self.state = SystemViewState()

    def builtin_app(self) -> AppRecord:
        return AppRecord(
            app_id=SYSTEM_APP_ID,
            display_name="Power",
            icon="PW",
            exit_gesture="",
            priority=170,
            persist=False,
        )

    def activate(self):
        with self._lock:
            self.state.selected_index = 0
            self.state.busy = False
            self.state.status = "Long press to select"

    def handle_button(self, is_long_press: bool):
        with self._lock:
            total = 4
            if not is_long_press:
                self.state.selected_index = (self.state.selected_index + 1) % total
                self._mark_dirty()
                return
            if self.state.busy:
                return
            selected_index = self.state.selected_index

        if selected_index == 0:
            self._request_exit()
            return

        action = self.ACTIONS[selected_index - 1]
        if action == "lock":
            self._lock_screen()
            return

        with self._lock:
            self.state.busy = True
            self.state.status = "Shutting down..." if action == "poweroff" else "Rebooting..."
        self._mark_dirty()
        self._spawn_worker("system-power-action", lambda: self._run_power_action(action))

    def handle_keyboard_action(self, action: str):
        with self._lock:
            total = 4
            if action == "up":
                self.state.selected_index = (self.state.selected_index - 1) % total
                self._mark_dirty()
                return
            if action == "down":
                self.state.selected_index = (self.state.selected_index + 1) % total
                self._mark_dirty()
                return
        if action == "submit":
            self.handle_button(True)

    def view_model(self) -> dict:
        with self._lock:
            items = [
                {"title": "Back", "meta": "Return to desktop"},
                {"title": "Lock screen", "meta": "Sleep display"},
                {"title": "Reboot", "meta": "Restart device"},
                {"title": "Shutdown", "meta": "Power off device"},
            ]
            return {
                "kind": "list",
                "title": "Power Menu",
                "subtitle": "Device controls",
                "items": items,
                "selected_index": min(self.state.selected_index, len(items) - 1),
                "status": self.state.status,
                "busy": self.state.busy,
            }

    def set_error(self, message: str):
        with self._lock:
            if not self.state.busy:
                return
            self.state.busy = False
            self.state.status = message
            self.state.last_refresh_at = time.time()
        self._mark_dirty()

    def refresh_async(self):
        with self._lock:
            self.state.last_refresh_at = time.time()

    def _run_power_action(self, action: str):
        systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"
        result = self._run_command(["sudo", "-n", systemctl, action], timeout=10.0)
        if result.returncode != 0:
            message = (result.stderr or result.stdout or f"{action} failed").strip()
            raise RuntimeError(message)
