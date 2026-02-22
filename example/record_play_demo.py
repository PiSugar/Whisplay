#!/usr/bin/env python3
"""
Whisplay Recording & Playback Demo — Radxa ZERO 3W / Raspberry Pi

Features:
  - Press button once: start recording (LCD shows recording screen, LED red blink)
  - Press button again: stop recording and auto-play (LCD shows playback screen, LED green)
  - After playback, return to idle state (LCD shows idle screen, LED blue breathing)

Dependencies:
  sudo apt install python3-pil alsa-utils

Usage:
  cd example
  sudo python3 record_play_demo.py
  # Or specify sound card:
  sudo python3 record_play_demo.py --card 1
"""

import sys
import os
import time
import argparse
import threading
import subprocess
import signal

sys.path.append(os.path.abspath("../Driver"))
from WhisPlay import WhisPlayBoard

from PIL import Image, ImageDraw, ImageFont

# ==================== Configuration ====================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RECORD_FILE = os.path.join(SCRIPT_DIR, "data", "recorded.wav")
MAX_RECORD_SEC = 60  # Maximum recording duration (seconds)


# ==================== State Machine ====================
class State:
    IDLE = 0
    RECORDING = 1
    PLAYING = 2


# ==================== LCD Screen Generation ====================
def make_text_image(text, sub_text="", bg_color=(0, 0, 0), text_color=(255, 255, 255),
                    width=240, height=280):
    """Generate RGB565 pixel data with text (for LCD display)"""
    img = Image.new('RGB', (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    # Try to load font, fall back to default
    font_large = None
    font_small = None
    for fpath in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                  "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"]:
        if os.path.exists(fpath):
            try:
                font_large = ImageFont.truetype(fpath, 28)
                font_small = ImageFont.truetype(fpath, 18)
            except Exception:
                pass
            break

    if font_large is None:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Center main text
    bbox = draw.textbbox((0, 0), text, font=font_large)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (width - tw) // 2
    y = (height - th) // 2 - 15
    draw.text((x, y), text, fill=text_color, font=font_large)

    # Sub text
    if sub_text:
        bbox2 = draw.textbbox((0, 0), sub_text, font=font_small)
        tw2 = bbox2[2] - bbox2[0]
        x2 = (width - tw2) // 2
        draw.text((x2, y + th + 15), sub_text, fill=text_color, font=font_small)

    # Convert to RGB565
    pixel_data = []
    for py in range(height):
        for px in range(width):
            r, g, b = img.getpixel((px, py))
            rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            pixel_data.extend([(rgb565 >> 8) & 0xFF, rgb565 & 0xFF])
    return pixel_data


def load_image_rgb565(filepath, screen_width=240, screen_height=280):
    """Load image file as RGB565 pixel data (scale maintaining aspect ratio + center crop)"""
    try:
        img = Image.open(filepath).convert('RGB')
        original_width, original_height = img.size
        aspect_ratio = original_width / original_height
        screen_aspect_ratio = screen_width / screen_height

        if aspect_ratio > screen_aspect_ratio:
            new_height = screen_height
            new_width = int(new_height * aspect_ratio)
            resized_img = img.resize((new_width, new_height))
            offset_x = (new_width - screen_width) // 2
            cropped_img = resized_img.crop(
                (offset_x, 0, offset_x + screen_width, screen_height))
        else:
            new_width = screen_width
            new_height = int(new_width / aspect_ratio)
            resized_img = img.resize((new_width, new_height))
            offset_y = (new_height - screen_height) // 2
            cropped_img = resized_img.crop(
                (0, offset_y, screen_width, offset_y + screen_height))

        pixel_data = []
        for py in range(screen_height):
            for px in range(screen_width):
                r, g, b = cropped_img.getpixel((px, py))
                rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                pixel_data.extend([(rgb565 >> 8) & 0xFF, rgb565 & 0xFF])
        return pixel_data
    except Exception:
        return None


# ==================== Main Program ====================
class RecordPlayDemo:
    def __init__(self, card_index=None):
        self.board = WhisPlayBoard()
        self.board.set_backlight(60)

        self.card_index = card_index or self._find_wm8960_card()
        self._record_proc = None
        self._play_proc = None

        self.state = State.IDLE
        self._lock = threading.Lock()
        self._record_thread = None
        self._play_thread = None
        self._led_thread = None
        self._led_running = False

        # Pre-generate LCD screens (using actual LCD dimensions 240x280)
        w, h = self.board.LCD_WIDTH, self.board.LCD_HEIGHT
        data_dir = os.path.join(SCRIPT_DIR, "data")
        self._screen_idle = (
            load_image_rgb565(os.path.join(data_dir, "test.png"), w, h) or
            make_text_image("READY", "Hold button to record",
                            bg_color=(0, 0, 40), text_color=(100, 180, 255),
                            width=w, height=h)
        )
        rec_img = os.path.join(data_dir, "recording.jpg")
        self._screen_recording = (
            load_image_rgb565(rec_img, w, h) if os.path.exists(rec_img) else
            make_text_image("● REC", "Release to stop",
                            bg_color=(60, 0, 0), text_color=(255, 80, 80),
                            width=w, height=h)
        )
        play_img = os.path.join(data_dir, "playing.jpg")
        self._screen_playing = (
            load_image_rgb565(play_img, w, h) if os.path.exists(play_img) else
            make_text_image("▶ PLAY", "Playing back...",
                            bg_color=(0, 40, 0), text_color=(80, 255, 80),
                            width=w, height=h)
        )

        # Register button callbacks (hold to record, release to stop)
        self.board.on_button_press(self._on_button_press)
        self.board.on_button_release(self._on_button_release)

        # Configure ALSA mixer
        self._setup_mixer()

    def _find_wm8960_card(self):
        """Find WM8960 sound card number from /proc/asound/cards"""
        try:
            with open("/proc/asound/cards") as f:
                for line in f:
                    if "wm8960" in line.lower():
                        return int(line.strip().split()[0])
        except Exception:
            pass
        return 1  # Default

    def _setup_mixer(self):
        """Ensure WM8960 mixer is configured correctly (recording input + playback output)"""
        card = str(self.card_index)
        cmds = [
            # Output routing
            ['amixer', '-c', card, 'sset', 'Left Output Mixer PCM', 'on'],
            ['amixer', '-c', card, 'sset', 'Right Output Mixer PCM', 'on'],
            ['amixer', '-c', card, 'sset', 'Speaker', '121'],
            ['amixer', '-c', card, 'sset', 'Playback', '230'],
            # Recording input
            ['amixer', '-c', card, 'sset', 'Left Input Mixer Boost', 'on'],
            ['amixer', '-c', card, 'sset', 'Right Input Mixer Boost', 'on'],
            ['amixer', '-c', card, 'sset', 'Capture', '45'],          # 71%, +16.5dB
            ['amixer', '-c', card, 'sset', 'ADC PCM', '195'],         # 76%, 0dB
            # Microphone gain
            ['amixer', '-c', card, 'sset', 'Left Input Boost Mixer LINPUT1', '2'],   # +20dB
            ['amixer', '-c', card, 'sset', 'Right Input Boost Mixer RINPUT1', '2'],  # +20dB
        ]
        for cmd in cmds:
            try:
                subprocess.run(cmd, capture_output=True, timeout=5)
            except Exception:
                pass

    # ==================== Button Handling ====================
    def _on_button_press(self):
        """Button pressed"""
        with self._lock:
            if self.state == State.IDLE:
                self._start_recording()
            elif self.state == State.RECORDING:
                # Fallback: if release event is missed, pressing again also stops recording
                self._stop_recording()
            elif self.state == State.PLAYING:
                self._stop_playback()

    def _on_button_release(self):
        """Button released — stop recording"""
        with self._lock:
            if self.state == State.RECORDING:
                self._stop_recording()

    # ==================== Recording ====================
    def _start_recording(self):
        self.state = State.RECORDING
        print("🎙️  Recording...")

        # Update LCD
        self._show_screen(self._screen_recording)

        # Red LED blink
        self._start_led_blink(255, 0, 0)

        # Start recording thread
        self._record_thread = threading.Thread(target=self._record_worker, daemon=True)
        self._record_thread.start()

    def _record_worker(self):
        """Recording worker thread — uses arecord to directly access hardware device, avoiding distortion"""
        hw_device = f"hw:{self.card_index},0"
        os.makedirs(os.path.dirname(RECORD_FILE), exist_ok=True)

        try:
            # Use 48000Hz — RK3566 I2S PLL can generate clean 12.288MHz MCLK for 48000Hz
            # 44100Hz requires 11.2896MHz, RK3566 PLL cannot divide precisely, causing clock jitter and distortion
            self._record_proc = subprocess.Popen(
                ['arecord', '-D', hw_device, '-f', 'S16_LE', '-r', '48000',
                 '-c', '2', '-t', 'wav', '-d', str(MAX_RECORD_SEC), RECORD_FILE],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            self._record_proc.wait()
            self._record_proc = None

        except Exception as e:
            print(f"Recording error: {e}")
            self._record_proc = None
            with self._lock:
                self.state = State.IDLE
            self._stop_led_blink()
            self.board.set_rgb(0, 0, 0)
            self._show_screen(self._screen_idle)
            return

        # Check if recording file was generated
        if not os.path.exists(RECORD_FILE) or os.path.getsize(RECORD_FILE) < 100:
            print("⚠️  Recording file is empty or not generated")
            with self._lock:
                self.state = State.IDLE
            self._stop_led_blink()
            self.board.set_rgb(0, 0, 0)
            self._show_screen(self._screen_idle)
            self._start_led_breath(0, 0, 255)
            return

        file_size = os.path.getsize(RECORD_FILE)
        # 48000Hz * 2ch * 2bytes = 192000 bytes/sec
        duration = max(0, (file_size - 44)) / 192000  # Subtract WAV header
        print(f"✅  Recording complete, duration: {duration:.1f}s, file: {RECORD_FILE}")

        # Auto-start playback
        self._start_playback()

    def _stop_recording(self):
        """Stop recording (triggered by button callback)"""
        print("⏹️  Stopping recording...")
        try:
            if self._record_proc and self._record_proc.poll() is None:
                self._record_proc.send_signal(signal.SIGINT)
        except Exception:
            pass

    # ==================== Playback ====================
    def _start_playback(self):
        with self._lock:
            self.state = State.PLAYING

        self._stop_led_blink()
        print("🔊  Playing back...")

        # Update LCD
        self._show_screen(self._screen_playing)
        # Green LED solid on
        self.board.set_rgb(0, 255, 0)

        # Start playback thread
        self._play_thread = threading.Thread(target=self._play_worker, daemon=True)
        self._play_thread.start()

    def _play_worker(self):
        """Playback worker thread — uses aplay to ensure correct sound card is used"""
        try:
            hw_device = f"hw:{self.card_index},0"
            proc = subprocess.Popen(
                ['aplay', '-D', hw_device, RECORD_FILE],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            self._play_proc = proc
            proc.wait()
            self._play_proc = None
        except Exception as e:
            print(f"Playback error: {e}")

        print("✅  Playback complete")

        # Return to idle
        with self._lock:
            self.state = State.IDLE
        self.board.set_rgb(0, 0, 0)
        self._show_screen(self._screen_idle)
        self._start_led_breath(0, 0, 255)

    def _stop_playback(self):
        """Stop playback"""
        try:
            if hasattr(self, '_play_proc') and self._play_proc:
                self._play_proc.terminate()
        except Exception:
            pass
        with self._lock:
            self.state = State.IDLE
        self._stop_led_blink()
        self.board.set_rgb(0, 0, 0)
        self._show_screen(self._screen_idle)
        print("⏹️  Playback stopped")

    # ==================== LED Effects ====================
    def _start_led_blink(self, r, g, b):
        self._stop_led_blink()
        self._led_running = True
        self._led_thread = threading.Thread(
            target=self._led_blink_loop, args=(r, g, b), daemon=True)
        self._led_thread.start()

    def _led_blink_loop(self, r, g, b):
        while self._led_running:
            self.board.set_rgb(r, g, b)
            time.sleep(0.4)
            self.board.set_rgb(0, 0, 0)
            time.sleep(0.4)

    def _start_led_breath(self, r, g, b):
        self._stop_led_blink()
        self._led_running = True
        self._led_thread = threading.Thread(
            target=self._led_breath_loop, args=(r, g, b), daemon=True)
        self._led_thread.start()

    def _led_breath_loop(self, r, g, b):
        """Breathing LED effect"""
        while self._led_running:
            # Fade in
            for i in range(0, 101, 5):
                if not self._led_running:
                    return
                f = i / 100.0
                self.board.set_rgb(int(r * f), int(g * f), int(b * f))
                time.sleep(0.03)
            # Fade out
            for i in range(100, -1, -5):
                if not self._led_running:
                    return
                f = i / 100.0
                self.board.set_rgb(int(r * f), int(g * f), int(b * f))
                time.sleep(0.03)

    def _stop_led_blink(self):
        self._led_running = False
        if self._led_thread and self._led_thread.is_alive():
            self._led_thread.join(timeout=1)
        self._led_thread = None

    # ==================== LCD ====================
    def _show_screen(self, pixel_data):
        try:
            self.board.draw_image(0, 0, self.board.LCD_WIDTH,
                                  self.board.LCD_HEIGHT, pixel_data)
        except Exception as e:
            print(f"LCD display error: {e}")

    # ==================== Run ====================
    def run(self):
        print("=" * 50)
        print(" Whisplay Recording & Playback Demo")
        print("=" * 50)
        print(f" Sound card: card {self.card_index}")
        print(f" Record file: {RECORD_FILE}")
        print(f" Max recording: {MAX_RECORD_SEC}s")
        print("")
        print(" Controls:")
        print("   Hold button → Start recording (red blink)")
        print("   Release     → Stop recording → Auto-play (green)")
        print("   Press while → Stop playback")
        print("   Ctrl+C     → Exit")
        print("=" * 50)

        # Show idle screen + blue breathing LED
        self._show_screen(self._screen_idle)
        self._start_led_breath(0, 0, 255)

        try:
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\nExiting...")
        finally:
            self._stop_recording_proc()
            self._stop_led_blink()
            self._stop_playback()
            self.board.set_rgb(0, 0, 0)
            self.board.set_backlight(0)
            self.board.cleanup()

    def _stop_recording_proc(self):
        """Force stop recording process"""
        try:
            if self._record_proc and self._record_proc.poll() is None:
                self._record_proc.terminate()
                self._record_proc.wait(timeout=2)
        except Exception:
            pass
        self._record_proc = None


# ==================== Entry Point ====================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Whisplay Recording & Playback Demo")
    parser.add_argument("--card", type=int, default=None,
                        help="WM8960 sound card number (default: auto-detect)")
    args = parser.parse_args()

    demo = RecordPlayDemo(card_index=args.card)
    demo.run()
