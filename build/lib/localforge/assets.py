"""Offline, hash-pinned bundled Adminer deployment."""

import hashlib
from importlib.resources import files

from .paths import atomic_write

ADMINER_VERSION = "6.1.2"
ADMINER_SHA256 = "7526b56dc5ecee5b5cc88206ac8f3a757392c1b991838efa71bf9f7abbb8172e"


def ensure_adminer(paths):
    payload = files("localforge").joinpath("assets/adminer.php").read_bytes()
    if hashlib.sha256(payload).hexdigest() != ADMINER_SHA256:
        raise ValueError("Bundled Adminer checksum mismatch; reinstall LocalForge")
    destination = paths.data / "welcome/adminer.php"
    if (
        destination.is_symlink()
        or not destination.exists()
        or destination.read_bytes() != payload
    ):
        atomic_write(destination, payload.decode("utf-8"))
    return destination
