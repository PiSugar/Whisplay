[English](README.md) | [中文](README_CN.md)

# PiSugar Whisplay Hat Driver

## Project Overview

This project provides comprehensive driver support for the **PiSugar Whisplay Hat**, enabling easy control of the onboard LCD screen, physical buttons, LED indicators, and audio functions.

More Details please refer to [Whisplay HAT Docs](https://docs.pisugar.com/docs/product-wiki/whisplay/intro)

---

### **💡 Bus Information Tip 💡**

The device utilizes **I2C, SPI, and I2S** buses. The **I2S and I2C buses** are used for audio and will be enabled automatically during driver installation. 

---

### Installation

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

### Driver Structure

All driver files are located in the `Driver` directory and primarily include:

#### 1. `Whisplay.py`

  * **Function**: This script encapsulates the LCD display, physical buttons, and LED indicators into easy-to-use Python objects, simplifying hardware operations.
  * **Quick Verification**: Refer to `example/test.py` to quickly test the LCD, LED, and button functions.

#### 2. WM8960 Audio Driver

  * **Source**: Audio driver support is provided by Waveshare.

  * **Installation**: Install by running the `install_wm8960_drive.sh` script:

    ```shell
    cd Driver
    sudo bash install_wm8960_drive.sh
    ```


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
    sudo bash run_test.sh --image test2.jpg --sound test.mp3
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


**Note: This software currently only supports the official full version of the operating system.**

## Links

- [PiSugar Whisplay Docs](https://docs.pisugar.com/docs/product-wiki/whisplay/intro)
- [whisplay-ai-chatbot](https://github.com/PiSugar/whisplay-ai-chatbot)
- [whisplay-lumon-mdr-ui](https://github.com/PiSugar/whisplay-lumon-mdr-ui)
