from daemon_pisugar import PiSugarManager


class StatusPoller:
    def __init__(self, pisugar: PiSugarManager):
        self.pisugar = pisugar
        self.wifi_signal_level: int | None = None
        self.battery_level: int | None = None

    def probe_wifi_signal_level(self) -> int | None:
        try:
            with open("/proc/net/wireless", "r", encoding="utf-8") as fp:
                lines = fp.read().splitlines()
        except Exception:
            return None
        for line in lines[2:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            quality_text = parts[2].rstrip(".")
            try:
                quality = float(quality_text)
            except ValueError:
                continue
            if quality <= 0:
                continue
            if quality >= 55:
                return 3
            if quality >= 35:
                return 2
            return 1
        return None

    def refresh(self) -> bool:
        wifi_signal_level = self.probe_wifi_signal_level()
        battery_level = self.pisugar.probe_battery_level()
        changed = (
            wifi_signal_level != self.wifi_signal_level
            or battery_level != self.battery_level
        )
        self.wifi_signal_level = wifi_signal_level
        self.battery_level = battery_level
        return changed
