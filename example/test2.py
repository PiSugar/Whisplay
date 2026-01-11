from time import sleep
from PIL import Image
import sys
import os
import argparse
import subprocess

# Import driver
sys.path.append(os.path.abspath("../Driver"))
try:
    from WhisPlay import WhisPlayBoard
except ImportError:
    print("Error: WhisPlay driver not found.")
    sys.exit(1)

# Initialize hardware
board = WhisPlayBoard()
board.set_backlight(50)

# Global variables
img1_data = None  # Recording stage (test1.jpg)
img2_data = None  # Playback stage (test2.jpg)
REC_FILE = "recorded_voice.wav"
recording_process = None


def load_jpg_as_rgb565(filepath, screen_width, screen_height):
    """Convert image to RGB565 format supported by the screen"""
    if not os.path.exists(filepath):
        print(f"Warning: File not found: {filepath}")
        return None

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
    for y in range(screen_height):
        for x in range(screen_width):
            r, g, b = cropped_img.getpixel((x, y))
            rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            pixel_data.extend([(rgb565 >> 8) & 0xFF, rgb565 & 0xFF])
    return pixel_data


def set_wm8960_volume_stable(volume_level: str):
    """Set wm8960 sound card volume"""
    CARD_NAME = 'wm8960soundcard'
    DEVICE_ARG = f'hw:{CARD_NAME}'
    try:
        subprocess.run(['amixer', '-D', DEVICE_ARG, 'sset', 'Speaker',
                       volume_level], check=False, capture_output=True)
        subprocess.run(['amixer', '-D', DEVICE_ARG, 'sset',
                       'Capture', '100'], check=False, capture_output=True)
    except Exception as e:
        print(f"ERROR: Failed to set volume: {e}")


def start_recording():
    """Enter recording stage: display test1.jpg and start arecord"""
    global recording_process, img1_data
    print(">>> Status: Entering recording stage (displaying test1)...")
    print(">>> Press the button to stop recording and playback...")

    if img1_data:
        board.draw_image(0, 0, board.LCD_WIDTH, board.LCD_HEIGHT, img1_data)

    # Start recording asynchronously
    command = ['arecord', '-D', 'hw:wm8960soundcard',
               '-f', 'S16_LE', '-r', '16000', '-c', '2', REC_FILE]
    recording_process = subprocess.Popen(command)


def on_button_pressed():
    """Button callback: stop recording -> color change -> display test2 -> play recording (blocking) -> return to recording"""
    global recording_process, img1_data, img2_data
    print(">>> Button pressed!")

    # 1. Stop recording
    if recording_process and recording_process.poll() is None:
        recording_process.terminate()
        recording_process.wait()

    # 2. Visual feedback: LED color sequence
    color_sequence = [(255, 0, 0, 0xF800),
                      (0, 255, 0, 0x07E0), (0, 0, 255, 0x001F)]
    for r, g, b, hex_code in color_sequence:
        board.fill_screen(hex_code)
        board.set_rgb(r, g, b)
        sleep(0.4)
    board.set_rgb(0, 0, 0)

    # 3. Playback feedback: display test2.jpg and play recorded audio
    if img2_data:
        board.draw_image(0, 0, board.LCD_WIDTH, board.LCD_HEIGHT, img2_data)

    print(">>> Playing back recording (displaying test2)...")
    subprocess.run(['aplay', '-D', 'plughw:wm8960soundcard', REC_FILE])

    # 4. Automatically return to recording stage
    start_recording()


# Register callback
board.on_button_press(on_button_pressed)

# --- Main program ---
parser = argparse.ArgumentParser()
parser.add_argument("--img1", default="recording.jpg", help="Image for recording stage")
parser.add_argument("--img2", default="playing.jpg", help="Image for playback stage")
parser.add_argument("--test_wav", default="test.wav")
args = parser.parse_args()

try:
    # 1. Load all image data first
    print("Initializing images...")
    img1_data = load_jpg_as_rgb565(
        args.img1, board.LCD_WIDTH, board.LCD_HEIGHT)
    img2_data = load_jpg_as_rgb565(
        args.img2, board.LCD_WIDTH, board.LCD_HEIGHT)

    # 2. Set volume
    set_wm8960_volume_stable("121")

    # 3. Play startup audio at launch (displaying test2.jpg)
    if os.path.exists(args.test_wav):
        if img2_data:
            board.draw_image(0, 0, board.LCD_WIDTH,
                             board.LCD_HEIGHT, img2_data)
        print(f">>> Playing startup audio: {args.test_wav} (displaying test2)")
        subprocess.run(
            ['aplay', '-D', 'plughw:wm8960soundcard', args.test_wav])

    # 4. After audio finishes, enter recording loop
    start_recording()

    while True:
        sleep(0.1)

except KeyboardInterrupt:
    print("\nProgram exited")
finally:
    if recording_process:
        recording_process.terminate()
    board.cleanup()
