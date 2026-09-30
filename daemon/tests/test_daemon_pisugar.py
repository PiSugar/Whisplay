import os
import sys
import types
import unittest
from unittest import mock


DAEMON_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if DAEMON_DIR not in sys.path:
    sys.path.insert(0, DAEMON_DIR)

from daemon_pisugar import PiSugarManager


class FakeSMBus:
    def __init__(self, bus_number, values=None):
        self.bus_number = bus_number
        self.values = list(values or [0])
        self.closed = False

    def read_byte_data(self, address, register):
        self.last_read = (address, register)
        if len(self.values) > 1:
            return self.values.pop(0)
        return self.values[0]

    def close(self):
        self.closed = True


class PiSugar3PowerButtonTests(unittest.TestCase):
    def test_detects_pisugar3_from_power_button_status_register(self):
        bus = FakeSMBus(1)
        smbus = types.SimpleNamespace(SMBus=lambda number: bus)
        manager = PiSugarManager()

        with mock.patch.dict(sys.modules, {"smbus": smbus}):
            self.assertTrue(manager.detect_pisugar3())

        self.assertEqual(bus.bus_number, 1)
        self.assertEqual(bus.last_read, (0x57, 0x02))
        self.assertTrue(manager.pisugar3_power_button_available)

    def test_short_power_button_press_returns_single_click(self):
        manager = PiSugarManager()
        manager.pisugar3_bus = FakeSMBus(1, [0x00, 0x01, 0x00])
        manager.pisugar3_power_button_exit_enabled = True
        manager.pisugar3_power_button_available = True

        self.assertFalse(manager.poll_pisugar3_power_button_single(1.00))
        self.assertFalse(manager.poll_pisugar3_power_button_single(1.10))
        self.assertTrue(manager.poll_pisugar3_power_button_single(1.50))

    def test_long_power_button_press_is_not_a_single_click(self):
        manager = PiSugarManager()
        manager.pisugar3_bus = FakeSMBus(1, [0x00, 0x01, 0x01, 0x00])
        manager.pisugar3_power_button_exit_enabled = True
        manager.pisugar3_power_button_available = True

        self.assertFalse(manager.poll_pisugar3_power_button_single(1.00))
        self.assertFalse(manager.poll_pisugar3_power_button_single(1.10))
        self.assertFalse(manager.poll_pisugar3_power_button_single(1.80))
        self.assertFalse(manager.poll_pisugar3_power_button_single(1.90))

    def test_disabled_integration_does_not_consume_power_button(self):
        manager = PiSugarManager()
        manager.pisugar3_bus = FakeSMBus(1, [0x01, 0x00])
        manager.pisugar3_power_button_available = True

        self.assertFalse(manager.poll_pisugar3_power_button_single(1.00))
        self.assertFalse(manager.poll_pisugar3_power_button_single(1.10))
        self.assertFalse(hasattr(manager.pisugar3_bus, "last_read"))


if __name__ == "__main__":
    unittest.main()
