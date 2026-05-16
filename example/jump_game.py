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

import select

EV_KEY = 0x01
KEY_SPACE = 57
_INPUT_EVENT_FORMAT = "llHHI"
_INPUT_EVENT_SIZE = struct.calcsize(_INPUT_EVENT_FORMAT)


def _start_keyboard_listener(on_press, on_release):
    """Listen for space key on external keyboards, trigger callbacks."""
    def _loop():
        import os as _os
        fds: dict[int, str] = {}
        last_scan = 0.0
        while True:
            now = time.monotonic()
            if now - last_scan >= 2.0:
                last_scan = now
                paths = set()
                by_id = "/dev/input/by-id"
                try:
                    for e in _os.listdir(by_id):
                        if e.endswith("-kbd"):
                            paths.add(_os.path.realpath(_os.path.join(by_id, e)))
                except FileNotFoundError:
                    pass
                if not paths:
                    try:
                        paths = {
                            _os.path.join("/dev/input", e)
                            for e in _os.listdir("/dev/input")
                            if e.startswith("event")
                        }
                    except FileNotFoundError:
                        pass
                stale = [fd for fd, p in fds.items() if p not in paths]
                for fd in stale:
                    try:
                        _os.close(fd)
                    except OSError:
                        pass
                    del fds[fd]
                for p in paths - set(fds.values()):
                    try:
                        fds[_os.open(p, _os.O_RDONLY | _os.O_NONBLOCK)] = p
                    except OSError:
                        pass
            if not fds:
                time.sleep(1)
                continue
            try:
                ready, _, _ = select.select(list(fds), [], [], 0.02)
            except (ValueError, OSError):
                for fd in list(fds):
                    try:
                        _os.close(fd)
                    except OSError:
                        pass
                fds.clear()
                continue
            for fd in ready:
                try:
                    data = _os.read(fd, _INPUT_EVENT_SIZE * 16)
                except OSError:
                    try:
                        _os.close(fd)
                    except OSError:
                        pass
                    fds.pop(fd, None)
                    continue
                off = 0
                while off + _INPUT_EVENT_SIZE <= len(data):
                    _, _, ev_type, code, value = struct.unpack(
                        _INPUT_EVENT_FORMAT, data[off:off + _INPUT_EVENT_SIZE]
                    )
                    if ev_type == EV_KEY and code == KEY_SPACE:
                        if value == 1:
                            on_press()
                        elif value == 0:
                            on_release()
                    off += _INPUT_EVENT_SIZE

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t


SCREEN_WIDTH = 240
SCREEN_HEIGHT = 280
FRAME_SIZE = SCREEN_WIDTH * SCREEN_HEIGHT * 2
TARGET_FPS = 18

PLATFORM_W = 64
PLATFORM_H = 28
PLATFORM_DEPTH = 30
JUMPER_W = 26
JUMPER_H = 38
WORLD_STEP_MIN = 104
WORLD_STEP_MAX = 188
JUMP_DURATION = 0.34
JUMP_ARC_HEIGHT = 58
MAX_CHARGE_SEC = 1.1

SKY_TOP = (240, 224, 191)
SKY_BOTTOM = (138, 199, 247)
HORIZON = (249, 239, 208)
SHADOW = (32, 45, 74)
WHITE = (255, 255, 255)


def rgb565_bytes(color):
    r, g, b = color
    value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return bytes([(value >> 8) & 0xFF, value & 0xFF])


def find_wm8960_card() -> str | None:
    try:
        with open("/proc/asound/cards", "r", encoding="utf-8") as handle:
            for line in handle:
                if "wm8960" in line.lower():
                    return line.split()[0]
    except Exception:
        return None
    return None


def setup_audio_mixer():
    card = find_wm8960_card()
    if card is None:
        return
    commands = [
        ["amixer", "-c", card, "sset", "Left Output Mixer PCM", "on"],
        ["amixer", "-c", card, "sset", "Right Output Mixer PCM", "on"],
        ["amixer", "-c", card, "sset", "Speaker", "121"],
        ["amixer", "-c", card, "sset", "Playback", "230"],
    ]
    for cmd in commands:
        try:
            subprocess.run(cmd, check=False, capture_output=True, text=True)
        except Exception:
            return


def synthesize_tone(path: str, frequency: float, duration_sec: float, volume: float, shape: str = "sine"):
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
            if shape == "square":
                raw = 1.0 if math.sin(phase) >= 0 else -1.0
            elif shape == "triangle":
                raw = 2.0 * abs(2.0 * ((frequency * t) % 1.0) - 1.0) - 1.0
            else:
                raw = math.sin(phase)
            fade_in = min(1.0, index / max(1, int(sample_rate * 0.02)))
            fade_out = min(1.0, (total_samples - index) / max(1, int(sample_rate * 0.06)))
            envelope = min(fade_in, fade_out)
            value = int(32767 * volume * envelope * raw)
            frames.extend(struct.pack("<h", value))
        wav_file.writeframes(frames)


class SoundEffects:
    def __init__(self):
        self.enabled = False
        self.sounds: dict[str, pygame.mixer.Sound] = {}
        try:
            setup_audio_mixer()
            pygame.mixer.pre_init(22050, size=-16, channels=1)
            pygame.mixer.init()
            self.enabled = True
            self._load()
        except Exception:
            self.enabled = False

    def _make_sound(self, name: str, frequency: float, duration_sec: float, volume: float, shape: str = "sine"):
        path = os.path.join(tempfile.gettempdir(), f"whisplay-jump-{name}.wav")
        synthesize_tone(path, frequency, duration_sec, volume, shape=shape)
        self.sounds[name] = pygame.mixer.Sound(path)

    def _load(self):
        self._make_sound("charge", 320, 0.08, 0.22, "triangle")
        self._make_sound("jump", 640, 0.14, 0.40, "square")
        self._make_sound("land", 420, 0.10, 0.35, "triangle")
        self._make_sound("score", 980, 0.08, 0.35, "sine")
        self._make_sound("fail", 210, 0.28, 0.55, "square")

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
class Tile:
    wx: float
    wy: float
    style: int


class Sprite:
    def __init__(self, image: Image.Image):
        rgba = image.convert("RGBA")
        self.width, self.height = rgba.size
        pixels = list(rgba.getdata())
        self.mask = [a > 0 for (_r, _g, _b, a) in pixels]
        self.colors = bytearray(self.width * self.height * 2)
        out = 0
        for r, g, b, _a in pixels:
            value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            self.colors[out] = (value >> 8) & 0xFF
            self.colors[out + 1] = value & 0xFF
            out += 2


class FastRenderer:
    DIGIT_SEGMENTS = {
        "0": "abcedf",
        "1": "bc",
        "2": "abged",
        "3": "abgcd",
        "4": "fgbc",
        "5": "afgcd",
        "6": "afgecd",
        "7": "abc",
        "8": "abcdefg",
        "9": "abcfgd",
    }

    PLATFORM_PALETTES = [
        {"top": (249, 210, 115), "left": (216, 144, 76), "right": (183, 105, 60), "outline": (120, 62, 38)},
        {"top": (176, 224, 155), "left": (96, 164, 102), "right": (74, 126, 96), "outline": (44, 78, 60)},
        {"top": (176, 198, 255), "left": (108, 134, 218), "right": (84, 102, 182), "outline": (54, 66, 126)},
    ]

    def __init__(self):
        self.base_frame = self._build_base_frame()
        self.platform_sprites = [self._build_platform_sprite(palette) for palette in self.PLATFORM_PALETTES]
        self.jumper_idle = self._build_jumper_sprite(1.0, False)
        self.jumper_charge = self._build_jumper_sprite(0.78, True)
        self.jumper_fly = self._build_jumper_sprite(1.0, True)
        self.title_font = self._load_font(24, bold=True)
        self.body_font = self._load_font(15)
        self.small_font = self._load_font(12)
        self.overlay_cache: dict[str, Sprite] = {}

    def _load_font(self, size: int, bold: bool = False):
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        for path in candidates:
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _image_to_rgb565(self, image: Image.Image) -> bytes:
        pixels = image.convert("RGB").load()
        frame = bytearray(FRAME_SIZE)
        idx = 0
        for y in range(SCREEN_HEIGHT):
            for x in range(SCREEN_WIDTH):
                r, g, b = pixels[x, y]
                value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                frame[idx] = (value >> 8) & 0xFF
                frame[idx + 1] = value & 0xFF
                idx += 2
        return bytes(frame)

    def _build_base_frame(self) -> bytes:
        image = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), SKY_TOP)
        draw = ImageDraw.Draw(image)
        for y in range(SCREEN_HEIGHT):
            blend = y / max(1, SCREEN_HEIGHT - 1)
            r = int(SKY_TOP[0] + (SKY_BOTTOM[0] - SKY_TOP[0]) * blend)
            g = int(SKY_TOP[1] + (SKY_BOTTOM[1] - SKY_TOP[1]) * blend)
            b = int(SKY_TOP[2] + (SKY_BOTTOM[2] - SKY_TOP[2]) * blend)
            draw.line((0, y, SCREEN_WIDTH, y), fill=(r, g, b))
        draw.rectangle((0, 160, SCREEN_WIDTH, SCREEN_HEIGHT), fill=HORIZON)
        for x in range(8, SCREEN_WIDTH, 34):
            h = 18 + (x % 44)
            draw.rectangle((x, 120 - h, x + 16, 160), fill=(196, 188, 198))
        draw.polygon([(0, 176), (120, 130), (239, 176), (239, 280), (0, 280)], fill=(237, 226, 204))
        draw.line((0, 176, 120, 130), fill=(210, 198, 180), width=2)
        draw.line((120, 130, 239, 176), fill=(210, 198, 180), width=2)
        return self._image_to_rgb565(image)

    def _build_platform_sprite(self, palette: dict) -> Sprite:
        width = PLATFORM_W + 12
        height = PLATFORM_H + PLATFORM_DEPTH + 14
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        cx = width // 2
        top_y = 10
        diamond = [
            (cx, top_y),
            (cx + PLATFORM_W // 2, top_y + PLATFORM_H // 2),
            (cx, top_y + PLATFORM_H),
            (cx - PLATFORM_W // 2, top_y + PLATFORM_H // 2),
        ]
        right_face = [
            diamond[1],
            (diamond[1][0], diamond[1][1] + PLATFORM_DEPTH),
            (diamond[2][0], diamond[2][1] + PLATFORM_DEPTH),
            diamond[2],
        ]
        left_face = [
            diamond[2],
            (diamond[2][0], diamond[2][1] + PLATFORM_DEPTH),
            (diamond[3][0], diamond[3][1] + PLATFORM_DEPTH),
            diamond[3],
        ]
        draw.polygon(right_face, fill=palette["right"])
        draw.polygon(left_face, fill=palette["left"])
        draw.polygon(diamond, fill=palette["top"])
        draw.line(diamond + [diamond[0]], fill=palette["outline"], width=2)
        draw.line(right_face + [right_face[0]], fill=palette["outline"], width=2)
        draw.line(left_face + [left_face[0]], fill=palette["outline"], width=2)
        return Sprite(image)

    def _build_jumper_sprite(self, scale_y: float, lean: bool) -> Sprite:
        width = 44
        height = 58
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        body_h = int(JUMPER_H * scale_y)
        top = height - 8 - body_h
        base_left = width // 2 - JUMPER_W // 2
        base_right = base_left + JUMPER_W
        body_color = (75, 52, 118, 255)
        body_shadow = (58, 40, 96, 255)
        head_color = (36, 36, 48, 255)
        draw.rounded_rectangle((base_left, top + 10, base_right, height - 8), radius=8, fill=body_color)
        draw.rectangle((base_left + JUMPER_W // 2, top + 10, base_right, height - 8), fill=body_shadow)
        head_offset = 4 if lean else 0
        draw.ellipse((base_left - 2 + head_offset, top - 2, base_right + 2 + head_offset, top + 22), fill=head_color)
        draw.ellipse((base_left + 4 + head_offset, top + 4, base_left + 12 + head_offset, top + 12), fill=(255, 255, 255, 70))
        return Sprite(image)

    def new_frame(self) -> bytearray:
        return bytearray(self.base_frame)

    def fill_rect(self, frame: bytearray, x0: int, y0: int, x1: int, y1: int, color):
        x0 = max(0, min(SCREEN_WIDTH, x0))
        x1 = max(0, min(SCREEN_WIDTH, x1))
        y0 = max(0, min(SCREEN_HEIGHT, y0))
        y1 = max(0, min(SCREEN_HEIGHT, y1))
        if x1 <= x0 or y1 <= y0:
            return
        color_bytes = rgb565_bytes(color)
        row = color_bytes * (x1 - x0)
        row_bytes = SCREEN_WIDTH * 2
        start = x0 * 2
        end = x1 * 2
        for y in range(y0, y1):
            offset = y * row_bytes
            frame[offset + start:offset + end] = row

    def blit_sprite(self, frame: bytearray, sprite: Sprite, left: int, top: int):
        row_bytes = SCREEN_WIDTH * 2
        for sy in range(sprite.height):
            dy = top + sy
            if dy < 0 or dy >= SCREEN_HEIGHT:
                continue
            for sx in range(sprite.width):
                dx = left + sx
                if dx < 0 or dx >= SCREEN_WIDTH:
                    continue
                idx = sy * sprite.width + sx
                if not sprite.mask[idx]:
                    continue
                src = idx * 2
                dst = dy * row_bytes + dx * 2
                frame[dst] = sprite.colors[src]
                frame[dst + 1] = sprite.colors[src + 1]

    def draw_shadow(self, frame: bytearray, center_x: int, center_y: int, radius_x: int, radius_y: int):
        shadow = rgb565_bytes((118, 120, 130))
        row_bytes = SCREEN_WIDTH * 2
        for dy in range(-radius_y, radius_y + 1):
            yy = center_y + dy
            if yy < 0 or yy >= SCREEN_HEIGHT:
                continue
            ratio = 1.0 - (dy * dy) / max(1, radius_y * radius_y)
            span = int(radius_x * max(0.0, ratio) ** 0.5)
            x0 = max(0, center_x - span)
            x1 = min(SCREEN_WIDTH, center_x + span + 1)
            row = shadow * (x1 - x0)
            offset = yy * row_bytes + x0 * 2
            frame[offset:offset + len(row)] = row

    def draw_digit(self, frame: bytearray, digit: str, x: int, y: int, scale: int, color):
        thickness = max(2, scale)
        width = scale * 6
        height = scale * 10
        segments = {
            "a": (x + thickness, y, x + width - thickness, y + thickness),
            "b": (x + width - thickness, y + thickness, x + width, y + height // 2),
            "c": (x + width - thickness, y + height // 2, x + width, y + height - thickness),
            "d": (x + thickness, y + height - thickness, x + width - thickness, y + height),
            "e": (x, y + height // 2, x + thickness, y + height - thickness),
            "f": (x, y + thickness, x + thickness, y + height // 2),
            "g": (x + thickness, y + height // 2 - thickness // 2, x + width - thickness, y + height // 2 + thickness // 2 + 1),
        }
        for segment in self.DIGIT_SEGMENTS.get(digit, ""):
            x0, y0, x1, y1 = segments[segment]
            self.fill_rect(frame, x0, y0, x1, y1, color)

    def draw_score(self, frame: bytearray, score: int):
        text = str(score)
        scale = 3
        digit_w = scale * 6
        gap = 4
        total_w = len(text) * digit_w + (len(text) - 1) * gap
        x = (SCREEN_WIDTH - total_w) // 2
        for char in text:
            self.draw_digit(frame, char, x, 16, scale, WHITE)
            x += digit_w + gap

    def get_overlay(self, state: str, score: int, best_score: int) -> Sprite:
        cache_key = f"{state}:{score}:{best_score}"
        if cache_key in self.overlay_cache:
            return self.overlay_cache[cache_key]
        image = Image.new("RGBA", (SCREEN_WIDTH, SCREEN_HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        if state == "ready":
            draw.rounded_rectangle((20, 82, SCREEN_WIDTH - 20, 176), radius=14, fill=(24, 36, 60, 156), outline=(255, 255, 255, 220), width=2)
            draw.text((80, 94), "Jump", fill=(255, 226, 138, 255), font=self.title_font)
            draw.text((42, 126), "Hold to charge", fill=(234, 240, 252, 255), font=self.body_font)
            draw.text((36, 148), "Release to jump", fill=(234, 240, 252, 255), font=self.body_font)
        elif state == "game_over":
            draw.rounded_rectangle((26, 86, SCREEN_WIDTH - 26, 190), radius=14, fill=(62, 26, 30, 226), outline=(255, 212, 110, 255), width=2)
            draw.text((48, 100), "Game Over", fill=(255, 212, 110, 255), font=self.title_font)
            draw.text((58, 136), f"score  {score}", fill=(252, 242, 222, 255), font=self.body_font)
            draw.text((58, 158), f"best   {best_score}", fill=(252, 242, 222, 255), font=self.body_font)
        sprite = Sprite(image)
        self.overlay_cache[cache_key] = sprite
        return sprite


class JumpGame:
    def __init__(self):
        self.board = create_whisplay_hardware(
            app_id=os.getenv("WHISPLAY_APP_ID", "whisplay-jump"),
            display_name="Jump Game",
            icon="J",
            exit_gesture="quad_click",
            priority=25,
            use_daemon_default_log=True,
        )
        self.board.set_backlight(100)
        self.running = True
        self.lock = threading.Lock()
        self.renderer = FastRenderer()
        self.sounds = SoundEffects()
        self.rng = random.Random()
        self.best_score = 0
        self.score = 0
        self.tiles: list[Tile] = []
        self.current_tile_index = 0
        self.next_tile_index = 1
        self.direction = "x"
        self.state = "ready"
        self.button_down = False
        self.charge_started_at = 0.0
        self.charge_ratio = 0.0
        self.jump_started_at = 0.0
        self.jump_from = (0.0, 0.0)
        self.jump_to = (0.0, 0.0)
        self.jumper_world = (0.0, 0.0)
        self.jumper_height = 0.0
        self.ready_overlay_until = 0.0
        self.frame_index = 0
        self._register_callbacks()
        self._reset_round()

    def _register_callbacks(self):
        self.board.on_button_press(self._on_button_press)
        self.board.on_button_release(self._on_button_release)
        if hasattr(self.board, "on_exit_request"):
            self.board.on_exit_request(self._on_exit_request)
        if hasattr(self.board, "on_focus_revoked"):
            self.board.on_focus_revoked(self._on_focus_revoked)
        _start_keyboard_listener(self._on_button_press, self._on_button_release)

    def _on_button_press(self):
        with self.lock:
            if self.state == "game_over":
                self._reset_round()
                return
            if self.state in {"ready", "landed"}:
                self.state = "charging"
                self.button_down = True
                self.charge_started_at = time.monotonic()
                self.charge_ratio = 0.0
                self.sounds.play("charge")

    def _on_button_release(self):
        with self.lock:
            if self.state == "charging":
                self.button_down = False
                self._start_jump()

    def _on_exit_request(self):
        with self.lock:
            self.running = False

    def _on_focus_revoked(self, _payload=None):
        with self.lock:
            self.running = False

    def _reset_round(self):
        self.score = 0
        self.tiles = [
            Tile(0.0, 0.0, 0),
            Tile(self._next_step_distance(), 0.0, 1),
            Tile(0.0, 0.0, 2),
        ]
        self.current_tile_index = 0
        self.next_tile_index = 1
        self.direction = "x"
        self._spawn_future_tile()
        self.state = "ready"
        self.charge_ratio = 0.0
        self.ready_overlay_until = time.monotonic() + 5.0
        self.jumper_world = (self.tiles[0].wx, self.tiles[0].wy)
        self.jumper_height = 0.0

    def _next_step_distance(self) -> float:
        # Mix shorter and much longer hops so the scene doesn't feel evenly spaced.
        if self.rng.random() < 0.45:
            return float(self.rng.randint(WORLD_STEP_MIN, 132))
        return float(self.rng.randint(150, WORLD_STEP_MAX))

    def _spawn_future_tile(self):
        current = self.tiles[self.next_tile_index]
        step = self._next_step_distance()
        axis = "y" if self.direction == "x" else "x"
        self.direction = axis
        if axis == "x":
            self.tiles[2] = Tile(current.wx + step, current.wy, self.rng.randint(0, 2))
        else:
            self.tiles[2] = Tile(current.wx, current.wy + step, self.rng.randint(0, 2))

    def _start_jump(self):
        current = self.tiles[self.current_tile_index]
        target = self.tiles[self.next_tile_index]
        delta_x = target.wx - current.wx
        delta_y = target.wy - current.wy
        distance = abs(delta_x) + abs(delta_y)
        strength = 0.52 + self.charge_ratio * 0.88
        if delta_x != 0:
            jump_distance = strength * distance
            self.jump_to = (current.wx + math.copysign(jump_distance, delta_x), current.wy)
        else:
            jump_distance = strength * distance
            self.jump_to = (current.wx, current.wy + math.copysign(jump_distance, delta_y))
        self.jump_from = self.jumper_world
        self.jump_started_at = time.monotonic()
        self.state = "jumping"
        self.sounds.play("jump")

    def _advance_tiles(self):
        landed = self.tiles[self.next_tile_index]
        self.tiles[0] = landed
        self.tiles[1] = self.tiles[2]
        self.current_tile_index = 0
        self.next_tile_index = 1
        self._spawn_future_tile()

    def _landing_result(self):
        current = self.tiles[self.current_tile_index]
        target = self.tiles[self.next_tile_index]
        x, y = self.jump_to
        tolerance = PLATFORM_W * 0.32
        if abs(x - target.wx) <= tolerance and abs(y - target.wy) <= tolerance:
            self.score += 1
            self.best_score = max(self.best_score, self.score)
            self.sounds.play("land")
            self.sounds.play("score")
            self.jumper_world = (target.wx, target.wy)
            self._advance_tiles()
            self.state = "landed"
            self.charge_ratio = 0.0
            return
        if abs(x - current.wx) <= tolerance and abs(y - current.wy) <= tolerance:
            self.jumper_world = (current.wx, current.wy)
            self.state = "landed"
            self.charge_ratio = 0.0
            self.sounds.play("land")
            return
        self._trigger_game_over()

    def _trigger_game_over(self):
        self.state = "game_over"
        self.jumper_world = self.jump_to
        self.best_score = max(self.best_score, self.score)
        self.sounds.play("fail")

    def _update(self):
        if self.state == "charging" and self.button_down:
            elapsed = time.monotonic() - self.charge_started_at
            self.charge_ratio = min(1.0, elapsed / MAX_CHARGE_SEC)
        elif self.state == "jumping":
            progress = min(1.0, (time.monotonic() - self.jump_started_at) / JUMP_DURATION)
            eased = 1.0 - (1.0 - progress) * (1.0 - progress)
            wx = self.jump_from[0] + (self.jump_to[0] - self.jump_from[0]) * eased
            wy = self.jump_from[1] + (self.jump_to[1] - self.jump_from[1]) * eased
            self.jumper_world = (wx, wy)
            self.jumper_height = JUMP_ARC_HEIGHT * 4.0 * progress * (1.0 - progress)
            if progress >= 1.0:
                self.jumper_height = 0.0
                self._landing_result()

    def _project(self, wx: float, wy: float, z: float = 0.0):
        anchor_x = self.tiles[self.current_tile_index].wx
        anchor_y = self.tiles[self.current_tile_index].wy
        rel_x = wx - anchor_x
        rel_y = wy - anchor_y
        screen_x = 120 + int((rel_x - rel_y) * 0.74)
        screen_y = 122 + int((rel_x + rel_y) * 0.33 - z)
        return screen_x, screen_y

    def _draw_platform(self, frame: bytearray, tile: Tile):
        sprite = self.renderer.platform_sprites[tile.style % len(self.renderer.platform_sprites)]
        center_x, top_y = self._project(tile.wx, tile.wy)
        self.renderer.blit_sprite(frame, sprite, center_x - sprite.width // 2, top_y - 12)

    def _draw_jumper(self, frame: bytearray):
        wx, wy = self.jumper_world
        center_x, center_y = self._project(wx, wy, self.jumper_height)
        shadow_x, shadow_y = self._project(wx, wy)
        self.renderer.draw_shadow(frame, shadow_x, shadow_y + 16, 13, 5)
        if self.state == "charging":
            sprite = self.renderer.jumper_charge
        elif self.state == "jumping":
            sprite = self.renderer.jumper_fly
        else:
            sprite = self.renderer.jumper_idle
        self.renderer.blit_sprite(frame, sprite, center_x - sprite.width // 2, center_y - sprite.height + 18)

    def _draw_power_bar(self, frame: bytearray):
        if self.state != "charging":
            return
        bar_x = 26
        bar_y = 228
        bar_w = 188
        bar_h = 16
        self.renderer.fill_rect(frame, bar_x, bar_y, bar_x + bar_w, bar_y + bar_h, (54, 60, 82))
        self.renderer.fill_rect(frame, bar_x + 2, bar_y + 2, bar_x + bar_w - 2, bar_y + bar_h - 2, (22, 28, 44))
        fill = int((bar_w - 4) * self.charge_ratio)
        if fill > 0:
            self.renderer.fill_rect(frame, bar_x + 2, bar_y + 2, bar_x + 2 + fill, bar_y + bar_h - 2, (251, 198, 94))

    def _draw_overlay(self, frame: bytearray):
        if self.state == "ready" and time.monotonic() < self.ready_overlay_until:
            overlay = self.renderer.get_overlay(self.state, self.score, self.best_score)
            self.renderer.blit_sprite(frame, overlay, 0, 0)
        elif self.state == "game_over":
            overlay = self.renderer.get_overlay(self.state, self.score, self.best_score)
            self.renderer.blit_sprite(frame, overlay, 0, 0)

    def _blit_frame(self, frame: bytearray):
        if hasattr(self.board, "_mmap") and self.board._mmap is not None:
            self.board._mmap.seek(0)
            self.board._mmap.write(frame)
            self.board._mmap.seek(0)
        else:
            self.board.draw_image(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, frame)

    def render(self):
        frame = self.renderer.new_frame()
        for tile in sorted(self.tiles, key=lambda item: item.wx + item.wy):
            self._draw_platform(frame, tile)
        self._draw_jumper(frame)
        self.renderer.draw_score(frame, self.score)
        self._draw_power_bar(frame)
        self._draw_overlay(frame)
        self._blit_frame(frame)

    def run(self):
        frame_interval = 1.0 / TARGET_FPS
        try:
            while True:
                start = time.monotonic()
                with self.lock:
                    if not self.running:
                        break
                    self._update()
                    self.render()
                    self.frame_index += 1
                elapsed = time.monotonic() - start
                if elapsed < frame_interval:
                    time.sleep(frame_interval - elapsed)
        finally:
            self.sounds.cleanup()
            self.board.cleanup()


if __name__ == "__main__":
    JumpGame().run()
