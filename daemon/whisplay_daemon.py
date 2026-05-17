import json
import mmap
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.append(RUNTIME_DIR)

from daemon_events import EventBroadcaster
from daemon_models import AppRecord
from daemon_pisugar import PiSugarManager
from daemon_renderer import DesktopRenderer
from daemon_shared import (
    BUTTON_LONG_PRESS_SEC,
    DEFAULT_APP_LOG_PATH,
    DEFAULT_DAEMON_HOME,
    DEFAULT_PISUGAR_HOME_BUTTON,
    EXIT_GESTURE_LONG_PRESS,
    EXIT_GESTURE_QUAD_CLICK,
    EXIT_REQUEST_TIMEOUT_SEC,
    FRAMEBUFFER_SIZE,
    FRAMEBUFFER_STRIDE,
    PENDING_LAUNCH_TIMEOUT_SEC,
    PIXEL_FORMAT,
    QUAD_CLICK_WINDOW_SEC,
    RENDER_FPS,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    STATUS_POLL_INTERVAL_SEC,
    VALID_EXIT_GESTURES,
    VALID_PISUGAR_HOME_BUTTONS,
    parse_args,
    resolve_runtime_config,
)
from internal_apps import ExternalKeyboardReader, InternalAppManager
from daemon_status import StatusPoller
from whisplay import WhisplayBoard


class WhisplayDaemon:
    def __init__(
        self,
        socket_path: str,
        apps_dir: str,
        settings_path: str,
        pisugar_home_button: str = DEFAULT_PISUGAR_HOME_BUTTON,
    ):
        self.socket_path = socket_path
        self.socket_dir = os.path.dirname(socket_path) or "."
        self.apps_dir = os.path.abspath(os.path.expanduser(apps_dir))
        self.settings_path = os.path.abspath(os.path.expanduser(settings_path))
        self.server_socket = None
        self.running = True
        self.state_lock = threading.RLock()
        self.event_broadcaster = EventBroadcaster()
        self.board = WhisplayBoard()
        self.desktop = DesktopRenderer(self.board, SCRIPT_DIR)
        self.pisugar = PiSugarManager()
        self.status_poller = StatusPoller(self.pisugar)
        self.internal_apps = InternalAppManager()
        self.keyboard_reader = ExternalKeyboardReader()
        self.pisugar_home_button = self._normalize_pisugar_home_button(pisugar_home_button)
        self.apps: dict[str, AppRecord] = {}
        self.selected_app_index = 0
        self.foreground_app_id: str | None = None
        self.pending_launch_app_id: str | None = None
        self.pending_launch_started_at = 0.0
        self.exit_request = None
        self.last_frame = None
        self._button_press_started_at = 0.0
        self._recent_release_times: list[float] = []
        self._foreground_long_press_fired = False
        self._last_status_poll_at = 0.0
        self._render_thread = threading.Thread(target=self._render_loop, daemon=True)
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._load_apps()
        self._register_internal_apps()
        self.board.on_button_press(self._on_button_pressed)
        self.board.on_button_release(self._on_button_released)

    def _move_desktop_selection(self, delta: int):
        apps = self._app_list()
        if not apps:
            self._render_desktop()
            return
        self.selected_app_index = (self.selected_app_index + delta) % len(apps)
        self._render_desktop()

    def _handle_keyboard_action(self, action: str):
        with self.state_lock:
            if self.foreground_app_id and self.internal_apps.text_input_active():
                self.internal_apps.handle_keyboard_action(self.foreground_app_id, action)
                if self.internal_apps.consume_dirty():
                    self._render_internal_app()
                return

            if action == "cancel":
                if self.foreground_app_id:
                    app = self.apps.get(self.foreground_app_id)
                    if app is not None:
                        if app.disable_esc_exit_key:
                            return
                        self._request_exit(app, "keyboard_escape_exit")
                return

            if self.foreground_app_id and self.internal_apps.is_internal_app(self.foreground_app_id):
                if action in {"up", "down", "submit"}:
                    self.internal_apps.handle_keyboard_action(self.foreground_app_id, action)
                    if self.internal_apps.exit_requested:
                        self.internal_apps.clear_exit_requested()
                        app = self.apps.get(self.foreground_app_id)
                        if app is not None:
                            self._release_focus(app, "list_back")
                        return
                    if self.internal_apps.consume_dirty():
                        self._render_internal_app()
                return

            if self.foreground_app_id:
                return

            if action == "up":
                self._move_desktop_selection(-1)
            elif action == "down":
                self._move_desktop_selection(1)
            elif action == "submit":
                selected = self._current_selected_app()
                if selected is not None:
                    self._launch_app(selected)

    def _normalize_priority(self, value) -> int:
        try:
            return int(value)
        except Exception:
            return 0

    def _normalize_exit_gesture(self, value) -> str:
        text = str(value or EXIT_GESTURE_QUAD_CLICK).strip().lower()
        if text not in VALID_EXIT_GESTURES:
            return EXIT_GESTURE_QUAD_CLICK
        return text

    def _normalize_pisugar_home_button(self, value) -> str:
        text = str(value or DEFAULT_PISUGAR_HOME_BUTTON).strip().lower()
        if text not in VALID_PISUGAR_HOME_BUTTONS:
            return DEFAULT_PISUGAR_HOME_BUTTON
        return text

    def _safe_app_filename(self, app_id: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", app_id.strip())
        return safe or "app"

    def _persisted_app_path(self, app_id: str) -> str:
        return os.path.join(self.apps_dir, f"{self._safe_app_filename(app_id)}.json")

    def _app_record_to_config(self, app: AppRecord) -> dict:
        return {
            "app_id": app.app_id,
            "display_name": app.display_name,
            "icon": app.icon,
            "launch_command": app.launch_command,
            "cwd": app.cwd,
            "env": app.env,
            "exit_gesture": app.exit_gesture,
            "priority": app.priority,
            "use_daemon_default_log": app.use_daemon_default_log,
            "persist": app.persist,
            "disable_esc_exit_key": app.disable_esc_exit_key,
        }

    def _load_apps(self):
        if not os.path.isdir(self.apps_dir):
            return
        for entry in sorted(os.listdir(self.apps_dir)):
            if not entry.endswith(".json"):
                continue
            path = os.path.join(self.apps_dir, entry)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as fp:
                    item = json.load(fp)
            except Exception as exc:
                print(f"[WhisplayDaemon] Failed to load app config {path}: {exc}")
                continue
            if not isinstance(item, dict):
                print(f"[WhisplayDaemon] Ignoring non-object app config: {path}")
                continue
            app_id = item.get("app_id")
            if not app_id:
                continue
            self.apps[app_id] = AppRecord(
                app_id=app_id,
                display_name=item.get("display_name", app_id),
                icon=item.get("icon", ""),
                launch_command=item.get("launch_command", ""),
                cwd=item.get("cwd", ""),
                env=item.get("env", {}) or {},
                exit_gesture=self._normalize_exit_gesture(item.get("exit_gesture")),
                priority=self._normalize_priority(item.get("priority", 0)),
                use_daemon_default_log=bool(item.get("use_daemon_default_log", False)),
                persist=bool(item.get("persist", False)),
                disable_esc_exit_key=bool(item.get("disable_esc_exit_key", False)),
            )

    def _register_internal_apps(self):
        for app in self.internal_apps.builtin_apps():
            if app.app_id not in self.apps:
                self.apps[app.app_id] = app

    def _save_app(self, app: AppRecord):
        path = self._persisted_app_path(app.app_id)
        if not app.persist:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            except Exception as exc:
                print(f"[WhisplayDaemon] Failed to remove app config {path}: {exc}")
            return
        os.makedirs(self.apps_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(self._app_record_to_config(app), fp, ensure_ascii=True, indent=2)

    def _app_list(self) -> list[AppRecord]:
        return sorted(
            self.apps.values(),
            key=lambda app: (-app.priority, app.display_name.lower(), app.app_id),
        )

    def _current_selected_app(self) -> AppRecord | None:
        apps = self._app_list()
        if not apps:
            return None
        self.selected_app_index %= len(apps)
        return apps[self.selected_app_index]

    def _render_desktop(self):
        self.last_frame = None
        running_app_id = None
        if not self.foreground_app_id:
            for app in self._app_list():
                if app.is_running():
                    running_app_id = app.app_id
                    break
        self.desktop.render(
            self._app_list(),
            self.selected_app_index,
            self.pending_launch_app_id,
            running_app_id,
            self.status_poller.wifi_signal_level,
            self.status_poller.battery_level,
        )

    def _render_internal_app(self):
        if not self.internal_apps.is_internal_app(self.foreground_app_id):
            return
        self.last_frame = None
        view_model = self.internal_apps.get_view_model(self.foreground_app_id)
        self.desktop.render_internal_app(view_model)

    def _allocate_framebuffer(self, app: AppRecord):
        framebuffer_path = f"/tmp/whisplay-fb-{app.app_id}-{uuid.uuid4().hex}.bin"
        framebuffer_file = open(framebuffer_path, "w+b")
        framebuffer_file.truncate(FRAMEBUFFER_SIZE)
        framebuffer_map = mmap.mmap(framebuffer_file.fileno(), FRAMEBUFFER_SIZE)
        framebuffer_map.write(b"\x00" * FRAMEBUFFER_SIZE)
        framebuffer_map.flush()
        framebuffer_map.seek(0)
        app.framebuffer_path = framebuffer_path
        app.framebuffer_file = framebuffer_file
        app.framebuffer_mmap = framebuffer_map

    def _teardown_framebuffer(self, app: AppRecord):
        if app.framebuffer_mmap is not None:
            try:
                app.framebuffer_mmap.close()
            except Exception:
                pass
            app.framebuffer_mmap = None
        if app.framebuffer_file is not None:
            try:
                app.framebuffer_file.close()
            except Exception:
                pass
            app.framebuffer_file = None
        if app.framebuffer_path:
            try:
                os.unlink(app.framebuffer_path)
            except Exception:
                pass
        app.framebuffer_path = None

    def _close_process_log(self, app: AppRecord):
        if app.process_log_handle is not None:
            try:
                app.process_log_handle.close()
            except Exception:
                pass
            app.process_log_handle = None

    def _grant_focus(self, app: AppRecord):
        if self.foreground_app_id and self.foreground_app_id != app.app_id:
            raise RuntimeError("another app is already foreground")
        app.session_token = uuid.uuid4().hex
        self._teardown_framebuffer(app)
        self._allocate_framebuffer(app)
        self.foreground_app_id = app.app_id
        self.pending_launch_app_id = None
        self.pending_launch_started_at = 0.0
        self.exit_request = None
        self.event_broadcaster.broadcast(
            "app_foreground_acquired",
            {
                "app_id": app.app_id,
                "session_token": app.session_token,
            },
            app_id=app.app_id,
        )

    def _release_focus(self, app: AppRecord, reason: str):
        self.event_broadcaster.broadcast(
            "app_focus_revoked",
            {"app_id": app.app_id, "reason": reason},
            app_id=app.app_id,
        )
        app.session_token = None
        self._teardown_framebuffer(app)
        self.foreground_app_id = None
        self.exit_request = None
        self._foreground_long_press_fired = False
        self.pending_launch_app_id = None
        self.pending_launch_started_at = 0.0
        self._render_desktop()
        self.event_broadcaster.broadcast("desktop_entered", {"reason": reason})

    def _request_exit(self, app: AppRecord, reason: str):
        print(f"[WhisplayDaemon] Exit requested for {app.app_id}: {reason}")
        if self.internal_apps.is_internal_app(app.app_id):
            self._release_focus(app, reason)
            return
        self._foreground_long_press_fired = True
        self.exit_request = {
            "app_id": app.app_id,
            "deadline": time.time() + EXIT_REQUEST_TIMEOUT_SEC,
            "reason": reason,
        }
        self.event_broadcaster.broadcast(
            "app_exit_requested",
            {"app_id": app.app_id, "reason": reason},
            app_id=app.app_id,
        )

    def _request_exit_from_pisugar(self):
        if not self.foreground_app_id:
            return
        app = self.apps.get(self.foreground_app_id)
        if app is None:
            return
        self._request_exit(app, f"pisugar_{self.pisugar.home_button_event}_exit")

    def _refresh_status_icons(self, force: bool = False):
        now = time.time()
        if not force and now - self._last_status_poll_at < STATUS_POLL_INTERVAL_SEC:
            return
        self._last_status_poll_at = now
        changed = self.status_poller.refresh()
        if changed and not self.foreground_app_id:
            self._render_desktop()

    def _init_pisugar_integration(self):
        sock_path = self.pisugar.socket_path()
        if not sock_path:
            print("[WhisplayDaemon] pisugar-server socket not found, skipping integration")
            return
        self.pisugar.sock_path = sock_path
        self.pisugar.cleanup_daemon_managed_hooks(sock_path)
        if self.pisugar_home_button == "none":
            print("[WhisplayDaemon] pisugar home button integration disabled by settings")
            return
        self.pisugar.home_button_event = self.pisugar_home_button
        custom_button = self.pisugar.has_custom_button_event(sock_path, self.pisugar_home_button)
        if custom_button is True:
            print(
                f"[WhisplayDaemon] pisugar {self.pisugar_home_button} button has custom event, "
                "daemon will not hijack it"
            )
            return
        if custom_button is None:
            print(
                f"[WhisplayDaemon] unable to determine pisugar {self.pisugar_home_button} button config, "
                "skip integration for safety"
            )
            return
        if self.pisugar.setup_button_hook(sock_path, self.pisugar_home_button):
            self.pisugar.home_button_exit_enabled = True
            print(
                f"[WhisplayDaemon] pisugar {self.pisugar_home_button} home hook enabled via {sock_path}"
            )
        else:
            print(
                f"[WhisplayDaemon] failed to install pisugar {self.pisugar_home_button} home hook"
            )

    def _launch_app(self, app: AppRecord):
        if self.internal_apps.is_internal_app(app.app_id):
            self.foreground_app_id = app.app_id
            self.pending_launch_app_id = None
            self.pending_launch_started_at = 0.0
            self.exit_request = None
            self._foreground_long_press_fired = False
            self.internal_apps.activate(app.app_id)
            self._render_internal_app()
            return
        if not app.launch_command:
            raise RuntimeError(f"app {app.app_id} has no launch command")
        if app.is_running():
            if self.event_broadcaster.has_app_subscribers(app.app_id):
                self._grant_focus(app)
            else:
                self.pending_launch_app_id = app.app_id
                self.pending_launch_started_at = time.time()
                self._render_desktop()
            return
        env = os.environ.copy()
        env.update(app.env or {})
        if app.use_daemon_default_log:
            env["WHISPLAY_DAEMON_DEFAULT_LOG"] = DEFAULT_APP_LOG_PATH
        cwd = app.cwd or None
        stdout_target = None
        try:
            if app.use_daemon_default_log:
                os.makedirs(DEFAULT_DAEMON_HOME, exist_ok=True)
                app.process_log_handle = open(DEFAULT_APP_LOG_PATH, "ab")
                stdout_target = app.process_log_handle
            app.process = subprocess.Popen(
                app.launch_command,
                shell=True,
                cwd=cwd,
                env=env,
                start_new_session=True,
                stdout=stdout_target,
                stderr=subprocess.STDOUT if stdout_target is not None else None,
            )
        except Exception:
            self._close_process_log(app)
            self.pending_launch_app_id = None
            self.pending_launch_started_at = 0.0
            self._render_desktop()
            raise
        self.pending_launch_app_id = app.app_id
        self.pending_launch_started_at = time.time()
        self._render_desktop()

    def _on_button_pressed(self):
        with self.state_lock:
            self._button_press_started_at = time.time()
            self._foreground_long_press_fired = False
            if not self.foreground_app_id or self.internal_apps.is_internal_app(self.foreground_app_id):
                self.board.set_rgb(0, 0, 255)
            if self.foreground_app_id and not self.internal_apps.is_internal_app(self.foreground_app_id):
                self.event_broadcaster.broadcast(
                    "button_pressed",
                    {"app_id": self.foreground_app_id},
                    app_id=self.foreground_app_id,
                )

    def _on_button_released(self):
        with self.state_lock:
            if not self.foreground_app_id or self.internal_apps.is_internal_app(self.foreground_app_id):
                self.board.set_rgb(0, 0, 0)
            now = time.time()
            press_duration = now - self._button_press_started_at if self._button_press_started_at else 0
            self._button_press_started_at = 0.0

            if self.foreground_app_id:
                app = self.apps.get(self.foreground_app_id)
                is_internal_app = self.internal_apps.is_internal_app(self.foreground_app_id)
                if not self.internal_apps.is_internal_app(self.foreground_app_id):
                    self.event_broadcaster.broadcast(
                        "button_released",
                        {"app_id": self.foreground_app_id},
                        app_id=self.foreground_app_id,
                    )
                if app and app.exit_gesture == EXIT_GESTURE_QUAD_CLICK:
                    quad_click_window_sec = 5.0 if is_internal_app else QUAD_CLICK_WINDOW_SEC
                    self._recent_release_times = [
                        value for value in self._recent_release_times if now - value <= quad_click_window_sec
                    ]
                    self._recent_release_times.append(now)
                    print(
                        f"[WhisplayDaemon] foreground click count={len(self._recent_release_times)} "
                        f"window={quad_click_window_sec}s app={self.foreground_app_id}"
                    )
                    if len(self._recent_release_times) >= 4:
                        self._recent_release_times = []
                        self._request_exit(app, "quad_click_exit")
                        return
                else:
                    self._recent_release_times = []
                if is_internal_app:
                    self.internal_apps.handle_button_release(
                        self.foreground_app_id,
                        press_duration >= BUTTON_LONG_PRESS_SEC,
                    )
                    self.pisugar.poll_home_trigger()
                    if self.internal_apps.exit_requested:
                        self.internal_apps.clear_exit_requested()
                        self._release_focus(app, "list_back")
                        return
                    self._render_internal_app()
                return

            self._recent_release_times = []
            apps = self._app_list()
            if not apps:
                self._render_desktop()
                return
            if press_duration >= BUTTON_LONG_PRESS_SEC:
                selected = self._current_selected_app()
                if selected is not None:
                    self._launch_app(selected)
            else:
                self.selected_app_index = (self.selected_app_index + 1) % len(apps)
                self._render_desktop()

    def _render_loop(self):
        interval = 1.0 / RENDER_FPS
        while self.running:
            frame = None
            with self.state_lock:
                app_id = self.foreground_app_id
                app = self.apps.get(app_id) if app_id else None
                framebuffer = app.framebuffer_mmap if app else None
                if framebuffer is not None:
                    framebuffer.seek(0)
                    frame = framebuffer.read(FRAMEBUFFER_SIZE)
            if frame is not None and frame != self.last_frame:
                self.board.draw_image(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, frame)
                self.last_frame = frame
            time.sleep(interval)

    def _monitor_loop(self):
        while self.running:
            with self.state_lock:
                for app in self.apps.values():
                    if app.process is not None and app.process.poll() is not None:
                        rc = app.process.returncode
                        app.process = None
                        self._close_process_log(app)
                        if self.pending_launch_app_id == app.app_id:
                            print(
                                f"[WhisplayDaemon] App launch ended before foreground: "
                                f"{app.app_id} rc={rc}"
                            )
                            self.pending_launch_app_id = None
                            self.pending_launch_started_at = 0.0
                        if self.foreground_app_id == app.app_id:
                            self._release_focus(app, "process_exit")
                        else:
                            self._render_desktop()
                if self.pending_launch_app_id and not self.foreground_app_id:
                    self._render_desktop()
                if self.foreground_app_id and self.internal_apps.is_internal_app(self.foreground_app_id):
                    self.internal_apps.tick(self.foreground_app_id)
                    if self.internal_apps.consume_dirty():
                        self._render_internal_app()
                if (
                    self.pending_launch_app_id
                    and not self.foreground_app_id
                    and self.pending_launch_started_at > 0
                    and (time.time() - self.pending_launch_started_at) >= PENDING_LAUNCH_TIMEOUT_SEC
                ):
                    print(
                        f"[WhisplayDaemon] Pending launch timeout: {self.pending_launch_app_id} "
                        f"after {PENDING_LAUNCH_TIMEOUT_SEC}s"
                    )
                    self.pending_launch_app_id = None
                    self.pending_launch_started_at = 0.0
                    self._render_desktop()
                if self._button_press_started_at > 0 and time.time() - self._button_press_started_at >= BUTTON_LONG_PRESS_SEC:
                    if self.board.button_pressed():
                        if self.foreground_app_id:
                            app = self.apps.get(self.foreground_app_id)
                            if app and app.exit_gesture == EXIT_GESTURE_LONG_PRESS and not self._foreground_long_press_fired:
                                self._request_exit(app, "long_press_exit")
                            elif self.internal_apps.is_internal_app(self.foreground_app_id):
                                flash_on = int(time.time() * 5) % 2 == 0
                                self.board.set_rgb(0, 255, 0) if flash_on else self.board.set_rgb(0, 0, 0)
                        else:
                            flash_on = int(time.time() * 5) % 2 == 0
                            self.board.set_rgb(0, 255, 0) if flash_on else self.board.set_rgb(0, 0, 0)
                    else:
                        self._button_press_started_at = 0.0
                        self._foreground_long_press_fired = False
                        if not self.foreground_app_id or self.internal_apps.is_internal_app(self.foreground_app_id):
                            self.board.set_rgb(0, 0, 0)
                if self.exit_request is not None and time.time() >= self.exit_request["deadline"]:
                    app = self.apps.get(self.exit_request["app_id"])
                    if app and self.foreground_app_id == app.app_id:
                        self._release_focus(app, "exit_timeout")
                if self.pisugar.poll_home_trigger():
                    self._request_exit_from_pisugar()
                self._refresh_status_icons()
            time.sleep(0.1)

    def _register_app(self, payload: dict) -> dict:
        app_id = str(payload.get("app_id", "")).strip()
        if not app_id:
            raise RuntimeError("app_id is required")
        record = self.apps.get(app_id)
        if record is None:
            record = AppRecord(
                app_id=app_id,
                display_name=str(payload.get("display_name") or app_id),
            )
            self.apps[app_id] = record
        record.display_name = str(payload.get("display_name") or record.display_name or app_id)
        record.icon = str(payload.get("icon") or record.icon or "")
        if payload.get("launch_command") is not None:
            record.launch_command = str(payload.get("launch_command") or "")
        if payload.get("cwd") is not None:
            record.cwd = str(payload.get("cwd") or "")
        if payload.get("env") is not None and isinstance(payload.get("env"), dict):
            record.env = {str(key): str(value) for key, value in payload.get("env", {}).items()}
        if payload.get("exit_gesture") is not None:
            record.exit_gesture = self._normalize_exit_gesture(payload.get("exit_gesture"))
        if payload.get("priority") is not None:
            record.priority = self._normalize_priority(payload.get("priority"))
        if payload.get("use_daemon_default_log") is not None:
            record.use_daemon_default_log = bool(payload.get("use_daemon_default_log"))
        if payload.get("persist") is not None:
            record.persist = bool(payload.get("persist"))
        if payload.get("disable_esc_exit_key") is not None:
            record.disable_esc_exit_key = bool(payload.get("disable_esc_exit_key"))
        self._save_app(record)
        self._render_desktop()
        return {
            "app_id": record.app_id,
            "display_name": record.display_name,
            "icon": record.icon,
            "exit_gesture": record.exit_gesture,
            "priority": record.priority,
            "use_daemon_default_log": record.use_daemon_default_log,
            "disable_esc_exit_key": record.disable_esc_exit_key,
            "running": record.is_running(),
        }

    def _list_apps_payload(self) -> list[dict]:
        selected = self._current_selected_app()
        return [
            {
                "app_id": app.app_id,
                "display_name": app.display_name,
                "icon": app.icon,
                "exit_gesture": app.exit_gesture,
                "priority": app.priority,
                "use_daemon_default_log": app.use_daemon_default_log,
                "disable_esc_exit_key": app.disable_esc_exit_key,
                "running": app.is_running(),
                "selected": selected is not None and selected.app_id == app.app_id,
                "foreground": self.foreground_app_id == app.app_id,
            }
            for app in self._app_list()
        ]

    def handle_command(self, request: dict, conn) -> tuple[dict, bool]:
        version = request.get("version", 1)
        if version != 1:
            return {"ok": False, "error": f"unsupported version: {version}"}, False

        cmd = str(request.get("cmd", "")).strip()
        payload = request.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}

        with self.state_lock:
            if cmd == "health.ping":
                return {
                    "ok": True,
                    "payload": {
                        "service": "whisplay-daemon",
                        "screen": {
                            "width": SCREEN_WIDTH,
                            "height": SCREEN_HEIGHT,
                            "stride": FRAMEBUFFER_STRIDE,
                            "pixel_format": PIXEL_FORMAT,
                        },
                        "foreground_app_id": self.foreground_app_id,
                    },
                }, False

            if cmd == "app.register":
                return {"ok": True, "payload": self._register_app(payload)}, False

            if cmd == "app.list":
                return {"ok": True, "payload": {"apps": self._list_apps_payload()}}, False

            if cmd == "app.launch":
                app_id = str(payload.get("app_id", "")).strip()
                app = self.apps.get(app_id)
                if app is None:
                    raise RuntimeError(f"unknown app: {app_id}")
                if self.foreground_app_id and self.foreground_app_id != app_id:
                    raise RuntimeError("cannot launch while another app is foreground")
                self._launch_app(app)
                return {"ok": True, "payload": {"app_id": app_id, "pending": True}}, False

            if cmd == "app.focus.acquire":
                app_id = str(payload.get("app_id", "")).strip()
                app = self.apps.get(app_id)
                if app is None:
                    raise RuntimeError(f"unknown app: {app_id}")
                if self.pending_launch_app_id and self.pending_launch_app_id != app_id and self.foreground_app_id != app_id:
                    raise RuntimeError("another app is pending foreground")
                self._grant_focus(app)
                return {
                    "ok": True,
                    "payload": {
                        "app_id": app.app_id,
                        "session_token": app.session_token,
                    },
                }, False

            if cmd == "framebuffer.acquire":
                app_id = str(payload.get("app_id", "")).strip()
                session_token = str(payload.get("session_token", "")).strip()
                app = self.apps.get(app_id)
                if app is None or app.session_token != session_token or self.foreground_app_id != app_id:
                    raise RuntimeError("invalid foreground session")
                return {
                    "ok": True,
                    "payload": {
                        "app_id": app.app_id,
                        "session_token": app.session_token,
                        "width": SCREEN_WIDTH,
                        "height": SCREEN_HEIGHT,
                        "stride": FRAMEBUFFER_STRIDE,
                        "pixel_format": PIXEL_FORMAT,
                        "buffer_handle": app.framebuffer_path,
                    },
                }, False

            if cmd == "app.focus.release":
                app_id = str(payload.get("app_id", "")).strip()
                session_token = str(payload.get("session_token", "")).strip()
                app = self.apps.get(app_id)
                if app is None or app.session_token != session_token:
                    raise RuntimeError("invalid session")
                if self.foreground_app_id == app_id:
                    self._release_focus(app, "app_release")
                return {"ok": True}, False

            if cmd == "app.exit.request":
                app_id = str(payload.get("app_id", "")).strip()
                app = self.apps.get(app_id)
                if app is None:
                    raise RuntimeError(f"unknown app: {app_id}")
                self._request_exit(app, "remote_request")
                return {"ok": True}, False

            if cmd == "backlight.set":
                self.board.set_backlight(int(payload.get("brightness", 0)))
                return {"ok": True}, False

            if cmd == "led.set":
                self.board.set_rgb(
                    int(payload.get("r", 0)),
                    int(payload.get("g", 0)),
                    int(payload.get("b", 0)),
                )
                return {"ok": True}, False

            if cmd == "led.fade":
                self.board.set_rgb_fade(
                    int(payload.get("r", 0)),
                    int(payload.get("g", 0)),
                    int(payload.get("b", 0)),
                    int(payload.get("duration_ms", 100)),
                )
                return {"ok": True}, False

            if cmd == "button.get_state":
                return {"ok": True, "payload": {"pressed": self.board.button_pressed()}}, False

            if cmd == "events.subscribe":
                app_id = str(payload.get("app_id", "")).strip() or None
                self.event_broadcaster.add(conn, app_id)
                return {"ok": True, "payload": {"subscribed": True, "app_id": app_id}}, True

        return {"ok": False, "error": f"unknown command: {cmd}"}, False

    def handle_client(self, conn):
        keep_open = False
        try:
            reader = conn.makefile("r")
            while self.running:
                line = reader.readline()
                if not line:
                    break
                if not line.strip():
                    continue
                try:
                    request = json.loads(line)
                except json.JSONDecodeError:
                    conn.sendall(b'{"ok": false, "error": "invalid json"}\n')
                    continue
                try:
                    response, keep_open = self.handle_command(request, conn)
                except Exception as exc:
                    response, keep_open = {"ok": False, "error": str(exc)}, False
                conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
                if keep_open:
                    while self.running:
                        time.sleep(1)
                    break
        except Exception as exc:
            print(f"[WhisplayDaemon] Client error: {exc}")
        finally:
            if keep_open:
                self.event_broadcaster.remove(conn)
            try:
                conn.close()
            except Exception:
                pass

    def start(self):
        os.makedirs(self.socket_dir, exist_ok=True)
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        self.server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_socket.bind(self.socket_path)
        os.chmod(self.socket_path, 0o666)
        self.server_socket.listen(8)
        self.board.set_rgb(0, 0, 0)
        self.board.set_backlight(100)
        self._refresh_status_icons(force=True)
        self._render_desktop()
        self._init_pisugar_integration()
        self.internal_apps.start()
        self.keyboard_reader.start(self._handle_keyboard_action)
        self._render_thread.start()
        self._monitor_thread.start()
        print(f"[WhisplayDaemon] Listening on {self.socket_path}")
        while self.running:
            try:
                conn, _ = self.server_socket.accept()
            except OSError:
                break
            thread = threading.Thread(target=self.handle_client, args=(conn,), daemon=True)
            thread.start()

    def stop(self):
        self.running = False
        try:
            if self.server_socket is not None:
                self.server_socket.close()
        except Exception:
            pass
        self.event_broadcaster.broadcast("daemon_stopping")
        self.internal_apps.stop()
        self.keyboard_reader.stop()
        with self.state_lock:
            for app in self.apps.values():
                self._teardown_framebuffer(app)
                self._close_process_log(app)
            self.foreground_app_id = None
        self.board.cleanup()
        try:
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
        except Exception:
            pass


daemon_instance = None


def cleanup_and_exit(_signum=None, _frame=None):
    global daemon_instance
    print("[WhisplayDaemon] Exiting...")
    if daemon_instance is not None:
        daemon_instance.stop()
    sys.exit(0)


if __name__ == "__main__":
    args = parse_args()
    runtime_config = resolve_runtime_config(args)
    daemon_instance = WhisplayDaemon(
        runtime_config["socket_path"],
        runtime_config["apps_dir"],
        runtime_config["settings_path"],
    )
    signal.signal(signal.SIGTERM, cleanup_and_exit)
    signal.signal(signal.SIGINT, cleanup_and_exit)
    signal.signal(signal.SIGQUIT, cleanup_and_exit)
    try:
        daemon_instance.start()
    except KeyboardInterrupt:
        cleanup_and_exit()
