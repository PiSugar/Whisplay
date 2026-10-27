[English](README.md) | [中文](README_CN.md)

# PiSugar Whisplay 扩展板驱动

## 项目概览

本项目为 **PiSugar Whisplay 扩展板** 提供完整的驱动程序支持，让您可以轻松控制板载的 LCD 屏幕、物理按键和 LED 指示灯，并支持音频功能。

**支持平台：**
- Raspberry Pi（所有带 40-pin 排针的型号）
- [Orange Pi Zero 2W](http://www.orangepi.org/html/hardWare/computerAndMicrocontrollers/details/Orange-Pi-Zero-2W.html)（H618；官方 Debian Bookworm 1.0.2 / Linux 6.1.31）
- [MangoPi MQ Quad](https://linux-sunxi.org/MangoPi_MQ-Quad)（全志 H616；已在 Orange Pi OS 3.0.8 / Linux `5.16.17-sun50iw9`、ES8389 和 V1 WM8960 上验证）
- [Orange Pi Zero 3W](http://www.orangepi.org/html/hardWare/computerAndMicrocontrollers/details/Orange-Pi-Zero-3W.html)（Allwinner A733；官方 Debian Bookworm 1.0.0 / Linux 6.6.98）
- [Radxa ZERO 3W](https://radxa.com/products/zeros/zero3w/) (RK3566)
- [Radxa Cubie A7Z](https://radxa.com/products/cubie/a7z/) (Allwinner A733)

更多详细信息请参考 [Whisplay HAT 文档](https://docs.pisugar.com/docs/product-wiki/whisplay/intro)

---

### **💡 总线信息提示 💡**

设备使用了 **I2C、SPI、I2S** 总线。其中 **I2S 和 I2C 总线** 用作音频驱动，会在安装驱动的时候自动启用。

---

### 安装

克隆项目后，统一使用自动识别安装入口：

```bash
git clone https://github.com/PiSugar/Whisplay.git --depth 1
cd Whisplay
sudo bash install_driver.sh
sudo reboot
```

如果 Raspberry Pi 启动时检测到已烧录的 PiSugar Whisplay HAT EEPROM，安装脚本会让 EEPROM 负责自动加载 `whisplay-soundcard` overlay，并移除旧安装遗留的手动 `dtoverlay=whisplay-soundcard` 配置。

#### MangoPi MQ Quad（芒果派）兼容性

已在 Orange Pi OS 3.0.8 Bullseye 镜像、厂商内核 `5.16.17-sun50iw9` 上，分别使用 ES8389 和 V1 WM8960 版 Whisplay HAT 完成真机验证。该镜像可能将主板识别为 Orange Pi Zero2，可使用 `sudo bash script/install_mangopi_mq_quad.sh` 明确选择芒果派安装器。安装后请重启加载模块和 overlay，不要热解绑 codec。

- 支持 LCD、背光、RGB LED、按键及 daemon 桌面/app 切换；Jump Game 和 Flappy Bird 已通过启用音效后的反复启动、退出测试。
- 已验证扬声器实际发声、两个麦克风通道有效录音，以及同时播放/录音、反复开关音频设备和重启后恢复。另用外部 USB 麦克风独立确认扬声器输出，并非仅通过 DMA 或数字回环判断音频正常。
- 使用 `whisplaysound` ALSA 声卡，音频总线为 48 kHz、两个 32-bit I2S slot。S16_LE、S24_LE、S32_LE 的 SoC 回环均通过声道映射和测试音频率检查。HAT 扬声器为单声道，左右声道映射在数字链路上验证。
- 游戏音频重采样需要保留 `libasound2-plugins`（芒果派安装器已包含）。可选 daemon 需另行运行 `sudo bash daemon/install_whisplay_daemon_service.sh` 安装。
- V1 WM8960 需要 `snd-soc-wm8960`，此厂商内核未附带该模块。安装器会在缺失时构建，前提是 `/lib/modules/$(uname -r)/build` 已提供匹配的内核头文件。统一驱动使用 V1 HAT 的 24 MHz 振荡器与 WM8960 自动 PLL 配置，不再将 24 MHz 直接作为 48 kHz 音频 SYSCLK。

以上结论仅覆盖已测试的 HAT 与厂商内核组合；其他镜像及内核版本未经过本轮芒果派回归验证，可能需要进一步适配。WM8960 的 `aplay` 进程耗时包含 PLL 锁定/启动开销，回归工具会单独测量录音中测试音的实际时长。`codec-loopback` 仅适用于 ES8389；WM8960 请使用 `soc-loopback` 和 `microphone`。

> ⚠️ **重要硬件警告（Orange Pi Zero 3W 和 Radxa Cubie A7Z）**
> Orange Pi Zero 3W 和 Radxa Cubie A7Z **必须搭配 Whisplay V2 硬件使用**。
> 请勿连接 Whisplay V1：其按钮线路带有 5V，按下按钮会导致主板立即断电。

使用示例脚本测试硬件功能：

```shell
cd Whisplay/example
sudo bash run_test.sh
```

### Whisplay Daemon 服务

`whisplay-daemon` 是可选的本地服务，统一管理 LCD、背光、RGB LED、按键事件和 app 前台切换。（单击切换 app，长按启动app，4下快速点击请求退出）

当前 daemon 还内置了四个系统入口：

- `Bluetooth`：进入内部页面后可扫描附近蓝牙设备，并对选中设备执行绑定/解绑
- `WiFi`：进入内部页面后可扫描附近 Wi‑Fi，选择网络并连接；Whisplay Joy 可使用屏幕键盘输入加密网络密码，原版型号仍支持外接键盘
- `Volume`：进入内部页面后可调节扬声器音量
- `Power`：进入 Power Menu，可锁屏、重启或关闭设备；锁屏后背光关闭，RGB 灯显示蓝色呼吸效果，按下 Whisplay 按钮即可唤醒

<p align="center">
  <img src="daemon/img/screenshots/whisplay_desktop.png" width="180" alt="Daemon 桌面" />
  &nbsp;&nbsp;
  <img src="daemon/img/screenshots/whisplay_bluetooth.png" width="180" alt="蓝牙页面" />
  &nbsp;&nbsp;
  <img src="daemon/img/screenshots/whisplay_wifi.png" width="180" alt="WiFi 页面" />
</p>
<p align="center"><em>左：桌面 App 列表 &nbsp;|&nbsp; 中：蓝牙管理 &nbsp;|&nbsp; 右：WiFi 连接</em></p>

如果你使用了 daemon，建议其他 app 不要直接访问硬件，而是通过注册到 daemon 来获取前台控制权和共享 framebuffer 访问。

安装并启动命令：

```shell
sudo bash daemon/install_whisplay_daemon_service.sh
systemctl status whisplay-daemon.service --no-pager
```

安装完成后，daemon 设置保存在 `~/.whisplay-daemon/settings.json`，app 入口从 `~/.whisplay-daemon/app/` 加载。

daemon 设置示例：

```json
{
  "apps_dir": "~/.whisplay-daemon/app",
  "model": "auto",
  "pisugar_home_button": "single",
  "autostart_app_id": ""
}
```

`autostart_app_id` 可填写 `~/.whisplay-daemon/app/` 中已注册 app 的 `app_id`，daemon
每次启动时会自动启动该 app；保持为空字符串或省略该字段则停留在 daemon 桌面。

`model` 默认为 `auto`：daemon 会读取 PiSugar HAT EEPROM，并将 product ID
`0x0001`、`0x0002`、`0x0003` 分别识别为 `whisplay`、`whisplay-plus`、
`whisplay-joy`。用户也可以填写具体型号来强制覆盖 EEPROM 检测结果。

Whisplay Joy 五轴按键映射为：S1/上
（物理 pin 32，BCM12）、S2/右（pin 33，BCM13）、S4/下（pin 31，BCM6）、
S3/左（pin 36，BCM16）、CENTER（pin 37，BCM26）。daemon 中上/下用于菜单
滚动，右键或 CENTER 确认，左键返回；原 Whisplay 按钮也可单击确认。外部 app
获得前台后 daemon 暂停自身的五轴导航，并将五轴按下/松开事件转交给该 app；
返回 daemon 后自动恢复菜单导航。

`pisugar_home_button` 用于控制 PiSugar 的哪个按键事件会触发“从前台 app 返回 daemon 首页”。支持 `single`、`double`、`long`、`none`，默认值为 `single`。检测到 PiSugar3（I²C bus 1、地址 `0x57`）时，daemon 会直接读取 `0x02` 寄存器的电源键状态，以电源键单击返回首页，不再占用 PiSugar 自定义按钮的单击事件；设为 `none` 仍可关闭此功能。

查看 daemon 日志：

```shell
journalctl -u whisplay-daemon.service -f
```

如果某个 app 配置了 `use_daemon_default_log: true`，它的 stdout/stderr 会追加到：

```shell
tail -f ~/.whisplay-daemon/daemon-app.log
```

### 项目结构

仓库根目录现在按职责拆分：

- `runtime/`：Python 运行时模块，包括 `whisplay.py` 和 `whisplay_client.py`
- `install_driver.sh`：自动识别平台的驱动安装入口
- `script/`：平台安装脚本
- `daemon/`：本地硬件 daemon、其服务安装脚本，以及 `default_apps/`
- `audio/`：音频安装资源、DTS overlay，以及内置的 Whisplay 统一声卡驱动
- `example/`：面向用户的示例程序

#### 1. `runtime/whisplay.py`

  * **功能**: LCD、物理按键、LED、五轴按键和 QMI8658 的公开 Python 入口。单独运行的 app 可在打开硬件前调用 `detect_model()`，或在创建 `WhisplayBoard` 后调用 `board.get_model()`、`board.get_joystick_state()` 和 `board.read_motion()`。
  * **快速验证**: 参考 `example/test.py` 文件，快速测试 LCD、LED 和按键功能。

#### 1.1 `runtime/whisplay_client.py`

  * **功能**: daemon 模式的 Python 客户端 helper。返回的 proxy 同样提供 `board.get_model()`，并通过 `hardware.get_info` 获取 daemon 当前生效的型号。

#### 1.2 `daemon/whisplay_daemon.py`

  * **功能**: 可选的本地硬件守护进程，独占 LCD、背光、RGB LED、按键和 app 生命周期，并通过本机 Unix Socket 暴露 app 注册、切换和共享 framebuffer 接口。
  * **协议**: 按行分隔的 JSON，固定 `version: 1`
  * **默认 Socket 路径**: `/tmp/whisplay-daemon.sock`
  * **支持命令**: `health.ping`、`hardware.get_info`、`joystick.get_state`、`motion.get_state`、`app.register`、`app.list`、`app.launch`、`app.autostart.set`、`app.focus.acquire`、`app.focus.release`、`app.exit.request`、`framebuffer.acquire`、`backlight.set`、`led.set`、`led.fade`、`button.get_state`、`events.subscribe`
  * **桌面交互**: 单击切换 app、长按启动/切到前台，前台 app 内快速按 4 下请求退出并回到桌面
  * **内建系统页**: 默认包含 `Bluetooth`、`WiFi`、`Volume` 和 `Power` 四个入口，均由 daemon 自身渲染，无需外部 app 进程
  * **WiFi 输入方式**: Whisplay Joy 选择加密网络后会显示微型 QWERTY 屏幕键盘。S1/S2/S4/S3 分别向上/右/下/左移动，CENTER 或短按原 Whisplay 按钮选择高亮按键；`ABC` 切换大小写及数字符号，`DEL` 删除，`SPACE` 输入空格，`JOIN` 发起连接。按住原 Whisplay 按钮达到长按阈值时会立即退出键盘并闪烁红灯，无需等待松开。原版 Whisplay 仍支持外接键盘（回车/退格/ESC）。
  * **PiSugar 返回集成**: daemon 会先通过 I²C 检测 PiSugar3；检测成功时直接使用电源键单击作为“返回首页”，并释放 PiSugar 自定义按钮单击事件。其他型号仍通过 `pisugar-server`，根据 `~/.whisplay-daemon/settings.json` 中的 `pisugar_home_button` 绑定 `single`、`double` 或 `long`；设为 `none` 可关闭此功能
  * **安装为服务**:
    ```shell
    sudo bash daemon/install_whisplay_daemon_service.sh
    ```
  * **安装结果**: 安装脚本会写入 `~/.whisplay-daemon/settings.json`，并把默认示例 app 的 JSON 同步到 `~/.whisplay-daemon/app/`
#### 2. 统一声卡驱动

  * **来源**: Raspberry Pi、Orange Pi Zero 2W/3W、MangoPi MQ Quad、Radxa ZERO 3W 和 Radxa Cubie A7Z 均使用本工程内置的统一 Whisplay 声卡驱动 `audio/whisplay-soundcard/`，兼容 WM8960 和 ES8389 两种 codec 变体。

  * **旧版驱动**: 旧版驱动位于 `support/wm8960` 分支。如有需要，可以先检出该分支再进行安装。

  * **安装**:
    - **自动识别**: 运行 `install_driver.sh`
    - **Raspberry Pi**: 运行 `script/install_raspberry_pi.sh`
    - **Orange Pi Zero 2W**: 运行 `script/install_orangepi_zero2w.sh`
    - **Orange Pi Zero 3W**: 运行 `script/install_orangepi_zero3w.sh`
    - **MangoPi MQ Quad**: 运行 `script/install_mangopi_mq_quad.sh`
    - **Radxa ZERO 3W**: 运行 `script/install_radxa_zero3w.sh`
    - **Radxa Cubie A7Z**: 运行 `script/install_radxa_cubie_a7z.sh`

    ```shell
    sudo bash install_driver.sh
    # 或按平台手动执行:
    sudo bash script/install_raspberry_pi.sh
    # Orange Pi Zero 2W:
    sudo bash script/install_orangepi_zero2w.sh
    # Orange Pi Zero 3W:
    sudo bash script/install_orangepi_zero3w.sh
    # MangoPi MQ Quad:
    sudo bash script/install_mangopi_mq_quad.sh
    # Radxa ZERO 3W:
    sudo bash script/install_radxa_zero3w.sh
    # Radxa Cubie A7Z:
    sudo bash script/install_radxa_cubie_a7z.sh
    ```

#### 3. `audio/whisplay-soundcard/`（统一声卡驱动）

  * **功能**: 内置统一声卡驱动源码、安装脚本、ALSA 配置和平台 DTS overlay。
  * **说明**: 各平台安装脚本都会直接从本工程构建并安装该驱动，不需要用户本地另有 `whisplay-soundcard` 仓库。
  * **MangoPi MQ Quad / Linux 5.16**：MQ overlay 会启用厂商 DMA 宽度与分频修正，以及每次流配置时的 APBIF 复位/FIFO 初始化，避免 app 切换后左右声道错位。ES8389 在 I2S 时钟稳定后初始化，保留 HAT 板载 MCLK 振荡器。这些厂商兼容处理仅作用于芒果派，不改变其他平台的时钟行为。在板上运行 `sudo python3 audio/whisplay-soundcard/scripts/test-mangopi-audio.py microphone` 可保存并分析真实扬声器到麦克风的录音；`soc-loopback`、`codec-loopback` 验证数字链路，`--format S24_LE` / `--format S32_LE` 验证更宽的 PCM 容器。测试会短暂停止并恢复 daemon，等待 daemon 就绪，同时恢复数字回环位；daemon 重启期间屏幕可能短暂黑屏。

#### 4. 设备树 Overlay

  * `audio/whisplay-soundcard/src/dts/whisplay-soundcard-orangepi-zero2w.dts`：H618 专用统一 overlay，音频 I2C1 使用物理 3/5 脚，AHUB/I2S0 使用物理 12/35/38/40 脚。安装器还会启用官方 `pi-i2c1`、`spi1-cs0-spidev` overlay（40-pin 物理 19/21/23/24 脚上的 SPI1 CS0），并把 Whisplay user overlay 写入 `/boot/orangepiEnv.txt`。
  * `audio/whisplay-soundcard/src/dts/whisplay-soundcard-mangopi-mq-quad.dts`：H616 专用统一 overlay，启用音频所需的 I2C1、AHUB/I2S0，以及 LCD 所需的 40-pin SPI1 CS0；安装器会将其写入 `/boot/orangepiEnv.txt` 的 `user_overlays`。
  * `audio/whisplay-soundcard/src/dts/whisplay-soundcard-orangepi-zero3w.dts`：A733 专用统一 overlay，音频 TWI0 使用物理 3/5 脚，I2S0 使用物理 12/35/38/40 脚，安装器会为 LCD 启用 SPI3 CS0。I2S0 MCLK/PB4 不会被占用，因为物理 pin 7 必须保留给 LCD reset。
  * **Orange Pi OS 1.0.2 内核头文件**：官方镜像仓库没有可安装的 headers 包。在内核严格为 `6.1.31-sun50iw9` 时，安装器会下载固定版本且经过 SHA-256 校验的 headers 归档来编译统一模块；其他内核版本需要自行在 `/lib/modules/$(uname -r)/build` 提供匹配头文件。
  * **Orange Pi Zero 3W 内核头文件**：在官方 `6.6.98-sun60iw2` 镜像上，安装器会下载固定版本并校验 SHA-256 的 A733 Linux 6.6.98 headers 包，调整 kernel release 后再构建模块。
  * **Orange Pi 音频约束**：H618 AHUB 固定使用 48 kHz、2 个 32-bit slot；统一驱动会设置厂商 PLL/TDM 时序，并补齐官方 6.1.31 BSP 缺少的 APBIF0 ↔ I2S0 双向路由。

#### 5. `audio/whisplay-soundcard/src/dts/whisplay-soundcard-radxa-zero3w.dts`（Radxa ZERO 3W）

  * **功能**: Radxa ZERO 3W (RK3566) 上统一声卡驱动的设备树 overlay 源文件，配置 WM8960、ES8389、I2C3 和 I2S3 音频接口。
  * **说明**: 此文件会由 `install_radxa_zero3w.sh` 自动编译并安装。

#### 6. `audio/whisplay-soundcard/src/dts/whisplay-soundcard-radxa-cubie-a7z.dts`（Radxa Cubie A7Z）

  * **功能**: Radxa Cubie A7Z (Allwinner A733) 上统一声卡驱动的设备树 overlay 源文件，配置 WM8960、ES8389、TWI7 和 I2S0 音频接口。
  * **音频约束**: A7Z 的厂商 I2S0 路径固定使用 48 kHz、2 个 32-bit slot；统一 machine driver 会同时设置 A733 私有的 1-bit I2S TX/RX 数据延迟，避免录音符号位错位。
  * **TWI 稳定性**: TWI7 使用 100 kHz engine mode；ES8389 的 regmap 事务带有限次重试和传输间隔，可吸收控制器偶发的仲裁丢失/BUS error。
  * **开机服务**: `whisplay-soundcard-a7z-recover.service` 和 `whisplay-soundcard-warmup.service` 会被安装但默认禁用。仅在明确需要对应 workaround 时，安装声卡驱动时设置 `WHISPLAY_A7Z_RECOVERY=1` 或 `WHISPLAY_A7Z_WARMUP=1`。
  * **说明**: 此文件会由 `install_radxa_cubie_a7z.sh` 自动编译并安装。


## 示例程序

`example` 目录下提供 7 个示例程序，如果你正在使用whisplay-daemon，你可以直接在 daemon 的桌面上看到它们的入口；如果没有使用 daemon，可以直接运行这些脚本来测试硬件功能和体验示例应用。

#### `run_test.sh`

  * **功能**: 运行完整硬件测试流程，覆盖屏幕、LED、扬声器、按键、麦克风和回放。检测到 Whisplay Joy 时，还会用图形界面引导用户依次按完五轴按键，并用 QMI8658 加速度计和陀螺仪的实时姿态数据驱动 3D 正方体旋转。
  * **使用方法**:
    ```shell
    cd example
    pip install -r requirements.txt --break-system-packages
    bash run_test.sh
    ```
    **效果**: 程序会先显示 logo 倒计时，再逐步展示每一项测试内容，并在最后显示简洁汇总结果。

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

#### `flappy_bird.py`

  * **功能**: 单按键 Flappy Bird 小游戏，带游戏音效。
  * **使用方法**:
    ```shell
    cd example
    sudo python3 flappy_bird.py
    ```
    **效果**: 短按控制小鸟上升，包含分数统计和 WM8960 音效输出。

#### `jump_game.py`

  * **功能**: 单按键“跳一跳”小游戏，带模拟 3D 倾斜视角和音效。
  * **使用方法**:
    ```shell
    cd example
    sudo python3 jump_game.py
    ```
    **效果**: 按住蓄力、松开跳跃，画面和性能针对 Pi Zero 2W 做了优化。

#### `tank_battle.py`

  * **功能**: 仅限 Whisplay Joy 的坦克大战小游戏，屏幕顺时针旋转为横屏显示，包含音效、可破坏砖墙、绿化带和河流。
  * **使用方法**:
    ```shell
    cd example
    sudo python3 tank_battle.py
    ```
    **效果**: 按住方向键移动，按 Whisplay 按钮发射炮弹；按 Joy Center 打开游戏菜单，可继续游戏或返回 daemon 桌面。

#### `pacman.py`

  * **功能**: 仅限 Whisplay Joy 的 Pac-Man 小游戏，屏幕顺时针旋转为横屏显示，包含普通豆、能量豆、幽灵追逐、关卡、生命、分数和音效。
  * **使用方法**:
    ```shell
    cd example
    sudo python3 pacman.py
    ```
    **效果**: 使用方向键在迷宫中移动，按 Whisplay 按钮开始、进入下一关或重新开始；按 Joy Center 打开游戏菜单，可继续游戏或返回 daemon 桌面。

#### `gyro_racer.py`

  * **功能**: 仅限 Whisplay Joy 的软件 3D 赛车游戏，包含透视投影道路网格、精细 3D 赛车、QMI8658 倾斜辅助转向、车流、碰撞爆炸、氮气、音效和实时陀螺仪状态指示器。
  * **使用方法**:
    ```shell
    cd example
    sudo python3 gyro_racer.py
    ```
    **效果**: 油门自动加速；方向键下控制刹车，左/右控制基础转向，倾斜 Joy 辅助转向。按 Whisplay 按钮释放氮气；按 Joy Center 打开游戏菜单，可继续游戏或返回 daemon 桌面。


**注意：本软件目前支持：**
- **Raspberry Pi**: 官方 full 版本操作系统
- **Radxa ZERO 3W**: Debian 12 (bookworm) 官方镜像
- **Radxa Cubie A7Z**: Debian 11 (bullseye) 官方镜像

**硬件安全提示：** Orange Pi Zero 3W 和 Radxa Cubie A7Z 必须搭配 **Whisplay V2** 使用。请勿在这两款主板上使用 Whisplay V1：其按钮线路带有 5V，按下按钮会导致主板立即断电。

## 文档和相关项目

### 官方文档

[PiSugar Whisplay 文档](https://docs.pisugar.com/docs/product-wiki/whisplay/intro)

### 接入指南

- [Third-Party App Integration Guide](APP_INTEGRATION.md)
- [第三方 App 接入指南](APP_INTEGRATION_CN.md)

### 相关项目

| 项目 | 作者 | 说明 |
|------|------|------|
| [whisplay-chatgpt](https://github.com/PiSugar/whisplay-chatgpt) | PiSugar | 基于 Whisplay HAT 的 Raspberry Pi ChatGPT 语音助手 |
| [whisplay-ai-chatbot](https://github.com/PiSugar/whisplay-ai-chatbot) | PiSugar | 使用 Whisplay HAT 作为显示和语音控制界面的 AI 聊天机器人 |
| [whisplay-xiaozhi](https://github.com/PiSugar/whisplay-xiaozhi) | PiSugar | 基于 Raspberry Pi 和 Whisplay HAT 的小智聊天机器人客户端实现 |
| [whisplay-talk](https://github.com/PiSugar/whisplay-talk) | PiSugar | 基于 Whisplay HAT 的语音交互项目 |
| [whisplay-lumon-mdr-ui](https://github.com/PiSugar/whisplay-lumon-mdr-ui) | PiSugar | Tiny Lumon MDR 设备实现 |
| [pizero-openclaw](https://github.com/sebastianvkl/pizero-openclaw) | Sebastianvkl | 使用 Whisplay HAT 显示和语音控制的 Openclaw 项目 |
| [pisugar-wx](https://github.com/hemna/pisugar-wx) | Hemna | Whisplay HAT 天气信息显示项目 |
