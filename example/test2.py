from time import sleep
from PIL import Image
import sys
import os
import argparse
import subprocess

# 导入驱动
sys.path.append(os.path.abspath("../Driver"))
try:
    from WhisPlay import WhisPlayBoard
except ImportError:
    print("错误: 无法找到 WhisPlay 驱动。")
    sys.exit(1)

# 初始化硬件
board = WhisPlayBoard()
board.set_backlight(50)

# 全局变量
img1_data = None  # 录音阶段 (test1.jpg)
img2_data = None  # 播放阶段 (test2.jpg)
REC_FILE = "recorded_voice.wav"
recording_process = None


def load_jpg_as_rgb565(filepath, screen_width, screen_height):
    """将图片转换为屏幕支持的 RGB565 格式"""
    if not os.path.exists(filepath):
        print(f"警告: 找不到文件 {filepath}")
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
    """设置 wm8960 声卡音量"""
    CARD_NAME = 'wm8960soundcard'
    DEVICE_ARG = f'hw:{CARD_NAME}'
    try:
        subprocess.run(['amixer', '-D', DEVICE_ARG, 'sset', 'Speaker',
                       volume_level], check=False, capture_output=True)
        subprocess.run(['amixer', '-D', DEVICE_ARG, 'sset',
                       'Capture', '100'], check=False, capture_output=True)
    except Exception as e:
        print(f"ERROR: 设置音量失败: {e}")


def start_recording():
    """进入录音阶段：显示 test1.jpg 并启动 arecord"""
    global recording_process, img1_data
    print(">>> 状态: 进入录音阶段 (显示 test1)...")

    if img1_data:
        board.draw_image(0, 0, board.LCD_WIDTH, board.LCD_HEIGHT, img1_data)

    # 异步启动录音
    command = ['arecord', '-D', 'hw:wm8960soundcard',
               '-f', 'S16_LE', '-r', '16000', '-c', '2', REC_FILE]
    recording_process = subprocess.Popen(command)


def on_button_pressed():
    """按键回调：停止录音 -> 变色 -> 显示 test2 -> 播放录音(阻塞) -> 回到录音"""
    global recording_process, img1_data, img2_data
    print(">>> 按钮按下!")

    # 1. 停止录音
    if recording_process and recording_process.poll() is None:
        recording_process.terminate()
        recording_process.wait()

    # 2. 视觉反馈：LED 颜色切换
    color_sequence = [(255, 0, 0, 0xF800),
                      (0, 255, 0, 0x07E0), (0, 0, 255, 0x001F)]
    for r, g, b, hex_code in color_sequence:
        board.fill_screen(hex_code)
        board.set_rgb(r, g, b)
        sleep(0.4)
    board.set_rgb(0, 0, 0)

    # 3. 播放反馈：显示 test2.jpg 并播放录制的音频
    if img2_data:
        board.draw_image(0, 0, board.LCD_WIDTH, board.LCD_HEIGHT, img2_data)

    print(f">>> 正在回放录音 (显示 test2)...")
    subprocess.run(['aplay', '-D', 'plughw:wm8960soundcard', REC_FILE])

    # 4. 自动回到录音阶段
    start_recording()


# 注册回调
board.on_button_press(on_button_pressed)

# --- 主程序 ---
parser = argparse.ArgumentParser()
parser.add_argument("--img1", default="recording.jpg", help="录音阶段图片")
parser.add_argument("--img2", default="playing.jpg", help="播放阶段图片")
parser.add_argument("--test_wav", default="test.wav")
args = parser.parse_args()

try:
    # 1. 先加载所有图片数据
    print("正在初始化图片...")
    img1_data = load_jpg_as_rgb565(
        args.img1, board.LCD_WIDTH, board.LCD_HEIGHT)
    img2_data = load_jpg_as_rgb565(
        args.img2, board.LCD_WIDTH, board.LCD_HEIGHT)

    # 2. 音量设置
    set_wm8960_volume_stable("121")

    # 3. 启动时播放音频 (此时展示 test2.jpg)
    if os.path.exists(args.test_wav):
        if img2_data:
            board.draw_image(0, 0, board.LCD_WIDTH,
                             board.LCD_HEIGHT, img2_data)
        print(f">>> 播放启动音频: {args.test_wav} (显示 test2)")
        subprocess.run(
            ['aplay', '-D', 'plughw:wm8960soundcard', args.test_wav])

    # 4. 音频播完后，正式进入录音循环
    start_recording()

    while True:
        sleep(0.1)

except KeyboardInterrupt:
    print("\n程序退出")
finally:
    if recording_process:
        recording_process.terminate()
    board.cleanup()
