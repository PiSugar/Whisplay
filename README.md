[English](README.md) | [中文](README_CN.md)

# PiSugar WhisPlay Expansion Board Driver

## Project Overview

This project provides full driver support for the **PiSugar WhisPlay expansion board**, allowing you to easily control the onboard LCD screen, physical buttons, and LED indicators, along with audio functionality.

---

### **💡 Bus Information Tip 💡**

The device utilizes **I2C, SPI, and I2S** buses. The **I2S and I2C buses** are used for audio functionality and will be automatically enabled during driver installation. The **SPI bus** needs to be enabled manually.

---

### Driver Structure

All driver files are located in the `Driver` directory and primarily include:

#### 1. `Whisplay.py`

  * **Function**: Encapsulates the LCD display, physical buttons, and LED indicators into easy-to-use Python objects, greatly simplifying hardware operations.
  * **Quick Verification**: Refer to the `example/test.py` file to quickly test LCD, LED, and button functionalities.

#### 2. WM8960 Audio Driver

  * **Source**: Thanks to Waveshare for providing audio driver support.

  * **Installation**: Install by running the `install_wm8960_drive.sh` script:

    ```shell
    cd Driver
    sudo bash install_wm8960_drive.sh
    ```


## Example Programs

The `example` directory provides several Python examples (currently without audio integration) to help you get started quickly.

#### 1. `test.py`

  * **Function**: Verifies that the LCD, LEDs, and buttons are working correctly.
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
    **Effect**: After running, the LCD will display a test image. Pressing any button will change the screen to a solid color, and the RGB LED will simultaneously change to the same color.

#### 2. `chatbot-ui.py`

  * **Function**: Provides a Socket interface for a voice chatbot, used to display current status and conversation content, facilitating external program calls.
  * **Usage**:
    1.  **Run UI Listener**: First, run `chatbot-ui.py` to listen on the port:
        ```shell
        cd example
        python chatbot-ui.py
        ```
        **Note**: The program will continuously listen on port `12345`. Once a client connects, it can send display information and receive button statuses.
    2.  **Test UI**: In a new terminal window, run `sockettest.py` to test the UI:
        ```shell
        python sockettest.py
        ```
        **Effect**: After running `sockettest.py`, clicking the button will cause the LCD display content to change randomly.

-----

**Note: Currently, only the official full version system is supported.**