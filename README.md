[English](README.md) | [中文](README_CN.md)

# PiSugar Whisplay Hat Driver

## Project Overview

This project provides comprehensive driver support for the **PiSugar Whisplay Hat**, enabling easy control of the onboard LCD screen, physical buttons, LED indicators, and audio functions.

**Supported Platforms:**
- Raspberry Pi (all models with 40-pin header)
- Radxa ZERO 3W (RK3566)
- Radxa Cubie A7Z (Allwinner A733)

More Details please refer to [Whisplay HAT Docs](https://docs.pisugar.com/docs/product-wiki/whisplay/intro)

---

### **💡 Bus Information Tip 💡**

The device utilizes **I2C, SPI, and I2S** buses. The **I2S and I2C buses** are used for audio and will be enabled automatically during driver installation. 

---

### Installation

After cloning the project, run the unified installer entry:

```bash
git clone https://github.com/PiSugar/Whisplay.git --depth 1
cd Whisplay
sudo bash install_driver.sh
sudo reboot
```

> ⚠️ **Important Hardware Warning (A7Z only)**  
> Due to circuit incompatibility, the physical button on Whisplay HAT is **not safe to use on Radxa Cubie A7Z**.  
> **Do not press the button**, otherwise the A7Z may shut down / lose power immediately.

Test the hardware functions with the demo script:

```shell
cd Whisplay/example
sudo bash run_test.sh
```

### Whisplay Daemon Service

`whisplay-daemon` is an optional local service that centrally manages LCD, backlight, RGB LED, button events, and app foreground switching. (Single click to switch app, long press to launch/foreground app, and 4 rapid clicks to request exit from foreground app)

The daemon now also ships with three built-in system entries:

- `Bluetooth`: opens an internal page that scans nearby Bluetooth devices and lets you bind or unbind the selected device
- `WiFi`: opens an internal page that scans nearby Wi-Fi networks and lets you connect; protected networks enter a single-button password page, and actual password input depends on an attached external keyboard
- `Volume`: opens an internal page for speaker volume adjustment

<p align="center">
  <img src="daemon/img/screenshots/whisplay_desktop.png" width="180" alt="Daemon Desktop" />
  &nbsp;&nbsp;
  <img src="daemon/img/screenshots/whisplay_bluetooth.png" width="180" alt="Bluetooth Page" />
  &nbsp;&nbsp;
  <img src="daemon/img/screenshots/whisplay_wifi.png" width="180" alt="WiFi Page" />
</p>
<p align="center"><em>Left: Desktop app launcher &nbsp;|&nbsp; Middle: Bluetooth manager &nbsp;|&nbsp; Right: WiFi connection</em></p>

If you are using the daemon, other apps is not recommended to directly access the hardware, and should instead register with the daemon to get foreground control and shared framebuffer access.

Install and start it with:

```shell
sudo bash daemon/install_whisplay_daemon_service.sh
systemctl status whisplay-daemon.service --no-pager
```

After installation, daemon settings are stored in `~/.whisplay-daemon/settings.json`, and app entries are loaded from `~/.whisplay-daemon/app/`.

Example daemon settings:

```json
{
  "apps_dir": "~/.whisplay-daemon/app",
  "pisugar_home_button": "single"
}
```

`pisugar_home_button` controls which PiSugar button gesture returns from the foreground app back to daemon home. Supported values are `single`, `double`, `long`, and `none`. The default is `single`.

To inspect daemon logs:

```shell
journalctl -u whisplay-daemon.service -f
```

If an app is configured with `use_daemon_default_log: true`, its stdout/stderr is appended to:

```shell
tail -f ~/.whisplay-daemon/daemon-app.log
```

### Project Structure

The repo root is organized by responsibility:

- `runtime/`: Python runtime modules including `whisplay.py` and `whisplay_client.py`
- `install_driver.sh`: auto-detecting driver installer
- `script/`: platform install scripts
- `daemon/`: local hardware daemon, its service installer, and `default_apps/`
- `audio/`: audio install assets and DTS overlays
- `example/`: end-user demos

#### 1. `runtime/whisplay.py`

  * **Function**: Public Python entry point for the LCD, physical button, and LED helper classes.
  * **Quick Verification**: Refer to `example/test.py` to quickly test the LCD, LED, and button functions.

#### 1.1 `runtime/whisplay_client.py`

  * **Function**: Python helper for daemon-mode apps.

#### 1.2 `daemon/whisplay_daemon.py`

  * **Function**: Optional local hardware daemon that owns the LCD, backlight, RGB LED, button, and app lifecycle, and exposes a local Unix socket API for app registration, app switching, and shared framebuffer handoff.
  * **Protocol**: line-delimited JSON with `version: 1`
  * **Default socket path**: `/tmp/whisplay-daemon.sock`
  * **Commands**: `health.ping`, `app.register`, `app.list`, `app.launch`, `app.focus.acquire`, `app.focus.release`, `app.exit.request`, `framebuffer.acquire`, `backlight.set`, `led.set`, `led.fade`, `button.get_state`, `events.subscribe`
  * **Desktop behavior**: single click cycles registered apps, long press launches/foregrounds the selected app, and 4 rapid clicks request exit from the foreground app
  * **Built-in system pages**: includes `Bluetooth`, `WiFi`, and `Volume` entries rendered by the daemon itself, without spawning an external app process
  * **Wi-Fi password input**: selecting a protected network enters a single-button password page; password entry depends on an attached external keyboard (arrow keys / Enter / Backspace / ESC)
  * **PiSugar home integration**: if `pisugar-server` is running, daemon can automatically bind the PiSugar `single`, `double`, or `long` button gesture as a return-to-home trigger according to `~/.whisplay-daemon/settings.json`; set `pisugar_home_button` to `none` to disable it
  * **Install as service**:
    ```shell
    sudo bash daemon/install_whisplay_daemon_service.sh
    ```
  * **Install result**: the installer writes `~/.whisplay-daemon/settings.json` and seeds the default example app JSON files into `~/.whisplay-daemon/app/`

#### 2. WM8960 Audio Driver

  * **Source**: Audio driver support is provided by Waveshare (Raspberry Pi) or custom overlay (Radxa).

  * **Installation**:
    - **Auto-detect**: Run `install_driver.sh`
    - **Raspberry Pi**: Run `script/install_raspberry_pi.sh`
    - **Radxa ZERO 3W**: Run `script/install_radxa_zero3w.sh`
    - **Radxa Cubie A7Z**: Run `script/install_radxa_cubie_a7z.sh`

    ```shell
    sudo bash install_driver.sh
    # Or run a platform-specific installer:
    sudo bash script/install_raspberry_pi.sh
    # For Radxa ZERO 3W:
    sudo bash script/install_radxa_zero3w.sh
    # For Radxa Cubie A7Z:
    sudo bash script/install_radxa_cubie_a7z.sh
    ```

#### 3. Device Tree Overlays (Radxa only)

  * `audio/wm8960-radxa-zero3.dts` - DT overlay for WM8960 codec on Radxa ZERO 3W (RK3566), configuring I2C3 and I2S3.
  * `audio/wm8960-cubie-a7z.dts` - DT overlay for WM8960 codec on Radxa Cubie A7Z (Allwinner A733), configuring TWI7 and I2S0.
  * **Note**: These are automatically compiled and installed by the respective install scripts.


## Example Programs

The `example` directory contains 4 end-user demo programs. If you are using whisplay-daemon, you can see their entries directly on the daemon desktop; if not using the daemon, you can run these scripts directly to test hardware functions and experience the demo applications.

#### `run_test.sh`

  * **Function**: Runs the end-to-end hardware test flow for screen, LED, speaker, button, microphone, and playback.
  * **Usage**:
    ```shell
    cd example
    sudo bash run_test.sh
    ```
    **Effect**: The demo shows the logo countdown first, then walks through each hardware test step with on-screen instructions and a final summary.

#### `play_mp4.py`

  * **Function**: This script plays an MP4 video file on the LCD screen.
  * **Prerequisites**: Ensure that `ffmpeg` is installed on your system. You can install it using:
    ```shell
    sudo apt-get install ffmpeg
    ```
  * **Download Test Video**:
    download a sample MP4 video to the `example/data` directory:
    ```shell
    cd example
    wget -O data/whisplay_test.mp4 https://img-storage.pisugar.uk/whisplay_test.mp4
    ```
  * **Usage**:
    execute the script in the `example` directory:
    ```shell
    sudo python3 play_mp4.py --file data/whisplay_test.mp4
    ```
    **Effect**: The specified MP4 video will be played on the LCD screen.

#### `flappy_bird.py`

  * **Function**: Single-button Flappy Bird demo with game sound effects.
  * **Usage**:
    ```shell
    cd example
    sudo python3 flappy_bird.py
    ```
    **Effect**: Short press makes the bird flap. The game includes pseudo-arcade visuals, score tracking, and WM8960 playback effects.

#### `jump_game.py`

  * **Function**: Single-button Jump Game demo with pseudo-3D tilted rendering and sound effects.
  * **Usage**:
    ```shell
    cd example
    sudo python3 jump_game.py
    ```
    **Effect**: Hold to charge and release to jump. The demo is tuned for Pi Zero 2W class performance and uses on-screen prompts plus game audio.


**Note: This software currently supports:**
- **Raspberry Pi**: Official full version of the operating system
- **Radxa ZERO 3W**: Debian 12 (bookworm) official image
- **Radxa Cubie A7Z**: Debian 11 (bullseye) official image

**A7Z Safety Notice:** On Radxa Cubie A7Z, please **do not click the physical button** on Whisplay HAT. Circuit incompatibility may cause immediate power-off.

## Documentation and Related Projects

### Official Documentation

[PiSugar Whisplay Docs](https://docs.pisugar.com/docs/product-wiki/whisplay/intro)

### Integration Guides

- [Third-Party App Integration Guide](APP_INTEGRATION.md)
- [第三方 App 接入指南](APP_INTEGRATION_CN.md)

### Related Projects

| Project | Author | Description |
|---------|--------|-------------|
| [whisplay-ai-chatbot](https://github.com/PiSugar/whisplay-ai-chatbot) | PiSugar | AI chatbot using Whisplay HAT as display and voice control interface |
| [whisplay-xiaozhi](https://github.com/PiSugar/whisplay-xiaozhi) | PiSugar | XiaoZhi chatbot client implementation for Raspberry Pi with Whisplay HAT |
| [whisplay-talk](https://github.com/PiSugar/whisplay-talk) | PiSugar | Voice interaction project based on Whisplay HAT |
| [whisplay-lumon-mdr-ui](https://github.com/PiSugar/whisplay-lumon-mdr-ui) | PiSugar | Tiny Lumon MDR device implementation |
| [pizero-openclaw](https://github.com/sebastianvkl/pizero-openclaw) | Sebastianvkl | Openclaw project with Whisplay HAT display and voice control |
| [pisugar-wx](https://github.com/hemna/pisugar-wx) | Hemna | Weather information display on Whisplay HAT |
