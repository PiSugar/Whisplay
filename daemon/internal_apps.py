import os
import re
import select
import struct
import subprocess
import threading
import time
from dataclasses import dataclass, field

from bluetooth_pairing_agent import BluetoothPairingAgent
from daemon_models import AppRecord


BLUETOOTH_APP_ID = "whisplay-bluetooth"
WIFI_APP_ID = "whisplay-wifi"
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


@dataclass
class BluetoothDevice:
    address: str
    name: str
    signal: int = 0
    paired: bool = False
    connected: bool = False
    trusted: bool = False


@dataclass
class WifiNetwork:
    ssid: str
    signal: int = 0
    security: str = ""
    active: bool = False
    ipv4: str = ""


@dataclass
class BluetoothViewState:
    devices: list[BluetoothDevice] = field(default_factory=list)
    selected_index: int = 0
    busy: bool = False
    status: str = "Loading..."
    last_refresh_at: float = 0.0
    pairing_active: bool = False
    pairing_message: str = ""
    pairing_detail: str = ""
    pairing_requires_confirmation: bool = False


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
        RESCAN_INTERVAL = 2.0
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
                if now - last_scan >= RESCAN_INTERVAL:
                    last_scan = now
                    current_paths = set(self._candidate_paths())
                    stale = [fd for fd, p in open_fds.items() if p not in current_paths]
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


class InternalAppManager:
    REFRESH_INTERVAL_SEC = 20.0
    MAX_VISIBLE_ITEMS = 10

    def __init__(self):
        self._lock = threading.RLock()
        self._dirty = False
        self._exit_requested = False
        self._threads: dict[str, threading.Thread] = {}
        self._pairing_agent = BluetoothPairingAgent(self._update_bluetooth_pairing_state)
        self.bluetooth = BluetoothViewState()
        self.wifi = WifiViewState()

    def start(self):
        self._pairing_agent.start()

    def stop(self):
        self._pairing_agent.stop()

    def builtin_apps(self) -> list[AppRecord]:
        return [
            AppRecord(
                app_id=BLUETOOTH_APP_ID,
                display_name="Bluetooth",
                icon="BT",
                exit_gesture="",
                priority=200,
                persist=False,
            ),
            AppRecord(
                app_id=WIFI_APP_ID,
                display_name="WiFi",
                icon="WF",
                exit_gesture="",
                priority=190,
                persist=False,
            ),
        ]

    def is_internal_app(self, app_id: str | None) -> bool:
        return app_id in {BLUETOOTH_APP_ID, WIFI_APP_ID}

    @property
    def exit_requested(self) -> bool:
        with self._lock:
            return self._exit_requested

    def clear_exit_requested(self):
        with self._lock:
            self._exit_requested = False

    def text_input_active(self) -> bool:
        with self._lock:
            return self.wifi.mode == "input"

    def consume_dirty(self) -> bool:
        with self._lock:
            dirty = self._dirty
            self._dirty = False
            return dirty

    def activate(self, app_id: str):
        with self._lock:
            if app_id == WIFI_APP_ID:
                self.wifi.mode = "list"
                self.wifi.password_target_ssid = ""
                self.wifi.password_buffer = ""
        self._mark_dirty()
        self.refresh_async(app_id, force=True)

    def tick(self, app_id: str | None):
        if not self.is_internal_app(app_id):
            return
        state = self.bluetooth if app_id == BLUETOOTH_APP_ID else self.wifi
        now = time.time()
        if state.busy:
            return
        if now - state.last_refresh_at >= self.REFRESH_INTERVAL_SEC:
            self.refresh_async(app_id, force=True)

    def handle_button_release(self, app_id: str, is_long_press: bool):
        if app_id == BLUETOOTH_APP_ID:
            self._handle_bluetooth_button(is_long_press)
        elif app_id == WIFI_APP_ID:
            self._handle_wifi_button(is_long_press)

    def handle_keyboard_action(self, app_id: str, action: str):
        if app_id == BLUETOOTH_APP_ID:
            self._handle_bluetooth_keyboard(action)
        elif app_id == WIFI_APP_ID:
            self._handle_wifi_keyboard(action)

    def get_view_model(self, app_id: str) -> dict:
        if app_id == BLUETOOTH_APP_ID:
            return self._bluetooth_view_model()
        if app_id == WIFI_APP_ID:
            return self._wifi_view_model()
        return {}

    def refresh_async(self, app_id: str, force: bool = False):
        if app_id == BLUETOOTH_APP_ID:
            self._spawn_worker("bluetooth-refresh", lambda: self._refresh_bluetooth(force_scan=force))
        elif app_id == WIFI_APP_ID:
            self._spawn_worker("wifi-refresh", self._refresh_wifi)

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
        with self._lock:
            if self.bluetooth.busy:
                self.bluetooth.status = text
                self.bluetooth.busy = False
                self.bluetooth.last_refresh_at = time.time()
            if self.wifi.busy:
                self.wifi.status = text
                self.wifi.busy = False
                self.wifi.last_refresh_at = time.time()
        self._mark_dirty()

    def _mark_dirty(self):
        with self._lock:
            self._dirty = True

    def _run_command(self, args: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)

    def _strip_ansi(self, text: str) -> str:
        return re.sub(r"\x1b\[[0-9;]*m", "", text)

    def _handle_bluetooth_button(self, is_long_press: bool):
        with self._lock:
            if self.bluetooth.pairing_active and self.bluetooth.pairing_requires_confirmation:
                if is_long_press:
                    self._pairing_agent.confirm()
                else:
                    self._pairing_agent.cancel()
                return
            total = len(self.bluetooth.devices) + 2
            if total <= 0:
                total = 1
            if not is_long_press:
                self.bluetooth.selected_index += 1
                if self.bluetooth.selected_index >= total:
                    self.bluetooth.selected_index = 0
                self._dirty = True
                return
            if self.bluetooth.busy:
                return
            selected_index = self.bluetooth.selected_index
        if selected_index == 0:
            self._exit_requested = True
            return
        if selected_index == 1:
            self.refresh_async(BLUETOOTH_APP_ID, force=True)
            return
        with self._lock:
            device = self.bluetooth.devices[selected_index - 2]
            self.bluetooth.busy = True
            self.bluetooth.status = f"Working: {device.name}"
        self._mark_dirty()
        self._spawn_worker(
            "bluetooth-action",
            lambda: self._toggle_bluetooth_device(device),
        )

    def _handle_bluetooth_keyboard(self, action: str):
        with self._lock:
            if self.bluetooth.pairing_active and self.bluetooth.pairing_requires_confirmation:
                if action == "submit":
                    self._pairing_agent.confirm()
                elif action == "cancel":
                    self._pairing_agent.cancel()
                return
            total = len(self.bluetooth.devices) + 2
            if action == "up":
                self.bluetooth.selected_index = (self.bluetooth.selected_index - 1) % max(total, 1)
                self._dirty = True
                return
            if action == "down":
                self.bluetooth.selected_index = (self.bluetooth.selected_index + 1) % max(total, 1)
                self._dirty = True
                return
        if action == "submit":
            self._handle_bluetooth_button(True)

    def _handle_wifi_button(self, is_long_press: bool):
        with self._lock:
            if self.wifi.mode == "input":
                if is_long_press:
                    self._cancel_wifi_password_input("Cancelled")
                return

            total = len(self.wifi.networks) + 2
            if not is_long_press:
                self.wifi.selected_index += 1
                if self.wifi.selected_index >= max(total, 1):
                    self.wifi.selected_index = 0
                self._dirty = True
                return
            if self.wifi.busy:
                return
            selected_index = self.wifi.selected_index

        if selected_index == 0:
            self._exit_requested = True
            return
        if selected_index == 1:
            self.refresh_async(WIFI_APP_ID, force=True)
            return

        with self._lock:
            network = self.wifi.networks[selected_index - 2]
            security = (network.security or "").strip()
            if security and security not in {"--", "NONE", "OPEN"}:
                self.wifi.mode = "input"
                self.wifi.password_target_ssid = network.ssid
                self.wifi.password_buffer = ""
                self.wifi.status = f"Type password: {network.ssid}"
                self._dirty = True
                return
            self.wifi.busy = True
            self.wifi.status = f"Connecting: {network.ssid}"
        self._mark_dirty()
        self._spawn_worker(
            "wifi-action",
            lambda: self._connect_wifi(network.ssid, ""),
        )

    def _handle_wifi_keyboard(self, action: str):
        with self._lock:
            if self.wifi.mode == "input":
                self._handle_external_keyboard_input(action)
                return
            total = len(self.wifi.networks) + 2
            if action == "up":
                self.wifi.selected_index = (self.wifi.selected_index - 1) % max(total, 1)
                self._dirty = True
                return
            if action == "down":
                self.wifi.selected_index = (self.wifi.selected_index + 1) % max(total, 1)
                self._dirty = True
                return
        if action == "submit":
            self._handle_wifi_button(True)

    def _handle_external_keyboard_input(self, action):
        with self._lock:
            if self.wifi.mode != "input":
                return
            if action == "cancel":
                self._cancel_wifi_password_input("Cancelled")
                return
            if action == "backspace":
                self.wifi.password_buffer = self.wifi.password_buffer[:-1]
                self._dirty = True
                return
            if action == "submit":
                ssid = self.wifi.password_target_ssid
                password = self.wifi.password_buffer
                self.wifi.mode = "list"
                self.wifi.busy = True
                self.wifi.status = f"Connecting: {ssid}"
                self._dirty = True
                self._spawn_worker("wifi-action", lambda: self._connect_wifi(ssid, password))
                return
            if isinstance(action, tuple) and len(action) == 2 and action[0] == "char":
                self.wifi.password_buffer += action[1]
                self._dirty = True

    def _cancel_wifi_password_input(self, status: str):
        self.wifi.mode = "list"
        self.wifi.password_target_ssid = ""
        self.wifi.password_buffer = ""
        self.wifi.status = status
        self._dirty = True

    def _bluetooth_view_model(self) -> dict:
        with self._lock:
            items = [
                {"title": "Back", "meta": "Return to desktop"},
                {"title": "Refresh scan", "meta": "Long press to rescan"},
            ]
            for device in self.bluetooth.devices:
                if not device.name or device.name.strip() == device.address.strip():
                    continue
                state_bits = []
                if device.connected:
                    state_bits.append("connected")
                if device.paired:
                    state_bits.append("paired")
                if device.trusted:
                    state_bits.append("trusted")
                state_text = ", ".join(state_bits) if state_bits else "available"
                items.append(
                    {
                        "title": device.name,
                        "meta": f"signal {device.signal}% | {state_text}",
                    }
                )
            return {
                "kind": "list",
                "title": "Bluetooth",
                "subtitle": "",
                "items": items,
                "selected_index": min(self.bluetooth.selected_index, max(len(items) - 1, 0)),
                "status": self.bluetooth.status,
                "busy": self.bluetooth.busy,
                "detail_lines": self._bluetooth_detail_lines(),
            }

    def _bluetooth_detail_lines(self) -> list[str]:
        if not self.bluetooth.pairing_active:
            return []
        lines = [self.bluetooth.pairing_message[:30]]
        if self.bluetooth.pairing_detail:
            lines.append(self.bluetooth.pairing_detail[:30])
        elif self.bluetooth.pairing_requires_confirmation:
            lines.append("Enter confirm  Esc cancel")
        return lines[:2]

    def _update_bluetooth_pairing_state(self, payload: dict):
        with self._lock:
            self.bluetooth.pairing_active = bool(payload.get("active", False))
            self.bluetooth.pairing_message = str(payload.get("message") or "")
            self.bluetooth.pairing_detail = str(payload.get("detail") or "")
            self.bluetooth.pairing_requires_confirmation = bool(payload.get("requires_confirmation", False))
            self._dirty = True

    def _wifi_view_model(self) -> dict:
        with self._lock:
            if self.wifi.mode == "input":
                return {
                    "kind": "keyboard",
                    "title": "WiFi Password",
                    "subtitle": self.wifi.password_target_ssid or "Protected network",
                    "password": "*" * len(self.wifi.password_buffer),
                    "password_length": len(self.wifi.password_buffer),
                    "status": self.wifi.status,
                }
            items = [
                {"title": "Back", "meta": "Return to desktop"},
                {"title": "Refresh list", "meta": "Long press to rescan"},
            ]
            for network in self.wifi.networks:
                security = network.security or "OPEN"
                state = "connected" if network.active else security
                if network.active and network.ipv4:
                    detail = network.ipv4
                else:
                    detail = f"signal {network.signal}%"
                items.append(
                    {
                        "title": network.ssid or "<Hidden SSID>",
                        "meta": f"{detail} | {state}",
                    }
                )
            return {
                "kind": "list",
                "title": "WiFi",
                "subtitle": "",
                "items": items,
                "selected_index": min(self.wifi.selected_index, max(len(items) - 1, 0)),
                "status": self.wifi.status,
                "busy": self.wifi.busy,
            }

    def _refresh_bluetooth(self, force_scan: bool = False):
        with self._lock:
            self.bluetooth.busy = True
            self.bluetooth.status = "Scanning Bluetooth..."
        self._mark_dirty()

        self._run_command(["bluetoothctl", "power", "on"], timeout=8.0)
        scan_map = self._scan_bluetooth_devices() if force_scan else {}
        devices_out = self._run_command(["bluetoothctl", "devices"], timeout=8.0)
        devices = []
        for address, name in self._parse_bluetooth_device_lines(devices_out.stdout).items():
            info = self._run_command(["bluetoothctl", "info", address], timeout=8.0)
            info_text = info.stdout or ""
            clean_name = self._resolve_bluetooth_name(address, name, info_text, scan_map.get(address, {}))
            if not clean_name:
                continue
            devices.append(
                BluetoothDevice(
                    address=address,
                    name=clean_name,
                    signal=self._resolve_bluetooth_signal(info_text, scan_map.get(address, {})),
                    paired="Paired: yes" in info_text,
                    connected="Connected: yes" in info_text,
                    trusted="Trusted: yes" in info_text,
                )
            )
        devices.sort(key=lambda item: ((not item.connected), -item.signal, (not item.paired), item.name.lower(), item.address))
        devices = devices[: self.MAX_VISIBLE_ITEMS]
        with self._lock:
            self.bluetooth.devices = devices
            self.bluetooth.selected_index = min(self.bluetooth.selected_index, len(devices) + 1)
            self.bluetooth.busy = False
            self.bluetooth.last_refresh_at = time.time()
            self.bluetooth.status = "No Bluetooth devices found" if not devices else "Long press to bind or unbind"
        self._mark_dirty()

    def _toggle_bluetooth_device(self, device: BluetoothDevice):
        self._run_command(["bluetoothctl", "power", "on"], timeout=8.0)
        if device.connected:
            result = self._run_command(["bluetoothctl", "remove", device.address], timeout=20.0)
            success = result.returncode == 0 and "not available" not in (result.stdout + result.stderr).lower()
            status = f"Unbound {device.name}" if success else f"Failed to unbind {device.name}"
        elif device.paired:
            connect = self._run_command(["bluetoothctl", "connect", device.address], timeout=30.0)
            success = connect.returncode == 0
            status = f"Connected {device.name}" if success else f"Failed to connect {device.name}"
        else:
            self._update_bluetooth_pairing_state(
                {
                    "active": True,
                    "message": "Pairing in progress",
                    "detail": device.address,
                    "requires_confirmation": False,
                }
            )
            success = False
            try:
                success = self._bind_new_device(device)
            except Exception as exc:
                exc_text = str(exc).strip()[:60]
                status = f"Pairing error: {exc_text}" if exc_text else "Pairing failed"
            finally:
                self._update_bluetooth_pairing_state({"active": False})
            if success:
                status = f"Bound {device.name}"
            elif 'status' not in locals():
                status = f"Failed to bind {device.name}"
        with self._lock:
            self.bluetooth.status = status
            self.bluetooth.busy = False
            if not success:
                self.bluetooth.last_refresh_at = time.time()
        self._mark_dirty()
        if success:
            self._refresh_bluetooth(force_scan=False)

    def _bind_new_device(self, device: BluetoothDevice) -> bool:
        pair_ok = False
        try:
            pair_result = self._run_command(["bluetoothctl", "pair", device.address], timeout=12.0)
            pair_output = (pair_result.stdout + pair_result.stderr).lower()
            if pair_result.returncode == 0 and "failed to pair" not in pair_output:
                pair_ok = True
            elif "already" in pair_output or "already paired" in pair_output:
                pair_ok = True
        except Exception:
            pair_ok = False

        self._run_command(["bluetoothctl", "connect", device.address], timeout=12.0)
        self._run_command(["bluetoothctl", "trust", device.address], timeout=8.0)
        try:
            self._run_command(["bluetoothctl", "connect", device.address], timeout=12.0)
        except Exception:
            pass
        return True

    def _parse_bluetooth_device_lines(self, text: str) -> dict[str, str]:
        devices = {}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line.startswith("Device "):
                continue
            parts = line.split(maxsplit=2)
            if len(parts) < 3:
                continue
            devices[parts[1]] = parts[2].strip() or parts[1]
        return devices

    def _parse_bluetooth_signal(self, info_text: str) -> int:
        for raw_line in info_text.splitlines():
            line = raw_line.strip()
            if not line.startswith("RSSI:"):
                continue
            value_text = line.split(":", 1)[1].strip().split()[0]
            try:
                rssi = int(value_text)
            except ValueError:
                return 0
            return max(0, min(100, 100 + rssi))
        return 0

    def _scan_bluetooth_devices(self) -> dict[str, dict]:
        result = self._run_command(["bluetoothctl", "--timeout", "5", "scan", "on"], timeout=9.0)
        text = self._strip_ansi((result.stdout or "") + "\n" + (result.stderr or ""))
        scan_map: dict[str, dict] = {}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if "Device " not in line:
                continue
            if " RSSI:" in line:
                match = re.search(r"Device\s+([0-9A-F:]{17})\s+RSSI:.*\((-?\d+)\)", line)
                if not match:
                    continue
                address = match.group(1)
                rssi = int(match.group(2))
                scan_map.setdefault(address, {})["signal"] = max(0, min(100, 100 + rssi))
                continue
            if "[NEW]" not in line:
                continue
            match = re.search(r"Device\s+([0-9A-F:]{17})\s+(.+)$", line)
            if not match:
                continue
            address = match.group(1)
            name = match.group(2).strip()
            if name:
                scan_map.setdefault(address, {})["name"] = name
        return scan_map

    def _resolve_bluetooth_name(self, address: str, listed_name: str, info_text: str, scan_entry: dict) -> str:
        candidates = []
        scan_name = str(scan_entry.get("name") or "").strip()
        if scan_name:
            candidates.append(scan_name)
        listed_name = str(listed_name or "").strip()
        if listed_name:
            candidates.append(listed_name)
        for raw_line in info_text.splitlines():
            line = raw_line.strip()
            if line.startswith("Name:"):
                candidates.append(line.split(":", 1)[1].strip())
            elif line.startswith("Alias:"):
                candidates.append(line.split(":", 1)[1].strip())
        for candidate in candidates:
            if candidate and not self._looks_like_address_alias(address, candidate):
                return candidate
        return ""

    def _looks_like_address_alias(self, address: str, candidate: str) -> bool:
        normalized_address = address.strip().lower()
        normalized_candidate = candidate.strip().lower()
        if not normalized_candidate:
            return True
        if normalized_candidate == normalized_address:
            return True
        if normalized_candidate == normalized_address.replace(":", "-"):
            return True
        return False

    def _resolve_bluetooth_signal(self, info_text: str, scan_entry: dict) -> int:
        scan_signal = scan_entry.get("signal")
        if isinstance(scan_signal, int) and scan_signal > 0:
            return scan_signal
        return self._parse_bluetooth_signal(info_text)

    def _get_connected_wifi_ipv4(self) -> str:
        device_result = self._run_command(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"],
            timeout=5.0,
        )
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

        ip_result = self._run_command(
            ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", wifi_device],
            timeout=5.0,
        )
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

    def _refresh_wifi(self):
        with self._lock:
            self.wifi.busy = True
            self.wifi.status = "Scanning WiFi..."
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
        networks = networks[: self.MAX_VISIBLE_ITEMS]
        with self._lock:
            self.wifi.networks = networks
            self.wifi.selected_index = min(self.wifi.selected_index, len(networks) + 1)
            self.wifi.busy = False
            self.wifi.last_refresh_at = time.time()
            self.wifi.status = "No WiFi networks found" if not networks else "Long press to connect"
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

    def _connect_wifi(self, ssid: str, password: str):
        args = ["nmcli", "device", "wifi", "connect", ssid]
        if password:
            args.extend(["password", password])
        result = self._run_command(args, timeout=45.0)
        success = result.returncode == 0
        status = f"Connected {ssid}" if success else f"Failed to connect {ssid}"
        with self._lock:
            self.wifi.busy = False
            self.wifi.status = status
            self.wifi.password_target_ssid = ""
            self.wifi.password_buffer = ""
            self.wifi.mode = "list"
        self._mark_dirty()
        self._refresh_wifi()
