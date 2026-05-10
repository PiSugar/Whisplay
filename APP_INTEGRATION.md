# Third-Party App Integration Guide

This guide explains how a third-party app can integrate with the `whisplay-daemon` service and run as a foreground app on Whisplay hardware.

## Overview

`whisplay-daemon` is the hardware owner and session manager. It keeps exclusive control of:

- LCD hardware
- RGB LED
- backlight
- physical button
- app lifecycle and foreground switching

Your app does **not** access GPIO or SPI directly when running under daemon mode.

Instead, your app should:

1. register itself with the daemon
2. subscribe to daemon events
3. acquire foreground focus
4. acquire the shared framebuffer handle
5. `mmap` the framebuffer and draw directly into it
6. release focus when exiting

For Python apps, the helper client now lives at `runtime/whisplay_client.py`.

## Repo Entry Points

In the current repository layout:

- hardware helper: `runtime/whisplay.py`
- daemon app helper: `runtime/whisplay_client.py`
- daemon runtime: `daemon/whisplay_daemon.py`
- daemon service installer: `daemon/install_whisplay_daemon_service.sh`
- platform driver installer (auto-detect): `install_driver.sh`

## Runtime Model

The daemon has two modes:

- Desktop mode:
  - single click cycles app selection
  - long press launches or foregrounds the selected app
- Foreground app mode:
  - normal button press/release events are forwarded to the foreground app
  - by default, 4 rapid clicks are reserved globally and trigger app exit request
  - apps can explicitly declare `exit_gesture: "long_press"` to use long press instead

When the configured exit gesture is detected, the daemon sends `app_exit_requested` to the foreground app. The app should stop work, release focus, and exit quickly.

## IPC Basics

- Transport: Unix domain socket
- Default path: `/tmp/whisplay-daemon.sock`
- Protocol: line-delimited JSON
- Version: `1`

Each request must use this shape:

```json
{
  "version": 1,
  "cmd": "health.ping",
  "payload": {}
}
```

Each response is one JSON object per line:

```json
{
  "ok": true,
  "payload": {}
}
```

## Core Commands

### `app.register`

Register the app in the daemon registry.

Payload:

```json
{
  "app_id": "my-app",
  "display_name": "My App",
  "icon": "MA",
  "launch_command": "python3 /path/to/my_app.py",
  "cwd": "/path/to",
  "env": {
    "MY_FLAG": "1"
  },
  "exit_gesture": "quad_click",
  "priority": 50,
  "use_daemon_default_log": true,
  "persist": true
}
```

Notes:

- `app_id` must be stable and unique.
- `launch_command` is what the daemon uses when the user launches the app from desktop.
- `persist: true` stores the app as a JSON file in `~/.whisplay-daemon/app/` for future boots.
- `exit_gesture` is optional. Valid values are `quad_click` and `long_press`. Default is `quad_click`.
- `priority` is optional. Higher values appear earlier on the desktop. Default is `0`.
- `use_daemon_default_log` is optional. When `true`, the app's stdout/stderr are appended to `~/.whisplay-daemon/daemon-app.log`.
- The daemon does not inject built-in apps at runtime. The install script seeds the default example app JSON files into `~/.whisplay-daemon/app/`.

### `app.list`

Returns registered apps, running status, selected state, and foreground state.

### `app.launch`

Asks the daemon to launch an app by `app_id`.

### `app.focus.acquire`

Foreground entry point for the running app.

Payload:

```json
{
  "app_id": "my-app"
}
```

Returns:

```json
{
  "ok": true,
  "payload": {
    "app_id": "my-app",
    "session_token": "..."
  }
}
```

### `framebuffer.acquire`

After focus is granted, the app requests framebuffer metadata.

Payload:

```json
{
  "app_id": "my-app",
  "session_token": "..."
}
```

Returns:

```json
{
  "ok": true,
  "payload": {
    "app_id": "my-app",
    "session_token": "...",
    "width": 240,
    "height": 280,
    "stride": 480,
    "pixel_format": "RGB565",
    "buffer_handle": "/tmp/whisplay-fb-my-app-....bin"
  }
}
```

### `app.focus.release`

Release foreground ownership and return the screen to daemon desktop.

Payload:

```json
{
  "app_id": "my-app",
  "session_token": "..."
}
```

### `events.subscribe`

Subscribe to event stream.

Payload for app-specific subscription:

```json
{
  "app_id": "my-app"
}
```

## Event Model

Your app should handle at least these events:

- `button_pressed`
- `button_released`
- `app_foreground_acquired`
- `app_exit_requested`
- `app_focus_revoked`

Recommended behavior:

- `button_pressed` / `button_released`: run normal app interaction
- `app_exit_requested`: stop audio, camera, background work, save state if needed, release focus, exit
- `app_focus_revoked`: stop drawing immediately and consider the framebuffer invalid

## Framebuffer Contract

V1 framebuffer layout:

- pixel format: `RGB565`
- width: `240`
- height: `280`
- stride: `width * 2` bytes

The app writes directly into the shared buffer. The daemon reads and flushes it to the LCD.

Important rules:

- Do not assume the framebuffer remains valid after `app_focus_revoked`.
- Do not keep drawing after focus release.
- Treat `buffer_handle` as session-scoped, not permanent.

## Minimal Python Example

```python
import json
import mmap
import socket

SOCKET_PATH = "/tmp/whisplay-daemon.sock"


def request(cmd, payload=None):
    body = {"version": 1, "cmd": cmd, "payload": payload or {}}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(SOCKET_PATH)
        client.sendall((json.dumps(body) + "\n").encode("utf-8"))
        line = client.makefile("r").readline().strip()
        return json.loads(line)


request("app.register", {
    "app_id": "demo-app",
    "display_name": "Demo App",
    "icon": "DM",
    "launch_command": "python3 /path/to/demo_app.py",
    "persist": True,
})

focus = request("app.focus.acquire", {"app_id": "demo-app"})
token = focus["payload"]["session_token"]

fb = request("framebuffer.acquire", {
    "app_id": "demo-app",
    "session_token": token,
})["payload"]

color = bytes([0xF8, 0x00])  # red in RGB565

with open(fb["buffer_handle"], "r+b") as fp:
    with mmap.mmap(fp.fileno(), 0) as buf:
        buf[:] = color * (fb["width"] * fb["height"])

request("app.focus.release", {
    "app_id": "demo-app",
    "session_token": token,
})
```

## Integration Checklist

- Register app with stable `app_id`
- Provide correct `launch_command`
- Subscribe to app events
- Acquire focus before acquiring framebuffer
- Map framebuffer and write RGB565 pixels directly
- Release focus on normal exit
- Handle `app_exit_requested` quickly
- Stop drawing after `app_focus_revoked`

## Testing Recommendations

- Verify app appears on daemon desktop
- Verify long press launches app
- Verify framebuffer writes show up on screen
- Verify button events reach app while foregrounded
- Verify 4-click exit returns to desktop
- Verify app handles daemon revoke safely

## Notes for App Authors

- If your app already has its own rendering stack, add a backend that writes RGB565 into the mapped buffer.
- Keep redraw logic deterministic; the daemon is continuously sampling the shared framebuffer.
- Do not rely on direct hardware access in daemon mode.
