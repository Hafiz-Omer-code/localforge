# Changelog

## 0.1.2 — 2026-10-20

- Unified `Paths.mysql_socket`, `Paths.php_socket` and versioned PHP socket accessors. Missing/empty XDG_RUNTIME_DIR now explicitly selects the protected `/tmp/lf-<uid>` fallback; byte budgeting includes maximum-length version labels.
- Restore default SELinux contexts after installing the hosts helper and polkit action; discover restorecon in sbin and propagate restoration failures.
- Bundle pinned, unchanged Adminer 6.1.2 MySQL/English, its checksum, Apache 2.0 license and provenance notice. Deploy offline into welcome/adminer.php, with localhost-only default-vhost PHP execution and an Overview launcher.
- Add safe `.php-version` selection and supervised per-label FPM processes for Nginx and Apache, private per-label ini/config/socket/log/PID files, live discovery, failed-start rollback, pool status and cleanup with PHP lifecycle.
- Seed matching system-runtime database extension defaults once, without loading arbitrary system ini snippets or cross-label ABI modules.
- Back up older generated configs before renderer/runtime migration; preserve private ini files and persistent extras. Detect legacy standalone controller locations during upgrade.
- Expand validation to 64 passing unit/native integration tests, plus GUI smoke, wheel asset inclusion, shell syntax and lint checks. Arch/Fedora desktop validation remains outstanding.

## 0.1.1 — 2026-10-20

- Extended binary discovery to Fedora's `/usr/libexec` and `/usr/lib` without changing the user's shell PATH.
- Added explicit server-scoped Apache `User` and `Group` using the effective numeric UID and GID, independent of the USER environment variable and group naming conventions.
- Added byte-length guards for all managed Unix sockets and a mode-0700 `/tmp/lf-<uid>` fallback. Reject symlink, public, foreign-owned and non-directory fallback paths before either connecting or serving.
- Updated installer standalone-controller detection to recognize short runtime paths.
- Added 12 regression tests; all 45 unit/native integration tests pass in the Debian sandbox. Arch/Fedora desktop certification remains outstanding.

## 0.1.0 — 2026-10-20

- Native PySide6 GUI with lifecycle controls, PHP selector, project browser, port dialog, config editor, logs, settings and tray.
- Same-UID Unix RPC controller with userland service configs and processes, explicit graceful shutdown, syntax checks, readiness waits and TCP conflict checks.
- Nginx/Apache FastCGI projects, automatic directory discovery and checked live vhost reload.
- Per-runtime PHP CLI/FPM registry, isolated ini scan paths and failed-switch rollback.
- Private MariaDB bootstrap, optional MySQL engine mode, Redis/Valkey and opt-in Node entry point.
- Arch/Fedora install scripts, generated systemd user unit and desktop entry, narrowly-scoped polkit hosts helper.
- Unit, native process, Unix RPC and offscreen Qt checks; architecture, runtime setup, operating guidance and explicit validation limits.
