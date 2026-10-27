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

from pillow_compat import draw_rounded_rectangle
from whisplay_client import create_whisplay_hardware


LOGICAL_WIDTH = 280
LOGICAL_HEIGHT = 240
SCREEN_WIDTH = 240
SCREEN_HEIGHT = 280
TARGET_FPS = 18
TILE_SIZE = 18
MAZE_X = 5
MAZE_Y = 34
HUD_Y = 13
MAZE_COLS = 15
MAZE_ROWS = 11
PACMAN_RADIUS = 6
PACMAN_SPEED = 61.0
GHOST_SPEED = 45.0
FRIGHTENED_SPEED = 36.0
POWER_DURATION = 7.0
WHISPLAY_JOY_MODEL = "whisplay-joy"

POWER_PELLET_TILES = ((1, 1), (13, 1), (1, 9), (13, 9))
PACMAN_SPAWN_TILE = (7, 9)
GHOST_SPAWN_TILES = ((5, 5), (7, 5), (9, 5))
EMPTY_SPAWN_TILES = (PACMAN_SPAWN_TILE, *GHOST_SPAWN_TILES)
GHOST_STYLES = (
    ("right", (244, 67, 54)),
    ("left", (247, 139, 196)),
    ("left", (66, 217, 232)),
)

DIRECTION_VECTOR = {
    "up": (0.0, -1.0),
    "right": (1.0, 0.0),
    "down": (0.0, 1.0),
    "left": (-1.0, 0.0),
}
OPPOSITE_DIRECTION = {
    "up": "down",
    "right": "left",
    "down": "up",
    "left": "right",
}

# The LCD framebuffer is rotated clockwise into landscape orientation.
ROTATED_CONTROL_DIRECTION = {
    "up": "left",
    "right": "up",
    "down": "right",
    "left": "down",
}


def tile_center(col: int, row: int) -> tuple[float, float]:
    return (
        MAZE_X + col * TILE_SIZE + TILE_SIZE / 2,
        MAZE_Y + row * TILE_SIZE + TILE_SIZE / 2,
    )


def maze_tile_has_exit(maze, tile: tuple[int, int]) -> bool:
    col, row = tile
    return any(
        maze[row + delta_row][col + delta_col] != "#"
        for delta_col, delta_row in ((1, 0), (-1, 0), (0, 1), (0, -1))
    )


def generate_maze(rng: random.Random) -> tuple[str, ...]:
    """Generate a connected 15x11 maze with a few extra loops."""
    grid = [["#" for _col in range(MAZE_COLS)] for _row in range(MAZE_ROWS)]
    nodes = [
        (col, row)
        for row in range(1, MAZE_ROWS - 1, 2)
        for col in range(1, MAZE_COLS - 1, 2)
    ]
    start = rng.choice(nodes)
    stack = [start]
    visited = {start}
    grid[start[1]][start[0]] = "."
    while stack:
        col, row = stack[-1]
        neighbors = []
        for delta_col, delta_row in ((2, 0), (-2, 0), (0, 2), (0, -2)):
            next_col = col + delta_col
            next_row = row + delta_row
            candidate = (next_col, next_row)
            if candidate in visited or candidate not in nodes:
                continue
            neighbors.append(candidate)
        if not neighbors:
            stack.pop()
            continue
        next_col, next_row = rng.choice(neighbors)
        grid[(row + next_row) // 2][(col + next_col) // 2] = "."
        grid[next_row][next_col] = "."
        visited.add((next_col, next_row))
        stack.append((next_col, next_row))

    loop_candidates = []
    for row in range(1, MAZE_ROWS - 1):
        for col in range(1, MAZE_COLS - 1):
            if grid[row][col] != "#":
                continue
            horizontal = grid[row][col - 1] == "." and grid[row][col + 1] == "."
            vertical = grid[row - 1][col] == "." and grid[row + 1][col] == "."
            if horizontal != vertical:
                loop_candidates.append((col, row))
    rng.shuffle(loop_candidates)
    for col, row in loop_candidates[:rng.randint(4, 8)]:
        grid[row][col] = "."

    for col, row in POWER_PELLET_TILES:
        grid[row][col] = "o"
    for col, row in EMPTY_SPAWN_TILES:
        grid[row][col] = " "
    maze = tuple("".join(row) for row in grid)
    if not all(maze_tile_has_exit(maze, tile) for tile in EMPTY_SPAWN_TILES):
        raise RuntimeError("generated maze contains a blocked actor spawn")
    return maze


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
        try:
            setup_audio_mixer()
            pygame.mixer.pre_init(22050, size=-16, channels=1)
            pygame.mixer.init()
            self._make("start", 420, 920, 0.20, 0.28)
            self._make("pellet", 760, 620, 0.045, 0.16, pulse=True)
            self._make("power", 280, 980, 0.18, 0.34, pulse=True)
            self._make("ghost", 1120, 420, 0.16, 0.38)
            self._make("death", 620, 90, 0.42, 0.48, pulse=True)
            self._make("clear", 520, 1240, 0.30, 0.34)
            self.enabled = True
        except Exception as exc:
            print(f"Pac-Man audio disabled: {exc}", file=sys.stderr)

    def _make(self, name, start, end, duration, volume, pulse=False):
        path = os.path.join(tempfile.gettempdir(), f"whisplay-pacman-{name}.wav")
        synthesize_tone(path, start, end, duration, volume, pulse=pulse)
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
class Actor:
    x: float
    y: float
    direction: str


@dataclass
class Ghost(Actor):
    color: tuple[int, int, int]
    spawn_col: int
    spawn_row: int
    respawn_at: float = 0.0


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


class PacManGame:
    def __init__(self):
        self.board = create_whisplay_hardware(
            app_id=os.getenv("WHISPLAY_APP_ID", "whisplay-pacman"),
            display_name="Pac-Man",
            icon="PM",
            exit_gesture="none",
            priority=29,
            use_daemon_default_log=True,
            supported_models=(WHISPLAY_JOY_MODEL,),
        )
        if self.board.get_model() != WHISPLAY_JOY_MODEL:
            self.board.cleanup()
            raise RuntimeError("Pac-Man requires whisplay-joy")
        self.board.set_backlight(100)
        self.running = True
        self.lock = threading.Lock()
        self.sounds = SoundEffects()
        self.font = ImageFont.load_default()
        self.rng = random.Random()
        self.score = 0
        self.lives = 3
        self.level = 1
        self.state = "ready"
        self.menu_open = False
        self.menu_selection = 0
        self.menu_opened_at = 0.0
        self.current_direction = "left"
        self.desired_direction = "left"
        self.power_until = 0.0
        self.pellets: set[tuple[int, int]] = set()
        self.power_pellets: set[tuple[int, int]] = set()
        self.pacman = Actor(*tile_center(*PACMAN_SPAWN_TILE), "left")
        self.ghosts: list[Ghost] = []
        self.frame_index = 0
        self.last_pellet_sound_at = 0.0
        self.maze: tuple[str, ...] = ()
        self._register_callbacks()
        self._reset_game()

    def _register_callbacks(self):
        self.board.on_button_press(self._on_button_press)
        self.board.on_joystick_press(self._on_direction_press)
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

    def _on_button_press(self):
        with self.lock:
            if self.menu_open:
                return
            if self.state == "ready":
                self.state = "playing"
                self.sounds.play("start")
            elif self.state == "game_over":
                self._reset_game()
                self.state = "playing"
                self.sounds.play("start")
            elif self.state == "won":
                self.level += 1
                self._reset_level("playing")
                self.sounds.play("start")

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
            self.desired_direction = direction
            if self.state == "ready":
                self.state = "playing"
                self.sounds.play("start")

    def _handle_menu_center(self):
        if not self.menu_open:
            self.menu_open = True
            self.menu_selection = 0
            self.menu_opened_at = time.monotonic()
            return
        if self.menu_selection == 1:
            self.running = False
            return
        self._resume_from_menu()

    def _resume_from_menu(self):
        paused_for = max(0.0, time.monotonic() - self.menu_opened_at)
        self.menu_open = False
        self.menu_opened_at = 0.0
        if self.power_until > 0.0:
            self.power_until += paused_for
        for ghost in self.ghosts:
            if ghost.respawn_at > 0.0:
                ghost.respawn_at += paused_for

    def _reset_game(self):
        self.score = 0
        self.lives = 3
        self.level = 1
        self._reset_level("ready")

    def _reset_level(self, state: str = "playing"):
        previous_maze = self.maze
        for _attempt in range(6):
            self.maze = generate_maze(self.rng)
            if self.maze != previous_maze:
                break
        self.pellets = set()
        self.power_pellets = set()
        for row, tiles in enumerate(self.maze):
            for col, tile in enumerate(tiles):
                if tile == ".":
                    self.pellets.add((col, row))
                elif tile == "o":
                    self.power_pellets.add((col, row))
        self.state = state
        self.power_until = 0.0
        self._reset_positions()

    def _reset_positions(self):
        self.pacman = Actor(*tile_center(*PACMAN_SPAWN_TILE), "left")
        self.current_direction = "left"
        self.desired_direction = "left"
        self.ghosts = [
            Ghost(*tile_center(col, row), direction, color, col, row)
            for (col, row), (direction, color) in zip(
                GHOST_SPAWN_TILES,
                GHOST_STYLES,
            )
        ]

    def _tile_is_wall(self, col: int, row: int) -> bool:
        if row < 0 or row >= len(self.maze):
            return True
        if col < 0 or col >= len(self.maze[0]):
            return True
        return self.maze[row][col] == "#"

    def _can_move_to(self, x: float, y: float, radius: float = PACMAN_RADIUS) -> bool:
        for corner_x in (x - radius, x + radius):
            for corner_y in (y - radius, y + radius):
                col = int((corner_x - MAZE_X) // TILE_SIZE)
                row = int((corner_y - MAZE_Y) // TILE_SIZE)
                if self._tile_is_wall(col, row):
                    return False
        return True

    @staticmethod
    def _actor_tile(actor: Actor) -> tuple[int, int]:
        return (
            int((actor.x - MAZE_X) // TILE_SIZE),
            int((actor.y - MAZE_Y) // TILE_SIZE),
        )

    def _near_tile_center(self, actor: Actor, tolerance: float = 2.8):
        col, row = self._actor_tile(actor)
        center_x, center_y = tile_center(col, row)
        return abs(actor.x - center_x) <= tolerance and abs(actor.y - center_y) <= tolerance

    def _try_turn_pacman(self):
        if self.desired_direction == self.current_direction:
            return
        if self.desired_direction == OPPOSITE_DIRECTION[self.current_direction]:
            self.current_direction = self.desired_direction
            self.pacman.direction = self.current_direction
            return
        if not self._near_tile_center(self.pacman):
            return
        col, row = self._actor_tile(self.pacman)
        center_x, center_y = tile_center(col, row)
        dx, dy = DIRECTION_VECTOR[self.desired_direction]
        if self._can_move_to(center_x + dx * 2, center_y + dy * 2):
            self.pacman.x = center_x
            self.pacman.y = center_y
            self.current_direction = self.desired_direction
            self.pacman.direction = self.current_direction

    def _move_actor(self, actor: Actor, direction: str, distance: float) -> bool:
        dx, dy = DIRECTION_VECTOR[direction]
        next_x = actor.x + dx * distance
        next_y = actor.y + dy * distance
        if not self._can_move_to(next_x, next_y):
            return False
        actor.x = next_x
        actor.y = next_y
        actor.direction = direction
        return True

    def _consume_pellet(self, now: float):
        tile = self._actor_tile(self.pacman)
        if tile in self.pellets:
            self.pellets.remove(tile)
            self.score += 10
            if now - self.last_pellet_sound_at >= 0.08:
                self.sounds.play("pellet")
                self.last_pellet_sound_at = now
        elif tile in self.power_pellets:
            self.power_pellets.remove(tile)
            self.score += 50
            self.power_until = now + POWER_DURATION
            self.sounds.play("power")
        if not self.pellets and not self.power_pellets:
            self.state = "won"
            self.sounds.play("clear")

    def _legal_ghost_directions(self, ghost: Ghost) -> list[str]:
        legal = []
        for direction, (dx, dy) in DIRECTION_VECTOR.items():
            if self._can_move_to(ghost.x + dx * 3, ghost.y + dy * 3):
                legal.append(direction)
        if len(legal) > 1:
            reverse = OPPOSITE_DIRECTION[ghost.direction]
            legal = [direction for direction in legal if direction != reverse] or legal
        return legal

    def _choose_ghost_direction(self, ghost: Ghost, frightened: bool):
        legal = self._legal_ghost_directions(ghost)
        if not legal:
            return OPPOSITE_DIRECTION[ghost.direction]
        if frightened or self.rng.random() < 0.24:
            return self.rng.choice(legal)
        target_col, target_row = self._actor_tile(self.pacman)

        def distance(direction: str):
            dx, dy = DIRECTION_VECTOR[direction]
            col, row = self._actor_tile(ghost)
            return abs(col + int(dx) - target_col) + abs(row + int(dy) - target_row)

        return min(legal, key=distance)

    def _update_pacman(self, now: float, dt: float):
        self._try_turn_pacman()
        if not self._move_actor(self.pacman, self.current_direction, PACMAN_SPEED * dt):
            col, row = self._actor_tile(self.pacman)
            self.pacman.x, self.pacman.y = tile_center(col, row)
        self._consume_pellet(now)

    def _update_ghosts(self, now: float, dt: float):
        frightened = now < self.power_until
        for ghost in self.ghosts:
            if ghost.respawn_at:
                if now < ghost.respawn_at:
                    continue
                ghost.x, ghost.y = tile_center(ghost.spawn_col, ghost.spawn_row)
                ghost.respawn_at = 0.0
            if self._near_tile_center(ghost, tolerance=2.2):
                col, row = self._actor_tile(ghost)
                ghost.x, ghost.y = tile_center(col, row)
                ghost.direction = self._choose_ghost_direction(ghost, frightened)
            speed = FRIGHTENED_SPEED if frightened else GHOST_SPEED + min(8, self.level)
            if not self._move_actor(ghost, ghost.direction, speed * dt):
                ghost.direction = self._choose_ghost_direction(ghost, frightened)

    def _check_ghost_collisions(self, now: float):
        for ghost in self.ghosts:
            if ghost.respawn_at:
                continue
            distance_squared = (ghost.x - self.pacman.x) ** 2 + (ghost.y - self.pacman.y) ** 2
            if distance_squared > 12 ** 2:
                continue
            if now < self.power_until:
                ghost.respawn_at = now + 2.8
                ghost.x, ghost.y = -40.0, -40.0
                self.score += 200
                self.sounds.play("ghost")
                continue
            self.lives -= 1
            self.sounds.play("death")
            if self.lives <= 0:
                self.state = "game_over"
            else:
                self._reset_positions()
            return

    def _update(self, now: float, dt: float):
        if self.menu_open:
            return
        if self.state != "playing":
            return
        self._update_pacman(now, dt)
        if self.state != "playing":
            return
        self._update_ghosts(now, dt)
        self._check_ghost_collisions(now)

    def _draw_maze(self, draw: ImageDraw.ImageDraw):
        draw.rectangle((0, 0, LOGICAL_WIDTH, LOGICAL_HEIGHT), fill=(3, 4, 14))
        for row, tiles in enumerate(self.maze):
            for col, tile in enumerate(tiles):
                x = MAZE_X + col * TILE_SIZE
                y = MAZE_Y + row * TILE_SIZE
                if tile == "#":
                    draw_rounded_rectangle(
                        draw,
                        (x + 1, y + 1, x + TILE_SIZE - 2, y + TILE_SIZE - 2),
                        radius=4,
                        fill=(10, 34, 91),
                        outline=(38, 111, 255),
                        width=2,
                    )
        for col, row in self.pellets:
            center_x, center_y = tile_center(col, row)
            draw.ellipse(
                (center_x - 1.5, center_y - 1.5, center_x + 1.5, center_y + 1.5),
                fill=(255, 221, 168),
            )
        pulse = 3 + (self.frame_index // 4) % 2
        for col, row in self.power_pellets:
            center_x, center_y = tile_center(col, row)
            draw.ellipse(
                (center_x - pulse, center_y - pulse, center_x + pulse, center_y + pulse),
                fill=(255, 239, 196),
            )

    def _draw_pacman(self, draw: ImageDraw.ImageDraw):
        x = int(self.pacman.x)
        y = int(self.pacman.y)
        mouth = 12 + (self.frame_index % 6) * 4
        heading = {"right": 0, "down": 90, "left": 180, "up": 270}[self.current_direction]
        draw.pieslice(
            (x - PACMAN_RADIUS, y - PACMAN_RADIUS, x + PACMAN_RADIUS, y + PACMAN_RADIUS),
            heading + mouth,
            heading + 360 - mouth,
            fill=(255, 220, 38),
        )

    def _draw_ghost(self, draw: ImageDraw.ImageDraw, ghost: Ghost, now: float):
        if ghost.respawn_at:
            return
        x = int(ghost.x)
        y = int(ghost.y)
        frightened = now < self.power_until
        color = (45, 74, 220) if frightened else ghost.color
        draw.ellipse((x - 7, y - 7, x + 7, y + 6), fill=color)
        draw.rectangle((x - 7, y, x + 7, y + 6), fill=color)
        for foot_x in (-5, 0, 5):
            draw.ellipse((x + foot_x - 3, y + 3, x + foot_x + 3, y + 9), fill=color)
        eye_dx, eye_dy = DIRECTION_VECTOR[ghost.direction]
        for offset in (-3, 3):
            draw.ellipse((x + offset - 2, y - 3, x + offset + 2, y + 2), fill=(245, 248, 255))
            draw.ellipse(
                (
                    x + offset - 1 + int(eye_dx),
                    y - 2 + int(eye_dy),
                    x + offset + 1 + int(eye_dx),
                    y + int(eye_dy),
                ),
                fill=(20, 30, 80),
            )

    def _draw_hud(self, draw: ImageDraw.ImageDraw):
        draw.text((8, HUD_Y), f"SCORE {self.score:05d}", font=self.font, fill=(255, 231, 92))
        draw.text((112, HUD_Y), f"LIFE {self.lives}", font=self.font, fill=(255, 231, 92))
        draw.text((212, HUD_Y), f"LV {self.level}", font=self.font, fill=(255, 231, 92))
        if self.state == "ready":
            self._draw_overlay(draw, "PAC-MAN", "BUTTON / D-PAD TO START")
        elif self.state == "game_over":
            self._draw_overlay(draw, "GAME OVER", "BUTTON TO RESTART")
        elif self.state == "won":
            self._draw_overlay(draw, "MAZE CLEAR", "BUTTON FOR NEXT LEVEL")

    def _draw_overlay(self, draw: ImageDraw.ImageDraw, title: str, subtitle: str):
        draw_rounded_rectangle(
            draw,
            (61, 96, 219, 151),
            radius=8,
            fill=(5, 8, 22),
            outline=(255, 219, 44),
            width=2,
        )
        title_x = 140 - len(title) * 3
        subtitle_x = 140 - len(subtitle) * 3
        draw.text((title_x, 107), title, font=self.font, fill=(255, 219, 44))
        draw.text((subtitle_x, 128), subtitle, font=self.font, fill=(242, 242, 230))

    def _draw_game_menu(self, draw: ImageDraw.ImageDraw):
        draw_rounded_rectangle(
            draw,
            (67, 68, 213, 174),
            radius=9,
            fill=(5, 8, 22),
            outline=(255, 219, 44),
            width=2,
        )
        title = "GAME MENU"
        draw.text((140 - len(title) * 3, 78), title, font=self.font, fill=(255, 219, 44))
        for index, label in enumerate(("RESUME", "EXIT TO HOME")):
            top = 101 + index * 27
            selected = index == self.menu_selection
            if selected:
                draw_rounded_rectangle(
                    draw,
                    (82, top - 4, 198, top + 15),
                    radius=4,
                    fill=(91, 76, 18),
                    outline=(255, 231, 92),
                    width=1,
                )
            color = (255, 248, 209) if selected else (152, 174, 181)
            draw.text((140 - len(label) * 3, top + 1), label, font=self.font, fill=color)
        hint = "UP/DOWN  CENTER SELECT"
        draw.text((140 - len(hint) * 3, 159), hint, font=self.font, fill=(132, 178, 193))

    def render(self, now: float):
        image = Image.new("RGB", (LOGICAL_WIDTH, LOGICAL_HEIGHT))
        draw = ImageDraw.Draw(image)
        self._draw_maze(draw)
        self._draw_pacman(draw)
        for ghost in self.ghosts:
            self._draw_ghost(draw, ghost, now)
        self._draw_hud(draw)
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
            self.sounds.cleanup()
            self.board.cleanup()


if __name__ == "__main__":
    PacManGame().run()
