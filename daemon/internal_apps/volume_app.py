import math
import os
import re
import struct
import tempfile
import time
import wave
from dataclasses import dataclass

from daemon_models import AppRecord


VOLUME_APP_ID = "whisplay-volume"


@dataclass
class VolumeViewState:
    selected_index: int = 0
    busy: bool = False
    status: str = "Loading..."
    last_refresh_at: float = 0.0
    current_percent: int = 0
    current_value: int = 0
    max_value: int = 230
    control_name: str = "Playback"
    card: str = ""


class VolumeInternalApp:
    OPTIONS = [100, 80, 60, 40, 20, 0]
    CURVE = [
        (0, 0),
        (10, 67),
        (20, 85),
        (30, 96),
        (40, 103),
        (50, 109),
        (60, 114),
        (70, 118),
        (80, 121),
        (90, 124),
        (100, 127),
    ]
    PREVIEW_FREQUENCIES = (740.0, 980.0)
    PREVIEW_DURATION_SEC = 0.10

    def __init__(self, lock, mark_dirty, run_command, spawn_worker, request_exit):
        self._lock = lock
        self._mark_dirty = mark_dirty
        self._run_command = run_command
        self._spawn_worker = spawn_worker
        self._request_exit = request_exit
        self.state = VolumeViewState()

    def builtin_app(self) -> AppRecord:
        return AppRecord(
            app_id=VOLUME_APP_ID,
            display_name="Volume",
            icon="VO",
            exit_gesture="",
            priority=180,
            persist=False,
        )

    def activate(self):
        with self._lock:
            self.state.selected_index = min(self.state.selected_index, len(self.OPTIONS))

    def handle_button(self, is_long_press: bool):
        with self._lock:
            total = len(self.OPTIONS) + 1
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
        percent = self.OPTIONS[selected_index - 1]
        with self._lock:
            self.state.busy = True
            self.state.status = f"Setting volume to {percent}%"
        self._mark_dirty()
        self._spawn_worker("volume-action", lambda: self._set_volume_percent(percent))

    def handle_keyboard_action(self, action: str):
        with self._lock:
            total = len(self.OPTIONS) + 1
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
            items = [{"title": "Back", "meta": "Return to desktop"}]
            current_option = self._nearest_option(self.state.current_percent)
            for percent in self.OPTIONS:
                items.append({"title": f"{percent}%", "meta": "current" if percent == current_option else "set level"})
            return {
                "kind": "list",
                "title": "Volume",
                "subtitle": f"Current {self.state.current_percent}%",
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
        self._spawn_worker("volume-refresh", self._refresh)

    def _find_wm8960_card(self) -> str:
        try:
            with open("/proc/asound/cards", "r", encoding="utf-8") as fp:
                fallback = ""
                for raw_line in fp:
                    line = raw_line.strip()
                    lower = line.lower()
                    if "whisplaysound" in lower:
                        parts = line.split()
                        if parts:
                            return parts[0]
                    if not fallback and ("wm8960" in lower or "es8389" in lower):
                        parts = line.split()
                        if parts:
                            fallback = parts[0]
                if fallback:
                    return fallback
        except Exception:
            return ""
        return ""

    def _detect_volume_control(self) -> tuple[str, str, int]:
        card = self._find_wm8960_card()
        if not card:
            raise RuntimeError("Whisplay sound card not found")
        raw_controls_result = self._run_command(["amixer", "-c", card, "controls"], timeout=5.0)
        if raw_controls_result.returncode == 0 and "name='speaker'" in (raw_controls_result.stdout or ""):
            control_name = "name=speaker"
            result = self._run_command(["amixer", "-c", card, "cget", control_name], timeout=5.0)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "amixer cget failed").strip())
            max_value = 100
            unified_limits = re.search(r"min=0,\s*max=(\d+)", result.stdout or "")
            if unified_limits:
                try:
                    max_value = int(unified_limits.group(1))
                except ValueError:
                    max_value = 100
            return card, control_name, max(1, max_value)

        controls_result = self._run_command(["amixer", "-c", card, "scontrols"], timeout=5.0)
        if controls_result.returncode != 0:
            raise RuntimeError((controls_result.stderr or controls_result.stdout or "amixer scontrols failed").strip())
        controls = controls_result.stdout or ""
        control_name = ""
        for candidate in ("Speaker", "Playback"):
            if f"Simple mixer control '{candidate}'" in controls:
                control_name = candidate
                break
        if not control_name:
            raise RuntimeError("No supported volume control found")
        result = self._run_command(["amixer", "-c", card, "get", control_name], timeout=5.0)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "amixer get failed").strip())
        output = result.stdout or ""
        max_value = 127
        limits_match = re.search(r"Limits:\s+Playback\s+(\d+)\s*-\s*(\d+)", output)
        if limits_match:
            try:
                min_value = int(limits_match.group(1))
                max_value = int(limits_match.group(2))
                if min_value != 0:
                    max_value -= min_value
            except ValueError:
                max_value = 127
        return card, control_name, max(1, max_value)

    def _read_current_value(self, card: str, control_name: str) -> int:
        command = ["amixer", "-c", card, "cget", control_name] if control_name.startswith("name=") else ["amixer", "-c", card, "get", control_name]
        result = self._run_command(command, timeout=5.0)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "amixer read failed").strip())
        output = result.stdout or ""
        match = re.search(r":\s*values=(\d+)", output)
        if match:
            return int(match.group(1))
        match = re.search(r"Playback\s+(\d+)\s+\[(\d+)%\]", output)
        if match:
            return int(match.group(1))
        match = re.search(r"\[(\d+)%\]", output)
        if match:
            return int(round(self.state.max_value * int(match.group(1)) / 100.0))
        raise RuntimeError("Unable to parse current volume")

    def _curve_percent_to_base_value(self, percent: int) -> float:
        if percent <= self.CURVE[0][0]:
            return float(self.CURVE[0][1])
        if percent >= self.CURVE[-1][0]:
            return float(self.CURVE[-1][1])
        for index in range(len(self.CURVE) - 1):
            percent1, value1 = self.CURVE[index]
            percent2, value2 = self.CURVE[index + 1]
            if percent1 <= percent <= percent2:
                ratio = (percent - percent1) / float(percent2 - percent1)
                return value1 + (value2 - value1) * ratio
        return 0.0

    def _base_value_to_curve_percent(self, value: float) -> int:
        if value <= self.CURVE[0][1]:
            return self.CURVE[0][0]
        if value >= self.CURVE[-1][1]:
            return self.CURVE[-1][0]
        for index in range(len(self.CURVE) - 1):
            percent1, value1 = self.CURVE[index]
            percent2, value2 = self.CURVE[index + 1]
            if value1 <= value <= value2:
                span = value2 - value1
                if span <= 0:
                    return percent1
                ratio = (value - value1) / float(span)
                return int(round(percent1 + (percent2 - percent1) * ratio))
        return 0

    def _curve_percent_to_device_value(self, percent: int, max_value: int) -> int:
        return max(0, min(max_value, int(round(self._curve_percent_to_base_value(max(0, min(100, percent))) * max_value / 127.0))))

    def _device_value_to_curve_percent(self, value: int, max_value: int) -> int:
        normalized = max(0.0, min(127.0, 127.0 * value / float(max_value)))
        return max(0, min(100, self._base_value_to_curve_percent(normalized)))

    def _is_unified_control(self, control_name: str) -> bool:
        return control_name.startswith("name=")

    def _nearest_option(self, percent: int) -> int:
        return min(self.OPTIONS, key=lambda candidate: (abs(candidate - percent), -candidate))

    def _write_preview_tone(self, path: str, frequency: float, duration_sec: float, volume: float):
        sample_rate = 22050
        total_samples = max(1, int(sample_rate * duration_sec))
        with wave.open(path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            frames = bytearray()
            for index in range(total_samples):
                t = index / sample_rate
                phase = 2.0 * math.pi * frequency * t
                fade_in = min(1.0, index / max(1, int(sample_rate * 0.015)))
                fade_out = min(1.0, (total_samples - index) / max(1, int(sample_rate * 0.035)))
                value = int(32767 * volume * min(fade_in, fade_out) * math.sin(phase))
                frames.extend(struct.pack("<h", value))
            wav_file.writeframes(frames)

    def _play_preview(self):
        path = os.path.join(tempfile.gettempdir(), "whisplay-volume-preview.wav")
        try:
            self._write_preview_tone(path, self.PREVIEW_FREQUENCIES[0], self.PREVIEW_DURATION_SEC, 0.34)
            self._run_command(["aplay", "-q", path], timeout=4.0)
            self._write_preview_tone(path, self.PREVIEW_FREQUENCIES[1], self.PREVIEW_DURATION_SEC, 0.28)
            self._run_command(["aplay", "-q", path], timeout=4.0)
        except Exception:
            return

    def _refresh(self):
        with self._lock:
            self.state.busy = True
            self.state.status = "Reading volume..."
        self._mark_dirty()
        card, control_name, max_value = self._detect_volume_control()
        current_value = self._read_current_value(card, control_name)
        if self._is_unified_control(control_name):
            current_percent = max(0, min(100, current_value))
        else:
            current_percent = self._device_value_to_curve_percent(current_value, max_value)
        with self._lock:
            self.state.card = card
            self.state.control_name = control_name
            self.state.max_value = max_value
            self.state.current_value = current_value
            self.state.current_percent = current_percent
            self.state.selected_index = self.OPTIONS.index(self._nearest_option(current_percent)) + 1
            self.state.busy = False
            self.state.last_refresh_at = time.time()
            self.state.status = f"{control_name} on card {card}"
        self._mark_dirty()

    def _set_volume_percent(self, percent: int):
        card, control_name, max_value = self._detect_volume_control()
        if self._is_unified_control(control_name):
            target_value = max(0, min(max_value, percent))
        else:
            target_value = self._curve_percent_to_device_value(percent, max_value)
        command = ["amixer", "-c", card, "cset", control_name, str(target_value)] if control_name.startswith("name=") else ["amixer", "-c", card, "set", control_name, str(target_value)]
        result = self._run_command(command, timeout=8.0)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "amixer write failed").strip())
        self._refresh()
        self._play_preview()
        with self._lock:
            self.state.status = f"Volume set to {percent}%"
            self.state.busy = False
            self.state.last_refresh_at = time.time()
        self._mark_dirty()
