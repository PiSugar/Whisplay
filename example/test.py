from time import sleep
from PIL import Image
import sys
import os
import argparse # Import the argparse module

sys.path.append(os.path.abspath("../Driver"))

from WhisPlay import WhisPlayBoard

board = WhisPlayBoard()

board.set_backlight(50)

# Declare a global variable to store the image data
# It will be initialized to None and loaded once
global_image_data = None
image_filepath = None # Declare a global variable for the image filepath

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
        cropped_img = resized_img.crop((offset_x, 0, offset_x + screen_width, screen_height))
    else:
        # Original image is taller or has the same aspect ratio, scale based on screen width
        new_width = screen_width
        new_height = int(new_width / aspect_ratio)
        resized_img = img.resize((new_width, new_height))
        # Calculate vertical offset to center the image
        offset_y = (new_height - screen_height) // 2
        # Crop the image to fit screen height
        cropped_img = resized_img.crop((0, offset_y, screen_width, offset_y + screen_height))

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
        board.draw_image(0, 0, board.LCD_WIDTH, board.LCD_HEIGHT, global_image_data)
        print(f"Image {os.path.basename(image_filepath)} displayed successfully from memory.")
    else:
        print("Image data not loaded yet. This should not happen after initial load.")

# Register button event
board.on_button_press(on_button_pressed)

# --- Argument Parsing ---
parser = argparse.ArgumentParser(description="Display an image on WhisPlay board and respond to button presses.")
parser.add_argument("--image", default="test.png", help="Path to the image file (default: test.png)")
args = parser.parse_args()

image_filepath = args.image # Set the global image_filepath

# --- Initial Image Loading ---
# Load the image once at the beginning of the script
try:
    global_image_data = load_jpg_as_rgb565(image_filepath, board.LCD_WIDTH, board.LCD_HEIGHT)
    board.draw_image(0, 0, board.LCD_WIDTH, board.LCD_HEIGHT, global_image_data)
    print(f"Image {os.path.basename(image_filepath)} loaded and displayed initially.")
except Exception as e:
    print(f"Failed to load initial image from {image_filepath}: {e}")

try:
    print("Waiting for button press (Press Ctrl+C to exit)...")
    while True:
        sleep(0.1)

except KeyboardInterrupt:
    print("Exiting program...")

finally:
    board.cleanup()