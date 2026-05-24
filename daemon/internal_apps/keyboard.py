import os
import select
import struct
import threading
import time


EV_KEY = 0x01
KEY_PRESS = 1
KEY_RELEASE = 0
SHIFT_CODES = {42, 54}
KEY_ENTER = 28
KEY_ESC = 1
KEY_BACKSPACE = 14
KEY_SPACE = 57
KEY_UP = 103
KEY_DOWN = 108
INPUT_EVENT_FORMAT = "llHHI"

KEYCODE_TO_CHAR = {
    2: ("1", "!"),
    3: ("2", "@"),
    4: ("3", "#"),
    5: ("4", "$"),
    6: ("5", "%"),
    7: ("6", "^"),
    8: ("7", "&"),
    9: ("8", "*"),
    10: ("9", "("),
    11: ("0", ")"),
    12: ("-", "_"),
    13: ("=", "+"),
    16: ("q", "Q"),
    17: ("w", "W"),
    18: ("e", "E"),
    19: ("r", "R"),
    20: ("t", "T"),
    21: ("y", "Y"),
    22: ("u", "U"),
    23: ("i", "I"),
    24: ("o", "O"),
    25: ("p", "P"),
    26: ("[", "{"),
    27: ("]", "}"),
    30: ("a", "A"),
    31: ("s", "S"),
    32: ("d", "D"),
    33: ("f", "F"),
    34: ("g", "G"),
    35: ("h", "H"),
    36: ("j", "J"),
    37: ("k", "K"),
    38: ("l", "L"),
    39: (";", ":"),
    40: ("'", '"'),
    41: ("`", "~"),
    43: ("\\", "|"),
    44: ("z", "Z"),
    45: ("x", "X"),
    46: ("c", "C"),
    47: ("v", "V"),
    48: ("b", "B"),
    49: ("n", "N"),
    50: ("m", "M"),
    51: (",", "<"),
    52: (".", ">"),
    53: ("/", "?"),
}


class ExternalKeyboardReader:
    def __init__(self):
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._callback = None
        self._active = False
        self._shift_down = False

    def start(self, callback):
        self.stop()
        self._callback = callback
        self._stop_event.clear()
        self._shift_down = False
        self._active = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._active = False
        self._stop_event.set()
        self._thread = None
        self._callback = None
        self._shift_down = False

    def _candidate_paths(self) -> list[str]:
        paths = []
        by_id_dir = "/dev/input/by-id"
        try:
            for entry in sorted(os.listdir(by_id_dir)):
                if not entry.endswith("-kbd"):
                    continue
                full_path = os.path.join(by_id_dir, entry)
                try:
                    paths.append(os.path.realpath(full_path))
                except OSError:
                    continue
        except FileNotFoundError:
            pass
        if paths:
            return paths
        try:
            return sorted(
                os.path.join("/dev/input", entry)
                for entry in os.listdir("/dev/input")
                if entry.startswith("event")
            )
        except FileNotFoundError:
            return []

    def _loop(self):
        event_size = struct.calcsize(INPUT_EVENT_FORMAT)
        rescan_interval = 2.0
        open_fds: dict[int, str] = {}
        open_paths: set[str] = set()
        last_scan = 0.0

        def _close_all():
            for fd in list(open_fds):
                try:
                    os.close(fd)
                except OSError:
                    pass
            open_fds.clear()
            open_paths.clear()

        try:
            while not self._stop_event.is_set():
                now = time.monotonic()
                if now - last_scan >= rescan_interval:
                    last_scan = now
                    current_paths = set(self._candidate_paths())
                    stale = [fd for fd, path in open_fds.items() if path not in current_paths]
                    for fd in stale:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                        open_paths.discard(open_fds.pop(fd))
                    for path in current_paths - open_paths:
                        try:
                            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                            open_fds[fd] = path
                            open_paths.add(path)
                        except OSError:
                            continue

                if not open_fds:
                    time.sleep(1.0)
                    continue

                try:
                    ready, _, _ = select.select(list(open_fds), [], [], 0.02)
                except (ValueError, OSError):
                    _close_all()
                    continue

                for fd in ready:
                    try:
                        data = os.read(fd, event_size * 32)
                    except OSError:
                        open_paths.discard(open_fds.pop(fd, None))
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                        continue
                    offset = 0
                    while offset + event_size <= len(data):
                        _, _, event_type, code, value = struct.unpack(
                            INPUT_EVENT_FORMAT, data[offset : offset + event_size]
                        )
                        self._handle_event(event_type, code, value)
                        offset += event_size
        finally:
            _close_all()

    def _handle_event(self, event_type: int, code: int, value: int):
        if event_type != EV_KEY:
            return
        if code in SHIFT_CODES:
            if value == KEY_PRESS:
                self._shift_down = True
            elif value == KEY_RELEASE:
                self._shift_down = False
            return
        if value not in {KEY_PRESS, 2} or not self._callback or not self._active:
            return
        if code == KEY_UP:
            self._callback("up")
            return
        if code == KEY_DOWN:
            self._callback("down")
            return
        if code == KEY_ENTER:
            self._callback("submit")
            return
        if code == KEY_ESC:
            self._callback("cancel")
            return
        if code == KEY_BACKSPACE:
            self._callback("backspace")
            return
        if code == KEY_SPACE:
            self._callback(("char", " "))
            return
        chars = KEYCODE_TO_CHAR.get(code)
        if not chars:
            return
        self._callback(("char", chars[1] if self._shift_down else chars[0]))

