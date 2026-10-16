import time
from dataclasses import dataclass, field

from daemon_models import AppRecord


WIFI_APP_ID = "whisplay-wifi"


@dataclass(frozen=True)
class ScreenKey:
    label: str
    value: str = ""
    shifted: str = ""
    action: str = ""
    width: float = 1.0


SCREEN_KEYBOARD_ROWS = (
    tuple(
        ScreenKey(normal, normal, shifted)
        for normal, shifted in zip("`1234567890-=", "~!@#$%^&*()_+")
    ),
    tuple(
        ScreenKey(normal, normal, shifted)
        for normal, shifted in zip("qwertyuiop[]\\", "QWERTYUIOP{}|")
    ),
    tuple(
        ScreenKey(normal, normal, shifted)
        for normal, shifted in zip("asdfghjkl;'", 'ASDFGHJKL:\"')
    ),
    (
        ScreenKey("ABC", action="shift", width=2.0),
        *(
            ScreenKey(normal, normal, shifted)
            for normal, shifted in zip("zxcvbnm,./", "ZXCVBNM<>?")
        ),
        ScreenKey("DEL", action="backspace", width=2.0),
    ),
    (
        ScreenKey("SPACE", " ", " ", width=8.0),
        ScreenKey("JOIN", action="submit", width=4.0),
    ),
)


@dataclass
class WifiNetwork:
    ssid: str
    signal: int = 0
    security: str = ""
    active: bool = False
    ipv4: str = ""


@dataclass
class WifiViewState:
    networks: list[WifiNetwork] = field(default_factory=list)
    selected_index: int = 0
    busy: bool = False
    status: str = "Loading..."
    last_refresh_at: float = 0.0
    mode: str = "list"
    password_target_ssid: str = ""
    password_buffer: str = ""
    keyboard_row: int = 1
    keyboard_col: int = 0
    keyboard_shifted: bool = False


class WifiInternalApp:
    def __init__(self, lock, mark_dirty, run_command, spawn_worker, request_exit):
        self._lock = lock
        self._mark_dirty = mark_dirty
        self._run_command = run_command
        self._spawn_worker = spawn_worker
        self._request_exit = request_exit
        self.state = WifiViewState()

    def builtin_app(self) -> AppRecord:
        return AppRecord(
            app_id=WIFI_APP_ID,
            display_name="WiFi",
            icon="WF",
            exit_gesture="",
            priority=190,
            persist=False,
        )

    def activate(self):
        with self._lock:
            self.state.mode = "list"
            self.state.password_target_ssid = ""
            self.state.password_buffer = ""
            self._reset_screen_keyboard()

    def text_input_active(self) -> bool:
        with self._lock:
            return self.state.mode == "input"

    def handle_button(self, is_long_press: bool):
        with self._lock:
            if self.state.mode == "input":
                if is_long_press:
                    self._cancel_password_input("Cancelled")
                return
            total = len(self.state.networks) + 2
            if not is_long_press:
                self.state.selected_index = (self.state.selected_index + 1) % max(total, 1)
                self._mark_dirty()
                return
            if self.state.busy:
                return
            selected_index = self.state.selected_index
        if selected_index == 0:
            self._request_exit()
            return
        if selected_index == 1:
            self.refresh_async()
            return
        with self._lock:
            network = self.state.networks[selected_index - 2]
            security = (network.security or "").strip()
            if security and security not in {"--", "NONE", "OPEN"}:
                self.state.mode = "input"
                self.state.password_target_ssid = network.ssid
                self.state.password_buffer = ""
                self._reset_screen_keyboard()
                self.state.status = f"Type password: {network.ssid}"
                self._mark_dirty()
                return
            self.state.busy = True
            self.state.status = f"Connecting: {network.ssid}"
        self._mark_dirty()
        self._spawn_worker("wifi-action", lambda: self._connect(network.ssid, ""))

    def handle_keyboard_action(self, action):
        with self._lock:
            if self.state.mode == "input":
                if isinstance(action, str) and action.startswith("keyboard_"):
                    self._handle_screen_keyboard_input(action.removeprefix("keyboard_"))
                    return
                self._handle_external_keyboard_input(action)
                return
            total = len(self.state.networks) + 2
            if action == "up":
                self.state.selected_index = (self.state.selected_index - 1) % max(total, 1)
                self._mark_dirty()
                return
            if action == "down":
                self.state.selected_index = (self.state.selected_index + 1) % max(total, 1)
                self._mark_dirty()
                return
        if action == "submit":
            self.handle_button(True)

    def view_model(self) -> dict:
        with self._lock:
            if self.state.mode == "input":
                rows = []
                for row_index, row in enumerate(SCREEN_KEYBOARD_ROWS):
                    keys = []
                    for col_index, key in enumerate(row):
                        label = self._screen_key_label(key)
                        keys.append(
                            {
                                "label": label,
                                "width": key.width,
                                "selected": (
                                    row_index == self.state.keyboard_row
                                    and col_index == self.state.keyboard_col
                                ),
                            }
                        )
                    rows.append(keys)
                selected_key = self._selected_screen_key()
                return {
                    "kind": "keyboard",
                    "title": "WiFi Password",
                    "subtitle": self.state.password_target_ssid or "Protected network",
                    "password": "*" * len(self.state.password_buffer),
                    "password_length": len(self.state.password_buffer),
                    "status": self.state.status,
                    "screen_keyboard": True,
                    "keyboard_rows": rows,
                    "selected_key": self._screen_key_label(selected_key),
                    "shifted": self.state.keyboard_shifted,
                }
            items = [
                {"title": "Back", "meta": "Return to desktop"},
                {"title": "Refresh list", "meta": "Long press to rescan"},
            ]
            for network in self.state.networks:
                state = "connected" if network.active else (network.security or "OPEN")
                detail = network.ipv4 if network.active and network.ipv4 else f"signal {network.signal}%"
                items.append({"title": network.ssid or "<Hidden SSID>", "meta": f"{detail} | {state}"})
            return {
                "kind": "list",
                "title": "WiFi",
                "subtitle": "",
                "items": items,
                "selected_index": min(self.state.selected_index, max(len(items) - 1, 0)),
                "status": self.state.status,
                "busy": self.state.busy,
            }

    def set_error(self, message: str):
        with self._lock:
            if self.state.busy:
                self.state.status = message
                self.state.busy = False
                self.state.last_refresh_at = time.time()
        self._mark_dirty()

    def refresh_async(self):
        self._spawn_worker("wifi-refresh", self._refresh)

    def _handle_external_keyboard_input(self, action):
        if action == "cancel":
            self._cancel_password_input("Cancelled")
            return
        if action == "backspace":
            self.state.password_buffer = self.state.password_buffer[:-1]
            self._mark_dirty()
            return
        if action == "submit":
            ssid = self.state.password_target_ssid
            password = self.state.password_buffer
            self.state.mode = "list"
            self.state.busy = True
            self.state.status = f"Connecting: {ssid}"
            self._mark_dirty()
            self._spawn_worker("wifi-action", lambda: self._connect(ssid, password))
            return
        if isinstance(action, tuple) and len(action) == 2 and action[0] == "char":
            self.state.password_buffer += action[1]
            self._mark_dirty()

    def _reset_screen_keyboard(self):
        self.state.keyboard_row = 1
        self.state.keyboard_col = 0
        self.state.keyboard_shifted = False

    def _selected_screen_key(self) -> ScreenKey:
        row = SCREEN_KEYBOARD_ROWS[self.state.keyboard_row]
        self.state.keyboard_col = max(0, min(self.state.keyboard_col, len(row) - 1))
        return row[self.state.keyboard_col]

    def _screen_key_label(self, key: ScreenKey) -> str:
        if key.action == "shift":
            return "abc" if self.state.keyboard_shifted else "ABC"
        if key.value == " ":
            return key.label
        if key.value:
            return key.shifted if self.state.keyboard_shifted else key.value
        return key.label

    def _screen_key_center(self, row_index: int, col_index: int) -> float:
        row = SCREEN_KEYBOARD_ROWS[row_index]
        total_width = sum(key.width for key in row)
        preceding_width = sum(key.width for key in row[:col_index])
        return (preceding_width + row[col_index].width / 2) / total_width

    def _move_screen_keyboard_vertical(self, delta: int):
        current_center = self._screen_key_center(
            self.state.keyboard_row, self.state.keyboard_col
        )
        target_row = (self.state.keyboard_row + delta) % len(SCREEN_KEYBOARD_ROWS)
        target = SCREEN_KEYBOARD_ROWS[target_row]
        target_col = min(
            range(len(target)),
            key=lambda index: abs(
                self._screen_key_center(target_row, index) - current_center
            ),
        )
        self.state.keyboard_row = target_row
        self.state.keyboard_col = target_col

    def _handle_screen_keyboard_input(self, action: str):
        row = SCREEN_KEYBOARD_ROWS[self.state.keyboard_row]
        if action == "left":
            self.state.keyboard_col = (self.state.keyboard_col - 1) % len(row)
            self._mark_dirty()
            return
        if action == "right":
            self.state.keyboard_col = (self.state.keyboard_col + 1) % len(row)
            self._mark_dirty()
            return
        if action == "up":
            self._move_screen_keyboard_vertical(-1)
            self._mark_dirty()
            return
        if action == "down":
            self._move_screen_keyboard_vertical(1)
            self._mark_dirty()
            return
        if action == "cancel":
            self._cancel_password_input("Cancelled")
            return
        if action != "select":
            return

        key = self._selected_screen_key()
        if key.action == "shift":
            self.state.keyboard_shifted = not self.state.keyboard_shifted
            self._mark_dirty()
            return
        if key.action == "backspace":
            self.state.password_buffer = self.state.password_buffer[:-1]
            self._mark_dirty()
            return
        if key.action == "submit":
            self._handle_external_keyboard_input("submit")
            return
        self.state.password_buffer += (
            key.shifted if self.state.keyboard_shifted else key.value
        )
        self._mark_dirty()

    def _cancel_password_input(self, status: str):
        self.state.mode = "list"
        self.state.password_target_ssid = ""
        self.state.password_buffer = ""
        self._reset_screen_keyboard()
        self.state.status = status
        self._mark_dirty()

    def _get_connected_wifi_ipv4(self) -> str:
        device_result = self._run_command(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"], timeout=5.0)
        if device_result.returncode != 0:
            return ""
        wifi_device = ""
        for line in device_result.stdout.splitlines():
            parts = line.strip().split(":")
            if len(parts) < 3:
                continue
            device_name, device_type, state = parts[0], parts[1], ":".join(parts[2:])
            if device_type != "wifi" or not state.startswith("connected"):
                continue
            wifi_device = device_name
            break
        if not wifi_device:
            return ""
        ip_result = self._run_command(["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", wifi_device], timeout=5.0)
        if ip_result.returncode != 0:
            return ""
        for line in ip_result.stdout.splitlines():
            if "IP4.ADDRESS" not in line:
                continue
            _, _, address = line.partition(":")
            address = address.strip()
            if address:
                return address.split("/", 1)[0]
        return ""

    def _refresh(self):
        with self._lock:
            self.state.busy = True
            self.state.status = "Scanning WiFi..."
        self._mark_dirty()
        result = self._run_command(
            [
                "nmcli",
                "-t",
                "--escape",
                "yes",
                "--fields",
                "IN-USE,SSID,SIGNAL,SECURITY",
                "device",
                "wifi",
                "list",
                "--rescan",
                "auto",
            ],
            timeout=15.0,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "nmcli failed").strip())

        networks = []
        seen_ssids = set()
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            fields = self._split_escaped_fields(line, 4, ":")
            if len(fields) != 4:
                continue
            in_use, ssid, signal_text, security = fields
            ssid = ssid.strip()
            if ssid in seen_ssids:
                continue
            seen_ssids.add(ssid)
            try:
                signal = int(signal_text or "0")
            except ValueError:
                signal = 0
            networks.append(
                WifiNetwork(
                    ssid=ssid,
                    signal=max(0, min(100, signal)),
                    security=(security or "OPEN").strip(),
                    active=in_use.strip() == "*",
                )
            )
        connected_ipv4 = self._get_connected_wifi_ipv4()
        if connected_ipv4:
            for network in networks:
                if network.active:
                    network.ipv4 = connected_ipv4
                    break
        networks.sort(key=lambda item: ((not item.active), -item.signal, item.ssid.lower()))
        networks = networks[:10]
        with self._lock:
            self.state.networks = networks
            self.state.selected_index = min(self.state.selected_index, len(networks) + 1)
            self.state.busy = False
            self.state.last_refresh_at = time.time()
            self.state.status = "No WiFi networks found" if not networks else "Long press to connect"
        self._mark_dirty()

    def _split_escaped_fields(self, line: str, expected_parts: int, separator: str) -> list[str]:
        parts = []
        current = []
        escaped = False
        for char in line:
            if escaped:
                current.append(char)
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == separator and len(parts) < expected_parts - 1:
                parts.append("".join(current))
                current = []
                continue
            current.append(char)
        parts.append("".join(current))
        return parts

    def _connect(self, ssid: str, password: str):
        args = ["nmcli", "device", "wifi", "connect", ssid]
        if password:
            args.extend(["password", password])
        result = self._run_command(args, timeout=45.0)
        status = f"Connected {ssid}" if result.returncode == 0 else f"Failed to connect {ssid}"
        with self._lock:
            self.state.busy = False
            self.state.status = status
            self.state.password_target_ssid = ""
            self.state.password_buffer = ""
            self.state.mode = "list"
        self._mark_dirty()
        self._refresh()
