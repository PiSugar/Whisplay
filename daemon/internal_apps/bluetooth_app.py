import re
import time
from dataclasses import dataclass, field

from bluetooth_pairing_agent import BluetoothPairingAgent
from daemon_models import AppRecord


BLUETOOTH_APP_ID = "whisplay-bluetooth"


@dataclass
class BluetoothDevice:
    address: str
    name: str
    signal: int = 0
    paired: bool = False
    connected: bool = False
    trusted: bool = False


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


class BluetoothInternalApp:
    def __init__(self, lock, mark_dirty, run_command, spawn_worker, set_error, request_exit, strip_ansi):
        self._lock = lock
        self._mark_dirty = mark_dirty
        self._run_command = run_command
        self._spawn_worker = spawn_worker
        self._set_error = set_error
        self._request_exit = request_exit
        self._strip_ansi = strip_ansi
        self.state = BluetoothViewState()
        self._pairing_agent = BluetoothPairingAgent(self._update_pairing_state)

    def start(self):
        self._pairing_agent.start()

    def stop(self):
        self._pairing_agent.stop()

    def builtin_app(self) -> AppRecord:
        return AppRecord(
            app_id=BLUETOOTH_APP_ID,
            display_name="Bluetooth",
            icon="BT",
            exit_gesture="",
            priority=200,
            persist=False,
        )

    def handle_button(self, is_long_press: bool):
        with self._lock:
            if self.state.pairing_active and self.state.pairing_requires_confirmation:
                if is_long_press:
                    self._pairing_agent.confirm()
                else:
                    self._pairing_agent.cancel()
                return
            total = len(self.state.devices) + 2
            if total <= 0:
                total = 1
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
        if selected_index == 1:
            self.refresh_async(force=True)
            return
        with self._lock:
            device = self.state.devices[selected_index - 2]
            self.state.busy = True
            self.state.status = f"Working: {device.name}"
        self._mark_dirty()
        self._spawn_worker("bluetooth-action", lambda: self._toggle_device(device))

    def handle_keyboard_action(self, action: str):
        with self._lock:
            if self.state.pairing_active and self.state.pairing_requires_confirmation:
                if action == "submit":
                    self._pairing_agent.confirm()
                elif action == "cancel":
                    self._pairing_agent.cancel()
                return
            total = len(self.state.devices) + 2
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
            items = [
                {"title": "Back", "meta": "Return to desktop"},
                {"title": "Refresh scan", "meta": "Long press to rescan"},
            ]
            for device in self.state.devices:
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
                items.append({"title": device.name, "meta": f"signal {device.signal}% | {state_text}"})
            return {
                "kind": "list",
                "title": "Bluetooth",
                "subtitle": "",
                "items": items,
                "selected_index": min(self.state.selected_index, max(len(items) - 1, 0)),
                "status": self.state.status,
                "busy": self.state.busy,
                "detail_lines": self._detail_lines(),
            }

    def set_error(self, message: str):
        with self._lock:
            if self.state.busy:
                self.state.status = message
                self.state.busy = False
                self.state.last_refresh_at = time.time()
        self._mark_dirty()

    def refresh_async(self, force: bool = False):
        self._spawn_worker("bluetooth-refresh", lambda: self._refresh(force_scan=force))

    def _detail_lines(self) -> list[str]:
        if not self.state.pairing_active:
            return []
        lines = [self.state.pairing_message[:30]]
        if self.state.pairing_detail:
            lines.append(self.state.pairing_detail[:30])
        elif self.state.pairing_requires_confirmation:
            lines.append("Enter confirm  Esc cancel")
        return lines[:2]

    def _update_pairing_state(self, payload: dict):
        with self._lock:
            self.state.pairing_active = bool(payload.get("active", False))
            self.state.pairing_message = str(payload.get("message") or "")
            self.state.pairing_detail = str(payload.get("detail") or "")
            self.state.pairing_requires_confirmation = bool(payload.get("requires_confirmation", False))
        self._mark_dirty()

    def _refresh(self, force_scan: bool = False):
        with self._lock:
            self.state.busy = True
            self.state.status = "Scanning Bluetooth..."
        self._mark_dirty()

        self._run_command(["bluetoothctl", "power", "on"], timeout=8.0)
        scan_map = self._scan_devices() if force_scan else {}
        devices_out = self._run_command(["bluetoothctl", "devices"], timeout=8.0)
        devices = []
        for address, name in self._parse_device_lines(devices_out.stdout).items():
            info = self._run_command(["bluetoothctl", "info", address], timeout=8.0)
            info_text = info.stdout or ""
            clean_name = self._resolve_name(address, name, info_text, scan_map.get(address, {}))
            if not clean_name:
                continue
            devices.append(
                BluetoothDevice(
                    address=address,
                    name=clean_name,
                    signal=self._resolve_signal(info_text, scan_map.get(address, {})),
                    paired="Paired: yes" in info_text,
                    connected="Connected: yes" in info_text,
                    trusted="Trusted: yes" in info_text,
                )
            )
        devices.sort(key=lambda item: ((not item.connected), -item.signal, (not item.paired), item.name.lower(), item.address))
        devices = devices[:10]
        with self._lock:
            self.state.devices = devices
            self.state.selected_index = min(self.state.selected_index, len(devices) + 1)
            self.state.busy = False
            self.state.last_refresh_at = time.time()
            self.state.status = "No Bluetooth devices found" if not devices else "Long press to bind or unbind"
        self._mark_dirty()

    def _toggle_device(self, device: BluetoothDevice):
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
            self._update_pairing_state(
                {"active": True, "message": "Pairing in progress", "detail": device.address, "requires_confirmation": False}
            )
            success = False
            try:
                success = self._bind_new_device(device)
            except Exception as exc:
                exc_text = str(exc).strip()[:60]
                status = f"Pairing error: {exc_text}" if exc_text else "Pairing failed"
            finally:
                self._update_pairing_state({"active": False})
            if success:
                status = f"Bound {device.name}"
            elif "status" not in locals():
                status = f"Failed to bind {device.name}"
        with self._lock:
            self.state.status = status
            self.state.busy = False
            if not success:
                self.state.last_refresh_at = time.time()
        self._mark_dirty()
        if success:
            self._refresh(force_scan=False)

    def _bind_new_device(self, device: BluetoothDevice) -> bool:
        try:
            pair_result = self._run_command(["bluetoothctl", "pair", device.address], timeout=12.0)
            pair_output = (pair_result.stdout + pair_result.stderr).lower()
            if pair_result.returncode == 0 and "failed to pair" not in pair_output:
                pass
            elif "already" not in pair_output and "already paired" not in pair_output:
                return False
        except Exception:
            return False

        self._run_command(["bluetoothctl", "connect", device.address], timeout=12.0)
        self._run_command(["bluetoothctl", "trust", device.address], timeout=8.0)
        try:
            self._run_command(["bluetoothctl", "connect", device.address], timeout=12.0)
        except Exception:
            pass
        return True

    def _parse_device_lines(self, text: str) -> dict[str, str]:
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

    def _parse_signal(self, info_text: str) -> int:
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

    def _scan_devices(self) -> dict[str, dict]:
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
                scan_map.setdefault(address, {})["signal"] = max(0, min(100, 100 + int(match.group(2))))
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

    def _resolve_name(self, address: str, listed_name: str, info_text: str, scan_entry: dict) -> str:
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
        return normalized_candidate == normalized_address.replace(":", "-")

    def _resolve_signal(self, info_text: str, scan_entry: dict) -> int:
        scan_signal = scan_entry.get("signal")
        if isinstance(scan_signal, int) and scan_signal > 0:
            return scan_signal
        return self._parse_signal(info_text)

