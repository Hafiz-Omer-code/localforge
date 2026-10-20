import copy
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import socket
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from localforge.assets import ADMINER_SHA256
from localforge.config import DEFAULTS, NAME, Config, find_binary, validate
from localforge.engine import Engine, port_available
from localforge.extensions import seed_system_database_extensions
from localforge.paths import (
    MAX_SOCKET_PATH,
    SOCKET_NAMES,
    Paths,
    atomic_write,
    ensure_private_runtime,
)
from localforge.php import project_override
from localforge.render import render, render_projects

loader = importlib.machinery.SourceFileLoader(
    "hosts_helper", str(Path(__file__).parents[1] / "packaging/hosts-helper")
)
spec = importlib.util.spec_from_loader(loader.name, loader)
hosts = importlib.util.module_from_spec(spec)
loader.exec_module(hosts)


class CoreTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="lf-")
        self.addCleanup(tmp.cleanup)
        self.p = Paths(tmp.name)
        self.p.ensure()
        self.c = copy.deepcopy(DEFAULTS)
        self.c["projects_root"] = str(Path(tmp.name) / "www")
        atomic_write(self.p.settings, json.dumps(self.c))

    def test_defaults(self):
        validate(self.c)

    def test_privileged_and_invalid_ports(self):
        for value in (80, 443, 0, 65536, "8080", True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                c = copy.deepcopy(self.c)
                c["ports"]["http"] = value
                validate(c)

    def test_duplicate_ports(self):
        self.c["ports"]["http"] = 3306
        with self.assertRaises(ValueError):
            validate(self.c)

    def test_dns_labels(self):
        for name in ("app", "my-app", "a", "9app"):
            self.assertIsNotNone(NAME.fullmatch(name))
        for name in (
            "../etc",
            "App",
            "foo.bar",
            "-app",
            "app-",
            "a" * 64,
            "x; return 200",
        ):
            self.assertIsNone(NAME.fullmatch(name))

    def test_path_injection(self):
        self.c["projects"] = [{"name": "app", "path": "/tmp/app\ninclude /etc/passwd"}]
        with self.assertRaises(ValueError):
            validate(self.c)

    def test_scan_idempotent(self):
        root = Path(self.c["projects_root"])
        for name in ("app", "my-app", "BAD", ".git", "two.words"):
            (root / name).mkdir(parents=True)
        (root / "file").write_text("not a directory")
        config = Config(self.p)
        self.assertEqual([p["name"] for p in config.scan()], ["app", "my-app"])
        self.assertEqual(config.scan(), [])

    def test_atomic_write(self):
        target = self.p.config / "atomic.txt"
        atomic_write(target, "one")
        atomic_write(target, "two")
        self.assertEqual(target.read_text(), "two")
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(target.parent.glob(".localforge-*")))

    def test_private_config(self):
        render(self.p, self.c)
        nginx = (self.p.generated / "nginx.conf").read_text()
        vhosts = (self.p.generated / "projects-nginx.conf").read_text()
        self.assertIn("listen 127.0.0.1:8080", vhosts)
        self.assertIn("uwsgi_temp_path", nginx)
        self.assertIn("scgi_temp_path", nginx)
        mysql = (self.p.generated / "my.cnf").read_text()
        self.assertIn("bind-address=127.0.0.1", mysql)
        self.assertIn(str(self.p.data / "database"), mysql)
        self.assertNotIn("/var/lib/mysql", mysql)

    def test_public_docroot_quoting(self):
        root = self.p.data / 'space $quote" site'
        (root / "public").mkdir(parents=True)
        self.c["projects"] = [{"name": "app", "path": str(root)}]
        render(self.p, self.c)
        text = (self.p.generated / "projects-nginx.conf").read_text()
        self.assertIn('space \\$quote\\" site/public', text)
        self.assertIn("try_files $uri =404", text)

    def test_ini_survives(self):
        ini = render(self.p, self.c)
        ini.write_text("memory_limit=128M\n")
        render(self.p, self.c)
        self.assertEqual(ini.read_text(), "memory_limit=128M\n")

    def test_mysql_x_disabled(self):
        self.c["database_kind"] = "mysql"
        render(self.p, self.c)
        self.assertIn("mysqlx=OFF", (self.p.generated / "my.cnf").read_text())

    def test_port_probe(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            sock.listen()
            self.assertFalse(port_available(port))
        self.assertTrue(port_available(port))

    def test_command_is_argv(self):
        engine = Engine(self.p)
        with patch("localforge.engine.executable", return_value="/usr/bin/nginx"):
            command = engine.command("nginx")
        self.assertIsInstance(command, list)
        self.assertEqual(command[-2:], ["-g", "daemon off;"])

    def test_log_traversal(self):
        engine = Engine(self.p)
        for name in ("../settings.json", "/etc/passwd", "foo/../bar.log"):
            with self.assertRaises(ValueError):
                engine.tail(name)

    def test_editor_whitelist(self):
        engine = Engine(self.p)
        with self.assertRaises(ValueError):
            engine.dispatch({"op": "write_config", "name": "/etc/hosts", "text": ""})
        engine.dispatch(
            {"op": "write_config", "name": "nginx-http.conf", "text": "# mine\n"}
        )
        self.assertEqual(
            engine.dispatch({"op": "read_config", "name": "nginx-http.conf"}),
            "# mine\n",
        )

    def test_running_guard(self):
        engine = Engine(self.p)
        with patch.object(engine, "active_services", return_value=["redis"]):
            with self.assertRaisesRegex(ValueError, "Stop all"):
                engine.apply(self.c)
            with self.assertRaisesRegex(ValueError, "Stop all"):
                engine.dispatch(
                    {"op": "write_config", "name": "nginx.conf", "text": "bad"}
                )

    def test_auto_scan_preserves_main(self):
        engine = Engine(self.p)
        main = self.p.generated / "nginx.conf"
        main.write_text(main.read_text() + "\n# retain note\n")
        (Path(self.c["projects_root"]) / "new-app").mkdir(parents=True)
        engine.auto_scan()
        self.assertEqual(engine.config.value["projects"][0]["name"], "new-app")
        self.assertIn(
            "new-app.test", (self.p.generated / "projects-nginx.conf").read_text()
        )
        self.assertIn("# retain note", main.read_text())

    def test_auto_scan_rollback(self):
        engine = Engine(self.p)
        (Path(self.c["projects_root"]) / "new-app").mkdir(parents=True)
        before = (self.p.generated / "projects-nginx.conf").read_text()
        with (
            patch.object(engine, "running", return_value=True),
            patch.object(engine, "reload", side_effect=ValueError("bad syntax")),
        ):
            engine.auto_scan()
        self.assertEqual(engine.config.value["projects"], [])
        self.assertEqual((self.p.generated / "projects-nginx.conf").read_text(), before)
        self.assertIn("bad syntax", engine.discovery_error)

    def test_nonempty_datadir_guard(self):
        engine = Engine(self.p)
        (self.p.data / "database/important").write_text("keep")
        with self.assertRaisesRegex(ValueError, "not empty"):
            engine.init_db()

    def test_binary_search_includes_fedora_private_executables(self):
        with (
            patch.dict(os.environ, {"PATH": "/custom/bin"}),
            patch(
                "localforge.config.shutil.which", return_value="/usr/libexec/mariadbd"
            ) as which,
        ):
            self.assertEqual(find_binary("mariadbd"), "/usr/libexec/mariadbd")
        search = which.call_args.kwargs["path"].split(os.pathsep)
        self.assertEqual(search[0], "/custom/bin")
        for directory in ("/usr/sbin", "/sbin", "/usr/libexec", "/usr/lib"):
            self.assertIn(directory, search)

    def test_apache_uses_actual_identity_not_user_environment(self):
        with patch.dict(os.environ, {"USER": "root"}):
            render(self.p, self.c)
        text = (self.p.generated / "httpd.conf").read_text()
        self.assertIn(f"User #{os.geteuid()}\nGroup #{os.getegid()}\n", text)
        self.assertNotIn("${USER}", text)
        self.assertEqual(sum(line.startswith("User ") for line in text.splitlines()), 1)
        self.assertEqual(
            sum(line.startswith("Group ") for line in text.splitlines()), 1
        )
        self.assertLess(text.index("User #"), text.index("<VirtualHost"))

    def test_normal_runtime_is_unchanged(self):
        self.assertFalse(self.p.short_runtime)
        self.assertEqual(self.p.socket, self.p.runtime / "manager.sock")

    def test_short_runtime_name_uses_fixed_tmp_and_uid(self):
        from localforge.paths import short_runtime_path

        with patch.dict(os.environ, {"TMPDIR": "/untrusted/location"}):
            self.assertEqual(short_runtime_path(), Path("/tmp") / f"lf-{os.getuid()}")

    def test_long_runtime_fallback_binds_all_managed_sockets(self):
        short = self.p.state / "short"
        long_root = self.p.data / ("x" * 110)
        with patch("localforge.paths.short_runtime_path", return_value=short):
            paths = Paths(long_root)
            paths.ensure()
        self.assertTrue(paths.short_runtime)
        self.assertEqual(paths.runtime, short)
        self.assertEqual(short.stat().st_mode & 0o777, 0o700)
        self.assertEqual(short.stat().st_uid, os.getuid())
        for name in SOCKET_NAMES:
            path = paths.runtime / name
            self.assertLessEqual(len(os.fsencode(path)), MAX_SOCKET_PATH)
            with socket.socket(socket.AF_UNIX) as sock:
                sock.bind(str(path))
                sock.listen()
            path.unlink()

    def test_long_xdg_runtime_fallback(self):
        short = self.p.state / "short"
        env = {
            "XDG_RUNTIME_DIR": str(self.p.data / ("x" * 110)),
            "XDG_CONFIG_HOME": str(self.p.config),
            "XDG_DATA_HOME": str(self.p.data),
            "XDG_STATE_HOME": str(self.p.state),
        }
        with (
            patch.dict(os.environ, env),
            patch("localforge.paths.short_runtime_path", return_value=short),
        ):
            paths = Paths()
        self.assertTrue(paths.short_runtime)
        self.assertEqual(paths.socket, short / "manager.sock")

    def test_socket_length_counts_bytes_not_characters(self):
        short = self.p.state / "short"
        root = self.p.data / ("é" * 40)
        self.assertLess(len(str(root / "run/manager.sock")), MAX_SOCKET_PATH)
        self.assertGreater(len(os.fsencode(root / "run/manager.sock")), MAX_SOCKET_PATH)
        with patch("localforge.paths.short_runtime_path", return_value=short):
            self.assertTrue(Paths(root).short_runtime)

    def test_fallback_reuses_private_directory(self):
        path = self.p.state / "short"
        path.mkdir(mode=0o700)
        before = path.stat().st_ino
        ensure_private_runtime(path)
        self.assertEqual(path.stat().st_ino, before)

    def test_fallback_rejects_symlinks(self):
        target = self.p.state / "target"
        target.mkdir(mode=0o755)
        path = self.p.state / "link"
        path.symlink_to(target, target_is_directory=True)
        before = target.stat().st_mode
        with self.assertRaises(OSError):
            ensure_private_runtime(path)
        self.assertEqual(target.stat().st_mode, before)

    def test_fallback_rejects_public_directory_without_repair(self):
        path = self.p.state / "public"
        path.mkdir(mode=0o755)
        path.chmod(0o755)
        with self.assertRaises(PermissionError):
            ensure_private_runtime(path)
        self.assertEqual(path.stat().st_mode & 0o777, 0o755)

    def test_fallback_rejects_foreign_owner(self):
        path = self.p.state / "foreign"
        path.mkdir(mode=0o700)
        foreign = SimpleNamespace(st_uid=os.getuid() + 1, st_mode=stat.S_IFDIR | 0o700)
        with (
            patch("localforge.paths.os.fstat", return_value=foreign),
            self.assertRaises(PermissionError),
        ):
            ensure_private_runtime(path)

    def test_fallback_rejects_regular_file(self):
        path = self.p.state / "not-a-directory"
        path.write_text("do not replace")
        with self.assertRaises(OSError):
            ensure_private_runtime(path)
        self.assertEqual(path.read_text(), "do not replace")

    def test_named_socket_properties_follow_runtime(self):
        self.assertEqual(self.p.mysql_socket, self.p.runtime / "mysql.sock")
        self.assertEqual(self.p.php_socket, self.p.runtime / "php.sock")
        short = self.p.state / "short"
        with patch("localforge.paths.short_runtime_path", return_value=short):
            paths = Paths(self.p.data / ("long" * 30))
        self.assertEqual(paths.mysql_socket, short / "mysql.sock")
        self.assertEqual(paths.php_socket, short / "php.sock")
        self.assertEqual(paths.php_version_socket("8.2"), short / "php-8.2.sock")

    def test_missing_xdg_runtime_uses_private_fallback(self):
        env = dict(os.environ)
        env.pop("XDG_RUNTIME_DIR", None)
        short = self.p.state / "short"
        with (
            patch.dict(os.environ, env, clear=True),
            patch("localforge.paths.short_runtime_path", return_value=short),
        ):
            paths = Paths()
        self.assertTrue(paths.short_runtime)
        self.assertEqual(paths.php_socket, short / "php.sock")
        self.assertEqual(paths.mysql_socket, short / "mysql.sock")

    def test_versioned_socket_length_reserved_at_resolution(self):
        short = self.p.state / "short"
        # manager.sock alone would fit; a maximum-length version label would not.
        candidate = self.p.data / ("m" * 45)
        self.assertLessEqual(
            len(os.fsencode(candidate / "run/manager.sock")), MAX_SOCKET_PATH
        )
        with patch("localforge.paths.short_runtime_path", return_value=short):
            paths = Paths(candidate)
        self.assertTrue(paths.short_runtime)
        self.assertLessEqual(
            len(os.fsencode(paths.php_version_socket("x" * 40))), MAX_SOCKET_PATH
        )

    def test_versioned_socket_rejects_path_injection(self):
        for label in ("../bad", "x/../../bad", "x" * 41, "8.2\n", ""):
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.p.php_version_socket(label)

    def project_fixture(self):
        root = self.p.data / "project"
        root.mkdir()
        self.c["php_versions"] = {"8.2": {"cli": "/usr/bin/php", "fpm": "/fake/fpm"}}
        self.c["projects"] = [{"name": "app", "path": str(root)}]
        return root, self.c["projects"][0]

    def test_php_version_absent_unknown_or_invalid_falls_back(self):
        root, project = self.project_fixture()
        self.assertIsNone(project_override(project, self.c))
        for text in ("missing", "../8.2", "x" * 300):
            (root / ".php-version").write_text(text)
            self.assertIsNone(project_override(project, self.c))
        (root / ".php-version").write_bytes(b"\xff")
        self.assertIsNone(project_override(project, self.c))

    def test_php_version_symlink_and_fifo_are_not_read(self):
        root, project = self.project_fixture()
        target = root / "version"
        target.write_text("8.2")
        selector = root / ".php-version"
        selector.symlink_to(target)
        self.assertIsNone(project_override(project, self.c))
        selector.unlink()
        os.mkfifo(selector)
        self.assertIsNone(project_override(project, self.c))

    def test_registered_php_version_renders_both_web_servers(self):
        root, project = self.project_fixture()
        (root / ".php-version").write_text("8.2\n")
        self.assertEqual(project_override(project, self.c), "8.2")
        labels = render_projects(self.p, self.c)
        self.assertEqual(labels, ["8.2"])
        for name in ("projects-nginx.conf", "projects-apache.conf"):
            self.assertIn(
                str(self.p.php_version_socket("8.2")),
                (self.p.generated / name).read_text(),
            )
        pool = (self.p.generated / "php-fpm-8.2.conf").read_text()
        self.assertIn(str(self.p.php_version_socket("8.2")), pool)
        self.assertTrue((self.p.config / "php/8.2/php.ini").exists())

    def test_adminer_asset_is_pinned_and_deployed(self):
        render(self.p, self.c)
        asset = self.p.data / "welcome/adminer.php"
        self.assertEqual(hashlib.sha256(asset.read_bytes()).hexdigest(), ADMINER_SHA256)
        text = (self.p.generated / "projects-nginx.conf").read_text()
        self.assertIn("location = /_adminer", text)
        self.assertIn(str(self.p.php_socket), text)
        self.assertIn(str(asset), text)
        self.assertNotIn("include fastcgi_params;", text)
        self.assertIn("AliasMatch", (self.p.generated / "httpd.conf").read_text())
        self.assertTrue((self.p.data / "welcome/public/index.html").exists())

    def test_upgrade_backs_up_generated_configs(self):
        Engine(self.p)
        main = self.p.generated / "nginx.conf"
        old = main.read_text() + "\n# migration-backup-test\n"
        main.write_text(old)
        (self.p.generated / ".format").unlink()
        Engine(self.p)
        backups = list((self.p.state / "config-backups").glob("*/nginx.conf"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), old)
        self.assertNotIn("# migration-backup-test", main.read_text())

    def test_system_db_extension_seeding_is_scoped_and_ordered(self):
        modules = self.p.data / "modules"
        modules.mkdir()
        for name in ("mysqlnd", "mysqli", "pdo", "pdo_mysql", "session"):
            (modules / (name + ".so")).touch()
        info = json.dumps({"directory": str(modules), "loaded": ["session"]})
        result = SimpleNamespace(returncode=0, stdout=info)
        self.c["php_versions"]["system"] = {"cli": "/fake/php", "fpm": "/fake/fpm"}
        with (
            patch("localforge.extensions.executable", return_value="/fake/php"),
            patch("localforge.extensions.subprocess.run", return_value=result),
        ):
            seed_system_database_extensions(self.p, self.c)
        target = self.p.config / "php/system/conf.d/20-localforge-database.ini"
        text = target.read_text()
        self.assertLess(text.index("mysqlnd.so"), text.index("mysqli.so"))
        self.assertLess(text.index("pdo.so"), text.index("pdo_mysql.so"))
        self.assertNotIn("session.so", text)
        target.write_text("; user edit\n")
        seed_system_database_extensions(self.p, self.c)
        self.assertEqual(target.read_text(), "; user edit\n")


class HostsTest(unittest.TestCase):
    def test_preserves_existing(self):
        text = "127.0.0.1 localhost\n# keep\n"
        new = hosts.update(text, 1000, ["app.test"])
        self.assertTrue(new.startswith(text))
        self.assertIn("127.0.0.1\tapp.test", new)

    def test_idempotent_deduplicated(self):
        text = hosts.update("", 1000, ["z.test", "a.test", "z.test"])
        self.assertEqual(hosts.update(text, 1000, ["z.test", "a.test"]), text)

    def test_remove_only_own_block(self):
        text = hosts.update("127.0.0.1 localhost\n", 1000, ["one.test"])
        text = hosts.update(text, 1001, ["two.test"])
        text = hosts.update(text, 1000, [])
        self.assertNotIn("one.test", text)
        self.assertIn("two.test", text)
        self.assertIn("localhost", text)

    def test_external_conflict(self):
        with self.assertRaises(ValueError):
            hosts.update("192.0.2.1 app.test\n", 1000, ["app.test"])

    def test_other_user_conflict(self):
        with self.assertRaises(ValueError):
            hosts.update(hosts.update("", 1000, ["app.test"]), 1001, ["app.test"])

    def test_malformed_block(self):
        with self.assertRaises(ValueError):
            hosts.update("# BEGIN LOCALFORGE UID 1000\n", 1000, [])

    def test_domain_validation(self):
        for name in (
            "evil.com",
            "a.test\n127.0.0.1 bank.com",
            "*.test",
            "-a.test",
            "a.b.test",
        ):
            self.assertIsNone(hosts.DOMAIN.fullmatch(name))
