
from time import sleep
from PIL import Image
import sys
import os
import argparse
import pygame  # Import pygame
import subprocess

sys.path.append(os.path.abspath("../Driver"))
from WhisPlay import WhisPlayBoard
board = WhisPlayBoard()
board.set_backlight(50)

global_image_data = None
image_filepath = None

# Initialize pygame mixer
pygame.mixer.init()
sound = None  # Global sound variable
playing = False  # Global variable to track if sound is playing


def set_wm8960_volume_stable(volume_level: str):
    """
    Sets the 'Speaker' volume for the wm8960 sound card using the amixer command.

    Args:
        volume_level (str): The desired volume value, e.g., '90%' or '121'.
    """

    CARD_NAME = 'wm8960soundcard'
    CONTROL_NAME = 'Speaker'
    DEVICE_ARG = f'hw:{CARD_NAME}'

    command = [
        'amixer',
        '-D', DEVICE_ARG,
        'sset',
        CONTROL_NAME,
        volume_level
    ]

    try:
        subprocess.run(command, check=True, capture_output=True, text=True)

        print(
            f"INFO: Successfully set '{CONTROL_NAME}' volume to {volume_level} on card '{CARD_NAME}'.")

    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to execute amixer.", file=sys.stderr)
        print(f"Command: {' '.join(command)}", file=sys.stderr)
        print(f"Return Code: {e.returncode}", file=sys.stderr)
        print(f"Error Output:\n{e.stderr}", file=sys.stderr)
    except FileNotFoundError:
        print("ERROR: 'amixer' command not found. Ensure it is installed and in PATH.", file=sys.stderr)


def load_jpg_as_rgb565(filepath, screen_width, screen_height):
    img = Image.open(filepath).convert('RGB')
    original_width, original_height = img.size

    aspect_ratio = original_width / original_height
    screen_aspect_ratio = screen_width / screen_height

    if aspect_ratio > screen_aspect_ratio:
        # Original image is wider, scale based on screen height
        new_height = screen_height
        new_width = int(new_height * aspect_ratio)
        resized_img = img.resize((new_width, new_height))
        # Calculate horizontal offset to center the image
        offset_x = (new_width - screen_width) // 2
        # Crop the image to fit screen width
        cropped_img = resized_img.crop(
            (offset_x, 0, offset_x + screen_width, screen_height))
    else:
        # Original image is taller or has the same aspect ratio, scale based on screen width
        new_width = screen_width
        new_height = int(new_width / aspect_ratio)
        resized_img = img.resize((new_width, new_height))
        # Calculate vertical offset to center the image
        offset_y = (new_height - screen_height) // 2
        # Crop the image to fit screen height
        cropped_img = resized_img.crop(
            (0, offset_y, screen_width, offset_y + screen_height))

    pixel_data = []
    for y in range(screen_height):
        for x in range(screen_width):
            r, g, b = cropped_img.getpixel((x, y))
            rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            pixel_data.extend([(rgb565 >> 8) & 0xFF, rgb565 & 0xFF])

    return pixel_data

# Button callback function


def on_button_pressed():
    print("Button pressed!")

    global sound, playing  # Use the global sound and playing variables

    # --- MODIFICATION START: Play sound BEFORE screen changes ---
    if sound:
        if playing:
            sound.stop()  # Stop the current sound if it's playing
            print("Stopping current sound...")
        sound.play()  # Play the sound from the beginning
        print("Playing sound concurrently with display changes...")
        playing = True  # Set the playing flag
    else:
        print("Sound not loaded.")
    # --- MODIFICATION END ---

    # Display red filled screen
    board.fill_screen(0xF800)  # Red RGB565
    board.set_rgb(255, 0, 0)
    sleep(0.5)

    # Display green filled screen
    board.fill_screen(0x07E0)  # Green RGB565
    board.set_rgb(0, 255, 0)
    sleep(0.5)

    # Display blue filled screen
    board.fill_screen(0x001F)  # Blue RGB565
    board.set_rgb(0, 0, 255)
    sleep(0.5)

    # Display the image using the globally stored data
    global global_image_data, image_filepath
    if global_image_data is not None:
        board.draw_image(0, 0, board.LCD_WIDTH,
                         board.LCD_HEIGHT, global_image_data)
        print(
            f"Image {os.path.basename(image_filepath)} displayed successfully from memory.")
    else:
        print("Image data not loaded yet. This should not happen after initial load.")


# Register button event
board.on_button_press(on_button_pressed)

# --- Argument Parsing ---
parser = argparse.ArgumentParser(
    description="Display an image and play sound on button press.")
parser.add_argument("--image", default="data/test.png",
                    help="Path to the image file (default: data/test.png)")
parser.add_argument("--sound", default="data/test.mp3",
                    # Add sound argument
                    help="Path to the sound file (default: data/test.mp3)")
args = parser.parse_args()

image_filepath = args.image
sound_filepath = args.sound  # Get sound filepath

# --- Initial Image Loading ---
# Load the image once at the beginning of the script
try:
    global_image_data = load_jpg_as_rgb565(
        image_filepath, board.LCD_WIDTH, board.LCD_HEIGHT)
    board.draw_image(0, 0, board.LCD_WIDTH,
                     board.LCD_HEIGHT, global_image_data)
    print(
        f"Image {os.path.basename(image_filepath)} loaded and displayed initially.")
except Exception as e:
    print(f"Failed to load initial image from {image_filepath}: {e}")

# Load the sound
try:
    sound = pygame.mixer.Sound(sound_filepath)
    print(f"Sound {os.path.basename(sound_filepath)} loaded successfully.")
    set_wm8960_volume_stable("121")  # Set volume to 121（74）
except Exception as e:
    print(f"Failed to load sound from {sound_filepath}: {e}")
    sound = None

try:
    print("Waiting for button press (Press Ctrl+C to exit)...")
    while True:
        # Check if the sound has finished playing and update the 'playing' flag
        if playing and not pygame.mixer.get_busy():
            playing = False
            # print("Sound finished playing.") # Optional print
        sleep(0.1)

except KeyboardInterrupt:
    print("Exiting program...")

finally:
    board.cleanup()
    pygame.mixer.quit()  # Quit the mixer
