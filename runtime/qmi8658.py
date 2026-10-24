from __future__ import annotations

import math
import struct
import time
from dataclasses import dataclass


QMI8658_DEFAULT_BUS = 1
QMI8658_DEFAULT_ADDRESS = 0x6B
QMI8658_CHIP_ID = 0x05


@dataclass(frozen=True)
class ImuSample:
    acceleration_g: tuple[float, float, float]
    angular_rate_dps: tuple[float, float, float]
    timestamp: float


class QMI8658:
    REG_WHO_AM_I = 0x00
    REG_CTRL1 = 0x02
    REG_CTRL2 = 0x03
    REG_CTRL3 = 0x04
    REG_CTRL5 = 0x06
    REG_CTRL7 = 0x08
    REG_DATA_START = 0x35

    # +/-4 g and +/-512 dps.
    ACCEL_SCALE = 8192.0
    GYRO_SCALE = 64.0

    def __init__(self, bus_number=QMI8658_DEFAULT_BUS, address=QMI8658_DEFAULT_ADDRESS, bus=None):
        self.bus_number = int(bus_number)
        self.address = int(address)
        self._owns_bus = bus is None
        if bus is None:
            try:
                import smbus  # type: ignore
            except ImportError:
                import smbus2 as smbus  # type: ignore
            bus = smbus.SMBus(self.bus_number)
        self.bus = bus
        self._saved_controls: dict[int, int] = {}
        chip_id = self.bus.read_byte_data(self.address, self.REG_WHO_AM_I)
        if chip_id != QMI8658_CHIP_ID:
            self.close()
            raise RuntimeError(
                f"QMI8658 not found at I2C 0x{self.address:02x} "
                f"(chip id 0x{chip_id:02x})"
            )
        for register in (
            self.REG_CTRL1,
            self.REG_CTRL2,
            self.REG_CTRL3,
            self.REG_CTRL5,
            self.REG_CTRL7,
        ):
            self._saved_controls[register] = self.bus.read_byte_data(self.address, register)
        self._configure()

    def _write(self, register: int, value: int):
        self.bus.write_byte_data(self.address, register, value)

    def _configure(self):
        self._write(self.REG_CTRL7, 0x00)
        self._write(self.REG_CTRL1, 0x40)  # address auto-increment, little endian
        self._write(self.REG_CTRL2, 0x16)  # +/-4 g, 125 Hz
        self._write(self.REG_CTRL3, 0x56)  # +/-512 dps, 112.1 Hz
        self._write(self.REG_CTRL5, 0x11)  # accel and gyro low-pass filters
        self._write(self.REG_CTRL7, 0x03)  # enable accel and gyro
        # The gyro itself starts quickly, but the enabled low-pass filters can
        # expose a decaying startup transient for several output periods.
        time.sleep(1.0)

    def read(self) -> ImuSample:
        data = self.bus.read_i2c_block_data(self.address, self.REG_DATA_START, 12)
        if len(data) != 12:
            raise RuntimeError(f"short QMI8658 sample: {len(data)} bytes")
        values = struct.unpack("<hhhhhh", bytes(data))
        acceleration = tuple(value / self.ACCEL_SCALE for value in values[:3])
        angular_rate = tuple(value / self.GYRO_SCALE for value in values[3:])
        return ImuSample(acceleration, angular_rate, time.monotonic())

    def close(self):
        if getattr(self, "bus", None) is None:
            return
        if self._saved_controls:
            try:
                self._write(self.REG_CTRL7, 0x00)
                for register in (
                    self.REG_CTRL1,
                    self.REG_CTRL2,
                    self.REG_CTRL3,
                    self.REG_CTRL5,
                ):
                    self._write(register, self._saved_controls[register])
                self._write(self.REG_CTRL7, self._saved_controls[self.REG_CTRL7])
            except Exception:
                pass
            self._saved_controls.clear()
        if self._owns_bus:
            try:
                self.bus.close()
            except Exception:
                pass
        self.bus = None

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        self.close()


class ComplementaryOrientation:
    def __init__(self, alpha=0.96):
        self.alpha = float(alpha)
        self.roll = 0.0
        self.pitch = 0.0
        self.yaw = 0.0
        self._last_timestamp: float | None = None
        self._gyro_bias = (0.0, 0.0, 0.0)

    def set_gyro_bias(self, samples: list[ImuSample]):
        if not samples:
            return
        count = float(len(samples))
        self._gyro_bias = tuple(
            sum(sample.angular_rate_dps[axis] for sample in samples) / count
            for axis in range(3)
        )

    def update(self, sample: ImuSample) -> tuple[float, float, float]:
        ax, ay, az = sample.acceleration_g
        gx, gy, gz = (
            sample.angular_rate_dps[index] - self._gyro_bias[index]
            for index in range(3)
        )
        accel_roll = math.degrees(math.atan2(ay, math.sqrt(ax * ax + az * az)))
        accel_pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az)))
        if self._last_timestamp is None:
            self.roll, self.pitch = accel_roll, accel_pitch
        else:
            dt = min(0.1, max(0.001, sample.timestamp - self._last_timestamp))
            self.roll = self.alpha * (self.roll + gx * dt) + (1.0 - self.alpha) * accel_roll
            self.pitch = self.alpha * (self.pitch + gy * dt) + (1.0 - self.alpha) * accel_pitch
            self.yaw = (self.yaw + gz * dt) % 360.0
        self._last_timestamp = sample.timestamp
        return self.roll, self.pitch, self.yaw
