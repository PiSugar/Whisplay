import argparse
import json
import os

from PIL import Image


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
EXIT_GESTURE_QUAD_CLICK = "quad_click"
EXIT_GESTURE_LONG_PRESS = "long_press"
VALID_EXIT_GESTURES = {EXIT_GESTURE_QUAD_CLICK, EXIT_GESTURE_LONG_PRESS}
DEFAULT_DAEMON_HOME = os.path.expanduser("~/.whisplay-daemon")
DEFAULT_SETTINGS_PATH = os.path.join(DEFAULT_DAEMON_HOME, "settings.json")
DEFAULT_APPS_DIR = os.path.join(DEFAULT_DAEMON_HOME, "app")
DEFAULT_SOCKET_PATH = "/tmp/whisplay-daemon.sock"
DEFAULT_APP_LOG_PATH = os.path.join(DEFAULT_DAEMON_HOME, "daemon-app.log")
STATUS_POLL_INTERVAL_SEC = 5.0
DEFAULT_PISUGAR_HOME_BUTTON = "single"
VALID_PISUGAR_HOME_BUTTONS = {"single", "double", "long", "none"}


def load_json_file(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        print(f"[WhisplayDaemon] Failed to load JSON from {path}: {exc}")
        return {}


def write_json_file(path: str, payload: dict):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=True, indent=2)
        fp.write("\n")


def resolve_runtime_config(args):
    settings_path = os.path.abspath(
        os.path.expanduser(
            args.settings_path
            or os.getenv("WHISPLAY_DAEMON_SETTINGS_PATH")
            or DEFAULT_SETTINGS_PATH
        )
    )
    settings = load_json_file(settings_path)
    apps_dir = os.path.abspath(
        os.path.expanduser(
            args.apps_dir
            or os.getenv("WHISPLAY_DAEMON_APPS_DIR")
            or settings.get("apps_dir")
            or DEFAULT_APPS_DIR
        )
    )
    stored_settings = dict(settings)
    changed = False
    if "socket_path" in stored_settings:
        del stored_settings["socket_path"]
        changed = True
    if stored_settings.get("apps_dir") != apps_dir:
        stored_settings["apps_dir"] = apps_dir
        changed = True
    pisugar_home_button = str(
        stored_settings.get("pisugar_home_button", DEFAULT_PISUGAR_HOME_BUTTON)
    ).strip().lower()
    if pisugar_home_button not in VALID_PISUGAR_HOME_BUTTONS:
        pisugar_home_button = DEFAULT_PISUGAR_HOME_BUTTON
    if stored_settings.get("pisugar_home_button") != pisugar_home_button:
        stored_settings["pisugar_home_button"] = pisugar_home_button
        changed = True
    if not os.path.exists(settings_path) or changed:
        write_json_file(settings_path, stored_settings)
    return {
        "settings_path": settings_path,
        "socket_path": DEFAULT_SOCKET_PATH,
        "apps_dir": apps_dir,
        "pisugar_home_button": pisugar_home_button,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Whisplay hardware daemon")
    parser.add_argument(
        "--settings-path",
        default=None,
        help="Path to daemon settings.json",
    )
    parser.add_argument(
        "--apps-dir",
        default=None,
        help="Directory containing one JSON file per persisted app entry",
    )
    return parser.parse_args()


def image_to_rgb565_bytes(image: Image.Image) -> bytes:
    try:
        import numpy as np
        arr = np.asarray(image.convert("RGB"), dtype=np.uint16)
        r = arr[:, :, 0]
        g = arr[:, :, 1]
        b = arr[:, :, 2]
        rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        return rgb565.astype(">u2").tobytes()
    except ImportError:
        pass
    image = image.convert("RGB")
    pixels = image.tobytes()
    length = len(pixels) // 3
    output = bytearray(length * 2)
    for i in range(length):
        off = i * 3
        r, g, b = pixels[off], pixels[off + 1], pixels[off + 2]
        rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        output[i * 2] = (rgb565 >> 8) & 0xFF
        output[i * 2 + 1] = rgb565 & 0xFF
    return bytes(output)


def calculate_luminance(color: tuple[int, int, int]) -> float:
    r, g, b = color
    return 0.299 * r + 0.587 * g + 0.114 * b
