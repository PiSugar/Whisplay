from __future__ import annotations

import math
import os
import random
import struct
import subprocess
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

from whisplay_client import create_whisplay_hardware
from pillow_compat import draw_rounded_rectangle


LOGICAL_WIDTH = 280
LOGICAL_HEIGHT = 240
SCREEN_WIDTH = 240
SCREEN_HEIGHT = 280
TILE_SIZE = 20
TARGET_FPS = 18
TANK_RADIUS = 7
PLAYER_SPEED = 62.0
ENEMY_SPEED = 34.0
BULLET_SPEED = 150.0
BULLET_COLLISION_DISTANCE = 5.0
WHISPLAY_JOY_MODEL = "whisplay-joy"

# B: destructible brick, H: hedge, W: river, .: road.
MAP_TEMPLATE = (
    "BBBBBBBBBBBBBB",
    "B....H....W..B",
    "B.BB.H.BB.W..B",
    "B....H....W..B",
    "B.WWWW.BB.W..B",
    "B......HH....B",
    "B.BB.B....BB.B",
    "B....B.WW....B",
    "B.HH.B.WW.B..B",
    "B....B....B..B",
    "B.B......H...B",
    "BBBBBBBBBBBBBB",
)

DIRECTION_VECTOR = {
    "up": (0.0, -1.0),
    "right": (1.0, 0.0),
    "down": (0.0, 1.0),
    "left": (-1.0, 0.0),
}


def moving_points_collide(
    first_start: tuple[float, float],
    first_end: tuple[float, float],
    second_start: tuple[float, float],
    second_end: tuple[float, float],
    collision_distance: float = BULLET_COLLISION_DISTANCE,
) -> bool:
    """Return whether two points' simultaneous frame paths come close enough."""
    relative_x = first_start[0] - second_start[0]
    relative_y = first_start[1] - second_start[1]
    relative_step_x = (
        first_end[0] - first_start[0] - second_end[0] + second_start[0]
    )
    relative_step_y = (
        first_end[1] - first_start[1] - second_end[1] + second_start[1]
    )
    step_length_squared = relative_step_x ** 2 + relative_step_y ** 2
    if step_length_squared > 0:
        closest_time = -(
            relative_x * relative_step_x + relative_y * relative_step_y
        ) / step_length_squared
        closest_time = max(0.0, min(1.0, closest_time))
    else:
        closest_time = 0.0
    closest_x = relative_x + relative_step_x * closest_time
    closest_y = relative_y + relative_step_y * closest_time
    return closest_x ** 2 + closest_y ** 2 <= collision_distance ** 2

# The framebuffer is rotated clockwise. These mappings keep movement aligned
# with the arrows when the whole device is held in the resulting landscape view.
ROTATED_CONTROL_DIRECTION = {
    "up": "left",
    "right": "up",
    "down": "right",
    "left": "down",
}


def find_sound_card() -> str | None:
    try:
        with open("/proc/asound/cards", "r", encoding="utf-8") as handle:
            fallback = None
            for line in handle:
                lower = line.lower()
                if "whisplaysound" in lower:
                    return line.split()[0]
                if fallback is None and ("wm8960" in lower or "es8389" in lower):
                    fallback = line.split()[0]
            return fallback
    except Exception:
        return None


def setup_audio_mixer():
    card = find_sound_card()
    if card is None:
        return
    os.environ["SDL_AUDIODRIVER"] = "alsa"
    os.environ["AUDIODEV"] = "whisplaysound"
    commands = (
        ["amixer", "-c", card, "cset", "name=speaker", "80"],
        ["amixer", "-c", card, "sset", "Left Output Mixer PCM", "on"],
        ["amixer", "-c", card, "sset", "Right Output Mixer PCM", "on"],
        ["amixer", "-c", card, "sset", "Speaker", "121"],
        ["amixer", "-c", card, "sset", "Playback", "230"],
    )
    for command in commands:
        try:
            subprocess.run(command, check=False, capture_output=True, text=True)
        except Exception:
            return


def synthesize_tone(
    path: str,
    start_frequency: float,
    end_frequency: float,
    duration: float,
    volume: float,
    noisy: bool = False,
):
    sample_rate = 22050
    sample_count = max(1, int(sample_rate * duration))
    rng = random.Random(17)
    phase = 0.0
    frames = bytearray()
    for index in range(sample_count):
        progress = index / sample_count
        frequency = start_frequency + (end_frequency - start_frequency) * progress
        phase += 2.0 * math.pi * frequency / sample_rate
        raw = (1.0 if math.sin(phase) >= 0 else -1.0)
        if noisy:
            raw = raw * 0.45 + rng.uniform(-1.0, 1.0) * 0.55
        attack = min(1.0, index / max(1, int(sample_rate * 0.01)))
        release = min(1.0, (sample_count - index) / max(1, int(sample_rate * 0.08)))
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
        try:
            setup_audio_mixer()
            pygame.mixer.pre_init(22050, size=-16, channels=1)
            pygame.mixer.init()
            self._make("shoot", 920, 360, 0.09, 0.33)
            self._make("brick", 280, 120, 0.10, 0.30, noisy=True)
            self._make("clash", 1250, 620, 0.07, 0.28)
            self._make("explosion", 190, 55, 0.28, 0.55, noisy=True)
            self._make("start", 440, 880, 0.18, 0.28)
            self.enabled = True
        except Exception as exc:
            print(f"Tank Battle audio disabled: {exc}", file=sys.stderr)

    def _make(self, name, start, end, duration, volume, noisy=False):
        path = os.path.join(tempfile.gettempdir(), f"whisplay-tank-{name}.wav")
        synthesize_tone(path, start, end, duration, volume, noisy=noisy)
        self.sounds[name] = pygame.mixer.Sound(path)

    def play(self, name: str):
        if self.enabled and name in self.sounds:
            self.sounds[name].play()

    def cleanup(self):
        if self.enabled:
            try:
                pygame.mixer.quit()
            except Exception:
                pass


@dataclass
class Tank:
    x: float
    y: float
    direction: str
    color: tuple[int, int, int]
    enemy: bool = False
    alive: bool = True
    decision_until: float = 0.0
    next_shot_at: float = 0.0


@dataclass
class Bullet:
    x: float
    y: float
    direction: str
    enemy: bool
    alive: bool = True


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


class TankBattleGame:
    def __init__(self):
        self.board = create_whisplay_hardware(
            app_id=os.getenv("WHISPLAY_APP_ID", "whisplay-tank-battle"),
            display_name="Tank Battle",
            icon="TB",
            exit_gesture="long_press",
            priority=30,
            use_daemon_default_log=True,
            supported_models=(WHISPLAY_JOY_MODEL,),
        )
        if self.board.get_model() != WHISPLAY_JOY_MODEL:
            self.board.cleanup()
            raise RuntimeError("Tank Battle requires whisplay-joy")
        self.board.set_backlight(100)
        self.running = True
        self.lock = threading.Lock()
        self.sounds = SoundEffects()
        self.font = ImageFont.load_default()
        self.rng = random.Random()
        self.pressed_directions: list[str] = []
        self.last_player_shot_at = 0.0
        self.score = 0
        self.lives = 3
        self.game_over = False
        self.message_until = 0.0
        self.map_tiles: list[list[str]] = []
        self.player = Tank(0, 0, "up", (245, 216, 62))
        self.enemies: list[Tank] = []
        self.bullets: list[Bullet] = []
        self._register_callbacks()
        self._reset_game()

    def _register_callbacks(self):
        self.board.on_button_press(self._on_fire)
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
        direction = ROTATED_CONTROL_DIRECTION.get(physical_direction)
        if direction is None:
            return
        with self.lock:
            if direction in self.pressed_directions:
                self.pressed_directions.remove(direction)
            self.pressed_directions.append(direction)
            self.player.direction = direction

    def _on_direction_release(self, physical_direction: str):
        direction = ROTATED_CONTROL_DIRECTION.get(physical_direction)
        if direction is None:
            return
        with self.lock:
            if direction in self.pressed_directions:
                self.pressed_directions.remove(direction)

    def _on_fire(self):
        with self.lock:
            if self.game_over:
                self._reset_game()
                return
            now = time.monotonic()
            if now - self.last_player_shot_at < 0.22:
                return
            self.last_player_shot_at = now
            self._spawn_bullet(self.player)
            self.sounds.play("shoot")

    def _reset_game(self):
        self.map_tiles = [list(row) for row in MAP_TEMPLATE]
        self.player = Tank(30.0, 210.0, "up", (245, 216, 62))
        self.enemies = [
            Tank(30.0, 30.0, "down", (224, 70, 58), enemy=True),
            Tank(250.0, 30.0, "down", (236, 132, 52), enemy=True),
            Tank(250.0, 170.0, "left", (186, 76, 210), enemy=True),
        ]
        now = time.monotonic()
        for index, enemy in enumerate(self.enemies):
            enemy.decision_until = now + 0.5 + index * 0.3
            enemy.next_shot_at = now + 1.0 + index * 0.5
        self.bullets = []
        self.pressed_directions = []
        self.score = 0
        self.lives = 3
        self.game_over = False
        self.message_until = now + 2.2
        self.sounds.play("start")

    def _tile_at(self, x: float, y: float) -> str:
        col = int(x // TILE_SIZE)
        row = int(y // TILE_SIZE)
        if row < 0 or row >= len(self.map_tiles) or col < 0 or col >= len(self.map_tiles[0]):
            return "B"
        return self.map_tiles[row][col]

    def _tank_can_move_to(self, tank: Tank, x: float, y: float) -> bool:
        for corner_x in (x - TANK_RADIUS, x + TANK_RADIUS):
            for corner_y in (y - TANK_RADIUS, y + TANK_RADIUS):
                if self._tile_at(corner_x, corner_y) in {"B", "W"}:
                    return False
        others = [self.player, *self.enemies]
        for other in others:
            if other is tank or not other.alive:
                continue
            if (x - other.x) ** 2 + (y - other.y) ** 2 < (TANK_RADIUS * 1.8) ** 2:
                return False
        return True

    def _move_tank(self, tank: Tank, direction: str, distance: float) -> bool:
        dx, dy = DIRECTION_VECTOR[direction]
        next_x = tank.x + dx * distance
        next_y = tank.y + dy * distance
        if not self._tank_can_move_to(tank, next_x, next_y):
            return False
        tank.x = next_x
        tank.y = next_y
        tank.direction = direction
        return True

    def _spawn_bullet(self, tank: Tank):
        dx, dy = DIRECTION_VECTOR[tank.direction]
        self.bullets.append(
            Bullet(
                tank.x + dx * 10,
                tank.y + dy * 10,
                tank.direction,
                enemy=tank.enemy,
            )
        )

    def _update_player(self, dt: float):
        if self.pressed_directions and self.player.alive:
            direction = self.pressed_directions[-1]
            self._move_tank(self.player, direction, PLAYER_SPEED * dt)

    def _update_enemies(self, now: float, dt: float):
        for enemy in self.enemies:
            if not enemy.alive:
                continue
            if now >= enemy.decision_until:
                enemy.direction = self.rng.choice(tuple(DIRECTION_VECTOR))
                enemy.decision_until = now + self.rng.uniform(0.45, 1.25)
            if not self._move_tank(enemy, enemy.direction, ENEMY_SPEED * dt):
                enemy.direction = self.rng.choice(tuple(DIRECTION_VECTOR))
                enemy.decision_until = now + 0.35
            if now >= enemy.next_shot_at:
                self._spawn_bullet(enemy)
                enemy.next_shot_at = now + self.rng.uniform(1.0, 2.2)

    def _destroy_brick(self, x: float, y: float) -> bool:
        col = int(x // TILE_SIZE)
        row = int(y // TILE_SIZE)
        if 0 <= row < len(self.map_tiles) and 0 <= col < len(self.map_tiles[0]):
            if self.map_tiles[row][col] == "B":
                self.map_tiles[row][col] = "."
                return True
        return False

    @staticmethod
    def _bullet_hits_tank(bullet: Bullet, tank: Tank) -> bool:
        return tank.alive and (bullet.x - tank.x) ** 2 + (bullet.y - tank.y) ** 2 <= 9 ** 2

    def _hit_player(self):
        self.lives -= 1
        self.sounds.play("explosion")
        if self.lives <= 0:
            self.player.alive = False
            self.game_over = True
            return
        self.player.x = 30.0
        self.player.y = 210.0
        self.player.direction = "up"

    def _cancel_colliding_bullets(self, previous_positions):
        for index, (first, first_x, first_y) in enumerate(previous_positions):
            if not first.alive:
                continue
            for second, second_x, second_y in previous_positions[index + 1:]:
                if not second.alive:
                    continue
                if moving_points_collide(
                    (first_x, first_y),
                    (first.x, first.y),
                    (second_x, second_y),
                    (second.x, second.y),
                ):
                    first.alive = False
                    second.alive = False
                    self.sounds.play("clash")
                    break

    def _update_bullets(self, dt: float):
        previous_positions = []
        for bullet in self.bullets:
            if not bullet.alive:
                continue
            previous_positions.append((bullet, bullet.x, bullet.y))
            dx, dy = DIRECTION_VECTOR[bullet.direction]
            bullet.x += dx * BULLET_SPEED * dt
            bullet.y += dy * BULLET_SPEED * dt

        self._cancel_colliding_bullets(previous_positions)

        for bullet in self.bullets:
            if not bullet.alive:
                continue
            tile = self._tile_at(bullet.x, bullet.y)
            if tile == "B":
                bullet.alive = False
                if self._destroy_brick(bullet.x, bullet.y):
                    self.sounds.play("brick")
                continue
            if bullet.enemy:
                if self._bullet_hits_tank(bullet, self.player):
                    bullet.alive = False
                    self._hit_player()
            else:
                for enemy in self.enemies:
                    if self._bullet_hits_tank(bullet, enemy):
                        bullet.alive = False
                        enemy.alive = False
                        self.score += 100
                        self.sounds.play("explosion")
                        break
        self.bullets = [bullet for bullet in self.bullets if bullet.alive]
        if not any(enemy.alive for enemy in self.enemies):
            self.game_over = True

    def _update(self, now: float, dt: float):
        if self.game_over:
            return
        self._update_player(dt)
        self._update_enemies(now, dt)
        self._update_bullets(dt)

    def _draw_map(self, draw: ImageDraw.ImageDraw):
        draw.rectangle((0, 0, LOGICAL_WIDTH, LOGICAL_HEIGHT), fill=(191, 176, 125))
        for row, tiles in enumerate(self.map_tiles):
            for col, tile in enumerate(tiles):
                x = col * TILE_SIZE
                y = row * TILE_SIZE
                if tile == "B":
                    draw.rectangle((x, y, x + 19, y + 19), fill=(150, 61, 42))
                    draw.line((x, y + 9, x + 19, y + 9), fill=(236, 151, 105), width=2)
                    draw.line((x + 9, y, x + 9, y + 9), fill=(236, 151, 105), width=2)
                    draw.line((x + 5, y + 10, x + 5, y + 19), fill=(236, 151, 105), width=2)
                    draw.rectangle((x, y, x + 19, y + 19), outline=(88, 38, 33))
                elif tile == "W":
                    draw.rectangle((x, y, x + 19, y + 19), fill=(42, 123, 190))
                    for wave_y in (5, 13):
                        draw.arc((x + 1, y + wave_y - 3, x + 10, y + wave_y + 3), 0, 180, fill=(119, 208, 232))
                        draw.arc((x + 9, y + wave_y - 3, x + 18, y + wave_y + 3), 180, 360, fill=(24, 82, 153))
                elif tile == "H":
                    draw.rectangle((x, y, x + 19, y + 19), fill=(49, 123, 48))
                    draw.ellipse((x + 1, y + 2, x + 12, y + 14), fill=(82, 171, 63))
                    draw.ellipse((x + 8, y + 5, x + 19, y + 18), fill=(30, 102, 43))
                else:
                    draw.rectangle((x, y, x + 19, y + 19), fill=(190, 175, 126))
                    draw.point((x + 5, y + 7), fill=(158, 145, 106))
                    draw.point((x + 15, y + 14), fill=(158, 145, 106))

    def _draw_tank(self, draw: ImageDraw.ImageDraw, tank: Tank):
        if not tank.alive:
            return
        x = int(tank.x)
        y = int(tank.y)
        draw_rounded_rectangle(
            draw,
            (x - 8, y - 8, x + 8, y + 8),
            radius=3,
            fill=tank.color,
            outline=(38, 40, 32),
            width=2,
        )
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(78, 84, 58))
        dx, dy = DIRECTION_VECTOR[tank.direction]
        draw.line((x, y, x + int(dx * 12), y + int(dy * 12)), fill=(33, 38, 30), width=3)
        if tank.enemy:
            draw.rectangle((x - 7, y - 7, x - 5, y + 7), fill=(62, 49, 39))
            draw.rectangle((x + 5, y - 7, x + 7, y + 7), fill=(62, 49, 39))

    def _draw_hedge_canopy(self, draw: ImageDraw.ImageDraw):
        for row, tiles in enumerate(self.map_tiles):
            for col, tile in enumerate(tiles):
                if tile != "H":
                    continue
                x = col * TILE_SIZE
                y = row * TILE_SIZE
                draw.ellipse((x + 2, y + 1, x + 13, y + 11), fill=(90, 180, 66))
                draw.ellipse((x + 8, y + 7, x + 19, y + 18), fill=(42, 128, 46))

    def _draw_hud(self, draw: ImageDraw.ImageDraw):
        draw_rounded_rectangle(
            draw,
            (74, 3, 206, 18),
            radius=5,
            fill=(24, 29, 28),
            outline=(238, 220, 145),
        )
        draw.text((82, 6), f"SCORE {self.score:04d}   LIFE {self.lives}", font=self.font, fill=(252, 239, 178))
        now = time.monotonic()
        if now < self.message_until:
            draw_rounded_rectangle(
                draw,
                (69, 92, 211, 148),
                radius=8,
                fill=(25, 32, 30),
                outline=(245, 216, 62),
                width=2,
            )
            draw.text((102, 102), "TANK BATTLE", font=self.font, fill=(245, 216, 62))
            draw.text((82, 121), "D-PAD MOVE  BUTTON FIRE", font=self.font, fill=(238, 238, 220))
        elif self.game_over:
            title = "MISSION CLEAR" if self.player.alive else "GAME OVER"
            draw_rounded_rectangle(
                draw,
                (75, 94, 205, 146),
                radius=8,
                fill=(37, 28, 27),
                outline=(245, 216, 62),
                width=2,
            )
            draw.text((101, 105), title, font=self.font, fill=(245, 216, 62))
            draw.text((91, 124), "BUTTON TO RESTART", font=self.font, fill=(248, 239, 216))

    def render(self):
        image = Image.new("RGB", (LOGICAL_WIDTH, LOGICAL_HEIGHT))
        draw = ImageDraw.Draw(image)
        self._draw_map(draw)
        self._draw_tank(draw, self.player)
        for enemy in self.enemies:
            self._draw_tank(draw, enemy)
        for bullet in self.bullets:
            x = int(bullet.x)
            y = int(bullet.y)
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(255, 246, 166), outline=(70, 51, 32))
        self._draw_hedge_canopy(draw)
        self._draw_hud(draw)
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
                    self.render()
                remaining = frame_interval - (time.monotonic() - started)
                if remaining > 0:
                    time.sleep(remaining)
        finally:
            self.sounds.cleanup()
            self.board.cleanup()


if __name__ == "__main__":
    TankBattleGame().run()
