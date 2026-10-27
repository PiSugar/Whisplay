from __future__ import annotations

import math
import os
import random
import struct
import sys
import tempfile
import threading
import time
import wave
from dataclasses import dataclass

import pygame
from PIL import Image, ImageDraw, ImageFont

current_dir = os.path.dirname(os.path.abspath(__file__))
runtime_dir = os.path.abspath(os.path.join(current_dir, "..", "runtime"))
if runtime_dir not in sys.path:
    sys.path.append(runtime_dir)

from pillow_compat import draw_rounded_rectangle
from whisplay_client import create_whisplay_hardware


LOGICAL_WIDTH = 280
LOGICAL_HEIGHT = 240
SCREEN_WIDTH = 240
SCREEN_HEIGHT = 280
TARGET_FPS = 18
HORIZON_Y = 57
ROAD_BOTTOM_Y = 240
HUD_Y = 13
FOCAL_LENGTH = 185.0
ROAD_HALF_WIDTH = 4.6
SCENE_FOG_COLOR = (218, 222, 207)
CAR_FOG_COLOR = (112, 122, 126)
FOG_START = 12.0
FOG_END = 260.0
MAX_SPEED = 190.0
ACCELERATION = 52.0
BRAKE_FORCE = 135.0
RACE_DURATION = 60.0
WHISPLAY_JOY_MODEL = "whisplay-joy"

DIRECTION_VECTOR = {
    "up": (0.0, -1.0),
    "right": (1.0, 0.0),
    "down": (0.0, 1.0),
    "left": (-1.0, 0.0),
}

# The LCD framebuffer is rotated clockwise into landscape orientation.
ROTATED_CONTROL_DIRECTION = {
    "up": "left",
    "right": "up",
    "down": "right",
    "left": "down",
}


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def track_world_x(distance: float) -> float:
    return 13.0 * math.sin(distance / 155.0) + 5.5 * math.sin(distance / 61.0)


def track_world_height(distance: float) -> float:
    return 1.7 * math.sin(distance / 137.0) + 0.65 * math.sin(distance / 53.0)


def track_heading(distance: float) -> float:
    delta = track_world_x(distance + 1.0) - track_world_x(distance - 1.0)
    return math.atan2(delta, 2.0)


def fused_gyro_steering(
    lateral_acceleration_g: float,
    neutral_acceleration_g: float,
    angular_rate_dps: float,
) -> float:
    tilt = clamp((lateral_acceleration_g - neutral_acceleration_g) / 0.42, -1.0, 1.0)
    turn_rate = clamp(angular_rate_dps / 115.0, -1.0, 1.0)
    # The sensor axes are mirrored after the LCD is rotated into landscape.
    # Reverse only motion steering; joystick directions are mapped separately.
    return -clamp(tilt * 0.82 + turn_rate * 0.18, -1.0, 1.0)


def synthesize_tone(
    path: str,
    start_frequency: float,
    end_frequency: float,
    duration: float,
    volume: float,
    pulse: bool = False,
):
    sample_rate = 22050
    sample_count = max(1, int(sample_rate * duration))
    phase = 0.0
    frames = bytearray()
    for index in range(sample_count):
        progress = index / sample_count
        frequency = start_frequency + (end_frequency - start_frequency) * progress
        phase += 2.0 * math.pi * frequency / sample_rate
        raw = math.sin(phase)
        if pulse:
            raw = 1.0 if raw >= 0 else -1.0
        attack = min(1.0, index / max(1, int(sample_rate * 0.008)))
        release = min(1.0, (sample_count - index) / max(1, int(sample_rate * 0.05)))
        sample = int(32767 * volume * min(attack, release) * raw)
        frames.extend(struct.pack("<h", sample))
    with wave.open(path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(frames)


class SoundEffects:
    def __init__(self):
        self.enabled = False
        self.sounds: dict[str, pygame.mixer.Sound] = {}
        self.engine_channel = None
        try:
            pygame.mixer.pre_init(22050, size=-16, channels=1)
            pygame.mixer.init()
            pygame.mixer.set_reserved(1)
            self._make("engine", 110, 135, 0.24, 0.14, pulse=True)
            self._make("start", 360, 920, 0.20, 0.30)
            self._make("nitro", 420, 1450, 0.24, 0.36, pulse=True)
            self._make("pass", 880, 1120, 0.09, 0.23)
            self._make("crash", 180, 55, 0.34, 0.52, pulse=True)
            self._make("finish", 520, 1180, 0.30, 0.34)
            self.engine_channel = pygame.mixer.Channel(0)
            self.enabled = True
        except Exception as exc:
            print(f"Gyro Racer audio disabled: {exc}", file=sys.stderr)

    def _make(self, name, start, end, duration, volume, pulse=False):
        path = os.path.join(tempfile.gettempdir(), f"whisplay-racer-{name}.wav")
        synthesize_tone(path, start, end, duration, volume, pulse=pulse)
        self.sounds[name] = pygame.mixer.Sound(path)

    def play(self, name: str):
        if self.enabled and name in self.sounds:
            self.sounds[name].play()

    def set_engine(self, running: bool, speed_ratio: float = 0.0):
        if not self.enabled or self.engine_channel is None:
            return
        if running:
            self.engine_channel.set_volume(0.12 + clamp(speed_ratio, 0.0, 1.0) * 0.16)
            if not self.engine_channel.get_busy():
                self.engine_channel.play(self.sounds["engine"], loops=-1)
        else:
            self.engine_channel.stop()

    def cleanup(self):
        if self.enabled:
            try:
                pygame.mixer.quit()
            except Exception:
                pass


@dataclass
class TrafficCar:
    distance: float
    lane: float
    speed: float
    color: tuple[int, int, int]
    passed: bool = False


@dataclass
class ExplosionParticle:
    x: float
    y: float
    z: float
    vx: float
    vy: float
    vz: float
    age: float
    lifetime: float
    size: float
    smoke: bool = False


def image_to_rgb565_bytes(image: Image.Image) -> bytes:
    try:
        import numpy as np

        pixels = np.asarray(image, dtype=np.uint16)
        values = (
            ((pixels[:, :, 0] & 0xF8) << 8)
            | ((pixels[:, :, 1] & 0xFC) << 3)
            | (pixels[:, :, 2] >> 3)
        ).astype(">u2")
        return values.tobytes()
    except ImportError:
        frame = bytearray()
        for red, green, blue in image.getdata():
            value = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
            frame.extend(((value >> 8) & 0xFF, value & 0xFF))
        return bytes(frame)


class GyroRacerGame:
    def __init__(self):
        self.board = create_whisplay_hardware(
            app_id=os.getenv("WHISPLAY_APP_ID", "whisplay-gyro-racer"),
            display_name="Gyro Racer",
            icon="GR",
            exit_gesture="none",
            priority=28,
            use_daemon_default_log=True,
            supported_models=(WHISPLAY_JOY_MODEL,),
        )
        if self.board.get_model() != WHISPLAY_JOY_MODEL:
            self.board.cleanup()
            raise RuntimeError("Gyro Racer requires whisplay-joy")
        self.board.set_backlight(100)
        self.running = True
        self.lock = threading.RLock()
        self.sounds = SoundEffects()
        self.font = ImageFont.load_default()
        self.rng = random.Random()
        self.state = "ready"
        self.menu_open = False
        self.menu_selection = 0
        self.menu_opened_at = 0.0
        self.pressed_directions: set[str] = set()
        self.speed = 0.0
        self.player_lateral = 0.0
        self.distance = 0.0
        self.score = 0
        self._distance_score_fraction = 0.0
        self.race_started_at = 0.0
        self.time_remaining = RACE_DURATION
        self.nitro_until = 0.0
        self.nitro_ready_at = 0.0
        self.traffic: list[TrafficCar] = []
        self.explosion_particles: list[ExplosionParticle] = []
        self.crash_shake_until = 0.0
        self.explosion_flash_until = 0.0
        self.explosion_flash_world: tuple[float, float, float] | None = None
        self.gyro_steer = 0.0
        self.gyro_tilt_x = 0.0
        self.gyro_rate = 0.0
        self.gyro_ready = False
        self.gyro_error = ""
        self._neutral_lateral_acceleration = 0.0
        self._calibration_samples: list[float] = []
        self.frame_index = 0
        self._register_callbacks()
        self._reset_race()
        self._motion_thread = threading.Thread(target=self._motion_loop, daemon=True)
        self._motion_thread.start()

    def _register_callbacks(self):
        self.board.on_button_press(self._on_button_press)
        self.board.on_joystick_press(self._on_direction_press)
        self.board.on_joystick_release(self._on_direction_release)
        if hasattr(self.board, "on_exit_request"):
            self.board.on_exit_request(self._on_exit_request)
        if hasattr(self.board, "on_focus_revoked"):
            self.board.on_focus_revoked(self._on_focus_revoked)

    def _on_exit_request(self):
        with self.lock:
            self.running = False

    def _on_focus_revoked(self, _payload=None):
        with self.lock:
            self.running = False

    def _on_direction_press(self, physical_direction: str):
        with self.lock:
            if physical_direction == "center":
                self._handle_menu_center()
                return
            direction = ROTATED_CONTROL_DIRECTION.get(physical_direction)
            if direction is None:
                return
            if self.menu_open:
                if direction in {"up", "down"}:
                    self.menu_selection = 1 - self.menu_selection
                return
            self.pressed_directions.add(direction)
            if self.state == "ready":
                self._start_race()

    def _on_direction_release(self, physical_direction: str):
        direction = ROTATED_CONTROL_DIRECTION.get(physical_direction)
        if direction is None:
            return
        with self.lock:
            self.pressed_directions.discard(direction)

    def _on_button_press(self):
        with self.lock:
            if self.menu_open:
                return
            now = time.monotonic()
            if self.state in {"ready", "finished"}:
                self._reset_race()
                self._start_race()
                return
            if now >= self.nitro_ready_at:
                self.nitro_until = now + 1.0
                self.nitro_ready_at = now + 5.5
                self.sounds.play("nitro")

    def _handle_menu_center(self):
        if not self.menu_open:
            self.menu_open = True
            self.menu_selection = 0
            self.menu_opened_at = time.monotonic()
            self.pressed_directions.clear()
            self.sounds.set_engine(False)
            return
        if self.menu_selection == 1:
            self.running = False
            return
        self._resume_from_menu()

    def _resume_from_menu(self):
        now = time.monotonic()
        paused_for = max(0.0, now - self.menu_opened_at)
        self.menu_open = False
        self.menu_opened_at = 0.0
        if self.state == "playing":
            self.race_started_at += paused_for
            for attribute in (
                "nitro_until",
                "nitro_ready_at",
                "crash_shake_until",
                "explosion_flash_until",
            ):
                deadline = getattr(self, attribute)
                if deadline > 0.0:
                    setattr(self, attribute, deadline + paused_for)
            self.sounds.set_engine(True, self.speed / MAX_SPEED)

    def _reset_race(self):
        self.state = "ready"
        self.speed = 0.0
        self.player_lateral = 0.0
        self.distance = 0.0
        self.score = 0
        self._distance_score_fraction = 0.0
        self.time_remaining = RACE_DURATION
        self.nitro_until = 0.0
        self.nitro_ready_at = 0.0
        self.explosion_particles = []
        self.crash_shake_until = 0.0
        self.explosion_flash_until = 0.0
        self.explosion_flash_world = None
        colors = ((230, 62, 52), (55, 182, 235), (248, 179, 48), (181, 71, 217))
        self.traffic = [
            TrafficCar(
                distance=115.0 + index * 105.0,
                lane=self.rng.choice((-0.72, -0.28, 0.28, 0.72)),
                speed=self.rng.uniform(55.0, 115.0),
                color=colors[index % len(colors)],
            )
            for index in range(7)
        ]
        self.sounds.set_engine(False)

    def _start_race(self):
        self.state = "playing"
        self.race_started_at = time.monotonic()
        self.sounds.play("start")
        self.sounds.set_engine(True, 0.0)

    def _motion_loop(self):
        while True:
            with self.lock:
                if not self.running:
                    return
            try:
                sample = self.board.read_motion()
                lateral_acceleration = float(sample.acceleration_g[1])
                forward_acceleration = float(sample.acceleration_g[0])
                angular_rate = float(sample.angular_rate_dps[2])
                with self.lock:
                    if len(self._calibration_samples) < 18:
                        self._calibration_samples.append(lateral_acceleration)
                        self._neutral_lateral_acceleration = sum(
                            self._calibration_samples
                        ) / len(self._calibration_samples)
                        self.gyro_ready = len(self._calibration_samples) >= 18
                    self.gyro_steer = fused_gyro_steering(
                        lateral_acceleration,
                        self._neutral_lateral_acceleration,
                        angular_rate,
                    )
                    self.gyro_tilt_x = clamp(forward_acceleration / 0.55, -1.0, 1.0)
                    self.gyro_rate = clamp(angular_rate / 180.0, -1.0, 1.0)
                    self.gyro_error = ""
            except Exception as exc:
                with self.lock:
                    self.gyro_ready = False
                    self.gyro_error = str(exc)
            time.sleep(0.035)

    def _combined_steering(self) -> float:
        joystick = 0.0
        if "left" in self.pressed_directions:
            joystick -= 1.0
        if "right" in self.pressed_directions:
            joystick += 1.0
        gyro = self.gyro_steer if self.gyro_ready else 0.0
        return clamp(joystick * 0.78 + gyro * 0.68, -1.0, 1.0)

    def _respawn_traffic(self, car: TrafficCar, minimum_distance: float = 390.0):
        car.distance = self.rng.uniform(minimum_distance, minimum_distance + 240.0)
        car.lane = self.rng.choice((-0.72, -0.28, 0.28, 0.72))
        car.speed = self.rng.uniform(58.0, 120.0)
        car.passed = False

    def _spawn_explosion(self, world_x: float, world_y: float, world_z: float):
        now = time.monotonic()
        self.crash_shake_until = now + 0.55
        self.explosion_flash_until = now + 0.20
        self.explosion_flash_world = (world_x, world_y + 0.8, world_z)
        for _index in range(30):
            self.explosion_particles.append(
                ExplosionParticle(
                    x=world_x + self.rng.uniform(-0.5, 0.5),
                    y=world_y + self.rng.uniform(0.25, 1.0),
                    z=world_z + self.rng.uniform(-0.8, 0.8),
                    vx=self.rng.uniform(-5.5, 5.5),
                    vy=self.rng.uniform(2.0, 7.5),
                    vz=self.rng.uniform(-4.5, 4.5),
                    age=0.0,
                    lifetime=self.rng.uniform(0.35, 0.9),
                    size=self.rng.uniform(0.10, 0.28),
                )
            )
        for _index in range(11):
            self.explosion_particles.append(
                ExplosionParticle(
                    x=world_x + self.rng.uniform(-0.45, 0.45),
                    y=world_y + self.rng.uniform(0.4, 1.1),
                    z=world_z + self.rng.uniform(-0.65, 0.65),
                    vx=self.rng.uniform(-1.4, 1.4),
                    vy=self.rng.uniform(1.0, 3.2),
                    vz=self.rng.uniform(-1.3, 1.3),
                    age=0.0,
                    lifetime=self.rng.uniform(0.75, 1.45),
                    size=self.rng.uniform(0.22, 0.48),
                    smoke=True,
                )
            )

    def _update_explosion(self, dt: float):
        alive = []
        for particle in self.explosion_particles:
            particle.age += dt
            if particle.age >= particle.lifetime:
                continue
            particle.x += particle.vx * dt
            particle.y += particle.vy * dt
            particle.z += particle.vz * dt
            particle.vy -= (2.0 if particle.smoke else 8.5) * dt
            particle.vx *= 0.985
            particle.vz *= 0.985
            alive.append(particle)
        self.explosion_particles = alive

    def _update_traffic(self, dt: float):
        for car in self.traffic:
            car.distance -= (self.speed - car.speed) * dt
            if -12.0 < car.distance < 17.0 and abs(car.lane - self.player_lateral) < 0.34:
                world_z = self.distance + car.distance
                world_x = track_world_x(world_z) + car.lane * ROAD_HALF_WIDTH
                self._spawn_explosion(world_x, track_world_height(world_z), world_z)
                self.speed *= 0.32
                self.score = max(0, self.score - 150)
                self.sounds.play("crash")
                self._respawn_traffic(car, 430.0)
                continue
            if car.distance < -16.0:
                self.score += 100
                self.sounds.play("pass")
                self._respawn_traffic(car)
            elif car.distance > 720.0:
                self._respawn_traffic(car, 330.0)

    def _update(self, now: float, dt: float):
        if self.menu_open:
            return
        self._update_explosion(dt)
        if self.state != "playing":
            return
        self.time_remaining = max(0.0, RACE_DURATION - (now - self.race_started_at))
        if self.time_remaining <= 0:
            self.state = "finished"
            self.speed = 0.0
            self.sounds.set_engine(False)
            self.sounds.play("finish")
            return

        braking = "down" in self.pressed_directions
        if braking:
            self.speed -= BRAKE_FORCE * dt
        else:
            self.speed += ACCELERATION * dt
        if now < self.nitro_until:
            self.speed += 118.0 * dt
        speed_limit = MAX_SPEED + (38.0 if now < self.nitro_until else 0.0)
        self.speed = clamp(self.speed, 0.0, speed_limit)

        steering = self._combined_steering()
        steering_response = 0.42 + self.speed / MAX_SPEED
        self.player_lateral += steering * steering_response * dt
        self.player_lateral = clamp(self.player_lateral, -1.28, 1.28)
        if abs(self.player_lateral) > 1.0:
            self.speed = max(0.0, self.speed - 92.0 * dt)

        self.distance += self.speed * dt
        self._distance_score_fraction += self.speed * dt * 0.08
        distance_points = int(self._distance_score_fraction)
        self.score += distance_points
        self._distance_score_fraction -= distance_points
        self._update_traffic(dt)
        self.sounds.set_engine(True, self.speed / MAX_SPEED)

    def _setup_camera(self, now: float):
        camera_z = self.distance - 6.8
        player_x = track_world_x(self.distance) + self.player_lateral * ROAD_HALF_WIDTH
        camera_x = track_world_x(camera_z) + (player_x - track_world_x(self.distance)) * 0.58
        camera_y = track_world_height(camera_z) + 3.25
        target_z = self.distance + 21.0
        target_y = track_world_height(target_z) + 0.35
        self._camera_x = camera_x
        self._camera_y = camera_y
        self._camera_z = camera_z
        self._camera_yaw = track_heading(self.distance + 9.0)
        self._camera_pitch = math.atan2(target_y - camera_y, target_z - camera_z)
        if now < self.crash_shake_until:
            strength = clamp((self.crash_shake_until - now) / 0.55, 0.0, 1.0)
            self._camera_shake_x = math.sin(self.frame_index * 4.7) * 4.0 * strength
            self._camera_shake_y = math.cos(self.frame_index * 5.3) * 3.0 * strength
        else:
            self._camera_shake_x = 0.0
            self._camera_shake_y = 0.0

    def _project_world(self, point):
        world_x, world_y, world_z = point
        dx = world_x - self._camera_x
        dy = world_y - self._camera_y
        dz = world_z - self._camera_z
        cosine_yaw = math.cos(self._camera_yaw)
        sine_yaw = math.sin(self._camera_yaw)
        view_x = dx * cosine_yaw - dz * sine_yaw
        forward = dx * sine_yaw + dz * cosine_yaw
        cosine_pitch = math.cos(self._camera_pitch)
        sine_pitch = math.sin(self._camera_pitch)
        view_y = dy * cosine_pitch - forward * sine_pitch
        view_z = dy * sine_pitch + forward * cosine_pitch
        if view_z <= 1.1:
            return None
        screen_x = LOGICAL_WIDTH / 2 + view_x * FOCAL_LENGTH / view_z + self._camera_shake_x
        screen_y = HORIZON_Y - view_y * FOCAL_LENGTH / view_z + self._camera_shake_y
        return screen_x, screen_y, view_z

    def _project_face(self, vertices):
        projected = [self._project_world(vertex) for vertex in vertices]
        if any(point is None for point in projected):
            return None
        return (
            tuple((point[0], point[1]) for point in projected),
            sum(point[2] for point in projected) / len(projected),
        )

    def _draw_world_face(self, draw, vertices, color, outline=None):
        projected = self._project_face(vertices)
        if projected is None:
            return
        points, depth = projected
        fill_color = self._distance_color(color, depth)
        outline_color = self._distance_color(outline, depth) if outline is not None else None
        draw.polygon(points, fill=fill_color, outline=outline_color)

    @staticmethod
    def _shade(color, factor: float):
        return tuple(int(clamp(channel * factor, 0, 255)) for channel in color)

    @staticmethod
    def _distance_color(
        color,
        depth: float,
        fog_color=SCENE_FOG_COLOR,
        fog_strength: float = 0.94,
        brightness: float = 1.0,
    ):
        """Lighten distant geometry while preserving useful foreground contrast."""
        base = tuple(clamp(channel * brightness, 0, 255) for channel in color)
        distance_ratio = clamp((depth - FOG_START) / (FOG_END - FOG_START), 0.0, 1.0)
        # Smooth the transition so nearby geometry keeps its original palette.
        distance_ratio = distance_ratio * distance_ratio * (3.0 - 2.0 * distance_ratio)
        amount = distance_ratio * fog_strength
        return tuple(
            int(base_channel * (1.0 - amount) + fog_channel * amount)
            for base_channel, fog_channel in zip(base, fog_color)
        )

    @staticmethod
    def _local_to_world(center_x, center_z, local_x, local_z, yaw):
        cosine = math.cos(yaw)
        sine = math.sin(yaw)
        return (
            center_x + local_x * cosine + local_z * sine,
            center_z - local_x * sine + local_z * cosine,
        )

    def _box_faces(self, center_x, base_y, center_z, width, height, length, yaw, color):
        local = (
            (-width / 2, -length / 2),
            (width / 2, -length / 2),
            (width / 2, length / 2),
            (-width / 2, length / 2),
        )
        bottom = []
        top = []
        for local_x, local_z in local:
            world_x, world_z = self._local_to_world(
                center_x,
                center_z,
                local_x,
                local_z,
                yaw,
            )
            bottom.append((world_x, base_y, world_z))
            top.append((world_x, base_y + height, world_z))
        vertices = bottom + top
        return (
            ((vertices[0], vertices[1], vertices[5], vertices[4]), self._shade(color, 0.70)),
            ((vertices[1], vertices[2], vertices[6], vertices[5]), self._shade(color, 0.83)),
            ((vertices[2], vertices[3], vertices[7], vertices[6]), self._shade(color, 0.94)),
            ((vertices[3], vertices[0], vertices[4], vertices[7]), self._shade(color, 0.62)),
            ((vertices[4], vertices[5], vertices[6], vertices[7]), self._shade(color, 1.16)),
        )

    def _offset_world(self, center_x, center_z, local_x, local_z, yaw):
        return self._local_to_world(center_x, center_z, local_x, local_z, yaw)

    def _draw_3d_car(self, draw, center_x, base_y, center_z, yaw, color, player=False):
        faces = list(self._box_faces(center_x, base_y + 0.18, center_z, 1.65, 0.60, 3.25, yaw, color))
        cabin_x, cabin_z = self._offset_world(center_x, center_z, 0.0, -0.18, yaw)
        faces.extend(
            self._box_faces(
                cabin_x,
                base_y + 0.76,
                cabin_z,
                1.18,
                0.62,
                1.48,
                yaw,
                (75, 148, 184),
            )
        )
        bumper_x, bumper_z = self._offset_world(center_x, center_z, 0.0, -1.66, yaw)
        faces.extend(
            self._box_faces(
                bumper_x,
                base_y + 0.27,
                bumper_z,
                1.72,
                0.16,
                0.13,
                yaw,
                (36, 39, 44),
            )
        )
        for local_x in (-0.87, 0.87):
            for local_z in (-1.02, 1.02):
                wheel_x, wheel_z = self._offset_world(center_x, center_z, local_x, local_z, yaw)
                faces.extend(
                    self._box_faces(
                        wheel_x,
                        base_y + 0.02,
                        wheel_z,
                        0.28,
                        0.46,
                        0.64,
                        yaw,
                        (20, 22, 24),
                    )
                )
        for local_x in (-0.53, 0.53):
            lamp_x, lamp_z = self._offset_world(center_x, center_z, local_x, -1.72, yaw)
            faces.extend(
                self._box_faces(
                    lamp_x,
                    base_y + 0.46,
                    lamp_z,
                    0.34,
                    0.18,
                    0.09,
                    yaw,
                    (255, 52, 37),
                )
            )
        if player:
            spoiler_x, spoiler_z = self._offset_world(center_x, center_z, 0.0, -1.38, yaw)
            faces.extend(
                self._box_faces(
                    spoiler_x,
                    base_y + 1.05,
                    spoiler_z,
                    1.65,
                    0.12,
                    0.30,
                    yaw,
                    self._shade(color, 0.70),
                )
            )
        projected_faces = []
        for vertices, face_color in faces:
            projected = self._project_face(vertices)
            if projected is not None:
                points, depth = projected
                projected_faces.append((depth, points, face_color))
        for depth, points, face_color in sorted(projected_faces, reverse=True):
            if player:
                fill_color = self._distance_color(face_color, depth, fog_strength=0.45)
                outline_color = (18, 20, 23)
            else:
                # Traffic remains darker than the road and scenery at the same
                # depth, while still getting lighter as it recedes.
                fill_color = self._distance_color(
                    face_color,
                    depth,
                    fog_color=CAR_FOG_COLOR,
                    fog_strength=0.78,
                    brightness=0.55,
                )
                outline_color = self._distance_color(
                    (8, 10, 13),
                    depth,
                    fog_color=(55, 62, 66),
                    fog_strength=0.35,
                )
            draw.polygon(points, fill=fill_color, outline=outline_color)

    def _road_depths(self):
        # The camera sits 6.8 world units behind the player. Start the mesh
        # just beyond its near plane so road and grass reach the bottom edge;
        # starting in front of the player leaves the lower half as flat green.
        depths = [-5.0]
        while depths[-1] < 520.0:
            depth = depths[-1]
            depths.append(depth + 3.0 + depth * 0.105)
        return depths

    def _draw_background(self, draw):
        draw.rectangle((0, 0, LOGICAL_WIDTH, LOGICAL_HEIGHT), fill=(69, 157, 222))
        draw.ellipse((23, 13, 50, 40), fill=(255, 232, 117))
        draw.polygon(((0, 61), (45, 25), (87, 62)), fill=(68, 104, 125))
        draw.polygon(((55, 62), (116, 17), (170, 63)), fill=(57, 91, 116))
        draw.polygon(((135, 63), (211, 24), (280, 62)), fill=(65, 105, 126))
        draw.rectangle((0, 56, LOGICAL_WIDTH, LOGICAL_HEIGHT), fill=(40, 132, 58))

    def _draw_3d_road(self, draw):
        self._draw_background(draw)
        depths = self._road_depths()
        for index in range(len(depths) - 2, -1, -1):
            z_near = self.distance + depths[index]
            z_far = self.distance + depths[index + 1]
            center_near = track_world_x(z_near)
            center_far = track_world_x(z_far)
            height_near = track_world_height(z_near)
            height_far = track_world_height(z_far)
            stripe = int(z_near / 18.0) % 2
            grass = (43, 145, 61) if stripe else (36, 124, 52)
            road = (67, 70, 77) if stripe else (62, 65, 72)
            outer_width = 34.0
            self._draw_world_face(
                draw,
                (
                    (center_near - outer_width, height_near - 0.05, z_near),
                    (center_near - ROAD_HALF_WIDTH, height_near - 0.05, z_near),
                    (center_far - ROAD_HALF_WIDTH, height_far - 0.05, z_far),
                    (center_far - outer_width, height_far - 0.05, z_far),
                ),
                grass,
            )
            self._draw_world_face(
                draw,
                (
                    (center_near + ROAD_HALF_WIDTH, height_near - 0.05, z_near),
                    (center_near + outer_width, height_near - 0.05, z_near),
                    (center_far + outer_width, height_far - 0.05, z_far),
                    (center_far + ROAD_HALF_WIDTH, height_far - 0.05, z_far),
                ),
                grass,
            )
            self._draw_world_face(
                draw,
                (
                    (center_near - ROAD_HALF_WIDTH, height_near, z_near),
                    (center_near + ROAD_HALF_WIDTH, height_near, z_near),
                    (center_far + ROAD_HALF_WIDTH, height_far, z_far),
                    (center_far - ROAD_HALF_WIDTH, height_far, z_far),
                ),
                road,
            )
            curb_color = (242, 238, 216) if stripe else (221, 50, 43)
            curb_width = 0.34
            for side in (-1.0, 1.0):
                edge_near = center_near + side * ROAD_HALF_WIDTH
                edge_far = center_far + side * ROAD_HALF_WIDTH
                inner_near = edge_near - side * curb_width
                inner_far = edge_far - side * curb_width
                self._draw_world_face(
                    draw,
                    (
                        (inner_near, height_near + 0.025, z_near),
                        (edge_near, height_near + 0.025, z_near),
                        (edge_far, height_far + 0.025, z_far),
                        (inner_far, height_far + 0.025, z_far),
                    ),
                    curb_color,
                )
            if int(z_near / 11.0) % 2 == 0:
                for fraction in (-1 / 3, 1 / 3):
                    marker_near = center_near + ROAD_HALF_WIDTH * fraction
                    marker_far = center_far + ROAD_HALF_WIDTH * fraction
                    marker_width = 0.10
                    self._draw_world_face(
                        draw,
                        (
                            (marker_near - marker_width, height_near + 0.03, z_near),
                            (marker_near + marker_width, height_near + 0.03, z_near),
                            (marker_far + marker_width, height_far + 0.03, z_far),
                            (marker_far - marker_width, height_far + 0.03, z_far),
                        ),
                        (242, 231, 174),
                    )

    def _draw_scenery(self, draw):
        start = int((self.distance + 24.0) // 29.0) * 29
        objects = []
        for world_z in range(start, int(self.distance + 440), 29):
            seed = int(world_z * 17) & 0xFFFF
            side = -1 if seed % 2 else 1
            offset = ROAD_HALF_WIDTH + 3.0 + (seed % 7) * 0.42
            world_x = track_world_x(world_z) + side * offset
            base_y = track_world_height(world_z)
            projection = self._project_world((world_x, base_y, world_z))
            top_projection = self._project_world((world_x, base_y + 4.2, world_z))
            if projection is not None and top_projection is not None:
                objects.append((projection[2], projection, top_projection, seed))
        for depth, base, top, seed in sorted(objects, reverse=True):
            trunk_width = max(1, int(75 / base[2]))
            trunk_color = self._distance_color((89, 62, 38), depth)
            draw.rectangle(
                (base[0] - trunk_width, top[1] + (base[1] - top[1]) * 0.52, base[0] + trunk_width, base[1]),
                fill=trunk_color,
            )
            canopy_radius = max(2, int(285 / base[2]))
            canopy_color = self._distance_color((30, 105 + seed % 35, 55), depth)
            canopy_outline = self._distance_color((25, 77, 43), depth)
            draw.polygon(
                (
                    (top[0], top[1]),
                    (base[0] - canopy_radius, base[1] - canopy_radius * 0.45),
                    (base[0] + canopy_radius, base[1] - canopy_radius * 0.45),
                ),
                fill=canopy_color,
                outline=canopy_outline,
            )

    def _draw_traffic(self, draw):
        visible = [car for car in self.traffic if 2.0 < car.distance < 520.0]
        for car in sorted(visible, key=lambda item: item.distance, reverse=True):
            world_z = self.distance + car.distance
            center_x = track_world_x(world_z) + car.lane * ROAD_HALF_WIDTH
            base_y = track_world_height(world_z)
            self._draw_3d_car(
                draw,
                center_x,
                base_y,
                world_z,
                track_heading(world_z),
                car.color,
            )

    def _draw_player_car(self, draw, now: float):
        world_z = self.distance + 0.9
        center_x = track_world_x(world_z) + self.player_lateral * ROAD_HALF_WIDTH
        base_y = track_world_height(world_z)
        steering_yaw = self._combined_steering() * 0.10
        self._draw_3d_car(
            draw,
            center_x,
            base_y,
            world_z,
            track_heading(world_z) + steering_yaw,
            (38, 211, 119),
            player=True,
        )
        if now < self.nitro_until:
            for local_x, flame_color in ((-0.46, (65, 202, 255)), (0.46, (255, 175, 42))):
                flame_x, flame_z = self._offset_world(
                    center_x,
                    world_z,
                    local_x,
                    -1.95 - (self.frame_index % 3) * 0.12,
                    track_heading(world_z) + steering_yaw,
                )
                projected = self._project_world((flame_x, base_y + 0.38, flame_z))
                if projected is not None:
                    radius = max(1, int(16 / projected[2]))
                    draw.ellipse(
                        (
                            projected[0] - radius,
                            projected[1] - radius,
                            projected[0] + radius,
                            projected[1] + radius * 2,
                        ),
                        fill=flame_color,
                    )

    def _draw_explosion(self, draw, now: float):
        particles = []
        for particle in self.explosion_particles:
            projected = self._project_world((particle.x, particle.y, particle.z))
            if projected is not None:
                particles.append((projected[2], particle, projected))
        for depth, particle, projected in sorted(particles, reverse=True):
            progress = particle.age / particle.lifetime
            radius = max(1, int(particle.size * FOCAL_LENGTH / depth * (1.0 + progress)))
            if particle.smoke:
                grey = int(88 - progress * 46)
                color = (grey, grey, grey + 5)
            else:
                color = (255, int(214 - progress * 120), int(48 - progress * 35))
            draw.ellipse(
                (
                    projected[0] - radius,
                    projected[1] - radius,
                    projected[0] + radius,
                    projected[1] + radius,
                ),
                fill=color,
            )
        if now < self.explosion_flash_until and self.explosion_flash_world is not None:
            projected = self._project_world(self.explosion_flash_world)
            if projected is not None:
                remaining = (self.explosion_flash_until - now) / 0.20
                radius = max(3, int((1.0 - remaining * 0.3) * 145 / projected[2]))
                draw.ellipse(
                    (
                        projected[0] - radius,
                        projected[1] - radius,
                        projected[0] + radius,
                        projected[1] + radius,
                    ),
                    fill=(255, 232, 105),
                    outline=(255, 90, 24),
                )

    def _draw_gyro_indicator(self, draw: ImageDraw.ImageDraw):
        bounds = (224, 5, 276, 42)
        draw_rounded_rectangle(
            draw,
            bounds,
            radius=6,
            fill=(14, 24, 39),
            outline=(101, 213, 242) if self.gyro_ready else (116, 120, 126),
            width=1,
        )
        draw.text((229, 8), "GYRO", font=self.font, fill=(184, 236, 248))
        center_x = 246
        center_y = 29
        draw.line((center_x - 10, center_y, center_x + 10, center_y), fill=(66, 97, 116))
        draw.line((center_x, center_y - 8, center_x, center_y + 8), fill=(66, 97, 116))
        dot_x = center_x + int(self.gyro_steer * 9)
        dot_y = center_y + int(self.gyro_tilt_x * 6)
        dot_color = (78, 239, 146) if self.gyro_ready else (224, 93, 72)
        draw.ellipse((dot_x - 2, dot_y - 2, dot_x + 2, dot_y + 2), fill=dot_color)
        rate_width = int(abs(self.gyro_rate) * 9)
        if self.gyro_rate < 0:
            draw.rectangle((center_x - rate_width, 39, center_x, 40), fill=(255, 191, 62))
        else:
            draw.rectangle((center_x, 39, center_x + rate_width, 40), fill=(255, 191, 62))

    def _draw_hud(self, draw: ImageDraw.ImageDraw, now: float):
        draw.text((8, HUD_Y), f"SPD {int(self.speed):03d}", font=self.font, fill=(250, 240, 178))
        draw.text((78, HUD_Y), f"SCORE {self.score:05d}", font=self.font, fill=(250, 240, 178))
        draw.text((177, HUD_Y), f"T {int(self.time_remaining):02d}", font=self.font, fill=(250, 240, 178))
        nitro_ratio = clamp((self.nitro_ready_at - now) / 5.5, 0.0, 1.0)
        draw.rectangle((8, 22, 58, 27), outline=(230, 232, 223))
        ready_width = int(48 * (1.0 - nitro_ratio))
        draw.rectangle((9, 23, 9 + ready_width, 26), fill=(56, 200, 244))
        draw.text((63, 20), "NITRO", font=self.font, fill=(178, 221, 238))
        self._draw_gyro_indicator(draw)
        if self.state == "ready":
            self._draw_overlay(draw, "GYRO RACER 3D", "AUTO GAS  DOWN BRAKE", "TILT / D-PAD STEER")
        elif self.state == "finished":
            self._draw_overlay(draw, "FINISH!", f"SCORE {self.score}", "BUTTON TO RESTART")

    def _draw_overlay(self, draw: ImageDraw.ImageDraw, title: str, line_one: str, line_two: str):
        draw_rounded_rectangle(
            draw,
            (58, 91, 222, 155),
            radius=8,
            fill=(11, 20, 35),
            outline=(79, 226, 154),
            width=2,
        )
        draw.text((140 - len(title) * 3, 101), title, font=self.font, fill=(80, 238, 157))
        draw.text((140 - len(line_one) * 3, 121), line_one, font=self.font, fill=(240, 239, 213))
        draw.text((140 - len(line_two) * 3, 138), line_two, font=self.font, fill=(184, 223, 239))

    def _draw_game_menu(self, draw: ImageDraw.ImageDraw):
        draw_rounded_rectangle(
            draw,
            (67, 68, 213, 174),
            radius=9,
            fill=(8, 14, 25),
            outline=(89, 226, 165),
            width=2,
        )
        title = "GAME MENU"
        draw.text((140 - len(title) * 3, 78), title, font=self.font, fill=(105, 239, 181))
        labels = ("RESUME", "EXIT TO HOME")
        for index, label in enumerate(labels):
            top = 101 + index * 27
            selected = index == self.menu_selection
            if selected:
                draw_rounded_rectangle(
                    draw,
                    (82, top - 4, 198, top + 15),
                    radius=4,
                    fill=(40, 103, 88),
                    outline=(116, 244, 185),
                    width=1,
                )
            color = (255, 248, 209) if selected else (152, 174, 181)
            draw.text((140 - len(label) * 3, top + 1), label, font=self.font, fill=color)
        hint = "UP/DOWN  CENTER SELECT"
        draw.text((140 - len(hint) * 3, 159), hint, font=self.font, fill=(132, 178, 193))

    def render(self, now: float):
        image = Image.new("RGB", (LOGICAL_WIDTH, LOGICAL_HEIGHT))
        draw = ImageDraw.Draw(image)
        self._setup_camera(now)
        self._draw_3d_road(draw)
        self._draw_scenery(draw)
        self._draw_traffic(draw)
        self._draw_player_car(draw, now)
        self._draw_explosion(draw, now)
        self._draw_hud(draw, now)
        if self.menu_open:
            self._draw_game_menu(draw)
        transpose = getattr(Image, "Transpose", Image)
        physical_image = image.transpose(transpose.ROTATE_270)
        frame = image_to_rgb565_bytes(physical_image)
        if hasattr(self.board, "_mmap") and self.board._mmap is not None:
            self.board._mmap.seek(0)
            self.board._mmap.write(frame)
            self.board._mmap.seek(0)
        else:
            self.board.draw_image(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, frame)

    def run(self):
        frame_interval = 1.0 / TARGET_FPS
        previous = time.monotonic()
        try:
            while True:
                started = time.monotonic()
                dt = min(0.06, started - previous)
                previous = started
                with self.lock:
                    if not self.running:
                        break
                    self._update(started, dt)
                    self.render(started)
                    self.frame_index += 1
                remaining = frame_interval - (time.monotonic() - started)
                if remaining > 0:
                    time.sleep(remaining)
        finally:
            with self.lock:
                self.running = False
            self.sounds.cleanup()
            self.board.cleanup()


if __name__ == "__main__":
    GyroRacerGame().run()
