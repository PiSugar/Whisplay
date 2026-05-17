import mmap
import subprocess
from dataclasses import dataclass, field

from daemon_shared import EXIT_GESTURE_QUAD_CLICK


@dataclass
class AppRecord:
    app_id: str
    display_name: str
    icon: str = ""
    launch_command: str = ""
    cwd: str = ""
    env: dict = field(default_factory=dict)
    exit_gesture: str = EXIT_GESTURE_QUAD_CLICK
    priority: int = 0
    use_daemon_default_log: bool = False
    persist: bool = False
    disable_esc_exit_key: bool = False
    process: subprocess.Popen | None = None
    process_log_handle = None
    subscribers: set = field(default_factory=set)
    session_token: str | None = None
    framebuffer_path: str | None = None
    framebuffer_file = None
    framebuffer_mmap: mmap.mmap | None = None

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None
