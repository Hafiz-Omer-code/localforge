"""Seed only matching system-runtime database extensions, without system ini reuse."""

import json
import os
import subprocess
from pathlib import Path

from .config import executable
from .paths import atomic_write


def seed_system_database_extensions(paths, config):
    runtime = config["php_versions"].get("system")
    if not runtime:
        return
    directory = paths.config / "php/system/conf.d"
    marker = directory / ".database-seeded"
    destination = directory / "20-localforge-database.ini"
    if marker.exists():
        return
    if destination.exists() or destination.is_symlink():
        # Respect an existing user-created file, without reading it as Python.
        atomic_write(marker, "User-managed extension configuration preserved.\n")
        return
    env = os.environ.copy()
    env["PHP_INI_SCAN_DIR"] = ""
    try:
        result = subprocess.run(
            [
                executable(runtime["cli"]),
                "-n",
                "-r",
                'echo json_encode(["directory"=>ini_get("extension_dir"),"loaded"=>get_loaded_extensions()]);',
            ],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
        )
        if result.returncode:
            return
        info = json.loads(result.stdout)
        root = Path(info["directory"])
        if not root.is_absolute():
            return
        loaded = {name.lower() for name in info["loaded"]}
        lines = ["; One-time LocalForge system-runtime database extension defaults."]
        # mysqlnd before consumers; PDO before pdo_mysql. No shared ABI across labels.
        for name in ("mysqlnd", "pdo", "mysqli", "pdo_mysql", "session"):
            file = root / f"{name}.so"
            if name not in loaded and file.is_file():
                quoted = str(file).replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'extension="{quoted}"')
        atomic_write(destination, "\n".join(lines) + "\n")
        atomic_write(marker, "Database extensions seeded; further edits are yours.\n")
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        # Missing drivers must not prevent other development services from starting.
        return
