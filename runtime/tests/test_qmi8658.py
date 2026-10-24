from __future__ import annotations

import os
import sys
import tempfile
import unittest


RUNTIME_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from qmi8658 import QMI8658
from whisplay_model import DEFAULT_MODEL, WHISPLAY_JOY_MODEL, detect_hat_model


class FakeBus:
    def __init__(self):
        self.registers = {
            QMI8658.REG_WHO_AM_I: 0x05,
            QMI8658.REG_CTRL1: 0x11,
            QMI8658.REG_CTRL2: 0x22,
            QMI8658.REG_CTRL3: 0x33,
            QMI8658.REG_CTRL5: 0x55,
            QMI8658.REG_CTRL7: 0x77,
        }
        self.writes = []
        self.sample = [0x00, 0x20, 0x00, 0xE0, 0x00, 0x00, 0x00, 0x40, 0x00, 0xC0, 0x00, 0x00]

    def read_byte_data(self, _address, register):
        return self.registers.get(register, 0)

    def write_byte_data(self, _address, register, value):
        self.registers[register] = value
        self.writes.append((register, value))

    def read_i2c_block_data(self, _address, register, length):
        assert register == QMI8658.REG_DATA_START
        return self.sample[:length]


class ModelDetectionTests(unittest.TestCase):
    def test_detects_joy_product_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            vendor = os.path.join(temp_dir, "vendor")
            product_id = os.path.join(temp_dir, "product_id")
            with open(vendor, "wb") as fp:
                fp.write(b"PiSugar\0")
            with open(product_id, "wb") as fp:
                fp.write(b"0x0003\0")
            self.assertEqual(detect_hat_model(vendor, product_id), WHISPLAY_JOY_MODEL)

    def test_unknown_vendor_falls_back_to_original_model(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            vendor = os.path.join(temp_dir, "vendor")
            product_id = os.path.join(temp_dir, "product_id")
            with open(vendor, "wb") as fp:
                fp.write(b"Other\0")
            with open(product_id, "wb") as fp:
                fp.write(b"0x0003\0")
            self.assertEqual(detect_hat_model(vendor, product_id), DEFAULT_MODEL)


class QMI8658Tests(unittest.TestCase):
    def test_configures_and_scales_six_axis_sample(self):
        bus = FakeBus()
        imu = QMI8658(bus=bus)
        sample = imu.read()
        self.assertEqual(sample.acceleration_g, (1.0, -1.0, 0.0))
        self.assertEqual(sample.angular_rate_dps, (256.0, -256.0, 0.0))
        self.assertIn((QMI8658.REG_CTRL7, 0x03), bus.writes)
        imu.close()
        self.assertEqual(bus.registers[QMI8658.REG_CTRL7], 0x77)


if __name__ == "__main__":
    unittest.main()
