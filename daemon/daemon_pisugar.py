import os
import re
import socket


PISUGAR_SOCKET_CANDIDATES = (
    "/tmp/pisugar-server.sock",
    "/run/pisugar-server.sock",
)
PISUGAR_BUTTON_EVENTS = ("single", "double", "long")
PISUGAR_TRIGGER_FILE = "/tmp/whisplay-daemon-home.flag"
PISUGAR_OLD_TRIGGER_FILE = "/tmp/whisplay-pisugar-long.flag"
PISUGAR_DEFAULT_SHELL_PLACEHOLDERS = {
    "echo longpress",
    "long echo longpress",
    "echo single",
    "single echo single",
    "echo singlepress",
    "single echo singlepress",
    "echo double",
    "double echo double",
    "echo doublepress",
    "double echo doublepress",
}


class PiSugarManager:
    def __init__(self):
        self.sock_path: str | None = None
        self.home_button_event = "none"
        self.home_button_exit_enabled = False
        self.last_trigger_mtime = 0.0

    def socket_path(self) -> str | None:
        for path in PISUGAR_SOCKET_CANDIDATES:
            if os.path.exists(path):
                return path
        return None

    def request(self, sock_path: str, command: str, timeout_sec: float = 1.5) -> str | None:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(timeout_sec)
                client.connect(sock_path)
                client.sendall((command.strip() + "\n").encode("utf-8"))
                chunks: list[bytes] = []
                while True:
                    try:
                        data = client.recv(4096)
                    except socket.timeout:
                        break
                    if not data:
                        break
                    chunks.append(data)
                    if b"\n" in data:
                        break
                if not chunks:
                    return None
                text = b"".join(chunks).decode("utf-8", "replace")
                for line in text.splitlines():
                    line = line.strip()
                    if line:
                        return line
        except Exception:
            return None
        return None

    def parse_bool_from_tail(self, text: str) -> bool | None:
        token = text.strip().split()[-1].lower() if text.strip() else ""
        if token in {"1", "true", "on"}:
            return True
        if token in {"0", "false", "off"}:
            return False
        return None

    def event_enabled(self, sock_path: str, event_name: str) -> bool | None:
        line = self.request(sock_path, f"get button_enable {event_name}")
        if line:
            value = self.parse_bool_from_tail(line)
            if value is not None:
                return value
        line = self.request(sock_path, "get button_enable")
        if not line:
            return None
        lower = line.lower()
        match = re.search(
            rf"button_enable:\s+{re.escape(event_name.lower())}\s+(true|false|1|0|on|off)\b",
            lower,
        )
        if match:
            token = match.group(1)
            return token in {"true", "1", "on"}
        return None

    def event_shell_value(self, sock_path: str, event_name: str) -> str | None:
        line = self.request(sock_path, f"get button_shell {event_name}")
        if line:
            return line.split(":", 1)[1].strip() if ":" in line else line.strip()
        line = self.request(sock_path, "get button_shell")
        if not line:
            return None
        match = re.search(
            rf"button_shell:\s+{re.escape(event_name)}\s+(.+)$",
            line,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        return match.group(1).strip()

    def is_daemon_managed_shell(self, value: str) -> bool:
        text = (value or "").strip().lower()
        if not text:
            return False
        return (
            PISUGAR_TRIGGER_FILE.lower() in text
            or PISUGAR_OLD_TRIGGER_FILE.lower() in text
        )

    def is_default_shell_placeholder(self, value: str) -> bool:
        return (value or "").strip().lower() in PISUGAR_DEFAULT_SHELL_PLACEHOLDERS

    def has_custom_button_event(self, sock_path: str, event_name: str) -> bool | None:
        enabled = self.event_enabled(sock_path, event_name)
        shell_value = self.event_shell_value(sock_path, event_name)
        if enabled is None or shell_value is None:
            return None
        shell_defined = bool(shell_value) and shell_value.lower() not in {"none", "null", "\"\""}
        if not shell_defined:
            return False
        if self.is_default_shell_placeholder(shell_value):
            return False
        if self.is_daemon_managed_shell(shell_value):
            return False
        return enabled and shell_defined

    def setup_button_hook(self, sock_path: str, event_name: str) -> bool:
        enable_resp = self.request(sock_path, f"set_button_enable {event_name} 1")
        if not enable_resp or "done" not in enable_resp.lower():
            return False
        shell_cmd = f"set_button_shell {event_name} sh -c date +%s%N > {PISUGAR_TRIGGER_FILE}"
        shell_resp = self.request(sock_path, shell_cmd)
        if not shell_resp or "done" not in shell_resp.lower():
            return False
        try:
            if os.path.exists(PISUGAR_TRIGGER_FILE):
                self.last_trigger_mtime = os.path.getmtime(PISUGAR_TRIGGER_FILE)
        except Exception:
            self.last_trigger_mtime = 0.0
        return True

    def clear_button_hook(self, sock_path: str, event_name: str) -> bool:
        shell_resp = self.request(sock_path, f"set_button_shell {event_name} none")
        enable_resp = self.request(sock_path, f"set_button_enable {event_name} 0")
        shell_ok = bool(shell_resp and "done" in shell_resp.lower())
        enable_ok = bool(enable_resp and "done" in enable_resp.lower())
        return shell_ok or enable_ok

    def cleanup_daemon_managed_hooks(self, sock_path: str):
        cleared_events: list[str] = []
        for event_name in PISUGAR_BUTTON_EVENTS:
            shell_value = self.event_shell_value(sock_path, event_name)
            if shell_value is None or not self.is_daemon_managed_shell(shell_value):
                continue
            if self.clear_button_hook(sock_path, event_name):
                cleared_events.append(event_name)
        if cleared_events:
            print(
                "[WhisplayDaemon] cleared stale pisugar daemon hooks: "
                + ", ".join(cleared_events)
            )

    def poll_home_trigger(self) -> bool:
        if not self.home_button_exit_enabled:
            return False
        try:
            mtime = os.path.getmtime(PISUGAR_TRIGGER_FILE)
        except FileNotFoundError:
            return False
        except Exception:
            return False
        if mtime > self.last_trigger_mtime:
            self.last_trigger_mtime = mtime
            return True
        return False

    def probe_battery_level(self) -> int | None:
        sock_path = self.socket_path()
        if not sock_path:
            return None
        line = self.request(sock_path, "get battery")
        if not line:
            return None
        match = re.search(r"battery:\s*(-?\d+)", line, flags=re.IGNORECASE)
        if not match:
            return None
        try:
            level = int(match.group(1))
        except ValueError:
            return None
        if level < 0:
            return None
        return min(100, level)
