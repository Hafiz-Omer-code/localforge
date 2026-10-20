import json
import os
import socket
import subprocess
import sys
import time

from .paths import Paths


def call(op, **kwargs):
    path = Paths().socket
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(180)
        sock.connect(str(path))
        sock.sendall((json.dumps({"op": op, **kwargs}) + "\n").encode())
        chunks, size = [], 0
        while True:
            piece = sock.recv(65536)
            if not piece:
                break
            chunks.append(piece)
            size += len(piece)
            if size > 4 * 1024 * 1024:
                raise RuntimeError("Controller response exceeded limit")
            if b"\n" in piece:
                break
        reply = json.loads(b"".join(chunks))
        if not reply["ok"]:
            raise RuntimeError(reply["error"])
        return reply["result"]


def ensure_daemon():
    if os.geteuid() == 0:
        raise RuntimeError("LocalForge must run as your normal desktop user, not root")
    p = Paths()
    p.ensure()
    try:
        call("snapshot")
        return
    except (OSError, ValueError):
        pass
    # Prefer the installed user unit. If systemd is unavailable, use standalone mode.
    try:
        started = (
            subprocess.run(
                ["systemctl", "--user", "start", "localforge.service"],
                capture_output=True,
                timeout=10,
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        started = False
    if not started:
        with (p.logs / "controller.log").open("ab") as log:
            subprocess.Popen(
                [sys.executable, "-m", "localforge.daemon"],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    for _ in range(60):
        try:
            call("snapshot")
            return
        except (OSError, ValueError):
            time.sleep(0.1)
    raise RuntimeError(
        f"Controller did not start; inspect {p.logs / 'controller.log'} or journalctl --user -u localforge"
    )
