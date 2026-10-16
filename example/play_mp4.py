import subprocess
import os
import sys
import gc
import shutil
import argparse
import urllib.request
import threading
from PIL import Image, ImageDraw, ImageFont

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
runtime_dir = os.path.join(project_root, "runtime")
if runtime_dir not in sys.path:
    sys.path.append(runtime_dir)

from whisplay_client import create_whisplay_hardware

def get_ffmpeg_cmd(video_path, width, height):
    model = "generic"
    try:
        with open('/proc/device-tree/model', 'r') as f:
            model = f.read().lower()
    except:
        pass

    input_args = []
    vf_params = f'scale={width}:{height}:flags=neighbor'

    if 'raspberry pi zero 2' in model or 'raspberry pi 3' in model:
        print(f"Device: {model.strip()} | Mode: Multi-thread")
        input_args = ['-threads', '4']
    elif 'raspberry pi zero' in model:
        print("Device: Pi Zero/W | Mode: HW Accel")
        input_args = ['-vcodec', 'h264_v4l2m2m']
    elif 'raspberry pi 4' in model or 'raspberry pi 5' in model:
        print(f"Device: {model.strip()} | Mode: High-perf")
        input_args = ['-threads', '4']
        vf_params = f'scale={width}:{height}:flags=bicubic'

    return ['ffmpeg'] + input_args + [
        '-i', video_path,
        '-vf', vf_params,
        '-vcodec', 'rawvideo',
        '-pix_fmt', 'rgb565be',
        '-f', 'image2pipe',
        '-loglevel', 'quiet',
        '-'
    ]

def start_audio_process(video_path):
    # Plays audio via ALSA's default device. Use ffplay or mpv if you have them.
    return subprocess.Popen(
        ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet', video_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

def stop_process(process):
    """Stop and reap one owned player/decoder, including its output pipe."""
    if process is None:
        return
    try:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=1)
    finally:
        if process.stdout is not None:
            process.stdout.close()

def play_video(video_path, video_url=None):
    board = create_whisplay_hardware(
        app_id=os.getenv("WHISPLAY_APP_ID", "whisplay-play-mp4"),
        display_name="Play MP4",
        icon="V",
        priority=10,
        use_daemon_default_log=True,
    )
    board.set_backlight(100)
    width, height = board.LCD_WIDTH, board.LCD_HEIGHT

    if not os.path.exists(video_path) and video_url:
        print(f"File not found, downloading from {video_url}")
        render_progress(board, width, height, 0, "Downloading...")
        if not download_video(video_url, video_path, board, width, height):
            print(f"Error: download failed for {video_url}")
            board.cleanup()
            sys.exit(1)

    running = [True]
    proc_ref = [None]
    audio_ref = [None]
    process_lock = threading.Lock()

    def on_focus_revoked(_payload=None):
        # Serialize with startup/restart so exit cannot leave a late-created
        # ffplay or ffmpeg running after the app has returned to the desktop.
        with process_lock:
            running[0] = False
            for child in (proc_ref[0], audio_ref[0]):
                if child is not None and child.poll() is None:
                    try:
                        child.kill()
                    except ProcessLookupError:
                        pass

    board.on_focus_revoked(on_focus_revoked)
    if hasattr(board, 'on_exit_request'):
        board.on_exit_request(on_focus_revoked)

    frame_size = width * height * 2
    buffer = bytearray(frame_size)

    def start_process():
        cmd = get_ffmpeg_cmd(video_path, width, height)
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=frame_size)

    try:
        with process_lock:
            if not running[0]:
                return
            process = start_process()
            proc_ref[0] = process
            audio_ref[0] = start_audio_process(video_path)

        gc.collect()
        gc.disable()
        print(f"Playing (loop): {video_path}. Press Ctrl+C to exit.")
        while running[0]:
            read = process.stdout.readinto(buffer)
            if not running[0]:
                break
            if read != frame_size:
                stop_process(process)
                with process_lock:
                    if not running[0]:
                        break
                    process = start_process()
                    proc_ref[0] = process
                continue
            board.draw_image(0, 0, width, height, buffer)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        with process_lock:
            running[0] = False
        for child in (proc_ref[0], audio_ref[0]):
            try:
                stop_process(child)
            except (OSError, subprocess.TimeoutExpired) as exc:
                print(f"Failed to stop media process: {exc}")
        gc.enable()
        board.cleanup()
        print("Exit.")

def render_progress(board, width, height, percent, status_text="Downloading..."):
    try:
        image = Image.new("RGB", (width, height), (7, 11, 18))
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size=14)
        except Exception:
            font = ImageFont.load_default()
        draw.text((20, height // 2 - 40), status_text, fill=(255, 255, 255), font=font)
        bar_w = width - 40
        bar_h = 14
        bar_x = 20
        bar_y = height // 2 - 8
        draw.rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), outline=(100, 180, 255))
        if percent >= 0:
            fill_w = int(bar_w * percent / 100)
            if fill_w > 2:
                draw.rectangle((bar_x + 2, bar_y + 2, bar_x + fill_w - 2, bar_y + bar_h - 2), fill=(60, 140, 255))
            draw.text((bar_x + bar_w // 2 - 20, bar_y + bar_h + 6), f"{percent:.0f}%", fill=(200, 200, 200), font=font)
        else:
            draw.text((bar_x + bar_w // 2 - 30, bar_y + bar_h + 6), "Please wait...", fill=(200, 200, 200), font=font)
        frame = bytearray()
        for y in range(height):
            for x in range(width):
                r, g, b = image.getpixel((x, y))
                rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                frame.append((rgb565 >> 8) & 0xFF)
                frame.append(rgb565 & 0xFF)
        board.draw_image(0, 0, width, height, bytes(frame))
    except Exception as e:
        print(f"Render progress error: {e}")


def download_video(url, dest_path, board, width, height):
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Whisplay/1.0"})
        response = urllib.request.urlopen(req, timeout=30)
        total_size = int(response.headers.get("Content-Length", 0))
        downloaded = 0
        block_size = 8192
        with open(dest_path, "wb") as f:
            while True:
                data = response.read(block_size)
                if not data:
                    break
                f.write(data)
                downloaded += len(data)
                if total_size > 0:
                    percent = downloaded / total_size * 100
                    render_progress(board, width, height, percent)
        render_progress(board, width, height, 100, "Download complete")
        return True
    except Exception as e:
        print(f"Download failed: {e}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", "-f", default=os.path.join(project_root, "example/data", "whisplay_test.mp4"))
    parser.add_argument("--url", "-u", default="https://img-storage.pisugar.uk/whisplay_test.mp4")
    args = parser.parse_args()

    if not shutil.which("ffmpeg"):
        print("Error: ffmpeg not found in PATH.")
        sys.exit(1)

    try:
        play_video(args.file, args.url)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
