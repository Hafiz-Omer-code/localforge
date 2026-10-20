"""Bounded .php-version selection and native per-version FPM supervision."""

import os
import signal
import socket
import stat
import subprocess
import time
from pathlib import Path

from .config import executable


def project_override(project, config):
    """Only a small, regular project-root file may select a registered label."""
    path = Path(project["path"]) / ".php-version"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None
            data = stream.read(257)
        if len(data) > 256:
            return None
        label = data.decode("utf-8").strip()
    except (OSError, UnicodeError):
        return None
    return label if label in config["php_versions"] else None


def required_overrides(config):
    return sorted(
        {label for p in config["projects"] if (label := project_override(p, config))}
    )


def selection_fingerprint(project):
    path = Path(project["path"]) / ".php-version"
    try:
        info = path.lstat()
        return (
            str(path),
            info.st_ino,
            info.st_mtime_ns,
            info.st_ctime_ns,
            info.st_size,
        )
    except OSError:
        return (str(path), None)


class FpmPools:
    def __init__(self, paths, config):
        self.paths, self.config = paths, config
        self.children, self.errors = {}, {}

    def running(self, label):
        child = self.children.get(label)
        return bool(child and child.poll() is None)

    def any_running(self):
        return any(self.running(label) for label in self.children)

    def command(self, label, check=False):
        from .render import render_fpm

        c = self.config.value
        if label not in c["php_versions"]:
            raise ValueError(f"Unregistered PHP override: {label}")
        ini = render_fpm(self.paths, label, versioned=True)
        return [
            executable(c["php_versions"][label]["fpm"]),
            "-c",
            str(ini),
            "-y",
            str(self.paths.generated / f"php-fpm-{label}.conf"),
            "-t" if check else "-F",
        ]

    def env(self, label):
        env = os.environ.copy()
        env["PHP_INI_SCAN_DIR"] = str(self.paths.config / "php" / label / "conf.d")
        env["PATH"] = str(self.paths.data / "bin") + os.pathsep + env.get("PATH", "")
        return env

    def check(self, label):
        result = subprocess.run(
            self.command(label, check=True),
            env=self.env(label),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode:
            raise ValueError(
                f"PHP override {label}: " + (result.stderr + result.stdout)[-6000:]
            )
        return result.stderr + result.stdout

    def start(self, label):
        if self.running(label):
            return
        path = self.paths.php_version_socket(label)
        if path.is_symlink() or (
            path.exists() and not stat.S_ISSOCK(path.lstat().st_mode)
        ):
            raise ValueError(f"Refusing unexpected PHP socket path: {path}")
        if path.exists():
            with socket.socket(socket.AF_UNIX) as probe:
                probe.settimeout(0.3)
                try:
                    probe.connect(str(path))
                except (ConnectionRefusedError, FileNotFoundError):
                    path.unlink(missing_ok=True)
                else:
                    raise ValueError(f"Unowned active PHP socket: {path}")
        self.check(label)
        logpath = self.paths.logs / f"php-{label}.log"
        with logpath.open("ab", buffering=0) as log:
            proc = subprocess.Popen(
                self.command(label),
                cwd=self.paths.data,
                env=self.env(label),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self.children[label] = proc
        self.errors.pop(label, None)
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise ValueError(
                        f"PHP override {label} exited {proc.returncode}; inspect {logpath}"
                    )
                with socket.socket(socket.AF_UNIX) as probe:
                    probe.settimeout(0.2)
                    try:
                        probe.connect(str(path))
                        return
                    except OSError:
                        pass
                time.sleep(0.1)
            raise ValueError(
                f"PHP override {label} startup timed out; inspect {logpath}"
            )
        except Exception as exc:
            self.stop(label)
            self.errors[label] = str(exc)
            raise

    def ensure(self, labels):
        started = []
        try:
            for label in labels:
                if not self.running(label):
                    self.start(label)
                    started.append(label)
        except Exception as exc:
            self.errors[label] = str(exc)
            for label in reversed(started):
                self.stop(label)
            raise
        return started

    def stop(self, label):
        proc = self.children.get(label)
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
            except ProcessLookupError:
                proc.wait(timeout=2)
        self.children.pop(label, None)
        self.errors.pop(label, None)

    def stop_all(self):
        for label in list(self.children):
            self.stop(label)

    def reload_all(self):
        for label in self.children:
            if self.running(label):
                self.check(label)
        for label, child in self.children.items():
            if self.running(label):
                os.kill(child.pid, signal.SIGUSR2)

    def snapshot(self):
        required = required_overrides(self.config.value)
        statuses = []
        for label in sorted(set(required) | set(self.children)):
            child = self.children.get(label)
            running = self.running(label)
            error = self.errors.get(label, "")
            if child and child.poll() not in (None, 0):
                error = f"Exited {child.returncode}"
            statuses.append(
                {
                    "version": label,
                    "running": running,
                    "pid": child.pid if running else None,
                    "socket": str(self.paths.php_version_socket(label)),
                    "required": label in required,
                    "error": error,
                }
            )
        return statuses
