[English](README.md) | [中文](README_CN.md)

# PiSugar WhisPlay 扩展板驱动

## 项目概览

本项目为 **PiSugar WhisPlay 扩展板** 提供完整的驱动程序支持，让您可以轻松控制板载的 LCD 屏幕、物理按键和 LED 指示灯，并支持音频功能。

---

### **💡 总线信息提示 💡**

设备使用了 **I2C、SPI、I2S** 总线。其中 **I2S 和 I2C 总线** 用作音频驱动，会在安装驱动的时候自动启动。**SPI 总线** 需要手动打开。

---

### 驱动程序结构

所有驱动文件都位于 `Driver` 目录下，主要包括：

#### 1. `Whisplay.py`

  * **功能**: 将 LCD 显示屏、物理按键和 LED 指示灯封装为易于使用的 Python 对象，极大简化了硬件操作。
  * **快速验证**: 参考 `example/test.py` 文件，快速测试 LCD、LED 和按键功能。

#### 2. WM8960 音频驱动

  * **来源**: 感谢 Waveshare 提供的音频驱动支持。

  * **安装**: 通过运行 `install_wm8960_drive.sh` 脚本进行安装：

    ```shell
    cd Driver
    sudo bash install_wm8960_drive.sh
    ```


## 示例程序

`example` 目录下提供了多个 Python 示例（目前不包含音频部分），帮助您快速上手。

#### 1. `test.py`

  * **功能**: 验证 LCD、LED 和按键是否正常工作。
  * **使用方法**:
    运行 `test.py`：
    ```shell
    cd example
    python test.py
    ```
    您也可以指定一张图片进行测试：
    ```shell
    python test.py test1.jpg
    ```
    **效果**: 程序运行后，LCD 将显示测试图片。按下任意按键，屏幕会变为纯色，同时 RGB LED 也将同步显示为相同的颜色。

#### 2. `chatbot-ui.py`

  * **功能**: 为语音聊天机器人提供一个 Socket 接口，用于显示当前状态和对话内容，方便外部程序调用。
  * **使用方法**:
    1.  **运行 UI 监听**: 首先运行 `chatbot-ui.py` 监听端口：
        ```shell
        cd example
        python chatbot-ui.py
        ```
        **说明**: 程序将持续监听 `12345` 端口。客户端连接后，可以发送显示信息，并接收按键状态。
    2.  **测试 UI**: 在一个新的终端窗口中运行 `sockettest.py` 来测试 UI：
        ```shell
        python sockettest.py
        ```
        **效果**: 运行 `sockettest.py` 后，点击按钮，LCD 显示内容将随机变化。

-----

**注意：目前仅支持官方 full 版本系统。**