[English](README.md) | [中文](README_CN.md)

# PiSugar Whisplay Hat Driver

## Project Overview

This project provides comprehensive driver support for the **PiSugar Whisplay Hat**, enabling easy control of the onboard LCD screen, physical buttons, LED indicators, and audio functions.

**Supported Platforms:**
- Raspberry Pi (all models with 40-pin header)
- Radxa ZERO 3W (RK3566)

More Details please refer to [Whisplay HAT Docs](https://docs.pisugar.com/docs/product-wiki/whisplay/intro)

---

### **💡 Bus Information Tip 💡**

The device utilizes **I2C, SPI, and I2S** buses. The **I2S and I2C buses** are used for audio and will be enabled automatically during driver installation. 

---

### Installation

#### Raspberry Pi

After cloning the github project, navigate to the Driver directory and use the script to install.

```bash
git clone https://github.com/PiSugar/Whisplay.git --depth 1
cd Whisplay/Driver
sudo bash install_wm8960_drive.sh
sudo reboot
```
The program can be tested after the driver is installed.

```shell
cd Whisplay/example
sudo bash run_test.sh
```

#### Radxa ZERO 3W

After cloning the github project, navigate to the Driver directory and use the Radxa-specific script to install.

```bash
git clone https://github.com/PiSugar/Whisplay.git --depth 1
cd Whisplay/Driver
sudo bash install_radxa_zero3w.sh
sudo reboot
```

The installation script will:
1. Install Python dependencies (`python3-libgpiod`, `python3-spidev`, `python3-pil`, `python3-pygame`)
2. Enable SPI3_M1 overlay (for LCD display)
3. Enable I2S3 overlay (for WM8960 audio)
4. Configure WM8960 audio driver (if kernel module is available)

After rebooting, test the setup:

```shell
cd Whisplay/example
sudo bash run_test.sh
```

### Driver Structure

All driver files are located in the `Driver` directory and primarily include:

#### 1. `Whisplay.py`

  * **Function**: This script encapsulates the LCD display, physical buttons, and LED indicators into easy-to-use Python objects, simplifying hardware operations. It **automatically detects the platform** (Raspberry Pi or Radxa ZERO 3W) and uses the appropriate GPIO library.
  * **Quick Verification**: Refer to `example/test.py` to quickly test the LCD, LED, and button functions.

#### 2. WM8960 Audio Driver

  * **Source**: Audio driver support is provided by Waveshare (Raspberry Pi) or custom overlay (Radxa).

  * **Installation**:
    - **Raspberry Pi**: Run `install_wm8960_drive.sh`
    - **Radxa ZERO 3W**: Run `install_radxa_zero3w.sh`

    ```shell
    cd Driver
    # For Raspberry Pi:
    sudo bash install_wm8960_drive.sh
    # For Radxa ZERO 3W:
    sudo bash install_radxa_zero3w.sh
    ```

#### 3. `wm8960-radxa-zero3.dts` (Radxa only)

  * **Function**: Device tree overlay source for the WM8960 codec on Radxa ZERO 3W (RK3566), configuring I2C3 and I2S3 for audio.
  * **Note**: This is automatically compiled and installed by `install_radxa_zero3w.sh`.


## Example Programs

The `example` directory contains Python examples to help you get started quickly.

#### `run_test.sh`

  * **Function**: This script verifies that the LCD, LEDs, and buttons are functioning correctly.
  * **Usage**:
    ```shell
    cd example
    sudo bash run_test.sh
    ```
    You can also specify an image or sound for testing:
    ```shell
    sudo bash run_test.sh --image data/test2.jpg --sound data/test.mp3
    ```
    **Effect**: When executed, the script will display a test image on the LCD. Pressing any button will change the screen to a solid color, and the RGB LED will simultaneously change to match that color.

#### `mic_test.sh`

  * **Function**: This script tests the microphone functionality.
  * **Usage**:
    ```shell
    cd example
    sudo bash mic_test.sh
    ```
    **Effect**: The script records audio from the microphone for 10 seconds and plays it back through the speaker.

#### `test2.py`

  * **Function**: This script demonstrates recording audio and playback functionality.
  * **Usage**:
    ```shell
    cd example
    sudo python3 test2.py
    ```
    **Effect**: The script displays an image indicating the recording stage. Pressing the button to stop recording will switch to the playback stage, displaying a different image while playing back the recorded audio. After playback, it returns to the recording stage again.

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


**Note: This software currently supports:**
- **Raspberry Pi**: Official full version of the operating system
- **Radxa ZERO 3W**: Debian 12 (bookworm) official image

## Links

- [PiSugar Whisplay Docs](https://docs.pisugar.com/docs/product-wiki/whisplay/intro)
- [whisplay-ai-chatbot](https://github.com/PiSugar/whisplay-ai-chatbot)
- [whisplay-lumon-mdr-ui](https://github.com/PiSugar/whisplay-lumon-mdr-ui)
