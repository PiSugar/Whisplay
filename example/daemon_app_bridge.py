import json
import mmap
import os
import socket
import threading
import time
import sys

sys.path.append(os.path.abspath("../Driver"))
from WhisPlay import WhisPlayBoard

DEFAULT_DAEMON_SOCKET_PATH = "/tmp/whisplay-daemon.sock"


class DaemonBoardProxy:
    LCD_WIDTH = 240
    LCD_HEIGHT = 280
    CornerHeight = 20

    def __init__(self, socket_path: str, app_id: str, display_name: str, icon: str):
        self.socket_path = socket_path
        self._app_id = app_id
        self._display_name = display_name
        self._icon = icon
        self._session_token = None
        self._fb_file = None
        self._mmap = None
        self._fb_stride = self.LCD_WIDTH * 2
        self._button_down = False
        self._running = False
        self._subscriber = None
        self.button_press_callback = None
        self.button_release_callback = None

    def _send_request(self, cmd: str, payload: dict | None = None) -> dict:
        body = {"version": 1, "cmd": cmd, "payload": payload or {}}
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(self.socket_path)
            client.sendall((json.dumps(body) + "\n").encode("utf-8"))
            line = client.makefile("r").readline().strip()
            response = json.loads(line) if line else {"ok": False, "error": "empty response"}
            if not response.get("ok"):
                raise RuntimeError(response.get("error", "daemon request failed"))
            return response

    def ping(self) -> bool:
        try:
            self._send_request("health.ping")
            return True
        except Exception:
            return False

    def register(self):
        self._send_request(
            "app.register",
            {
                "app_id": self._app_id,
                "display_name": self._display_name,
                "icon": self._icon,
            },
        )

    def acquire_foreground(self, timeout_sec: float = 5.0):
        deadline = time.time() + timeout_sec
        last_error = None
        while time.time() < deadline:
            try:
                response = self._send_request("app.focus.acquire", {"app_id": self._app_id})
                self._session_token = response["payload"]["session_token"]
                fb = self._send_request(
                    "framebuffer.acquire",
                    {"app_id": self._app_id, "session_token": self._session_token},
                )["payload"]
                self._attach_framebuffer(fb["buffer_handle"], int(fb["stride"]))
                return
            except Exception as exc:
                last_error = exc
                time.sleep(0.2)
        raise RuntimeError(f"failed to acquire foreground: {last_error}")

    def _attach_framebuffer(self, buffer_handle: str, stride: int):
        self._detach_framebuffer()
        self._fb_stride = stride
        self._fb_file = open(buffer_handle, "r+b")
        self._mmap = mmap.mmap(self._fb_file.fileno(), 0)

    def _detach_framebuffer(self):
        if self._mmap is not None:
            try:
                self._mmap.close()
            except Exception:
                pass
            self._mmap = None
        if self._fb_file is not None:
            try:
                self._fb_file.close()
            except Exception:
                pass
            self._fb_file = None

    def release_focus(self):
        if self._session_token:
            try:
                self._send_request(
                    "app.focus.release",
                    {"app_id": self._app_id, "session_token": self._session_token},
                )
            except Exception:
                pass
        self._session_token = None
        self._detach_framebuffer()

    def start_event_listener(self):
        if self._subscriber is not None:
            return
        self._running = True
        self._subscriber = threading.Thread(target=self._event_loop, daemon=True)
        self._subscriber.start()

    def _event_loop(self):
        while self._running:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(self.socket_path)
                    req = {"version": 1, "cmd": "events.subscribe", "payload": {"app_id": self._app_id}}
                    client.sendall((json.dumps(req) + "\n").encode("utf-8"))
                    reader = client.makefile("r")
                    _ack = reader.readline()
                    for line in reader:
                        if not self._running:
                            return
                        event = json.loads(line.strip()) if line.strip() else {}
                        name = event.get("event")
                        if name == "button_pressed":
                            self._button_down = True
                            if self.button_press_callback:
                                self.button_press_callback()
                        elif name == "button_released":
                            self._button_down = False
                            if self.button_release_callback:
                                self.button_release_callback()
                        elif name in ("app_exit_requested", "app_focus_revoked"):
                            self.release_focus()
                            os._exit(0)
            except Exception:
                time.sleep(0.5)

    def set_backlight(self, brightness):
        self._send_request("backlight.set", {"brightness": int(brightness)})

    def set_rgb(self, r, g, b):
        self._send_request("led.set", {"r": int(r), "g": int(g), "b": int(b)})

    def set_rgb_fade(self, r_target, g_target, b_target, duration_ms=100):
        self._send_request(
            "led.fade",
            {"r": int(r_target), "g": int(g_target), "b": int(b_target), "duration_ms": int(duration_ms)},
        )

    def draw_image(self, x, y, width, height, pixel_data):
        if self._mmap is None:
            return
        data = bytes(pixel_data if not isinstance(pixel_data, bytes) else pixel_data)
        row_bytes = width * 2
        for row in range(height):
            src = row * row_bytes
            dst = ((y + row) * self._fb_stride) + (x * 2)
            self._mmap[dst:dst + row_bytes] = data[src:src + row_bytes]

    def fill_screen(self, color):
        if self._mmap is None:
            return
        high = (int(color) >> 8) & 0xFF
        low = int(color) & 0xFF
        self._mmap.seek(0)
        self._mmap.write(bytes([high, low]) * (self.LCD_WIDTH * self.LCD_HEIGHT))
        self._mmap.seek(0)

    def button_pressed(self):
        return self._button_down

    def on_button_press(self, callback):
        self.button_press_callback = callback

    def on_button_release(self, callback):
        self.button_release_callback = callback

    def cleanup(self):
        self._running = False
        self.release_focus()


def create_whisplay_hardware(app_id: str, display_name: str, icon: str):
    socket_path = DEFAULT_DAEMON_SOCKET_PATH
    proxy = DaemonBoardProxy(socket_path, app_id, display_name, icon)
    if proxy.ping():
        proxy.register()
        proxy.start_event_listener()
        proxy.acquire_foreground()
        return proxy
    return WhisPlayBoard()

