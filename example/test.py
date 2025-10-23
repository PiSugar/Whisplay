from time import sleep
from PIL import Image
import sys
import os
sys.path.append(os.path.abspath("../Driver"))

from echoview import EchoViewBoard

board = EchoViewBoard()

board.set_backlight(50)
def load_jpg_as_rgb565(filepath, screen_width, screen_height):
    img = Image.open(filepath).convert('RGB')
    original_width, original_height = img.size

    aspect_ratio = original_width / original_height
    screen_aspect_ratio = screen_width / screen_height

    if aspect_ratio > screen_aspect_ratio:
        # 原始图像更宽，以屏幕高度为基准缩放
        new_height = screen_height
        new_width = int(new_height * aspect_ratio)
        resized_img = img.resize((new_width, new_height))
        # 计算水平方向的偏移量，使图像居中
        offset_x = (new_width - screen_width) // 2
        # 裁剪图像以适应屏幕宽度
        cropped_img = resized_img.crop((offset_x, 0, offset_x + screen_width, screen_height))
    else:
        # 原始图像更高或宽高比相同，以屏幕宽度为基准缩放
        new_width = screen_width
        new_height = int(new_width / aspect_ratio)
        resized_img = img.resize((new_width, new_height))
        # 计算垂直方向的偏移量，使图像居中
        offset_y = (new_height - screen_height) // 2
        # 裁剪图像以适应屏幕高度
        cropped_img = resized_img.crop((0, offset_y, screen_width, offset_y + screen_height))

    pixel_data = []
    for y in range(screen_height):
        for x in range(screen_width):
            r, g, b = cropped_img.getpixel((x, y))
            rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            pixel_data.extend([(rgb565 >> 8) & 0xFF, rgb565 & 0xFF])

    return pixel_data
# 按钮回调函数
def on_button_pressed():
    print("按钮被按下了！")


    # 显示红色填充屏幕
    board.fill_screen(0xF800)  # 红色 RGB565
    board.set_rgb(255, 0, 0)

    sleep(0.5)

    # 显示绿色填充屏幕
    board.fill_screen(0x07E0)  # 绿色 RGB565
    board.set_rgb(0, 255, 0)

    sleep(0.5)

    # 显示蓝色填充屏幕
    board.fill_screen(0x001F)  # 蓝色 RGB565
    board.set_rgb(0, 0, 255)

    # 显示 test.jpg 图片
    try:
        img_data = load_jpg_as_rgb565("test.png", board.LCD_WIDTH, board.LCD_HEIGHT)
        board.draw_image(0, 0, board.LCD_WIDTH, board.LCD_HEIGHT, img_data)
        print("图片 test.jpg 显示成功")
    except Exception as e:
        print("图片加载失败：", e)

# 注册按钮事件
board.on_button_press(on_button_pressed)

    # 显示 test.jpg 图片
try:
    img_data = load_jpg_as_rgb565("test.png", board.LCD_WIDTH, board.LCD_HEIGHT)
    board.draw_image(0, 0, board.LCD_WIDTH, board.LCD_HEIGHT, img_data)
    print("图片 test.jpg 显示成功")
except Exception as e:
    print("图片加载失败：", e)
    
try:
    print("等待按钮按下（按 Ctrl+C 退出）...")
    while True:
        sleep(0.1)

except KeyboardInterrupt:
    print("退出程序...")

finally:
    board.cleanup()
