import copy
import json
import os
import re
import shutil
from pathlib import Path

from .paths import atomic_write

SERVICES = ("php", "nginx", "apache", "database", "redis", "node")
DEFAULTS = {
    "schema": 1,
    "web_server": "nginx",
    "projects_root": str(Path.home() / "www"),
    "ports": {"http": 8080, "database": 3306, "redis": 6379, "node": 3000},
    "binaries": {
        "nginx": "nginx",
        "apache": "httpd",
        "database": "mariadbd",
        "redis": "redis-server",
        "node": "node",
    },
    "php_versions": {},
    "active_php": "",
    "projects": [],
    "node": {"cwd": "", "entry": "server.js", "args": []},
    "database_kind": "mariadb",
}
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def find_binary(value):
    return shutil.which(
        value,
        path=os.pathsep.join(
            [
                os.environ.get("PATH", ""),
                "/usr/sbin",
                "/sbin",
                "/usr/libexec",
                "/usr/lib",
            ]
        ),
    )


def executable(value):
    if not isinstance(value, str) or not value or any(c in value for c in "\n\r\0"):
        raise ValueError("Invalid executable")
    found = find_binary(value)
    if not found:
        raise ValueError(f"Executable not found: {value}")
    return str(Path(found).absolute())


def validate(c):
    if c.get("schema") != 1:
        raise ValueError("Unsupported settings schema")
    if c["web_server"] not in ("nginx", "apache"):
        raise ValueError("web_server must be nginx or apache")
    if c["database_kind"] not in ("mariadb", "mysql"):
        raise ValueError("database_kind must be mariadb or mysql")
    ports = list(c["ports"].values())
    if set(c["ports"]) != {"http", "database", "redis", "node"} or any(
        type(p) is not int or not 1024 <= p <= 65535 for p in ports
    ):
        raise ValueError("Use integer unprivileged ports from 1024 to 65535")
    if len(set(ports)) != len(ports):
        raise ValueError("Each service must have a different TCP port")
    for value in [
        c["projects_root"],
        c["node"]["cwd"],
        c["node"]["entry"],
        *c["binaries"].values(),
    ]:
        if not isinstance(value, str) or any(ch in value for ch in "\n\r\0"):
            raise ValueError("Paths must be strings without control characters")
    if not isinstance(c["node"]["args"], list) or not all(
        isinstance(x, str) for x in c["node"]["args"]
    ):
        raise ValueError("Node args must be a list of strings")
    seen = set()
    for p in c["projects"]:
        if not NAME.fullmatch(p["name"]) or p["name"] in seen:
            raise ValueError("Project names must be unique lowercase DNS labels")
        seen.add(p["name"])
        root = Path(p["path"])
        if not root.is_absolute() or any(x in str(root) for x in "\n\r\0"):
            raise ValueError(
                "Project paths must be absolute and contain no control characters"
            )
    for name, runtime in c["php_versions"].items():
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", name):
            raise ValueError("Invalid PHP version label")
        for key in ("cli", "fpm"):
            if not isinstance(runtime[key], str) or any(
                ch in runtime[key] for ch in "\n\r\0"
            ):
                raise ValueError("Invalid PHP runtime path")
    if c["active_php"] and c["active_php"] not in c["php_versions"]:
        raise ValueError("Active PHP runtime is not registered")
    return c


class Config:
    def __init__(self, paths):
        self.paths = paths
        paths.ensure()
        if paths.settings.exists():
            self.value = validate(json.loads(paths.settings.read_text()))
        else:
            self.value = copy.deepcopy(DEFAULTS)
            self.discover()
            self.save()

    def discover(self):
        c = self.value
        for name, candidates in {
            "database": ("mariadbd", "mysqld"),
            "apache": ("httpd", "apache2"),
        }.items():
            found = next((find_binary(v) for v in candidates if find_binary(v)), None)
            if found:
                c["binaries"][name] = found
                if name == "database" and Path(found).name == "mysqld":
                    c["database_kind"] = "mysql"
        cli, fpm = find_binary("php"), find_binary("php-fpm")
        if cli and fpm:
            c["php_versions"].setdefault("system", {"cli": cli, "fpm": fpm})
        for folder in sorted((self.paths.data / "runtimes/php").iterdir()):
            cli, fpm = folder / "bin/php", folder / "sbin/php-fpm"
            if cli.is_file() and fpm.is_file():
                c["php_versions"].setdefault(
                    folder.name, {"cli": str(cli), "fpm": str(fpm)}
                )
        if not c["active_php"] and c["php_versions"]:
            c["active_php"] = next(iter(c["php_versions"]))

    def save(self):
        validate(self.value)
        atomic_write(self.paths.settings, json.dumps(self.value, indent=2) + "\n")

    def replace(self, value):
        self.value = validate(copy.deepcopy(value))
        self.save()

    def scan(self):
        root = Path(self.value["projects_root"]).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        known = {p["name"] for p in self.value["projects"]}
        added = []
        for folder in sorted(root.iterdir()):
            if (
                folder.is_dir()
                and NAME.fullmatch(folder.name)
                and folder.name not in known
            ):
                item = {"name": folder.name, "path": str(folder.resolve())}
                self.value["projects"].append(item)
                added.append(item)
        self.save()
        return added
