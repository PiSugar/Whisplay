import argparse
import json
import mmap
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

from WhisPlay import WhisPlayBoard


SCREEN_WIDTH = 240
SCREEN_HEIGHT = 280
PIXEL_FORMAT = "RGB565"
BYTES_PER_PIXEL = 2
FRAMEBUFFER_STRIDE = SCREEN_WIDTH * BYTES_PER_PIXEL
FRAMEBUFFER_SIZE = FRAMEBUFFER_STRIDE * SCREEN_HEIGHT
BUTTON_LONG_PRESS_SEC = 0.7
QUAD_CLICK_WINDOW_SEC = 3.0
EXIT_REQUEST_TIMEOUT_SEC = 1.5
RENDER_FPS = 20
PENDING_LAUNCH_TIMEOUT_SEC = 8.0


def parse_args():
    parser = argparse.ArgumentParser(description="Whisplay hardware daemon")
    parser.add_argument(
        "--socket-path",
        default=os.getenv("WHISPLAY_DAEMON_SOCKET_PATH", "/tmp/whisplay-daemon.sock"),
        help="Unix socket path for the local IPC server",
    )
    parser.add_argument(
        "--apps-config",
        default=os.getenv(
            "WHISPLAY_DAEMON_APPS_PATH",
            os.path.join(os.path.dirname(__file__), "whisplay_apps.json"),
        ),
        help="Path to the persisted app registry config",
    )
    return parser.parse_args()


def image_to_rgb565_bytes(image: Image.Image) -> bytes:
    image = image.convert("RGB")
    output = bytearray()
    for y in range(image.height):
        for x in range(image.width):
            r, g, b = image.getpixel((x, y))
            rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            output.append((rgb565 >> 8) & 0xFF)
            output.append(rgb565 & 0xFF)
    return bytes(output)


@dataclass
class AppRecord:
    app_id: str
    display_name: str
    icon: str = ""
    launch_command: str = ""
    cwd: str = ""
    env: dict = field(default_factory=dict)
    persist: bool = False
    process: subprocess.Popen | None = None
    subscribers: set = field(default_factory=set)
    session_token: str | None = None
    framebuffer_path: str | None = None
    framebuffer_file = None
    framebuffer_mmap: mmap.mmap | None = None

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None


class EventBroadcaster:
    def __init__(self):
        self._global_subscribers = set()
        self._app_subscribers: dict[str, set] = {}
        self._lock = threading.Lock()

    def add(self, conn, app_id: str | None):
        with self._lock:
            if app_id:
                self._app_subscribers.setdefault(app_id, set()).add(conn)
            else:
                self._global_subscribers.add(conn)

    def remove(self, conn):
        with self._lock:
            self._global_subscribers.discard(conn)
            for subscribers in self._app_subscribers.values():
                subscribers.discard(conn)

    def has_app_subscribers(self, app_id: str) -> bool:
        with self._lock:
            return bool(self._app_subscribers.get(app_id))

    def broadcast(
        self,
        event: str,
        payload: dict | None = None,
        app_id: str | None = None,
    ):
        message = {"event": event}
        if payload:
            message["payload"] = payload
        wire = (json.dumps(message) + "\n").encode("utf-8")
        with self._lock:
            targets = list(self._global_subscribers)
            if app_id:
                targets.extend(list(self._app_subscribers.get(app_id, set())))
        for conn in targets:
            try:
                conn.sendall(wire)
            except Exception:
                self.remove(conn)
                try:
                    conn.close()
                except Exception:
                    pass


class DesktopRenderer:
    def __init__(self, board: WhisPlayBoard):
        self.board = board
        self.title_font = self._load_font(20)
        self.body_font = self._load_font(16)
        self.small_font = self._load_font(14)
        self.zoom_sizes = {
            -2: self._load_font(12),
            -1: self._load_font(14),
            0: self._load_font(18),
            1: self._load_font(14),
            2: self._load_font(12),
        }

    def _load_font(self, size: int):
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        for path in candidates:
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
        return ImageFont.load_default()

    def render(
        self,
        apps: list[AppRecord],
        selected_index: int,
        pending_app_id: str | None = None,
        running_app_id: str | None = None,
    ):
        image = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), (7, 11, 18))
        draw = ImageDraw.Draw(image)
        top_margin = 24
        left = 12
        draw.text((left, top_margin), "Whisplay Desktop", fill=(255, 255, 255), font=self.title_font)
        draw.text((left, top_margin + 28), "click: next  hold: open", fill=(160, 190, 210), font=self.small_font)

        if not apps:
            draw.text((left, top_margin + 48), "No apps registered", fill=(255, 200, 120), font=self.body_font)
            frame = image_to_rgb565_bytes(image)
            self.board.draw_image(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, list(frame))
            return

        selected = apps[selected_index % len(apps)]
        status = "running" if selected.is_running() else "stopped"
        # draw.text((left, top_margin + 48), "Selected App", fill=(80, 160, 255), font=self.small_font)
        selected_color = (120, 255, 140) if pending_app_id == selected.app_id else (80, 160, 255)
        draw.text((left, top_margin + 48), f"{selected.display_name}", fill=selected_color, font=self.body_font)
        draw.text((left, top_margin + 72), f"State: {status}", fill=(170, 220, 170), font=self.small_font)

        y = top_margin + 116
        total = len(apps)
        display_items = []
        for offset in range(-2, 3):
            idx = (selected_index + offset) % total
            display_items.append((apps[idx], idx, offset))
        arrow_x = left
        text_x = left + 18
        for app, idx, offset in display_items:
            font = self.zoom_sizes.get(offset, self.small_font)
            if pending_app_id == app.app_id:
                item_color = (120, 255, 140)
            elif offset == 0:
                item_color = (255, 255, 255)
            elif abs(offset) == 1:
                item_color = (160, 170, 190)
            else:
                item_color = (100, 110, 130)
            if idx == selected_index:
                draw.text((arrow_x, y), ">", fill=item_color, font=font)
            draw.text((text_x, y), app.display_name, fill=item_color, font=font)
            y += 22

        modal_app_id = pending_app_id or running_app_id
        if modal_app_id:
            modal_w, modal_h = 188, 64
            modal_x = (SCREEN_WIDTH - modal_w) // 2
            modal_y = (SCREEN_HEIGHT - modal_h) // 2
            draw.rounded_rectangle(
                (modal_x, modal_y, modal_x + modal_w, modal_y + modal_h),
                radius=10,
                fill=(16, 28, 40),
                outline=(90, 150, 200),
                width=2,
            )
            modal_title = "Opening app..." if pending_app_id else "App running..."
            draw.text((modal_x + 14, modal_y + 12), modal_title, fill=(255, 255, 255), font=self.body_font)
            draw.text((modal_x + 14, modal_y + 36), modal_app_id, fill=(120, 255, 140), font=self.small_font)
            spinner_frames = ["|", "/", "-", "\\"]
            spinner = spinner_frames[int(time.time() * 8) % len(spinner_frames)]
            draw.text((modal_x + modal_w - 22, modal_y + 12), spinner, fill=(120, 220, 255), font=self.body_font)

        frame = image_to_rgb565_bytes(image)
        self.board.draw_image(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, list(frame))


class WhisplayDaemon:
    def __init__(self, socket_path: str, apps_config_path: str):
        self.socket_path = socket_path
        self.socket_dir = os.path.dirname(socket_path) or "."
        self.apps_config_path = apps_config_path
        self.server_socket = None
        self.running = True
        self.state_lock = threading.RLock()
        self.event_broadcaster = EventBroadcaster()
        self.board = WhisPlayBoard()
        self.desktop = DesktopRenderer(self.board)
        self.apps: dict[str, AppRecord] = {}
        self.selected_app_index = 0
        self.foreground_app_id: str | None = None
        self.pending_launch_app_id: str | None = None
        self.pending_launch_started_at = 0.0
        self.exit_request = None
        self.last_frame = None
        self._button_press_started_at = 0.0
        self._recent_release_times: list[float] = []
        self._render_thread = threading.Thread(target=self._render_loop, daemon=True)
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._load_apps()
        self._ensure_builtin_apps()
        self.board.on_button_press(self._on_button_pressed)
        self.board.on_button_release(self._on_button_released)

    def _ensure_builtin_apps(self):
        example_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "example"))
        run_test_sh = os.path.join(example_dir, "run_test.sh")
        if not os.path.exists(run_test_sh):
            return
        self.apps.setdefault(
            "whisplay-run-test",
            AppRecord(
                app_id="whisplay-run-test",
                display_name="Run Test",
                icon="T",
                launch_command=f"bash {run_test_sh}",
                cwd=example_dir,
                env={"WHISPLAY_APP_ID": "whisplay-run-test"},
                persist=False,
            ),
        )
        self.apps.setdefault(
            "whisplay-test2",
            AppRecord(
                app_id="whisplay-test2",
                display_name="Test2",
                icon="2",
                launch_command="python3 test2.py",
                cwd=example_dir,
                env={"WHISPLAY_APP_ID": "whisplay-test2"},
                persist=False,
            ),
        )
        self.apps.setdefault(
            "whisplay-record-play",
            AppRecord(
                app_id="whisplay-record-play",
                display_name="Record Demo",
                icon="R",
                launch_command="python3 record_play_demo.py",
                cwd=example_dir,
                env={"WHISPLAY_APP_ID": "whisplay-record-play"},
                persist=False,
            ),
        )
        self.apps.setdefault(
            "whisplay-play-mp4",
            AppRecord(
                app_id="whisplay-play-mp4",
                display_name="Play MP4",
                icon="V",
                launch_command="python3 play_mp4.py",
                cwd=example_dir,
                env={"WHISPLAY_APP_ID": "whisplay-play-mp4"},
                persist=False,
            ),
        )

    def _load_apps(self):
        if not os.path.exists(self.apps_config_path):
            return
        try:
            with open(self.apps_config_path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
        except Exception as exc:
            print(f"[WhisplayDaemon] Failed to load app config: {exc}")
            return
        for item in data.get("apps", []):
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
                persist=bool(item.get("persist", False)),
            )

    def _save_apps(self):
        os.makedirs(os.path.dirname(self.apps_config_path) or ".", exist_ok=True)
        payload = {
            "apps": [
                {
                    "app_id": app.app_id,
                    "display_name": app.display_name,
                    "icon": app.icon,
                    "launch_command": app.launch_command,
                    "cwd": app.cwd,
                    "env": app.env,
                    "persist": app.persist,
                }
                for app in self.apps.values()
                if app.persist
            ]
        }
        with open(self.apps_config_path, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=True, indent=2)

    def _app_list(self) -> list[AppRecord]:
        return sorted(self.apps.values(), key=lambda app: app.display_name.lower())

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
        )

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
        self.pending_launch_app_id = None
        self.pending_launch_started_at = 0.0
        self._render_desktop()
        self.event_broadcaster.broadcast("desktop_entered", {"reason": reason})

    def _request_exit(self, app: AppRecord, reason: str):
        print(f"[WhisplayDaemon] Exit requested for {app.app_id}: {reason}")
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

    def _launch_app(self, app: AppRecord):
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
        cwd = app.cwd or None
        try:
            app.process = subprocess.Popen(
                app.launch_command,
                shell=True,
                cwd=cwd,
                env=env,
                start_new_session=True,
            )
        except Exception:
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
            if not self.foreground_app_id:
                self.board.set_rgb(0, 0, 255)
            if self.foreground_app_id:
                self.event_broadcaster.broadcast(
                    "button_pressed",
                    {"app_id": self.foreground_app_id},
                    app_id=self.foreground_app_id,
                )

    def _on_button_released(self):
        with self.state_lock:
            if not self.foreground_app_id:
                self.board.set_rgb(0, 0, 0)
            now = time.time()
            press_duration = now - self._button_press_started_at if self._button_press_started_at else 0
            self._button_press_started_at = 0.0

            if self.foreground_app_id:
                app = self.apps.get(self.foreground_app_id)
                self.event_broadcaster.broadcast(
                    "button_released",
                    {"app_id": self.foreground_app_id},
                    app_id=self.foreground_app_id,
                )
                self._recent_release_times = [
                    value for value in self._recent_release_times if now - value <= QUAD_CLICK_WINDOW_SEC
                ]
                self._recent_release_times.append(now)
                print(
                    f"[WhisplayDaemon] foreground click count={len(self._recent_release_times)} "
                    f"window={QUAD_CLICK_WINDOW_SEC}s app={self.foreground_app_id}"
                )
                if app and len(self._recent_release_times) >= 4:
                    self._recent_release_times = []
                    self._request_exit(app, "quad_click_exit")
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
            with self.state_lock:
                app_id = self.foreground_app_id
                app = self.apps.get(app_id) if app_id else None
                framebuffer = app.framebuffer_mmap if app else None
                if framebuffer is not None:
                    framebuffer.seek(0)
                    frame = framebuffer.read(FRAMEBUFFER_SIZE)
                    if frame != self.last_frame:
                        self.board.draw_image(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, list(frame))
                        self.last_frame = frame
            time.sleep(interval)

    def _monitor_loop(self):
        while self.running:
            with self.state_lock:
                for app in self.apps.values():
                    if app.process is not None and app.process.poll() is not None:
                        rc = app.process.returncode
                        app.process = None
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
                if (
                    self.pending_launch_app_id
                    and not self.foreground_app_id
                ):
                    self._render_desktop()
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
                if (
                    self._button_press_started_at > 0
                    and not self.foreground_app_id
                    and time.time() - self._button_press_started_at >= BUTTON_LONG_PRESS_SEC
                ):
                    if self.board.button_pressed():
                        flash_on = int(time.time() * 5) % 2 == 0
                        self.board.set_rgb(0, 255, 0) if flash_on else self.board.set_rgb(0, 0, 0)
                    else:
                        self._button_press_started_at = 0.0
                        self.board.set_rgb(0, 0, 0)
                if self.exit_request is not None and time.time() >= self.exit_request["deadline"]:
                    app = self.apps.get(self.exit_request["app_id"])
                    if app and self.foreground_app_id == app.app_id:
                        self._release_focus(app, "exit_timeout")
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
        if payload.get("persist") is not None:
            record.persist = bool(payload.get("persist"))
            self._save_apps()
        self._render_desktop()
        return {
            "app_id": record.app_id,
            "display_name": record.display_name,
            "icon": record.icon,
            "running": record.is_running(),
        }

    def _list_apps_payload(self) -> list[dict]:
        selected = self._current_selected_app()
        return [
            {
                "app_id": app.app_id,
                "display_name": app.display_name,
                "icon": app.icon,
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
        self._render_desktop()
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
        with self.state_lock:
            for app in self.apps.values():
                self._teardown_framebuffer(app)
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
    daemon_instance = WhisplayDaemon(args.socket_path, args.apps_config)
    signal.signal(signal.SIGTERM, cleanup_and_exit)
    signal.signal(signal.SIGINT, cleanup_and_exit)
    signal.signal(signal.SIGQUIT, cleanup_and_exit)
    try:
        daemon_instance.start()
    except KeyboardInterrupt:
        cleanup_and_exit()
