#!/usr/bin/env python3

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.append(RUNTIME_DIR)

from whisplay_client import create_whisplay_hardware


DATA_DIR = os.path.join(SCRIPT_DIR, "data")
TEST_IMAGE_PATH = os.path.join(DATA_DIR, "test.png")
TEST_WAV_PATH = os.path.join(DATA_DIR, "test.wav")
RECORD_FILE_PATH = os.path.join(DATA_DIR, "run_test_record.wav")
MAX_RECORD_SEC = 12
INTRO_COUNTDOWN_SEC = 5


@dataclass
class TestStage:
    key: str
    title: str
    subtitle: str


class RunTestFlow:
    def __init__(self, card_index: int | None = None):
        self.board = create_whisplay_hardware(
            app_id=os.getenv("WHISPLAY_APP_ID", "whisplay-run-test"),
            display_name="Run Test",
            icon="T",
            use_daemon_default_log=True,
        )
        self.board.set_backlight(70)

        if card_index is None:
            self.card_index, self.card_name = self._find_whisplay_card()
        else:
            self.card_index = card_index
            self.card_name = self._find_card_name_for_index(card_index)
        self.lock = threading.RLock()
        self.running = True
        self.phase = "intro"
        self.step_index = 0
        self.results = {
            "display": None,
            "led": None,
            "speaker": None,
            "button": None,
            "record": None,
        }
        self.steps = [
            TestStage("display", "Display Test", "Screen colors and image"),
            TestStage("led", "LED Test", "RGB indicator changes"),
            TestStage("speaker", "Speaker Test", "Play confirmation sound"),
            TestStage("button", "Button Test", "Press and release detection"),
            TestStage("record", "Mic Test", "Hold to record, release to play"),
            TestStage("done", "Test Complete", "All checks finished"),
        ]
        self.button_press_seen = False
        self.button_release_seen = False
        self.record_started_at = 0.0
        self.record_result = "Not started"
        self._record_proc = None
        self._play_proc = None
        self._record_thread = None
        self._play_thread = None
        self._record_error = None
        self._playback_error = None
        self._record_completed = False
        self._image_frame = self._load_test_image()
        self._logo_image = self._load_logo_image()

        self.title_font = self._load_font(24, bold=True)
        self.body_font = self._load_font(18, bold=False)
        self.body_compact_font = self._load_font(16, bold=False)
        self.small_font = self._load_font(14, bold=False)
        self.small_compact_font = self._load_font(12, bold=False)

        self.board.on_button_press(self._on_button_press)
        self.board.on_button_release(self._on_button_release)
        if hasattr(self.board, "on_exit_request"):
            self.board.on_exit_request(self._on_exit_request)
        if hasattr(self.board, "on_focus_revoked"):
            self.board.on_focus_revoked(self._on_focus_revoked)

        self._setup_mixer()

    def _load_font(self, size: int, bold: bool):
        candidates = []
        if bold:
            candidates.extend(
                [
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
                ]
            )
        candidates.extend(
            [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            ]
        )
        for path in candidates:
            if not os.path.exists(path):
                continue
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _find_whisplay_card(self) -> tuple[int, str]:
        try:
            with open("/proc/asound/cards", "r", encoding="utf-8") as fp:
                fallback = None
                for line in fp:
                    lower = line.lower()
                    parts = line.strip().split()
                    if not parts:
                        continue
                    card_index = int(parts[0])
                    card_name = parts[1].strip("[]:") if len(parts) > 1 else str(card_index)
                    if "whisplaysound" in lower:
                        return card_index, card_name
                    if fallback is None and ("wm8960" in lower or "es8389" in lower):
                        fallback = (card_index, card_name)
                if fallback is not None:
                    return fallback
        except Exception:
            pass
        return 1, "1"

    def _find_wm8960_card(self) -> int:
        card_index, _card_name = self._find_whisplay_card()
        return card_index

    def _find_card_name_for_index(self, card_index: int) -> str:
        try:
            with open("/proc/asound/cards", "r", encoding="utf-8") as fp:
                prefix = str(card_index)
                for line in fp:
                    parts = line.strip().split()
                    if parts and parts[0] == prefix and len(parts) > 1:
                        return parts[1].strip("[]:")
        except Exception:
            pass
        return str(card_index)

    def _alsa_playback_devices(self) -> list[str]:
        if self.card_name == "whisplaysound":
            return ["whisplaysound", "default"]
        devices = []
        if self.card_name and not self.card_name.isdigit():
            devices.append(f"plughw:CARD={self.card_name}")
        devices.append(f"plughw:{self.card_index}")
        return devices

    def _alsa_capture_device(self) -> str:
        return self._alsa_playback_devices()[0]

    def _setup_mixer(self):
        card = str(self.card_index)
        unified_commands = [
            ["amixer", "-c", card, "cset", "name=speaker", "80"],
            ["amixer", "-c", card, "cset", "name=mic", "50"],
        ]
        commands = [
            ["amixer", "-c", card, "sset", "Left Output Mixer PCM", "on"],
            ["amixer", "-c", card, "sset", "Right Output Mixer PCM", "on"],
            ["amixer", "-c", card, "sset", "Speaker", "121"],
            ["amixer", "-c", card, "sset", "Playback", "230"],
            ["amixer", "-c", card, "sset", "Left Input Mixer Boost", "on"],
            ["amixer", "-c", card, "sset", "Right Input Mixer Boost", "on"],
            ["amixer", "-c", card, "sset", "Capture", "45"],
            ["amixer", "-c", card, "sset", "ADC PCM", "195"],
            ["amixer", "-c", card, "sset", "Left Input Boost Mixer LINPUT1", "2"],
            ["amixer", "-c", card, "sset", "Right Input Boost Mixer RINPUT1", "2"],
        ]
        for command in unified_commands + commands:
            try:
                subprocess.run(command, check=False, capture_output=True, timeout=5)
            except Exception:
                pass

    def _on_exit_request(self, _payload=None):
        self.running = False

    def _on_focus_revoked(self, _payload=None):
        self.running = False

    def _on_button_press(self):
        with self.lock:
            if self.phase == "button_wait_press":
                self.button_press_seen = True
                self.phase = "button_wait_release"
                self.board.set_rgb(0, 180, 255)
                self._show_status(
                    "Button Test",
                    [
                        "Current test:",
                        "Button press detected.",
                        "",
                        "Now release the button",
                        "to finish this step.",
                    ],
                    accent=(0, 150, 255),
                    footer="Waiting for release...",
                )
            elif self.phase == "record_ready":
                self._start_recording()

    def _on_button_release(self):
        with self.lock:
            if self.phase == "button_wait_release":
                self.button_release_seen = True
                self.phase = "button_done"
                self.results["button"] = True
                self.board.set_rgb(0, 255, 0)
                self._show_status(
                    "Button Test",
                    [
                        "Current test:",
                        "Button press detected.",
                        "Button release detected.",
                        "",
                        "Button path is working.",
                    ],
                    accent=(0, 190, 90),
                    footer="Button test passed",
                )
            elif self.phase == "record_recording":
                self._stop_recording()

    def _rgb565_bytes(self, image: Image.Image) -> bytes:
        rgb = image.convert("RGB")
        output = bytearray()
        for y in range(rgb.height):
            for x in range(rgb.width):
                r, g, b = rgb.getpixel((x, y))
                value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                output.append((value >> 8) & 0xFF)
                output.append(value & 0xFF)
        return bytes(output)

    def _wrap_text(self, draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
        if not text:
            return [""]
        words = text.split()
        if not words:
            return [text]

        lines: list[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if (bbox[2] - bbox[0]) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    def _measure_text_height(self, draw: ImageDraw.ImageDraw, text: str, font, default: int) -> int:
        if not text:
            return default
        bbox = draw.textbbox((0, 0), text, font=font)
        return max(default, bbox[3] - bbox[1])

    def _accent_text_color(self, accent) -> tuple[int, int, int]:
        r, g, b = accent
        luminance = (0.299 * r) + (0.587 * g) + (0.114 * b)
        return (18, 24, 32) if luminance >= 170 else (255, 255, 255)

    def _render_panel(
        self,
        title: str,
        lines: list[str],
        accent=(60, 150, 255),
        footer: str = "",
        background=(11, 16, 24),
        step_override: int | None = None,
    ) -> bytes:
        width, height = self.board.LCD_WIDTH, self.board.LCD_HEIGHT
        image = Image.new("RGB", (width, height), background)
        draw = ImageDraw.Draw(image)
        content_width = width - 48

        panel = (12, 16, width - 12, height - 16)
        draw.rounded_rectangle(panel, radius=18, fill=(20, 28, 40), outline=(40, 55, 72), width=2)
        draw.rounded_rectangle((20, 22, width - 20, 58), radius=12, fill=accent)

        current_step = self.step_index + 1 if step_override is None else step_override
        step_label = f"STEP {max(1, current_step)}/{len(self.steps)}"
        draw.text((30, 31), step_label, fill=self._accent_text_color(accent), font=self.small_font)
        title_lines = self._wrap_text(draw, title, self.title_font, content_width)
        title_y = 74
        title_height = self._measure_text_height(draw, "Ag", self.title_font, 22)
        for line in title_lines[:2]:
            draw.text((24, title_y), line, fill=(255, 255, 255), font=self.title_font)
            title_y += title_height + 2

        footer_font = self.small_font
        footer_lines = self._wrap_text(draw, footer, footer_font, width - 56) if footer else []
        footer_height = 0
        footer_line_height = self._measure_text_height(draw, "Ag", footer_font, 13)
        if footer_lines:
            footer_height = max(42, 16 + len(footer_lines) * footer_line_height)

        wrapped_options = []
        for font, gap, blank_gap in [
            (self.body_font, 6, 12),
            (self.body_compact_font, 4, 10),
        ]:
            wrapped_lines: list[str] = []
            for line in lines:
                if line == "":
                    wrapped_lines.append("")
                else:
                    wrapped_lines.extend(self._wrap_text(draw, line, font, content_width))
            line_height = self._measure_text_height(draw, "Ag", font, 17)
            total_height = 0
            for wrapped in wrapped_lines:
                total_height += blank_gap if wrapped == "" else line_height + gap
            wrapped_options.append((font, wrapped_lines, line_height, gap, blank_gap, total_height))

        available_bottom = height - 32 - footer_height - 12
        body_font, wrapped_lines, line_height, gap, blank_gap, _ = wrapped_options[0]
        for option in wrapped_options:
            if title_y + option[5] <= available_bottom:
                body_font, wrapped_lines, line_height, gap, blank_gap, _ = option
                break
            body_font, wrapped_lines, line_height, gap, blank_gap, _ = option

        y = title_y + 10
        for line in wrapped_lines:
            if y >= available_bottom:
                break
            if line == "":
                y += blank_gap
                continue
            draw.text((24, y), line, fill=(214, 225, 236), font=body_font)
            y += line_height + gap

        if footer_lines:
            footer_top = height - 22 - footer_height
            draw.rounded_rectangle((20, footer_top, width - 20, height - 22), radius=12, fill=(28, 37, 50))
            footer_y = footer_top + 8
            for line in footer_lines[:3]:
                draw.text((28, footer_y), line, fill=(150, 205, 255), font=footer_font)
                footer_y += footer_line_height

        return self._rgb565_bytes(image)

    def _show_status(
        self,
        title: str,
        lines: list[str],
        accent=(60, 150, 255),
        footer: str = "",
        background=(11, 16, 24),
        step_override: int | None = None,
    ):
        frame = self._render_panel(
            title=title,
            lines=lines,
            accent=accent,
            footer=footer,
            background=background,
            step_override=step_override,
        )
        self.board.draw_image(0, 0, self.board.LCD_WIDTH, self.board.LCD_HEIGHT, frame)

    def _load_test_image(self) -> bytes | None:
        if not os.path.exists(TEST_IMAGE_PATH):
            return None
        try:
            img = Image.open(TEST_IMAGE_PATH).convert("RGB")
            screen_w, screen_h = self.board.LCD_WIDTH, self.board.LCD_HEIGHT
            src_w, src_h = img.size
            aspect = src_w / src_h
            screen_aspect = screen_w / screen_h
            if aspect > screen_aspect:
                new_h = screen_h
                new_w = int(new_h * aspect)
                img = img.resize((new_w, new_h))
                offset = (new_w - screen_w) // 2
                img = img.crop((offset, 0, offset + screen_w, screen_h))
            else:
                new_w = screen_w
                new_h = int(new_w / aspect)
                img = img.resize((new_w, new_h))
                offset = (new_h - screen_h) // 2
                img = img.crop((0, offset, screen_w, offset + screen_h))
            return self._rgb565_bytes(img)
        except Exception as exc:
            print(f"Failed to load test image: {exc}")
            return None

    def _load_logo_image(self) -> Image.Image | None:
        if not os.path.exists(TEST_IMAGE_PATH):
            return None
        try:
            image = Image.open(TEST_IMAGE_PATH).convert("RGB")
            max_width = self.board.LCD_WIDTH - 56
            max_height = 148
            image.thumbnail((max_width, max_height))
            return image
        except Exception as exc:
            print(f"Failed to load intro logo: {exc}")
            return None

    def _show_intro_countdown(self, seconds_left: int):
        width, height = self.board.LCD_WIDTH, self.board.LCD_HEIGHT
        image = Image.new("RGB", (width, height), (10, 14, 22))
        draw = ImageDraw.Draw(image)

        draw.rounded_rectangle((12, 16, width - 12, height - 16), radius=18, fill=(20, 28, 40), outline=(40, 55, 72), width=2)
        if self._logo_image is not None:
            logo = self._logo_image
            logo_x = (width - logo.width) // 2
            logo_y = 34
            image.paste(logo, (logo_x, logo_y))
        else:
            draw.text((70, 76), "Whisplay", fill=(255, 255, 255), font=self.title_font)

        countdown_text = f"Test will start in {seconds_left}s..."
        countdown_box = (24, 196, width - 24, 244)
        draw.rounded_rectangle(countdown_box, radius=14, fill=(228, 236, 245))
        count_color = (16, 22, 30)
        count_font = self.body_compact_font
        lines = self._wrap_text(draw, countdown_text, count_font, width - 72)
        line_height = self._measure_text_height(draw, "Ag", count_font, 16)
        total_height = len(lines) * line_height
        start_y = countdown_box[1] + ((countdown_box[3] - countdown_box[1] - total_height) // 2) - 2
        for index, line in enumerate(lines[:2]):
            bbox = draw.textbbox((0, 0), line, font=count_font)
            line_width = bbox[2] - bbox[0]
            draw.text((((width - line_width) // 2), start_y + index * line_height), line, fill=count_color, font=count_font)

        self.board.draw_image(0, 0, width, height, self._rgb565_bytes(image))

    def _show_color_frame(self, color565: int, title: str, label: str):
        self.board.fill_screen(color565)
        overlay = self._render_panel(
            title,
            [label],
            accent=(255, 255, 255),
            footer="Display should look solid and stable",
            background=(0, 0, 0),
            step_override=1,
        )
        self.board.draw_image(0, 0, self.board.LCD_WIDTH, self.board.LCD_HEIGHT, overlay)

    def _wait(self, duration: float) -> bool:
        end_at = time.time() + duration
        while self.running and time.time() < end_at:
            time.sleep(0.05)
        return self.running

    def _play_wav(self, path: str) -> bool:
        for device in self._alsa_playback_devices():
            try:
                proc = subprocess.Popen(
                    ["aplay", "-D", device, path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self._play_proc = proc
                while self.running and proc.poll() is None:
                    time.sleep(0.05)
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=1)
                if proc.returncode == 0:
                    if device.startswith("plughw:"):
                        print(f"Playback succeeded via {device} after hw fallback")
                    return True
                stderr = proc.stderr.read() if proc.stderr else ""
                print(f"Playback via {device} failed: {stderr.strip()}")
            except Exception as exc:
                print(f"Playback via {device} failed: {exc}")
            finally:
                self._play_proc = None
        return False

    def _stop_process(self, proc):
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=1)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _start_recording(self):
        os.makedirs(os.path.dirname(RECORD_FILE_PATH), exist_ok=True)
        self.record_result = "Recording..."
        self.record_started_at = time.time()
        self._record_completed = False
        self._record_error = None
        self._playback_error = None
        self.phase = "record_recording"
        self.board.set_rgb(255, 0, 0)
        self._show_status(
            "Mic Test",
            [
                "Current test:",
                "Recording from microphone.",
                "",
                "Keep holding the button.",
                "Release to stop and play back.",
            ],
            accent=(220, 58, 58),
            footer="Recording...",
        )
        self._record_thread = threading.Thread(target=self._record_worker, daemon=True)
        self._record_thread.start()

    def _record_worker(self):
        capture_device = self._alsa_capture_device()
        try:
            self._record_proc = subprocess.Popen(
                [
                    "arecord",
                    "-D",
                    capture_device,
                    "-f",
                    "S16_LE",
                    "-r",
                    "48000",
                    "-c",
                    "2",
                    "-t",
                    "wav",
                    "-d",
                    str(MAX_RECORD_SEC),
                    RECORD_FILE_PATH,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._record_proc.wait()
        except Exception as exc:
            self._record_error = str(exc)
        finally:
            self._record_proc = None

        if not self.running:
            return
        if self._record_error:
            with self.lock:
                self.phase = "record_ready"
                self.record_result = "Record failed"
                self.results["record"] = False
                self.board.set_rgb(255, 120, 0)
                self._show_status(
                    "Mic Test",
                    [
                        "Current test:",
                        "Recording could not start.",
                        "",
                        "Hold the button to try again.",
                    ],
                    accent=(255, 130, 0),
                    footer=self._record_error[:40],
                )
            return

        if not os.path.exists(RECORD_FILE_PATH) or os.path.getsize(RECORD_FILE_PATH) <= 256:
            with self.lock:
                self.phase = "record_ready"
                self.record_result = "Record too short"
                self.results["record"] = False
                self.board.set_rgb(255, 120, 0)
                self._show_status(
                    "Mic Test",
                    [
                        "Current test:",
                        "No usable recording captured.",
                        "",
                        "Hold the button longer",
                        "and release to retry.",
                    ],
                    accent=(255, 130, 0),
                    footer="Recording file is empty",
                )
            return

        self._record_completed = True
        self._start_playback_thread()

    def _stop_recording(self):
        try:
            if self._record_proc is not None and self._record_proc.poll() is None:
                self._record_proc.send_signal(signal.SIGINT)
        except Exception:
            pass
        self.record_result = "Recorded"
        self.phase = "record_finishing"
        self._show_status(
            "Mic Test",
            [
                "Current test:",
                "Recording stopped.",
                "",
                "Preparing playback...",
            ],
            accent=(255, 130, 0),
            footer="Please wait",
        )

    def _start_playback_thread(self):
        with self.lock:
            self.phase = "record_playing"
            self.board.set_rgb(0, 255, 0)
            self._show_status(
                "Mic Test",
                [
                    "Current test:",
                    "Playing back the recording.",
                    "",
                    "You should hear",
                    "your recorded voice.",
                ],
                accent=(0, 180, 90),
                footer="Playback in progress",
            )
        self._play_thread = threading.Thread(target=self._playback_worker, daemon=True)
        self._play_thread.start()

    def _playback_worker(self):
        ok = self._play_wav(RECORD_FILE_PATH)
        if not self.running:
            return
        with self.lock:
            if ok:
                self.phase = "done"
                self.record_result = "Mic path passed"
                self.results["record"] = True
                self.results["speaker"] = True
                self.board.set_rgb(0, 255, 120)
                self._show_status(
                    "Test Complete",
                    self._final_summary_lines(),
                    accent=(0, 180, 90),
                    footer="Run Test finished",
                    step_override=len(self.steps),
                )
            else:
                self.phase = "record_ready"
                self.record_result = "Playback failed"
                self.results["record"] = False
                self.board.set_rgb(255, 120, 0)
                self._show_status(
                    "Mic Test",
                    [
                        "Current test:",
                        "Playback failed.",
                        "",
                        "Hold the button to retry.",
                    ],
                    accent=(255, 130, 0),
                    footer="Could not play recording",
                )

    def _run_intro(self):
        self.phase = "intro"
        for seconds_left in range(INTRO_COUNTDOWN_SEC, 0, -1):
            self._show_intro_countdown(seconds_left)
            if not self._wait(1.0):
                return False
        return True

    def _run_display_test(self):
        self.step_index = 0
        self.phase = "display"
        for color565, label, accent in [
            (0xF800, "Solid red screen", (255, 90, 90)),
            (0x07E0, "Solid green screen", (90, 255, 140)),
            (0x001F, "Solid blue screen", (100, 160, 255)),
        ]:
            self.board.fill_screen(color565)
            self._show_status(
                "Display Test",
                [
                    "Current test:",
                    label,
                    "",
                    "Check for stable fill",
                    "and no tearing.",
                ],
                accent=accent,
                footer="Display check",
                background=(0, 0, 0),
                step_override=1,
            )
            if not self._wait(0.8):
                return False

        if self._image_frame is not None:
            self.board.draw_image(0, 0, self.board.LCD_WIDTH, self.board.LCD_HEIGHT, self._image_frame)
            overlay = self._render_panel(
                "Display Test",
                [
                    "Current test:",
                    "Reference image rendering.",
                    "",
                    "Check image clarity",
                    "and scaling.",
                ],
                accent=(255, 255, 255),
                footer="Reference image check",
                background=(0, 0, 0),
                step_override=1,
            )
            self.board.draw_image(0, 0, self.board.LCD_WIDTH, self.board.LCD_HEIGHT, overlay)
            if not self._wait(1.0):
                return False
        self.results["display"] = True
        return True

    def _run_led_test(self):
        self.step_index = 1
        self.phase = "led"
        for rgb, label in [
            ((255, 0, 0), "LED should be red"),
            ((0, 255, 0), "LED should be green"),
            ((0, 0, 255), "LED should be blue"),
            ((255, 255, 255), "LED should be white"),
        ]:
            self.board.set_rgb(*rgb)
            self._show_status(
                "LED Test",
                [
                    "Current test:",
                    label,
                    "",
                    "Confirm the LED color",
                    "matches the screen hint.",
                ],
                accent=rgb,
                footer="LED color check",
                step_override=2,
            )
            if not self._wait(0.7):
                return False
        self.board.set_rgb(0, 0, 0)
        self.results["led"] = True
        return True

    def _run_speaker_test(self):
        self.step_index = 2
        self.phase = "speaker"
        self.board.set_rgb(255, 180, 0)
        self._show_status(
            "Speaker Test",
            [
                "Current test:",
                "Play confirmation sound.",
                "",
                "You should hear",
                "the sample audio now.",
            ],
            accent=(255, 180, 0),
            footer="Listening...",
            step_override=3,
        )
        ok = self._play_wav(TEST_WAV_PATH)
        self.results["speaker"] = ok
        self.board.set_rgb(0, 0, 0)
        if not ok:
            self._show_status(
                "Speaker Test",
                [
                    "Current test:",
                    "Sample audio failed to play.",
                    "",
                    "Check WM8960 audio",
                    "and try again.",
                ],
                accent=(255, 80, 80),
                footer="Speaker test failed",
                step_override=3,
            )
            return self._wait(1.2)
        self._show_status(
            "Speaker Test",
            [
                "Current test:",
                "Sample audio finished.",
                "",
                "If you heard it clearly,",
                "speaker path is good.",
            ],
            accent=(80, 220, 120),
            footer="Speaker test passed",
            step_override=3,
        )
        return self._wait(1.0)

    def _final_summary_lines(self) -> list[str]:
        def label(name: str, passed: bool | None) -> str:
            if passed is True:
                return f"{name}: OK"
            if passed is False:
                return f"{name}: CHECK"
            return f"{name}: SKIP"

        return [
            label("Display", self.results["display"]),
            label("LED", self.results["led"]),
            label("Speaker", self.results["speaker"]),
            label("Button", self.results["button"]),
            label("Mic", self.results["record"]),
        ]

    def _run_button_test(self):
        self.step_index = 3
        self.phase = "button_wait_press"
        self.button_press_seen = False
        self.button_release_seen = False
        self._show_status(
            "Button Test",
            [
                "Current test:",
                "Press the hardware button once.",
                "",
                "This step verifies",
                "both press and release.",
            ],
            accent=(0, 150, 255),
            footer="Waiting for button press",
            step_override=4,
        )
        while self.running and self.phase not in {"button_done", "record_ready", "done"}:
            time.sleep(0.05)
        if self.phase == "button_done" and self.running:
            if not self._wait(0.8):
                return False
            self.phase = "record_ready"
        return self.running

    def _run_record_test(self):
        self.step_index = 4
        self.phase = "record_ready"
        self.board.set_rgb(80, 80, 255)
        self._show_status(
            "Mic Test",
            [
                "Current test:",
                "Hold the button to record.",
                "Release to stop and play back.",
                "",
                "This verifies microphone",
                "and speaker loopback.",
            ],
            accent=(80, 110, 255),
            footer="Waiting for hold-to-record",
            step_override=5,
        )
        while self.running and self.phase != "done":
            time.sleep(0.05)
        return self.running

    def cleanup(self):
        self.running = False
        self._stop_process(self._record_proc)
        self._stop_process(self._play_proc)
        self.board.set_rgb(0, 0, 0)
        self.board.cleanup()

    def run(self):
        print("=" * 52)
        print(" Whisplay Run Test")
        print("=" * 52)
        print(f"Using WM8960 card: {self.card_index}")
        print("Flow: display -> LED -> speaker -> button -> mic")
        print("Mic test: hold to record, release to play back")
        print("=" * 52)

        try:
            if not self._run_intro():
                return
            if not self._run_display_test():
                return
            if not self._run_led_test():
                return
            if not self._run_speaker_test():
                return
            if not self._run_button_test():
                return
            self._run_record_test()
            while self.running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("Exiting Run Test")
        finally:
            self.cleanup()


def main():
    parser = argparse.ArgumentParser(description="Whisplay end-to-end hardware test flow")
    parser.add_argument(
        "--card",
        type=int,
        default=None,
        help="Whisplay sound card number, defaults to auto-detect",
    )
    args = parser.parse_args()
    RunTestFlow(card_index=args.card).run()


if __name__ == "__main__":
    main()
