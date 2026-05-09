[English](README.md) | [中文](README_CN.md)

# PiSugar Whisplay 扩展板驱动

## 项目概览

本项目为 **PiSugar Whisplay 扩展板** 提供完整的驱动程序支持，让您可以轻松控制板载的 LCD 屏幕、物理按键和 LED 指示灯，并支持音频功能。

**支持平台：**
- Raspberry Pi（所有带 40-pin 排针的型号）
- Radxa ZERO 3W (RK3566)
- Radxa Cubie A7Z (Allwinner A733)

更多详细信息请参考 [Whisplay HAT 文档](https://docs.pisugar.com/docs/product-wiki/whisplay/intro)

---

### **💡 总线信息提示 💡**

设备使用了 **I2C、SPI、I2S** 总线。其中 **I2S 和 I2C 总线** 用作音频驱动，会在安装驱动的时候自动启用。

---

### 安装

#### Raspberry Pi

克隆项目后，进入 Driver 目录并运行安装脚本。

```bash
git clone https://github.com/PiSugar/Whisplay.git --depth 1
cd Whisplay/Driver
sudo bash install_wm8960_drive.sh
sudo reboot
```

驱动安装完成后，可以运行测试程序：

```shell
cd Whisplay/example
sudo bash run_test.sh
```

#### Radxa ZERO 3W

克隆项目后，进入 Driver 目录并运行 Radxa 专用安装脚本。

```bash
git clone https://github.com/PiSugar/Whisplay.git --depth 1
cd Whisplay/Driver
sudo bash install_radxa_zero3w.sh
sudo reboot
```

安装脚本将执行以下操作：
1. 安装 Python 依赖（`python3-libgpiod`、`python3-spidev`、`python3-pil`、`python3-pygame`）
2. 启用 SPI3_M1 overlay（用于 LCD 显示屏）
3. 启用 I2S3 overlay（用于 WM8960 音频）
4. 配置 WM8960 音频驱动（如果内核模块可用）

重启后，运行测试：

```shell
cd Whisplay/example
sudo bash run_test.sh
```

#### Radxa Cubie A7Z

> ⚠️ **重要硬件警告（仅 A7Z）**  
> 由于电路不兼容，Whisplay HAT 的物理按键在 Radxa Cubie A7Z 上**不可使用**。  
> **请勿点击按键**，否则可能导致 A7Z 立即断电。

克隆项目后，进入 Driver 目录并运行 Cubie A7Z 专用安装脚本。

```bash
git clone https://github.com/PiSugar/Whisplay.git --depth 1
cd Whisplay/Driver
sudo bash install_radxa_cubie_a7z.sh
sudo reboot
```

安装脚本将执行以下操作：
1. 安装 Python 依赖（`python3-libgpiod`、`python3-spidev`、`python3-pil`、`python3-pygame`）
2. 启用 SPI1 overlay（用于 LCD 显示屏）
3. 启用 TWI7 overlay（用于 WM8960 I2C 通信）
4. 编译并安装 WM8960 音频 overlay 和内核模块
5. 配置 ALSA 混音器

重启后，运行测试：

```shell
cd Whisplay/example
sudo bash run_test.sh
```

### 驱动程序结构

所有驱动文件都位于 `Driver` 目录下，主要包括：

#### 1. `Whisplay.py`

  * **功能**: 将 LCD 显示屏、物理按键和 LED 指示灯封装为易于使用的 Python 对象，极大简化了硬件操作。程序会**自动检测平台**（Raspberry Pi 或 Radxa ZERO 3W）并使用对应的 GPIO 库。
  * **快速验证**: 参考 `example/test.py` 文件，快速测试 LCD、LED 和按键功能。

#### 1.1 `whisplay_daemon.py`

  * **功能**: 可选的本地硬件守护进程，独占 LCD、背光、RGB LED、按键和 app 生命周期，并通过本机 Unix Socket 暴露 app 注册、切换和共享 framebuffer 接口。
  * **协议**: 按行分隔的 JSON，固定 `version: 1`
  * **默认 Socket 路径**: `/tmp/whisplay-daemon.sock`
  * **支持命令**: `health.ping`、`app.register`、`app.list`、`app.launch`、`app.focus.acquire`、`app.focus.release`、`app.exit.request`、`framebuffer.acquire`、`backlight.set`、`led.set`、`led.fade`、`button.get_state`、`events.subscribe`
  * **桌面交互**: 单击切换 app、长按启动/切到前台，前台 app 内快速按 4 下请求退出并回到桌面
  * **安装为服务**:
    ```shell
    cd Driver
    sudo bash install_whisplay_daemon_service.sh
    ```

#### 2. WM8960 音频驱动

  * **来源**: 音频驱动支持由 Waveshare（Raspberry Pi）提供，或使用自定义 overlay（Radxa）。

  * **安装**:
    - **Raspberry Pi**: 运行 `install_wm8960_drive.sh`
    - **Radxa ZERO 3W**: 运行 `install_radxa_zero3w.sh`
    - **Radxa Cubie A7Z**: 运行 `install_radxa_cubie_a7z.sh`

    ```shell
    cd Driver
    # Raspberry Pi:
    sudo bash install_wm8960_drive.sh
    # Radxa ZERO 3W:
    sudo bash install_radxa_zero3w.sh
    # Radxa Cubie A7Z:
    sudo bash install_radxa_cubie_a7z.sh
    ```

#### 3. `wm8960-radxa-zero3.dts`（仅限 Radxa）

  * **功能**: Radxa ZERO 3W (RK3566) 上 WM8960 编解码器的设备树 overlay 源文件，配置 I2C3 和 I2S3 音频接口。
  * **说明**: 此文件会由 `install_radxa_zero3w.sh` 自动编译并安装。

#### 4. `wm8960-cubie-a7z.dts`（仅限 Radxa）

  * **功能**: Radxa Cubie A7Z (Allwinner A733) 上 WM8960 编解码器的设备树 overlay 源文件，配置 TWI7 和 I2S0 音频接口。
  * **说明**: 此文件会由 `install_radxa_cubie_a7z.sh` 自动编译并安装。


## 示例程序

`example` 目录下提供了 Python 示例，帮助您快速上手。

#### `run_test.sh`

  * **功能**: 验证 LCD、LED 和按键是否正常工作。
  * **使用方法**:
    ```shell
    cd example
    sudo bash run_test.sh
    ```
    您也可以指定图片或音频进行测试：
    ```shell
    sudo bash run_test.sh --image data/test2.jpg --sound data/test.mp3
    ```
    **效果**: 程序运行后，LCD 将显示测试图片。按下任意按键，屏幕会变为纯色，同时 RGB LED 也将同步显示为相同的颜色。

#### `mic_test.sh`

  * **功能**: 测试麦克风输入功能。
  * **使用方法**:
    ```shell
    cd example
    sudo bash mic_test.sh
    ```
    **效果**: 程序录制 10 秒钟麦克风音频，随后通过扬声器播放录音内容。

#### `test2.py`

  * **功能**: 演示录音与回放功能。
  * **使用方法**:
    ```shell
    cd example
    sudo python3 test2.py
    ```
    **效果**: 程序显示一张表示录音阶段的图片。按下按钮停止录音后，会切换到回放阶段并显示不同图片，同时播放录制的音频。播放结束后返回录音阶段。

#### `play_mp4.py`

  * **功能**: 在 LCD 屏幕上播放 MP4 视频文件。
  * **前置条件**: 确保系统已安装 `ffmpeg`：
    ```shell
    sudo apt-get install ffmpeg
    ```
  * **下载测试视频**:
    将示例 MP4 视频下载到 `example/data` 目录：
    ```shell
    cd example
    wget -O data/whisplay_test.mp4 https://img-storage.pisugar.uk/whisplay_test.mp4
    ```
  * **使用方法**:
    在 `example` 目录下执行：
    ```shell
    sudo python3 play_mp4.py --file data/whisplay_test.mp4
    ```
    **效果**: 指定的 MP4 视频将在 LCD 屏幕上播放。

#### `whisplay_daemon_client.py`

  * **功能**: 用于测试 daemon 的健康检查、app 注册/列举/启动、LED/背光控制、前台获取和按键事件订阅。
  * **使用方式**:
    ```shell
    cd example
    python3 whisplay_daemon_client.py ping
    python3 whisplay_daemon_client.py apps
    python3 whisplay_daemon_client.py register demo DemoApp --launch-command "python3 /path/to/app.py"
    python3 whisplay_daemon_client.py led 255 0 0 --fade
    python3 whisplay_daemon_client.py foreground demo --color f800
    python3 whisplay_daemon_client.py subscribe
    ```


**注意：本软件目前支持：**
- **Raspberry Pi**: 官方 full 版本操作系统
- **Radxa ZERO 3W**: Debian 12 (bookworm) 官方镜像
- **Radxa Cubie A7Z**: Debian 11 (bullseye) 官方镜像

**A7Z 安全提示：** 在 Radxa Cubie A7Z 上，请**不要点击 Whisplay HAT 的物理按键**。由于电路不兼容，点击可能导致设备立即断电。

## 相关链接

- [PiSugar Whisplay Docs](https://docs.pisugar.com/docs/product-wiki/whisplay/intro)
- [Third-Party App Integration Guide](APP_INTEGRATION.md)
- [第三方 App 接入指南](APP_INTEGRATION_CN.md)
- [whisplay-ai-chatbot](https://github.com/PiSugar/whisplay-ai-chatbot)
- [whisplay-lumon-mdr-ui](https://github.com/PiSugar/whisplay-lumon-mdr-ui)
