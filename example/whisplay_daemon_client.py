import argparse
import json
import mmap
import os
import socket


DEFAULT_SOCKET_PATH = os.getenv("WHISPLAY_DAEMON_SOCKET_PATH", "/tmp/whisplay-daemon.sock")


def request(cmd: str, payload: dict | None = None, socket_path: str = DEFAULT_SOCKET_PATH):
    body = {"version": 1, "cmd": cmd, "payload": payload or {}}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(socket_path)
        client.sendall((json.dumps(body) + "\n").encode("utf-8"))
        line = client.makefile("r").readline().strip()
        print(line)
        return json.loads(line)


def main():
    parser = argparse.ArgumentParser(description="Whisplay daemon test client")
    parser.add_argument("--socket", default=DEFAULT_SOCKET_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("ping")
    subparsers.add_parser("button")
    subparsers.add_parser("apps")

    register = subparsers.add_parser("register")
    register.add_argument("app_id")
    register.add_argument("display_name")
    register.add_argument("--launch-command", default="")
    register.add_argument("--cwd", default="")

    launch = subparsers.add_parser("launch")
    launch.add_argument("app_id")

    led = subparsers.add_parser("led")
    led.add_argument("r", type=int)
    led.add_argument("g", type=int)
    led.add_argument("b", type=int)
    led.add_argument("--fade", action="store_true")
    led.add_argument("--duration-ms", type=int, default=300)

    backlight = subparsers.add_parser("backlight")
    backlight.add_argument("brightness", type=int)

    foreground = subparsers.add_parser("foreground")
    foreground.add_argument("app_id")
    foreground.add_argument("--color", default="f800", help="RGB565 hex color, e.g. f800")

    subscribe = subparsers.add_parser("subscribe")
    subscribe.add_argument("--app-id", default="")

    args = parser.parse_args()
    if args.command == "ping":
        request("health.ping", socket_path=args.socket)
        return
    if args.command == "button":
        request("button.get_state", socket_path=args.socket)
        return
    if args.command == "apps":
        request("app.list", socket_path=args.socket)
        return
    if args.command == "register":
        request(
            "app.register",
            {
                "app_id": args.app_id,
                "display_name": args.display_name,
                "launch_command": args.launch_command,
                "cwd": args.cwd,
                "persist": True,
            },
            socket_path=args.socket,
        )
        return
    if args.command == "launch":
        request("app.launch", {"app_id": args.app_id}, socket_path=args.socket)
        return
    if args.command == "led":
        request(
            "led.fade" if args.fade else "led.set",
            {"r": args.r, "g": args.g, "b": args.b, "duration_ms": args.duration_ms},
            socket_path=args.socket,
        )
        return
    if args.command == "backlight":
        request("backlight.set", {"brightness": args.brightness}, socket_path=args.socket)
        return
    if args.command == "foreground":
        focus = request("app.focus.acquire", {"app_id": args.app_id}, socket_path=args.socket)
        token = focus["payload"]["session_token"]
        fb = request(
            "framebuffer.acquire",
            {"app_id": args.app_id, "session_token": token},
            socket_path=args.socket,
        )["payload"]
        color = int(args.color, 16)
        high = (color >> 8) & 0xFF
        low = color & 0xFF
        with open(fb["buffer_handle"], "r+b") as fp:
            with mmap.mmap(fp.fileno(), 0) as mapped:
                mapped[:] = bytes([high, low]) * (fb["width"] * fb["height"])
        return
    if args.command == "subscribe":
        body = {"version": 1, "cmd": "events.subscribe", "payload": {"app_id": args.app_id or None}}
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(args.socket)
            client.sendall((json.dumps(body) + "\n").encode("utf-8"))
            reader = client.makefile("r")
            print(reader.readline().strip())
            while True:
                line = reader.readline()
                if not line:
                    break
                print(line.strip())


if __name__ == "__main__":
    main()
