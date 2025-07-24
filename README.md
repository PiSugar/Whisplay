# PiSugar WhisPlay 扩展板驱动 / PiSugar WhisPlay Expansion Board Driver

## 项目概览 / Project Overview

本项目为 **PiSugar WhisPlay 扩展板** 提供完整的驱动程序支持，让您可以轻松控制板载的 LCD 屏幕、物理按键和 LED 指示灯，并支持音频功能。

This project provides full driver support for the **PiSugar WhisPlay expansion board**, allowing you to easily control the onboard LCD screen, physical buttons, and LED indicators, along with audio functionality.

---

### **💡 总线信息提示 / Bus Information Tip 💡**

设备使用了 **I2C、SPI、I2S** 总线。其中 **I2S 和 I2C 总线** 用作音频驱动，会在安装驱动的时候自动启动。**SPI 总线** 需要手动打开。

The device utilizes **I2C, SPI, and I2S** buses. The **I2S and I2C buses** are used for audio functionality and will be automatically enabled during driver installation. The **SPI bus** needs to be enabled manually.

---

### 驱动程序结构 / Driver Structure

所有驱动文件都位于 `Driver` 目录下，主要包括：

All driver files are located in the `Driver` directory and primarily include:

#### 1\. `Whisplay.py`

  * **功能 / Function**: 将 LCD 显示屏、物理按键和 LED 指示灯封装为易于使用的 Python 对象，极大简化了硬件操作。 / Encapsulates the LCD display, physical buttons, and LED indicators into easy-to-use Python objects, greatly simplifying hardware operations.
  * **快速验证 / Quick Verification**: 参考 `example/test.py` 文件，快速测试 LCD、LED 和按键功能。 / Refer to the `example/test.py` file to quickly test LCD, LED, and button functionalities.

#### 2\. WM8960 音频驱动 / WM8960 Audio Driver

  * **来源 / Source**: 感谢 Waveshare 提供的音频驱动支持。 / Thanks to Waveshare for providing audio driver support.

  * **安装 / Installation**: 通过运行 `install_wm8960_drive.sh` 脚本进行安装： / Install by running the `install_wm8960_drive.sh` script:

    ```shell
    cd Driver
    sudo bash install_wm8960_drive.sh
    ```


## 示例程序 / Example Programs

`example` 目录下提供了多个 Python 示例（目前不包含音频部分），帮助您快速上手。

The `example` directory provides several Python examples (currently without audio integration) to help you get started quickly.

#### 1\. `test.py`

  * **功能 / Function**: 验证 LCD、LED 和按键是否正常工作。 / Verifies that the LCD, LEDs, and buttons are working correctly.
  * **使用方法 / Usage**:
    运行 `test.py`： / Run `test.py`:
    ```shell
    cd example
    python test.py
    ```
    您也可以指定一张图片进行测试： / You can also specify an image for testing:
    ```shell
    python test.py test1.jpg
    ```
    **效果 / Effect**: 程序运行后，LCD 将显示测试图片。按下任意按键，屏幕会变为纯色，同时 RGB LED 也将同步显示为相同的颜色。 / After running, the LCD will display a test image. Pressing any button will change the screen to a solid color, and the RGB LED will simultaneously change to the same color.

#### 2\. `chatbot-ui.py`

  * **功能 / Function**: 为语音聊天机器人提供一个 Socket 接口，用于显示当前状态和对话内容，方便外部程序调用。 / Provides a Socket interface for a voice chatbot, used to display current status and conversation content, facilitating external program calls.
  * **使用方法 / Usage**:
    1.  **运行 UI 监听 / Run UI Listener**: 首先运行 `chatbot-ui.py` 监听端口： / First, run `chatbot-ui.py` to listen on the port:
        ```shell
        cd example
        python chatbot-ui.py
        ```
        **说明 / Note**: 程序将持续监听 `12345` 端口。客户端连接后，可以发送显示信息，并接收按键状态。 / The program will continuously listen on port `12345`. Once a client connects, it can send display information and receive button statuses.
    2.  **测试 UI / Test UI**: 在一个新的终端窗口中运行 `sockettest.py` 来测试 UI： / In a new terminal window, run `sockettest.py` to test the UI:
        ```shell
        python sockettest.py
        ```
        **效果 / Effect**: 运行 `sockettest.py` 后，点击按钮，LCD 显示内容将随机变化。 / After running `sockettest.py`, clicking the button will cause the LCD display content to change randomly.

-----

**注意：目前仅支持官方 full 版本系统。 / Note: Currently, only the official full version system is supported.**