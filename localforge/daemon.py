"""User-only Unix-socket service controller. No TCP management endpoint."""

import fcntl
import json
import os
import signal
import socket
import socketserver
import struct
import threading
import time

from .engine import Engine
from .paths import Paths

MAX_REQUEST = 1024 * 1024


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    block_on_close = False
    last_scan = 0

    def service_actions(self):
        if time.monotonic() - self.last_scan >= 5:
            self.last_scan = time.monotonic()
            # Do not delay a lifecycle operation just to poll project directories.
            if self.engine_lock.acquire(blocking=False):
                try:
                    self.engine.auto_scan()
                finally:
                    self.engine_lock.release()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(180)
        # Filesystem permissions AND Linux kernel peer credentials.
        _, uid, _ = struct.unpack(
            "3i", self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        if uid != os.getuid():
            return
        shutdown = False
        try:
            line = self.rfile.readline(MAX_REQUEST + 1)
            if len(line) > MAX_REQUEST or not line.endswith(b"\n"):
                raise ValueError("Request too large or incomplete")
            request = json.loads(line)
            with self.server.engine_lock:
                if request.get("op") == "shutdown":
                    result = self.server.engine.stop_all()
                    shutdown = True
                else:
                    result = self.server.engine.dispatch(request)
            reply = {"ok": True, "result": result}
        except Exception as exc:
            reply = {"ok": False, "error": str(exc)}
        try:
            self.wfile.write((json.dumps(reply) + "\n").encode())
        except (BrokenPipeError, ConnectionResetError):
            pass
        if shutdown:
            threading.Thread(target=self.server.shutdown, daemon=True).start()


def main():
    if os.geteuid() == 0:
        raise SystemExit("Do not run the LocalForge controller as root.")
    os.umask(0o077)
    paths = Paths()
    paths.ensure()
    lock = (paths.runtime / "manager.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("LocalForge controller is already running")
    paths.socket.unlink(missing_ok=True)
    engine = Engine(paths)
    with Server(str(paths.socket), Handler) as server:
        paths.socket.chmod(0o600)
        server.engine = engine
        server.engine_lock = threading.RLock()

        def stop(signum, frame):
            threading.Thread(target=server.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            server.serve_forever(poll_interval=0.3)
        finally:
            with server.engine_lock:
                engine.stop_all()
            paths.socket.unlink(missing_ok=True)
            lock.close()


if __name__ == "__main__":
    main()
