[English](README.md) | [中文](README_CN.md)

# PiSugar Whisplay Hat Driver

## Project Overview

This project provides comprehensive driver support for the **PiSugar Whisplay Hat**, enabling easy control of the onboard LCD screen, physical buttons, LED indicators, and audio functions.

---

### **💡 Bus Information Tip 💡**

The device utilizes **I2C, SPI, and I2S** buses. The **I2S and I2C buses** are used for audio and will be enabled automatically during driver installation. The **SPI bus** needs to be enabled manually.

---

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

The `example` directory contains several Python examples to help you get started quickly. Note that these examples do not currently include audio integration.

#### 1. `test.py`

  * **Function**: This script verifies that the LCD, LEDs, and buttons are functioning correctly.
  * **Usage**:
    Run `test.py`:
    ```shell
    cd example
    python test.py
    ```
    You can also specify an image for testing:
    ```shell
    python test.py test1.jpg
    ```
    **Effect**: When executed, the script will display a test image on the LCD. Pressing any button will change the screen to a solid color, and the RGB LED will simultaneously change to match that color.

#### 2. `chatbot-ui.py`

  * **Function**: This script provides a socket interface for a voice chatbot, allowing external programs to display status updates and conversation content.
  * **Usage**:
    1.  **Run UI Listener**: First, run `chatbot-ui.py` to listen on the port:
        ```shell
        cd example
        python chatbot-ui.py
        ```
        **Note**: The program listens on port `12345`. Once connected, a client can send information to be displayed and receive button status updates.
    2.  **Test UI**: In a new terminal window, run `sockettest.py` to test the UI:
        ```shell
        python sockettest.py
        ```
        **Effect**: After running `sockettest.py`, pressing a button will trigger the content on the LCD display to change randomly.

-----

**Note: This software currently only supports the official full version of the operating system.**