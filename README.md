# PiSugar Echoview 扩展板驱动程序 / PiSugar Echoview Expansion Board Driver

## 中文

本项目包含 PiSugar Echoview 扩展板的驱动程序。

### 驱动程序目录

驱动程序文件位于 `Driver` 目录下。

### 使用方法

请参考 `example` 目录下的 `test.py` 程序以了解如何使用驱动程序。

### 音频说明

**注意：** 目前代码不包含音频部分的驱动。PiSugar Echoview 扩展板上的音频功能通过 WM8960 音频编解码器实现。在正确安装 WM8960 的驱动程序后，音频设备将直接作为系统的标准声卡使用，您可以使用标准的音频播放和录制接口进行调用，无需额外的代码集成到本项目驱动中。

---

## English

This project contains the driver for the PiSugar Echoview expansion board.

### Driver Directory

The driver files are located in the `Driver` directory.

### Usage

Please refer to the `test.py` program in the `example` directory for instructions on how to use the driver.

### Audio Information

**Note:** The current code does not include the driver for the audio part. The audio functionality on the PiSugar Echoview expansion board is implemented using the WM8960 audio codec. After correctly installing the WM8960 driver, the audio device will be directly available as a standard system sound card. You can use standard audio playback and recording interfaces to call it without needing to integrate additional code into this project's driver.