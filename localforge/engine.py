import copy
import os
import shlex
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

from . import __version__
from .assets import ensure_adminer
from .config import SERVICES, Config, executable, validate
from .extensions import seed_system_database_extensions
from .paths import atomic_write
from .php import FpmPools, required_overrides, selection_fingerprint
from .render import render, render_projects

PORT_KEY = {
    "nginx": "http",
    "apache": "http",
    "database": "database",
    "redis": "redis",
    "node": "node",
}


def port_available(port):
    """Check actual bind availability, including wildcard listeners (race still possible)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def port_listeners():
    try:
        result = subprocess.run(
            ["ss", "-H", "-ltnp"], capture_output=True, text=True, timeout=3
        )
        return result.stdout if result.returncode == 0 else result.stderr
    except (OSError, subprocess.TimeoutExpired):
        return "ss unavailable; bind-based conflict detection remains active."


class Engine:
    """Single-owner foreground processes. The RPC server serializes all operations."""

    def __init__(self, paths):
        if os.geteuid() == 0:
            raise RuntimeError("LocalForge engines must not run as root")
        self.paths = paths
        self.config = Config(paths)
        self.discovery_fingerprint = None
        self.discovery_error = ""
        self.children = {}
        self.errors = {}
        self.pools = FpmPools(paths, self.config)
        marker = paths.generated / ".format"
        expected = f"{__version__}\n{paths.runtime}\n"
        if not marker.exists() or marker.read_text() != expected:
            if (paths.generated / "nginx.conf").exists():
                backup = paths.state / "config-backups" / str(time.time_ns())
                shutil.copytree(paths.generated, backup)
            render(paths, self.config.value)
        ensure_adminer(paths)
        seed_system_database_extensions(paths, self.config.value)
        self.update_php_shim()

    def auto_scan(self):
        """Discover new folders/public roots; never remove an explicitly registered project."""
        root = Path(self.config.value["projects_root"]).expanduser()
        if not root.is_dir() and not self.config.value["projects"]:
            return
        try:
            fingerprint = (
                tuple(
                    sorted(
                        (p.name, str(p.resolve()), (p / "public").is_dir())
                        for p in (root.iterdir() if root.is_dir() else [])
                        if p.is_dir()
                    )
                ),
                tuple(selection_fingerprint(p) for p in self.config.value["projects"]),
            )
            if fingerprint == self.discovery_fingerprint:
                return
            self.discovery_fingerprint = fingerprint
            previous = copy.deepcopy(self.config.value)
            names = ("projects-nginx.conf", "projects-apache.conf")
            backups = {n: (self.paths.generated / n).read_text() for n in names}
            started_pools = []
            try:
                if root.is_dir():
                    self.config.scan()
                overrides = render_projects(self.paths, self.config.value)
                if self.running("php") or any(
                    self.running(s) for s in ("nginx", "apache")
                ):
                    started_pools = self.pools.ensure(overrides)
                for server in ("nginx", "apache"):
                    if self.running(server):
                        self.reload(
                            server
                        )  # offline syntax check precedes the reload signal
                self.discovery_error = ""
            except Exception:
                for label in started_pools:
                    self.pools.stop(label)
                self.config.replace(previous)
                for name, text in backups.items():
                    atomic_write(self.paths.generated / name, text)
                raise
        except Exception as exc:
            self.discovery_error = str(exc)
            self.discovery_fingerprint = None

    def update_php_shim(self):
        c = self.config.value
        version = c["php_versions"].get(c["active_php"])
        dest = self.paths.data / "bin/php"
        if version:
            ini = self.paths.config / "php" / c["active_php"] / "php.ini"
            atomic_write(
                dest,
                "#!/bin/sh\nPHP_INI_SCAN_DIR="
                + shlex.quote(str(ini.parent / "conf.d"))
                + "\nexport PHP_INI_SCAN_DIR\nexec "
                + shlex.quote(version["cli"])
                + " -c "
                + shlex.quote(str(ini))
                + ' "$@"\n',
                0o700,
            )
        elif dest.exists():
            dest.unlink()

    def running(self, service):
        proc = self.children.get(service)
        return bool(proc and proc.poll() is None)

    def active_services(self):
        return [
            x
            for x in SERVICES
            if self.running(x) or (x == "php" and self.pools.any_running())
        ]

    def command(self, name, check=False):
        c, p = self.config.value, self.paths
        g = p.generated
        if name == "php":
            version = c["php_versions"].get(c["active_php"])
            if not version:
                raise ValueError("Register a PHP CLI and PHP-FPM pair first")
            fpm = executable(version["fpm"])
            ini = p.config / "php" / c["active_php"] / "php.ini"
            return [
                fpm,
                "-c",
                str(ini),
                "-y",
                str(g / "php-fpm.conf"),
                "-t" if check else "-F",
            ]
        binary = executable(c["binaries"][name])
        if name == "nginx":
            return [
                binary,
                "-p",
                str(p.data / "nginx") + "/",
                "-c",
                str(g / "nginx.conf"),
            ] + (["-t"] if check else ["-g", "daemon off;"])
        if name == "apache":
            return [binary, "-f", str(g / "httpd.conf")] + (
                ["-t"] if check else ["-DFOREGROUND"]
            )
        if name == "database":
            return [binary, f"--defaults-file={g / 'my.cnf'}"]
        if name == "redis":
            return [binary, str(g / "redis.conf")]
        if name == "node":
            node = c["node"]
            if not node["cwd"] or not Path(node["cwd"]).is_dir():
                raise ValueError("Set Node cwd and entry in Settings first")
            entry = Path(node["cwd"]) / node["entry"]
            if not entry.is_file():
                raise ValueError(f"Node entry not found: {entry}")
            return [binary, str(entry), *node["args"]]
        raise ValueError("Unknown service")

    def check(self, name):
        if name in ("nginx", "apache", "php"):
            env = os.environ.copy()
            if name == "php":
                env["PHP_INI_SCAN_DIR"] = str(
                    self.paths.config
                    / "php"
                    / self.config.value["active_php"]
                    / "conf.d"
                )
            result = subprocess.run(
                self.command(name, check=True),
                capture_output=True,
                text=True,
                timeout=15,
                env=env,
            )
            if result.returncode:
                raise ValueError((result.stderr + result.stdout)[-6000:])
            return (result.stderr + result.stdout)[-6000:]
        return (
            "No safe offline syntax checker for this service; inspect its startup log."
        )

    def start(self, name):
        if name not in SERVICES:
            raise ValueError("Unknown service")
        if name in ("nginx", "apache") and name != self.config.value["web_server"]:
            raise ValueError(
                "Select this web server in Settings first; both cannot share the HTTP port"
            )
        overrides = (
            render_projects(self.paths, self.config.value) if name == "php" else []
        )
        if self.running(name):
            if name == "php":
                self.pools.ensure(overrides)
            return f"{name} already running"
        port = self.config.value["ports"].get(PORT_KEY.get(name))
        if port and not port_available(port):
            raise ValueError(
                f"Port {port} is occupied. Change it in Settings or stop its owner.\n{port_listeners()}"
            )
        if name == "database" and not (self.paths.data / "database/mysql").exists():
            raise ValueError(
                "Database is not initialized. Use Initialize database (never uses the system datadir)."
            )
        if name in ("nginx", "apache"):
            self.start("php")
        if name == "php" and self.paths.php_socket.exists():
            # No adoption / no killing a PID from a stale pidfile.
            with socket.socket(socket.AF_UNIX) as probe:
                probe.settimeout(0.3)
                try:
                    probe.connect(str(self.paths.php_socket))
                except (ConnectionRefusedError, FileNotFoundError):
                    self.paths.php_socket.unlink(missing_ok=True)
                else:
                    raise ValueError(
                        "An unowned PHP socket is active. Stop its owner before starting LocalForge PHP."
                    )
        self.check(name)
        command = self.command(name)
        env = os.environ.copy()
        env["PATH"] = str(self.paths.data / "bin") + os.pathsep + env.get("PATH", "")
        cwd = self.paths.data
        if name == "php":
            env["PHP_INI_SCAN_DIR"] = str(
                self.paths.config / "php" / self.config.value["active_php"] / "conf.d"
            )
        if name == "node":
            env.update(PORT=str(port), HOST="127.0.0.1", NODE_ENV="development")
            cwd = Path(self.config.value["node"]["cwd"])
        with (self.paths.logs / f"{name}.log").open("ab", buffering=0) as log:
            log.write(
                f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} start {name} ---\n".encode()
            )
            proc = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self.children[name] = proc
        self.errors.pop(name, None)
        deadline = time.monotonic() + (20 if name == "database" else 8)
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                self.errors[name] = f"Exited {proc.returncode}; see {name}.log"
                raise ValueError(self.errors[name] + "\n" + self.tail(f"{name}.log"))
            if name == "php":
                with socket.socket(socket.AF_UNIX) as probe:
                    probe.settimeout(0.2)
                    try:
                        probe.connect(str(self.paths.php_socket))
                        break
                    except OSError:
                        pass
            else:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        return f"{name} running"
                except OSError:
                    pass
            time.sleep(0.1)
        else:
            self.stop(name)
            self.errors[name] = (
                "Startup timed out: expected listener did not become ready"
            )
            raise ValueError(self.errors[name] + "\n" + self.tail(f"{name}.log"))
        # A PHP connection succeeded. Starting project pools is transactional for
        # newly created children; don't leave a partially started PHP stack.
        try:
            self.pools.ensure(overrides)
            return f"{name} running"
        except Exception:
            self.stop(name)
            raise

    def stop(self, name):
        if name == "php":
            self.pools.stop_all()
        proc = self.children.get(name)
        if not proc or proc.poll() is not None:
            self.children.pop(name, None)
            self.errors.pop(name, None)
            return f"{name} stopped"
        # These are our own Popen handles/process groups, never user-supplied PIDs.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=15 if name == "database" else 8)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        except ProcessLookupError:
            proc.wait(timeout=2)
        self.children.pop(name, None)
        self.errors.pop(name, None)
        return f"{name} stopped"

    def reload(self, name):
        if not self.running(name):
            raise ValueError(f"{name} is not running")
        self.check(name)
        if name in ("nginx", "apache"):
            os.kill(
                self.children[name].pid,
                signal.SIGHUP if name == "nginx" else signal.SIGUSR1,
            )
        elif name == "php":
            self.pools.ensure(required_overrides(self.config.value))
            self.pools.reload_all()
            os.kill(self.children[name].pid, signal.SIGUSR2)
        else:
            raise ValueError(f"{name} has no safe generic reload. Use Restart instead.")
        return f"{name} reload requested; check logs for asynchronous failures"

    def stop_all(self):
        for name in ("node", "apache", "nginx", "php", "redis", "database"):
            self.stop(name)
        return "All managed services stopped"

    def start_all(self):
        result = []
        # Node is opt-in: an empty working directory means disabled.
        selected = ["php", self.config.value["web_server"], "database", "redis"]
        if self.config.value["node"]["cwd"]:
            selected.append("node")
        for name in selected:
            try:
                result.append(self.start(name))
            except Exception as exc:
                self.errors[name] = str(exc)
                result.append(f"{name}: {exc}")
        return "\n".join(result)

    def apply(self, value):
        if self.active_services():
            raise ValueError(
                "Stop all services before applying settings or regenerating configs"
            )
        validate(value)
        old = copy.deepcopy(self.config.value)
        try:
            render(self.paths, value)
            self.config.replace(value)
            seed_system_database_extensions(self.paths, self.config.value)
            self.update_php_shim()
        except Exception:
            self.config.replace(old)
            render(self.paths, old)
            self.update_php_shim()
            raise
        return "Settings saved; managed configs regenerated. Start services when ready."

    def switch_php(self, version):
        if version not in self.config.value["php_versions"]:
            raise ValueError("Unknown PHP runtime")
        runtime = self.config.value["php_versions"][version]
        executable(runtime["cli"])
        executable(runtime["fpm"])
        old = copy.deepcopy(self.config.value)
        active = [
            s
            for s in ("php", "nginx", "apache")
            if self.running(s) or (s == "php" and self.pools.any_running())
        ]
        for s in reversed(active):
            self.stop(s)
        try:
            c = copy.deepcopy(old)
            c["active_php"] = version
            # Leave unrelated daemons running; only PHP/web configs change here.
            render(self.paths, c)
            self.config.replace(c)
            self.update_php_shim()
            self.check("php")
            for s in active:
                self.start(s)
        except Exception as exc:
            for s in reversed(active):
                self.stop(s)
            render(self.paths, old)
            self.config.replace(old)
            self.update_php_shim()
            rollback_errors = []
            for s in active:
                try:
                    self.start(s)
                except Exception as rollback:
                    rollback_errors.append(str(rollback))
            raise ValueError(
                f"PHP switch failed; restored previous selection: {exc}. Rollback errors: {rollback_errors}"
            ) from exc
        return f"Active PHP: {version} (CLI shim + web FPM)"

    def init_db(self):
        if self.running("database"):
            raise ValueError("Stop database before initializing")
        datadir = self.paths.data / "database"
        if any(datadir.iterdir()):
            raise ValueError(
                "Database directory is not empty; refusing to overwrite. Inspect it manually."
            )
        binary = executable(self.config.value["binaries"]["database"])
        if self.config.value["database_kind"] == "mariadb":
            installer = executable("mariadb-install-db")
            import pwd

            command = [
                installer,
                "--no-defaults",
                f"--datadir={datadir}",
                "--auth-root-authentication-method=socket",
                f"--auth-root-socket-user={pwd.getpwuid(os.getuid()).pw_name}",
                "--skip-test-db",
            ]
        else:
            command = [
                binary,
                f"--defaults-file={self.paths.generated / 'my.cnf'}",
                "--initialize-insecure",
            ]
        with (self.paths.logs / "database-init.log").open("ab") as log:
            result = subprocess.run(
                command, stdout=log, stderr=subprocess.STDOUT, timeout=120
            )
        if result.returncode:
            raise ValueError(
                "Initialization failed; inspect database-init.log. Partial data is never auto-deleted."
            )
        return (
            "Database initialized. MariaDB: connect as your OS username over its private Unix socket. "
            "MySQL: root initially has NO password; set one immediately before using TCP. "
            "Create a password-protected non-root account for web database clients."
        )

    def tail(self, name):
        if Path(name).name != name or not name.endswith(".log"):
            raise ValueError("Invalid log name")
        path = self.paths.logs / name
        if not path.exists():
            return "No log yet."
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 48000))
            return stream.read().decode("utf-8", "replace")

    def snapshot(self):
        c = self.config.value
        statuses = []
        for name in SERVICES:
            proc = self.children.get(name)
            running = self.running(name)
            port = c["ports"].get(PORT_KEY.get(name))
            if proc and proc.poll() not in (None, 0) and name not in self.errors:
                self.errors[name] = f"Exited {proc.returncode}"
            statuses.append(
                {
                    "name": name,
                    "running": running,
                    "pid": proc.pid if running else None,
                    "port": port,
                    "conflict": bool(port and not running and not port_available(port)),
                    "disabled": name in ("nginx", "apache") and name != c["web_server"],
                    "error": self.errors.get(name, ""),
                }
            )
        return {
            "services": statuses,
            "php_pools": self.pools.snapshot(),
            "adminer_url": f"http://localhost:{c['ports']['http']}/_adminer",
            "settings": copy.deepcopy(c),
            "listeners": port_listeners(),
            "discovery_error": self.discovery_error,
            "logs": sorted(p.name for p in self.paths.logs.glob("*.log")),
            "paths": {
                k: str(getattr(self.paths, k))
                for k in (
                    "config",
                    "data",
                    "runtime",
                    "logs",
                    "mysql_socket",
                    "php_socket",
                )
            },
        }

    def editable_configs(self):
        p = self.paths
        ini = (
            p.config
            / "php"
            / (self.config.value["active_php"] or "default")
            / "php.ini"
        )
        return {
            "nginx.conf": p.generated / "nginx.conf",
            "httpd.conf": p.generated / "httpd.conf",
            "php.ini": ini,
            "my.cnf": p.generated / "my.cnf",
            "redis.conf": p.generated / "redis.conf",
            **{
                name: p.config / name
                for name in (
                    "nginx-http.conf",
                    "apache-extra.conf",
                    "my-extra.cnf",
                    "redis-extra.conf",
                )
            },
        }

    def dispatch(self, request):
        op = request.get("op")
        name = request.get("service")
        if op in ("start", "stop", "restart", "reload", "check"):
            if name not in SERVICES:
                raise ValueError("Unknown service")
            if op == "restart":
                self.stop(name)
                return self.start(name)
            return getattr(self, op)(name)
        if op in ("read_config", "write_config"):
            name = request["name"]
            if name == "hosts" and op == "read_config":
                return Path("/etc/hosts").read_text()
            configs = self.editable_configs()
            if name not in configs:
                raise ValueError("Unknown configuration file")
            if op == "read_config":
                return configs[name].read_text()
            if self.active_services():
                raise ValueError("Stop all services before editing configs")
            text = request["text"]
            if not isinstance(text, str) or len(text.encode()) > 512000:
                raise ValueError("Configuration too large")
            atomic_write(configs[name], text)
            return "File saved. Use Check config before starting. Regeneration replaces managed configs; keep customizations in the extra files."
        if op == "snapshot":
            return self.snapshot()
        if op == "start_all":
            return self.start_all()
        if op == "stop_all":
            return self.stop_all()
        if op == "restart_all":
            self.stop_all()
            return self.start_all()
        if op == "reload_all":
            results = []
            for s in self.active_services():
                try:
                    results.append(self.reload(s))
                except ValueError as exc:
                    results.append(str(exc))
            return "\n".join(results)
        if op == "apply":
            return self.apply(request["settings"])
        if op == "scan":
            if self.active_services():
                raise ValueError(
                    "Stop services before scanning projects (regenerates virtual hosts)"
                )
            self.config.scan()
            render(self.paths, self.config.value)
            return "Project directories scanned; virtual hosts regenerated. Sync hosts separately."
        if op == "discover":
            if self.active_services():
                raise ValueError("Stop services before discovering runtimes")
            self.config.discover()
            return self.apply(self.config.value)
        if op == "switch_php":
            return self.switch_php(request["version"])
        if op == "init_db":
            return self.init_db()
        if op == "tail":
            return self.tail(request["name"])
        raise ValueError("Unknown operation")
