import json
import threading


class EventBroadcaster:
    def __init__(self):
        self._global_subscribers = set()
        self._app_subscribers: dict[str, set] = {}
        self._lock = threading.Lock()

    def add(self, conn, app_id: str | None):
        with self._lock:
            if app_id:
                self._app_subscribers.setdefault(app_id, set()).add(conn)
            else:
                self._global_subscribers.add(conn)

    def remove(self, conn):
        with self._lock:
            self._global_subscribers.discard(conn)
            for subscribers in self._app_subscribers.values():
                subscribers.discard(conn)

    def has_app_subscribers(self, app_id: str) -> bool:
        with self._lock:
            return bool(self._app_subscribers.get(app_id))

    def broadcast(
        self,
        event: str,
        payload: dict | None = None,
        app_id: str | None = None,
    ):
        message = {"event": event}
        if payload:
            message["payload"] = payload
        wire = (json.dumps(message) + "\n").encode("utf-8")
        with self._lock:
            targets = list(self._global_subscribers)
            if app_id:
                targets.extend(list(self._app_subscribers.get(app_id, set())))
        for conn in targets:
            try:
                conn.sendall(wire)
            except Exception:
                self.remove(conn)
                try:
                    conn.close()
                except Exception:
                    pass
