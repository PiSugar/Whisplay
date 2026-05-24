import re
import subprocess
import threading
import time

from .bluetooth_app import BLUETOOTH_APP_ID, BluetoothInternalApp
from .volume_app import VOLUME_APP_ID, VolumeInternalApp
from .wifi_app import WIFI_APP_ID, WifiInternalApp


class InternalAppManager:
    REFRESH_INTERVAL_SEC = 20.0

    def __init__(self):
        self._lock = threading.RLock()
        self._dirty = False
        self._exit_requested = False
        self._threads: dict[str, threading.Thread] = {}
        self.bluetooth = BluetoothInternalApp(
            self._lock,
            self._mark_dirty,
            self._run_command,
            self._spawn_worker,
            self._set_error,
            self._request_exit,
            self._strip_ansi,
        )
        self.wifi = WifiInternalApp(
            self._lock,
            self._mark_dirty,
            self._run_command,
            self._spawn_worker,
            self._request_exit,
        )
        self.volume = VolumeInternalApp(
            self._lock,
            self._mark_dirty,
            self._run_command,
            self._spawn_worker,
            self._request_exit,
        )
        self._apps = {
            BLUETOOTH_APP_ID: self.bluetooth,
            WIFI_APP_ID: self.wifi,
            VOLUME_APP_ID: self.volume,
        }

    def start(self):
        self.bluetooth.start()

    def stop(self):
        self.bluetooth.stop()

    def builtin_apps(self):
        return [self.bluetooth.builtin_app(), self.wifi.builtin_app(), self.volume.builtin_app()]

    def is_internal_app(self, app_id: str | None) -> bool:
        return app_id in self._apps

    @property
    def exit_requested(self) -> bool:
        with self._lock:
            return self._exit_requested

    def clear_exit_requested(self):
        with self._lock:
            self._exit_requested = False

    def text_input_active(self) -> bool:
        return self.wifi.text_input_active()

    def consume_dirty(self) -> bool:
        with self._lock:
            dirty = self._dirty
            self._dirty = False
            return dirty

    def activate(self, app_id: str):
        app = self._apps.get(app_id)
        if app is not None and hasattr(app, "activate"):
            app.activate()
        self._mark_dirty()
        self.refresh_async(app_id, force=True)

    def tick(self, app_id: str | None):
        app = self._apps.get(app_id)
        if app is None:
            return
        state = app.state
        now = time.time()
        if state.busy:
            return
        if now - state.last_refresh_at >= self.REFRESH_INTERVAL_SEC:
            self.refresh_async(app_id, force=True)

    def handle_button_release(self, app_id: str, is_long_press: bool):
        app = self._apps.get(app_id)
        if app is not None:
            app.handle_button(is_long_press)

    def handle_keyboard_action(self, app_id: str, action):
        app = self._apps.get(app_id)
        if app is not None:
            app.handle_keyboard_action(action)

    def get_view_model(self, app_id: str) -> dict:
        app = self._apps.get(app_id)
        return app.view_model() if app is not None else {}

    def refresh_async(self, app_id: str, force: bool = False):
        app = self._apps.get(app_id)
        if app is None:
            return
        if app_id == BLUETOOTH_APP_ID:
            app.refresh_async(force=force)
        else:
            app.refresh_async()

    def _request_exit(self):
        with self._lock:
            self._exit_requested = True

    def _spawn_worker(self, name: str, target):
        with self._lock:
            thread = self._threads.get(name)
            if thread and thread.is_alive():
                return
            thread = threading.Thread(target=self._run_worker, args=(target,), daemon=True)
            self._threads[name] = thread
            thread.start()

    def _run_worker(self, target):
        try:
            target()
        except Exception as exc:
            self._set_error(str(exc))

    def _set_error(self, message: str):
        text = (message or "operation failed").strip()
        self.bluetooth.set_error(text)
        self.wifi.set_error(text)
        self.volume.set_error(text)

    def _mark_dirty(self):
        with self._lock:
            self._dirty = True

    def _run_command(self, args: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)

    def _strip_ansi(self, text: str) -> str:
        return re.sub(r"\x1b\[[0-9;]*m", "", text)

