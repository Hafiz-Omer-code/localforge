import os
import re
import stat
from pathlib import Path

# Conservative byte budget for Linux sockaddr_un and the managed engine sockets.
MAX_SOCKET_PATH = 103
SOCKET_NAMES = ("manager.sock", "php.sock", "mysql.sock", "php-" + "x" * 40 + ".sock")


def short_runtime_path():
    # A fixed trusted base, not caller-controlled TMPDIR.
    return Path("/tmp") / f"lf-{os.getuid()}"


def ensure_private_runtime(path):
    """Fail closed on precreated public, foreign-owned or symlink directories."""
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    # O_NOFOLLOW prevents following a precreated symlink; fstat checks the opened
    # inode rather than a separate path lookup. /tmp's sticky bit protects names
    # owned by us against rename/unlink by other ordinary users.
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise PermissionError(
                f"Unsafe fallback runtime directory {path}: it must be owned by "
                "the current user with mode 0700; inspect it manually"
            )
    finally:
        os.close(fd)


class Paths:
    def __init__(self, root=None):
        home = Path.home()
        missing_runtime = root is None and not os.environ.get("XDG_RUNTIME_DIR")
        if root is not None:
            root = Path(root)
            self.config, self.data, self.state, self.runtime = (
                root / x for x in ("config", "data", "state", "run")
            )
        else:
            self.config = (
                Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "localforge"
            )
            self.data = (
                Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
                / "localforge"
            )
            self.state = (
                Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))
                / "localforge"
            )
            self.runtime = (
                Path(os.environ.get("XDG_RUNTIME_DIR", self.state / "run"))
                / "localforge"
            )
        self.logs = self.state / "logs"
        self.generated = self.config / "generated"
        self.settings = self.config / "settings.json"
        # Linux's 108-byte sockaddr_un includes the terminating NUL. Check
        # encoded bytes (not Unicode characters) for every managed socket.
        self.short_runtime = missing_runtime or any(
            len(os.fsencode(self.runtime / name)) > MAX_SOCKET_PATH
            for name in SOCKET_NAMES
        )
        if self.short_runtime:
            self.runtime = short_runtime_path()
            # Validate before a client can connect as well as before daemon use.
            ensure_private_runtime(self.runtime)
        if any(
            len(os.fsencode(self.runtime / name)) > MAX_SOCKET_PATH
            for name in SOCKET_NAMES
        ):
            raise ValueError("Fallback runtime socket path is too long")
        self.socket = self.runtime / "manager.sock"

    @property
    def mysql_socket(self):
        return self.runtime / "mysql.sock"

    @property
    def php_socket(self):
        return self.runtime / "php.sock"

    def php_version_socket(self, label):
        if not isinstance(label, str) or not re.fullmatch(
            r"[A-Za-z0-9._-]{1,40}", label
        ):
            raise ValueError("Invalid PHP runtime label")
        path = self.runtime / f"php-{label}.sock"
        if len(os.fsencode(path)) > MAX_SOCKET_PATH:
            raise ValueError("Versioned PHP socket path is too long")
        return path

    def ensure(self):
        if self.short_runtime:
            ensure_private_runtime(self.runtime)
        for path in (
            self.config,
            self.data,
            self.state,
            self.runtime,
            self.logs,
            self.generated,
            self.data / "runtimes/php",
            self.data / "bin",
        ):
            if path == self.runtime and self.short_runtime:
                continue
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.chmod(0o700)


def atomic_write(path, text, mode=0o600):
    import tempfile

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".localforge-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
