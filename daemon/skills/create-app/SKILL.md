---
name: create-whisplay-app
description: >-
  Create a Whisplay app and integrate it with the daemon. Use when the user
  wants to build a new app for the Whisplay hardware (240x280 LCD, button,
  RGB LED), register it with the daemon, or understand the daemon IPC protocol.
---

# Creating a Whisplay App

## Architecture Overview

Whisplay daemon manages app lifecycle on a 240×280 RGB565 LCD with a single button and an RGB LED. Apps communicate with the daemon over a Unix domain socket at `/tmp/whisplay-daemon.sock` using newline-delimited JSON.

The runtime SDK (`runtime/whisplay_client.py`) provides `WhisplayDaemonProxy` which handles socket communication, framebuffer rendering, and event subscription automatically.

## Quick Start

### 1. Create the app script

```python
import os
import sys
import time
import threading

# Add runtime to path
current_dir = os.path.dirname(os.path.abspath(__file__))
runtime_dir = os.path.abspath(os.path.join(current_dir, "..", "runtime"))
if runtime_dir not in sys.path:
    sys.path.append(runtime_dir)

from whisplay_client import create_whisplay_hardware

SCREEN_WIDTH = 240
SCREEN_HEIGHT = 280


class MyApp:
    def __init__(self):
        self.board = create_whisplay_hardware(
            app_id="my-app-id",
            display_name="My App",
            icon="M",
            exit_gesture="quad_click",
            priority=10,
            use_daemon_default_log=True,
        )
        self.running = True
        self.board.on_button_press(self._on_press)
        self.board.on_button_release(self._on_release)
        if hasattr(self.board, "on_exit_request"):
            self.board.on_exit_request(self._on_exit)
        if hasattr(self.board, "on_focus_revoked"):
            self.board.on_focus_revoked(self._on_revoked)

    def _on_press(self):
        pass

    def _on_release(self):
        pass

    def _on_exit(self):
        self.running = False

    def _on_revoked(self, _payload=None):
        self.running = False

    def run(self):
        try:
            while self.running:
                self._render()
                time.sleep(1.0 / 20)
        finally:
            self.board.cleanup()

    def _render(self):
        # Write RGB565 pixels to framebuffer
        self.board.fill_screen(0x0000)  # black


if __name__ == "__main__":
    MyApp().run()
```

### 2. Create the app config JSON

Place in `~/.whisplay-daemon/app/<app-id>.json`:

```json
{
  "app_id": "my-app-id",
  "display_name": "My App",
  "icon": "M",
  "launch_command": "python3 /path/to/my_app.py",
  "cwd": "/path/to/app/dir",
  "env": {
    "WHISPLAY_APP_ID": "my-app-id"
  },
  "exit_gesture": "quad_click",
  "priority": 10,
  "use_daemon_default_log": true,
  "persist": true
}
```

Or use `persist=True` in `create_whisplay_hardware()` to auto-register on first launch.

## App Config Fields

| Field | Type | Description |
|-------|------|-------------|
| `app_id` | string | Unique identifier (required) |
| `display_name` | string | Name shown on desktop |
| `icon` | string | 1-2 char icon for desktop list |
| `launch_command` | string | Shell command to start the app |
| `cwd` | string | Working directory for launch |
| `env` | object | Extra environment variables |
| `exit_gesture` | string | `"quad_click"` (4 clicks in 3s) or `"long_press"` (0.7s hold) |
| `priority` | int | Desktop sort order (higher = top) |
| `use_daemon_default_log` | bool | Redirect stdout to daemon log |
| `persist` | bool | Save config to disk |
| `disable_esc_exit_key` | bool | Prevent ESC key from triggering exit |

## `create_whisplay_hardware()` API

Returns a `WhisplayDaemonProxy` if daemon is running, otherwise falls back to direct `WhisplayBoard` hardware access.

```python
board = create_whisplay_hardware(
    app_id="my-app",
    display_name="My App",
    icon="M",
    launch_command="python3 my_app.py",  # optional, for re-launch
    launch_cwd="/path/to/dir",           # optional
    persist=True,
    exit_gesture="quad_click",
    priority=10,
    use_daemon_default_log=True,
)
```

## WhisplayDaemonProxy Methods

| Method | Description |
|--------|-------------|
| `fill_screen(color)` | Fill entire screen with RGB565 color value |
| `draw_image(x, y, w, h, data)` | Write RGB565 pixel bytes to framebuffer region |
| `set_backlight(brightness)` | Set LCD backlight (0-100) |
| `set_rgb(r, g, b)` | Set LED color (0-255 each) |
| `set_rgb_fade(r, g, b, ms)` | Fade LED to color over duration |
| `button_pressed()` | Returns current button state |
| `on_button_press(cb)` | Register press callback |
| `on_button_release(cb)` | Register release callback |
| `on_exit_request(cb)` | Called when user triggers exit gesture |
| `on_focus_revoked(cb)` | Called when daemon forcibly takes back focus |
| `release_focus()` | Voluntarily release foreground |
| `cleanup()` | Release resources and disconnect |

## Screen & Pixel Format

- Resolution: 240×280
- Format: RGB565 big-endian (2 bytes per pixel)
- Framebuffer size: 240 × 280 × 2 = 134,400 bytes
- Stride: 480 bytes per row

RGB565 conversion:

```python
def to_rgb565(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
```

For PIL Image rendering:

```python
from PIL import Image, ImageDraw
from daemon_shared import image_to_rgb565_bytes

img = Image.new("RGB", (240, 280), (0, 0, 0))
draw = ImageDraw.Draw(img)
draw.text((10, 10), "Hello", fill=(255, 255, 255))
frame = image_to_rgb565_bytes(img)
board.draw_image(0, 0, 240, 280, frame)
```

## Daemon IPC Protocol

All communication is newline-delimited JSON over Unix socket `/tmp/whisplay-daemon.sock`.

### Request format

```json
{"version": 1, "cmd": "<command>", "payload": {}}
```

### Key commands

| Command | Purpose |
|---------|---------|
| `health.ping` | Check daemon status, get screen info |
| `app.register` | Register/update app entry |
| `app.list` | List all registered apps |
| `app.launch` | Launch an app by id |
| `app.focus.acquire` | Claim foreground + framebuffer |
| `framebuffer.acquire` | Get framebuffer file path |
| `app.focus.release` | Release foreground voluntarily |
| `app.exit.request` | Request another app to exit |
| `backlight.set` | `{"brightness": 0-100}` |
| `led.set` | `{"r": 0-255, "g": 0-255, "b": 0-255}` |
| `led.fade` | `{"r", "g", "b", "duration_ms"}` |
| `events.subscribe` | Subscribe to real-time events (keeps connection open) |

### Event stream

After `events.subscribe`, daemon pushes events as JSON lines:

| Event | Payload | Trigger |
|-------|---------|---------|
| `button_pressed` | `{app_id}` | Physical button down |
| `button_released` | `{app_id}` | Physical button up |
| `app_exit_requested` | `{app_id, reason}` | Exit gesture detected |
| `app_focus_revoked` | `{app_id, reason}` | Daemon forcibly revokes focus |
| `app_foreground_acquired` | `{app_id, session_token}` | App got foreground |
| `desktop_entered` | `{reason}` | Returned to desktop |

## App Lifecycle

1. Daemon starts → loads JSON configs from `~/.whisplay-daemon/app/`
2. User selects app on desktop (button cycle + long press, or keyboard)
3. Daemon runs `launch_command` as subprocess
4. App calls `app.focus.acquire` → gets `session_token`
5. App calls `framebuffer.acquire` → gets shared memory file path
6. App writes RGB565 frames to shared memory; daemon renders at 20 FPS
7. Exit gesture triggers `app_exit_requested` event
8. App calls `app.focus.release` or exits → desktop resumes

## Best Practices

- Always register `on_exit_request` and `on_focus_revoked` callbacks
- Call `board.cleanup()` in a `finally` block
- Use `use_daemon_default_log=True` for easier debugging
- Keep render loop at ≤20 FPS to match daemon refresh rate
- Use PIL for complex graphics, convert to RGB565 before writing
- Set `priority` to control desktop ordering (higher = appears first)
- Use `env.WHISPLAY_APP_ID` so the app can read its own ID from environment
