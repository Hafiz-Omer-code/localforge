"""Opt-in native tests; private datadirs and ephemeral ports, no /etc modifications."""

import copy
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from localforge.config import find_binary
from localforge.engine import Engine
from localforge.paths import Paths

ENABLED = os.environ.get("LOCALFORGE_INTEGRATION") == "1"


@unittest.skipUnless(ENABLED, "Set LOCALFORGE_INTEGRATION=1 for native smoke tests")
class NativeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lf-")
        self.addCleanup(self.temp.cleanup)
        self.p = Paths(self.temp.name)
        self.e = Engine(self.p)
        self.addCleanup(self.e.stop_all)
        c = copy.deepcopy(self.e.config.value)
        ports = set()
        for key in c["ports"]:
            while True:
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    port = sock.getsockname()[1]
                if port not in ports:
                    ports.add(port)
                    c["ports"][key] = port
                    break
        fpm = next(
            (
                find_binary(x)
                for x in ("php-fpm", "php-fpm8.4", "php-fpm8.3", "php-fpm8.2")
                if find_binary(x)
            ),
            None,
        )
        if fpm and find_binary("php"):
            c["php_versions"]["native"] = {"cli": find_binary("php"), "fpm": fpm}
            c["active_php"] = "native"
        c["projects_root"] = str(Path(self.temp.name) / "www")
        root = Path(c["projects_root"]) / "smoke"
        root.mkdir(parents=True)
        (root / "index.php").write_text('<?php echo "LocalForge-PHP:".PHP_VERSION;')
        c["projects"] = [{"name": "smoke", "path": str(root)}]
        self.e.apply(c)
        self.c = c

    def need_php(self):
        if "native" not in self.c["php_versions"]:
            self.skipTest("PHP CLI/FPM not installed")

    def fetch(self, name="smoke"):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.c['ports']['http']}/",
            headers={"Host": name + ".test"},
        )
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.read().decode()

    def test_nginx_php_reload_restart_auto_vhost(self):
        self.need_php()
        if not find_binary("nginx"):
            self.skipTest("nginx not installed")
        self.e.start("nginx")
        self.assertIn("LocalForge-PHP:", self.fetch())
        self.e.reload("nginx")
        self.e.reload("php")
        root = Path(self.c["projects_root"]) / "new-app"
        root.mkdir()
        (root / "index.php").write_text('<?php echo "AUTO-VHOST";')
        self.e.auto_scan()
        deadline = time.monotonic() + 4
        text = ""
        while time.monotonic() < deadline:
            text = self.fetch("new-app")
            if "AUTO-VHOST" in text:
                break
            time.sleep(0.1)
        self.assertEqual(text, "AUTO-VHOST")
        self.e.stop("nginx")
        self.e.start("nginx")
        self.assertIn("LocalForge-PHP:", self.fetch())

    def test_apache_php(self):
        self.need_php()
        if not find_binary("httpd") and not find_binary("apache2"):
            self.skipTest("Apache not installed")
        c = copy.deepcopy(self.c)
        c["web_server"] = "apache"
        self.e.apply(c)
        self.e.start("apache")
        self.assertIn("LocalForge-PHP:", self.fetch())
        self.e.reload("apache")

    def test_redis_ping_restart(self):
        if not find_binary("redis-server"):
            self.skipTest("Redis not installed")
        self.e.start("redis")
        with socket.create_connection(("127.0.0.1", self.c["ports"]["redis"])) as sock:
            sock.sendall(b"*1\r\n$4\r\nPING\r\n")
            self.assertEqual(sock.recv(100), b"+PONG\r\n")
        self.e.stop("redis")
        self.e.start("redis")
        self.assertTrue(self.e.running("redis"))

    def test_mariadb_init_socket_auth_restart(self):
        if not find_binary("mariadbd") or not find_binary("mariadb-install-db"):
            self.skipTest("MariaDB not installed")
        self.e.init_db()
        self.e.start("database")
        import pwd

        user = pwd.getpwuid(os.getuid()).pw_name
        result = subprocess.run(
            [
                find_binary("mariadb"),
                "--no-defaults",
                "--protocol=socket",
                f"--socket={self.p.mysql_socket}",
                f"--user={user}",
                "-Nse",
                "SELECT 40+2",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "42")
        self.e.stop("database")
        self.e.start("database")

    def test_node_env_port_lifecycle(self):
        if not find_binary("node"):
            self.skipTest("Node not installed")
        root = Path(self.temp.name) / "node"
        root.mkdir()
        (root / "server.js").write_text(
            "require('http').createServer((q,s)=>s.end('NODE-OK')).listen(process.env.PORT,process.env.HOST)"
        )
        c = copy.deepcopy(self.c)
        c["node"]["cwd"] = str(root)
        self.e.apply(c)
        self.e.start("node")
        with urllib.request.urlopen(
            f"http://127.0.0.1:{c['ports']['node']}"
        ) as response:
            self.assertEqual(response.read(), b"NODE-OK")
        self.e.stop("node")
        self.assertFalse(self.e.running("node"))
        status = next(s for s in self.e.snapshot()["services"] if s["name"] == "node")
        self.assertEqual(status["error"], "")

    def test_failed_php_switch_rollback(self):
        self.need_php()
        c = copy.deepcopy(self.c)
        c["php_versions"]["broken"] = {"cli": find_binary("php"), "fpm": "/bin/false"}
        self.e.apply(c)
        self.e.start("php")
        with self.assertRaisesRegex(ValueError, "restored previous"):
            self.e.switch_php("broken")
        self.assertEqual(self.e.config.value["active_php"], "native")
        self.assertTrue(self.e.running("php"))
        result = subprocess.run(
            [str(self.p.data / "bin/php"), "-r", "echo PHP_VERSION;"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertTrue(result.stdout[0].isdigit())

    def test_adminer_nginx(self):
        self.check_adminer("nginx")

    def test_adminer_apache(self):
        self.check_adminer("apache")

    def check_adminer(self, server):
        self.need_php()
        if not find_binary(
            "nginx" if server == "nginx" else "apache2"
        ) and not find_binary("httpd"):
            self.skipTest("web server not installed")
        c = copy.deepcopy(self.c)
        c["web_server"] = server
        self.e.apply(c)
        self.e.start(server)
        base = f"http://127.0.0.1:{c['ports']['http']}"

        def get(path, host="localhost"):
            req = urllib.request.Request(base + path, headers={"Host": host})
            return urllib.request.urlopen(req, timeout=4)

        with get("/_adminer") as response:
            body = response.read().decode()
            self.assertEqual(response.status, 200)
            self.assertIn("Adminer", body)
            self.assertNotIn("<?php", body)
            self.assertIn("text/html", response.headers["Content-Type"])
        with get("/_adminer?file=default.css") as response:
            self.assertIn("text/css", response.headers["Content-Type"])
            self.assertGreater(len(response.read()), 100)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            get("/_adminer", "unrelated.test")
        self.assertIn(raised.exception.code, (403, 404))
        try:
            with get("/adminer.php") as response:
                self.assertNotIn(b"<?php", response.read())
        except urllib.error.HTTPError as error:
            self.assertEqual(error.code, 404)

    def test_project_php_overrides_nginx(self):
        self.check_project_php_overrides("nginx")

    def test_project_php_overrides_apache(self):
        self.check_project_php_overrides("apache")

    def check_project_php_overrides(self, server):
        self.need_php()
        if not find_binary(
            "nginx" if server == "nginx" else "apache2"
        ) and not find_binary("httpd"):
            self.skipTest("web server not installed")
        c = copy.deepcopy(self.c)
        c["web_server"] = server
        c["php_versions"]["8.2"] = c["php_versions"]["native"].copy()
        c["php_versions"]["other"] = c["php_versions"]["native"].copy()
        # Same installed binary, distinct ini markers: proves FPM socket routing,
        # not actual availability of PHP 8.2 or a second installed ABI.
        self.e.apply(c)
        ini = self.p.config / "php/8.2/php.ini"
        ini.parent.mkdir(parents=True, exist_ok=True)
        ini.write_text("date.timezone=Pacific/Honolulu\n")
        root = Path(c["projects"][0]["path"])
        (root / "index.php").write_text('<?php echo ini_get("date.timezone");')
        version_file = root / ".php-version"
        version_file.write_text("8.2\n")
        self.e.start(server)
        self.assertEqual(self.fetch(), "Pacific/Honolulu")
        self.assertTrue(self.e.pools.running("8.2"))
        self.assertTrue(self.p.php_version_socket("8.2").exists())
        status = self.e.snapshot()
        self.assertTrue(
            any(p["version"] == "8.2" and p["running"] for p in status["php_pools"])
        )
        self.e.switch_php("other")
        self.assertEqual(self.fetch(), "Pacific/Honolulu")
        version_file.write_text("not-registered\n")
        self.e.auto_scan()
        deadline = time.monotonic() + 4
        result = ""
        while time.monotonic() < deadline:
            result = self.fetch()
            if result == "UTC":
                break
            time.sleep(0.1)
        self.assertEqual(result, "UTC")
        self.e.stop_all()
        self.assertFalse(self.e.pools.any_running())

    def test_failed_project_pool_prevents_partial_php_start(self):
        self.need_php()
        c = copy.deepcopy(self.c)
        c["php_versions"]["broken"] = {"cli": find_binary("php"), "fpm": "/bin/false"}
        self.e.apply(c)
        (Path(c["projects"][0]["path"]) / ".php-version").write_text("broken")
        with self.assertRaises(ValueError):
            self.e.start("php")
        self.assertFalse(self.e.running("php"))
        self.assertFalse(self.e.pools.any_running())

    def test_live_override_file_change_starts_pool(self):
        self.need_php()
        if not find_binary("nginx"):
            self.skipTest("nginx not installed")
        c = copy.deepcopy(self.c)
        c["php_versions"]["project"] = c["php_versions"]["native"].copy()
        self.e.apply(c)
        self.e.start("nginx")
        self.e.auto_scan()
        root = Path(c["projects"][0]["path"])
        ini = self.p.config / "php/project/php.ini"
        ini.parent.mkdir(parents=True, exist_ok=True)
        ini.write_text("date.timezone=Pacific/Honolulu\n")
        (root / "index.php").write_text('<?php echo ini_get("date.timezone");')
        (root / ".php-version").write_text("project")
        self.e.auto_scan()
        self.assertTrue(self.e.pools.running("project"))
        deadline = time.monotonic() + 4
        result = ""
        while time.monotonic() < deadline:
            result = self.fetch()
            if result == "Pacific/Honolulu":
                break
            time.sleep(0.1)
        self.assertEqual(result, "Pacific/Honolulu")

    def test_mariadb_resolved_fallback_socket(self):
        if not find_binary("mariadbd"):
            self.skipTest("MariaDB not installed")
        base = Path(self.temp.name) / "fallback-case"
        short = Path(self.temp.name) / "short"
        with patch("localforge.paths.short_runtime_path", return_value=short):
            paths = Paths(base / ("long" * 28))
            engine = Engine(paths)
        self.addCleanup(engine.stop_all)
        self.assertEqual(paths.mysql_socket, short / "mysql.sock")
        c = copy.deepcopy(self.c)
        engine.apply(c)
        engine.init_db()
        engine.start("database")
        import pwd

        user = pwd.getpwuid(os.getuid()).pw_name
        result = subprocess.run(
            [
                find_binary("mariadb"),
                "--no-defaults",
                "--protocol=socket",
                f"--socket={paths.mysql_socket}",
                f"--user={user}",
                "-Nse",
                "SELECT 42",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "42")


@unittest.skipUnless(ENABLED, "Set LOCALFORGE_INTEGRATION=1 for IPC tests")
class RPCTest(unittest.TestCase):
    def test_daemon_socket_lifecycle(self):
        with tempfile.TemporaryDirectory(prefix="lf-") as tmp:
            env = os.environ.copy()
            for key, folder in (
                ("XDG_CONFIG_HOME", "c"),
                ("XDG_DATA_HOME", "d"),
                ("XDG_STATE_HOME", "s"),
                ("XDG_RUNTIME_DIR", "r"),
            ):
                env[key] = str(Path(tmp) / folder)
            process = subprocess.Popen(
                [sys.executable, "-m", "localforge.daemon"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            path = Path(tmp) / "r/localforge/manager.sock"
            try:
                deadline = time.monotonic() + 6
                while (
                    not path.exists()
                    and time.monotonic() < deadline
                    and process.poll() is None
                ):
                    time.sleep(0.05)
                self.assertTrue(path.exists(), "daemon socket missing")
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                with socket.socket(socket.AF_UNIX) as sock:
                    sock.connect(str(path))
                    sock.sendall(b'{"op":"snapshot"}\n')
                    result = json.loads(sock.makefile().readline())
                    self.assertTrue(result["ok"])
                    self.assertEqual(len(result["result"]["services"]), 6)
                with socket.socket(socket.AF_UNIX) as sock:
                    sock.connect(str(path))
                    sock.sendall(b'{"op":"shutdown"}\n')
                    self.assertTrue(json.loads(sock.makefile().readline())["ok"])
                process.wait(timeout=15)
            finally:
                if process.poll() is None:
                    process.terminate()
                process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0)
            self.assertFalse(path.exists())
