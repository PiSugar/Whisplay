# 第三方 App 接入指南

本文说明第三方 app 如何接入 `whisplay-daemon`，并作为 Whisplay 硬件上的前台应用运行。

## 概览

`whisplay-daemon` 是硬件拥有者和会话管理器，独占以下资源：

- LCD 屏幕
- RGB LED
- 背光
- 物理按键
- app 生命周期与前台切换

在 daemon 模式下，你的 app **不应** 直接访问 GPIO 或 SPI。

正确接入方式是：

1. 向 daemon 注册 app
2. 订阅 daemon 事件
3. 获取前台焦点
4. 获取共享 framebuffer 句柄
5. 使用 `mmap` 映射 framebuffer，并直接写像素
6. 退出时释放前台焦点

对于 Python app，推荐直接复用 `runtime/whisplay_client.py` 这个 helper。

## 仓库入口路径

当前仓库结构下，建议按下面路径接入：

- 硬件 helper：`runtime/whisplay.py`
- daemon 模式 app helper：`runtime/whisplay_client.py`
- daemon 运行脚本：`daemon/whisplay_daemon.py`
- daemon 服务安装脚本：`daemon/install_whisplay_daemon_service.sh`
- 平台驱动安装入口（自动识别）：`install_driver.sh`

## 运行模型

daemon 有两种状态：

- 桌面模式：
  - 单击切换当前选中的 app
  - 长按启动或切换到该 app 前台
- 前台 app 模式：
  - 普通按下/松开事件会转发给前台 app
  - 默认情况下，快速按 4 下是全局保留手势，用于请求退出当前 app
  - app 也可以显式声明 `exit_gesture: "long_press"`，改为长按退出

当 daemon 检测到当前 app 配置的退出手势后，会向前台 app 发送 `app_exit_requested`。app 应尽快停止工作、释放前台并退出。

## IPC 基础

- 传输方式：Unix domain socket
- 默认路径：`/tmp/whisplay-daemon.sock`
- 协议：按行分隔的 JSON
- 版本：`1`

每个请求都使用以下格式：

```json
{
  "version": 1,
  "cmd": "health.ping",
  "payload": {}
}
```

响应为每行一个 JSON 对象：

```json
{
  "ok": true,
  "payload": {}
}
```

## 核心命令

### `app.register`

将 app 注册到 daemon 的 app 列表中。

Payload：

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

说明：

- `app_id` 必须稳定且唯一
- `launch_command` 是桌面启动该 app 时 daemon 实际执行的命令
- `persist: true` 会将该 app 以单独 JSON 文件形式持久化保存到 `~/.whisplay-daemon/app/`
- `exit_gesture` 是可选项，可取 `quad_click` 或 `long_press`，默认值为 `quad_click`
- `priority` 是可选项，值越大在桌面中排得越靠前，默认值为 `0`
- `use_daemon_default_log` 是可选项。为 `true` 时，app 的 stdout/stderr 会追加写入 `~/.whisplay-daemon/daemon-app.log`
- daemon 运行时不会再注入内置 app。默认示例 app 的 JSON 文件由安装脚本同步到 `~/.whisplay-daemon/app/`

### `app.list`

返回已注册 app 列表、运行状态、桌面选中状态和前台状态。

### `app.launch`

通过 `app_id` 请求 daemon 启动一个 app。

### `app.focus.acquire`

运行中的 app 请求进入前台。

Payload：

```json
{
  "app_id": "my-app"
}
```

返回：

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

前台焦点拿到之后，app 再请求 framebuffer 元数据。

Payload：

```json
{
  "app_id": "my-app",
  "session_token": "..."
}
```

返回：

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

释放前台 ownership，并将屏幕归还给 daemon 桌面。

Payload：

```json
{
  "app_id": "my-app",
  "session_token": "..."
}
```

### `events.subscribe`

订阅事件流。

app 级订阅的 payload：

```json
{
  "app_id": "my-app"
}
```

## 事件模型

第三方 app 至少应处理这些事件：

- `button_pressed`
- `button_released`
- `app_foreground_acquired`
- `app_exit_requested`
- `app_focus_revoked`

推荐行为：

- `button_pressed` / `button_released`：执行正常交互逻辑
- `app_exit_requested`：停止音频、相机、后台任务，必要时保存状态，然后释放前台并退出
- `app_focus_revoked`：立即停止绘制，并视 framebuffer 为失效

## Framebuffer 约定

V1 的 framebuffer 布局如下：

- 像素格式：`RGB565`
- 宽度：`240`
- 高度：`280`
- stride：`width * 2` 字节

app 直接向共享 buffer 写像素，daemon 负责将其刷到物理 LCD。

重要约束：

- 收到 `app_focus_revoked` 后，不要继续使用该 framebuffer
- 释放前台后，不能继续写屏
- `buffer_handle` 是会话级资源，不是永久路径

## 最小 Python 示例

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

color = bytes([0xF8, 0x00])  # RGB565 红色

with open(fb["buffer_handle"], "r+b") as fp:
    with mmap.mmap(fp.fileno(), 0) as buf:
        buf[:] = color * (fb["width"] * fb["height"])

request("app.focus.release", {
    "app_id": "demo-app",
    "session_token": token,
})
```

## 接入检查清单

- 使用稳定的 `app_id` 注册 app
- 提供正确的 `launch_command`
- 订阅 app 事件
- 先获取前台焦点，再获取 framebuffer
- 使用 RGB565 直接写入共享 buffer
- 正常退出时释放前台
- 收到 `app_exit_requested` 后快速退出
- 收到 `app_focus_revoked` 后停止写屏

## 测试建议

- 确认 app 会出现在 daemon 桌面中
- 确认长按可以启动 app
- 确认 framebuffer 写入可以显示到屏幕
- 确认前台状态下按钮事件能到达 app
- 确认 4 连击退出后能返回桌面
- 确认 daemon 收回前台后 app 不会继续错误写屏

## 给 App 作者的建议

- 如果你的 app 已有自己的渲染栈，建议增加一个输出后端，将结果转换为 RGB565 后写入映射 buffer
- 绘制逻辑尽量保持稳定和确定性，因为 daemon 会持续读取共享 framebuffer
- 在 daemon 模式下，不要依赖直接操作硬件
