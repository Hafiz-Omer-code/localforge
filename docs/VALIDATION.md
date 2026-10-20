# Validation and release status

**LocalForge 0.1.2 — developer preview**  
Validated in the provided Linux sandbox on 2026-10-20. This is an implemented, runnable native desktop project, not a UI mockup; it is not yet a distribution-certified release.

## Executed environment

- Debian GNU/Linux 13, x86_64, glibc 2.41 (sandbox/container, not a full desktop VM).
- Python 3.13.16; PySide6 / Qt 6.12.0.
- Nginx 1.26.3; Apache 2.4.69.
- PHP CLI/FPM 8.4.26 (Debian's installed runtime, registered with explicit paths).
- MariaDB 11.8.6; Redis 8.0.2; Node.js 20.20.2.
- All service integration tests run as UID 1000 with disposable private directories and ephemeral loopback ports. No tests edit `/etc/hosts`, system service configs or existing database data.

## Results

| Check | Result |
|---|---|
| Core + hosts + installer-harness unit tests | **50 passed** |
| Native engines + Unix controller integration tests | **14 passed** |
| Combined unittest suite | **64 passed**, no skips with native dependencies installed |
| Fedora libexec search, numeric Apache identity, byte-length socket guard and safe fallback rejection/binding | Passed (unit regressions; fallback sockets actually bound) |
| Explicit resolved MySQL/PHP sockets; missing/long XDG runtime fallback and actual MariaDB query through fallback socket | Passed |
| Project-version FPM startup, live selector changes, global-switch pinning and failed-pool rollback under both web servers | Passed (distinct ini markers on the same installed PHP binary, not legacy ABI coverage) |
| Pinned Adminer HTTP HTML/CSS, no PHP-source exposure and unrelated-Host rejection on Nginx and Apache | Passed |
| SELinux restorecon invocation and failure propagation | Passed with fake shell tools; actual enforcing Fedora remains untested |
| Actual Nginx → private FPM → PHP HTTP response | Passed |
| Actual Apache → private FPM → PHP HTTP response | Passed |
| Web/PHP reload and HTTP service restart | Passed |
| New directory → generated vhost → checked live reload → HTTP response | Passed |
| Failed PHP switch → prior runtime restored and restarted | Passed |
| Private CLI PHP wrapper | Passed |
| MariaDB private initialization, OS-user socket auth SQL query, stop/restart | Passed |
| Redis RESP PING and stop/restart | Passed |
| Node HOST/PORT entry point, HTTP response, stop | Passed |
| Unix RPC peer-owned socket, 0600 mode, status and graceful shutdown | Passed |
| Actual PySide6 window + asynchronous engine snapshot | Passed offscreen: 6 service rows, 2 projects |
| Screenshot from actual native GUI | `screenshots/overview.png` |
| Shell syntax, all install/uninstall scripts (`bash -n`) | Passed |
| Python syntax / compileall | Passed |
| Ruff E4/E7/E9/F/I lint and formatting (application + tests) | Passed |
| Polkit XML parse | Passed |
| PEP 517 wheel build (`pip wheel --no-deps .`) | Passed; bundled PHP, notice and full license verified inside wheel |

Raw suite output: [test-results.txt](test-results.txt). The screenshot uses actual running Nginx, PHP-FPM and Redis processes on randomly allocated test ports; it does not claim default port or distro validation.

## Reproduce

```bash
# From source checkout with Python, PySide6 and engine packages installed:
PYTHONPATH=. python -m unittest discover -s tests -v
PYTHONPATH=. LOCALFORGE_INTEGRATION=1 python -m unittest discover -s tests -v
PYTHONPATH=. QT_QPA_PLATFORM=offscreen python scripts/gui-smoke.py
bash -n install-arch.sh install-fedora.sh scripts/*.sh
python -m compileall -q localforge
```

With no integration opt-in, fourteen native/IPC tests are intentionally skipped. With opt-in but missing individual engine dependencies, engine-specific tests skip rather than installing software or substituting fake services. Core tests do not require Qt.

## Not executed / remaining release gates

1. **Arch desktop:** actual `pacman -Syu` install, a current system Python/PySide wheel, package extension paths, user-unit enablement, tray/polkit desktop agent and uninstall. Dependency names/scripts are supplied; no Arch VM was available.
2. **Fedora desktop:** actual `dnf` install, Redis/Valkey repository selection, SELinux transitions/AVCs, systemd user unit, polkit metadata-preserving hosts edit, Wayland/X11/tray. No Fedora VM was available. No automatic SELinux exceptions are shipped.
3. **Root hosts helper end to end:** transformation, domain restrictions and conflict behavior are unit-tested; XML is parsed. The privileged helper was not installed or authorized against the sandbox's real `/etc/hosts`, nor has it received an independent security audit. Test cancel/deny, malformed requests, conflicting users, labels, mount failures and preservation of unrelated entries before a production desktop rollout.
4. **Each legacy PHP version:** registry and coordinated FPM switch mechanisms are implemented; actual 7.4 / 8.1 / 8.2 / 8.3 distributions, extension ABIs and library compatibility were not validated. The installer intentionally installs only distro-current PHP. No obsolete runtime bundle is implied.
5. **Oracle MySQL:** command/config mode and initial-password instructions are implemented, but only MariaDB was native-integration-tested. Validate the intended vendor/version before use.
6. **External DBeaver:** native/Flatpak discovery and launcher paths are supplied but a graphical DBeaver/JDBC session was not exercised. Adminer is bundled and its HTTP HTML/CSS execution and Host restrictions were exercised under both servers. Interactive Adminer database login was not exercised; phpMyAdmin is not bundled.
7. **Systemd desktop boot/login/logout:** user-unit source is generated by the installer; actual unit installation and cgroup behavior were not exercised in this no-user-manager container. Standalone Unix controller startup/shutdown was exercised directly.
8. **DNS example:** optional dnsmasq/resolved recipe is documentation, not automated or validated against a live target network resolver. The default automated integration is explicit UID-scoped hosts sync.

## Intentional scope limits

- User-process/config/data isolation; **not** namespaces, containers or a malicious-code sandbox.
- A global CLI/default/Adminer FPM plus one master per selected project label; not per-project shell environment switching.
- One Node foreground entry point, not an npm/package/runtime download manager.
- No bare port-80 URL, automatic TLS, certificate trust, blanket firewall redirect or privileged binder.
- No automatic runtime patching, old-PHP compilation, individual engine crash restart, database migrations/backup automation, credential vault or log rotation.
- Settings apply/full scan/PHP switch regenerate managed files; persist custom changes in designated includes and versioned ini files.
- Deleted project folders remain registered until deliberately removed. Hostname sync is explicit and authenticated, not a silent root background mutation.
- Readiness is process/listener based. Apps must honor configured ports; app-specific HTTP/SQL health checks and bind-race-proof listener ownership checks are future hardening.
- Graceful web reload can fail after the offline checker succeeds; inspect logs. Standalone SIGKILL can leave children; systemd cgroup supervision is preferred.

These boundaries are intentional and exposed in the UI/docs rather than presented as completed features.
