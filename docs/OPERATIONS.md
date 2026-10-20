# Operations, DNS and security

## Ports and configuration

Defaults: HTTP `8080`, database `3306`, Redis `6379`, optional Node `3000`. All manager-assigned ports must be distinct integers from 1024 through 65535. The HTTP server, database and cache bind IPv4 loopback in generated configs. PHP-FPM uses a private Unix socket.

To change a port, stop all services, use **Settings → Edit ports**, confirm the draft and Apply settings, then restart. A real loopback bind probe detects active TCP ownership immediately before launch. The active port table is from `ss -H -ltnp`; Linux may hide PIDs belonging to another user. SO_REUSEADDR handles prior connections in TIME_WAIT. The check cannot eliminate a time-of-check/time-of-use race; the engine's own bind error remains authoritative.

If generated config files are manually edited to listen on a different port, update settings as well; readiness probes and project URLs use settings. Settings-defined port changes regenerate managed config files, so keep reusable customizations in `nginx-http.conf`, `apache-extra.conf`, `my-extra.cnf` and `redis-extra.conf`.

User configuration is executable-adjacent, trusted data: Nginx/Apache modules, PHP directives or a Node entry can execute code as you. The text editor is a convenience, not a configuration firewall. The daemon accepts same-UID clients only and is not intended as a boundary against your own compromised desktop account.

### Standard ports 80/443

This implementation chooses unprivileged ports rather than a root binder. Thus the URL is `project.test:8080`, not just `project.test`. There is deliberately no `setcap` on Python/Node/PHP, root GUI, passwordless sudo rule, authbind wrapper or persistent firewall redirect.

If your organization requires no-port URLs, use a separately administered loopback reverse proxy with narrowly scoped configuration and TLS certificate management. Do not change `net.ipv4.ip_unprivileged_port_start` globally or grant general-purpose interpreters `CAP_NET_BIND_SERVICE` merely for this application. Local TLS/certificate trust is not implemented in this preview.

## Safe hosts management (implemented default)

GUI/CLI invoke `/usr/bin/pkexec /usr/lib/localforge/hosts-helper` as a child of the ordinary desktop user. The polkit action is restricted to that exact root-owned executable and requires active-session administrator authentication each time (`auth_admin`, not a keep-authorized rule). It grants no general command execution or arbitrary file writing.

Helper properties:

- No command-line arguments or caller-controlled file path.
- Invoking UID is from pkexec, must be a regular desktop UID (1000+); root/non-desktop invocation is refused.
- Bounded stdin JSON; at most 500 exact lowercase single-label `.test` domains. No wildcard, IP, newline, arbitrary suffix or shell string.
- Updates only `# BEGIN LOCALFORGE UID N` … `# END LOCALFORGE UID N`.
- Rejects domains that already exist outside the invoking user's block, including other users' blocks.
- Uses a global helper lock, validates a regular root-owned `/etc/hosts`, rejects symlinks, writes a sibling temporary file and atomically replaces the target.
- Preserves owner, mode and extended attributes (including SELinux context); checks target inode/content before replacement.
- Fails closed if metadata copying or atomic replacement is unsupported (for example a bind-mounted container hosts file).

No existing system service or unrelated hosts line is deliberately removed. Root and other administrators can edit the file outside the helper's advisory lock, so edits cannot be made globally transaction-safe against all external tools.

Hostname changes are **explicit**, not performed by the background poller. New vhosts can be ready before name resolution exists. Press Sync hosts after new projects appear. An empty project list sync removes your block. The configuration editor's `hosts` view is read-only and never writes `/etc/hosts` directly.

Multiple desktop users cannot own the same `.test` name simultaneously. Their HTTP, database and cache ports must also differ because user processes share the host network namespace.

## Optional wildcard DNS (manual administrator setup)

Hosts management is sufficient for the implemented feature set and coexists with `systemd-resolved`, NetworkManager and ordinary glibc lookup. LocalForge does **not** automatically rewrite `/etc/resolv.conf`, NetworkManager settings, resolved drop-ins, or dnsmasq system services.

If you deliberately want wildcard `.test` DNS, a separate administrator-managed dnsmasq can be configured. For example, when `127.0.0.2:53` is free and systemd-resolved is your host resolver:

```ini
# /etc/dnsmasq.d/localforge.conf  (admin-owned example)
listen-address=127.0.0.2
bind-interfaces
port=53
no-resolv
no-hosts
local=/test/
address=/test/127.0.0.1
```

Configure the host resolver to route **only** `.test` to it:

```ini
# /etc/systemd/resolved.conf.d/localforge.conf  (admin-owned example)
[Resolve]
DNS=127.0.0.2
Domains=~test
```

Install dnsmasq through `pacman -S dnsmasq` or `dnf install dnsmasq` if needed. Inspect `ss -luntp` for conflicts first, validate with `dnsmasq --test`, then have the administrator restart the affected services. This recipe assumes systemd-resolved is active and `/etc/resolv.conf` is already wired to its stub. Arch installations need not use resolved; Fedora installations can have NetworkManager-specific DNS modes. Do not blindly apply this configuration to a different resolver topology, an existing dnsmasq deployment or a corporate VPN. Test split-DNS behavior and rollback requirements on your actual desktop. A dedicated dnsmasq config may need to be isolated from an existing server's other listen interfaces.

Validate with `resolvectl query app.test` and `getent hosts app.test`. Browser DNS-over-HTTPS can bypass host DNS policy; disable that behavior for local development if necessary. Wildcard DNS resolves arbitrary names; vhost generation still only serves registered projects. The GUI currently retains explicit-host sync as its only automated DNS/hosts action.

## Fedora / SELinux

The installer restores default SELinux contexts with `sudo restorecon -Rv /usr/lib/localforge /usr/share/polkit-1/actions` immediately after installing the helper and policy, when restorecon is available (including sbin lookup). Failures abort installation. This prevents user staging labels from being retained, but does not substitute for an enforcing Fedora VM test. No permissive mode or custom allow rule is used.

Do not disable SELinux to make a development tool work. Generated files stay user-owned under your home; executables are run from the user manager instead of root `httpd_t` service units. Actual domain transitions and home-directory access depend on Fedora release and local policy.

When a check/start fails, first read the engine error log, then inspect AVCs with the administrator's tools (`ausearch -m AVC -ts recent`, `journalctl`). Apply the **minimum appropriate** file labeling or reviewed local policy after understanding the denial. This project deliberately does not install blanket SELinux allow rules, `chcon` your entire home, run `setenforce 0`, or set global booleans. Changing distro service ports does not imply changing system service unit ports; these are separate user-owned daemons. Real Fedora VM validation remains required.

## Long runtime paths

If XDG_RUNTIME_DIR is missing/empty, or any managed socket (including a maximum-length versioned FPM name) would exceed 103 encoded bytes, LocalForge uses `/tmp/lf-<uid>/` instead of the requested XDG runtime directory. It validates ownership, type and exact mode 0700 before use; it never follows a symlink or fixes an untrusted existing fallback automatically. A precreated unsafe path causes a clear failure, not an insecure connection. Have an administrator inspect a foreign-owned path rather than deleting it blindly. Use `localforge status` to locate the actual sockets; do not assume `$XDG_RUNTIME_DIR/localforge` when the fallback is active. Upgrades also check this fallback for an existing standalone controller.

## Desktop issues

- Qt on Wayland uses the native platform when available; on X11 install the XCB runtime dependencies from the installers. If the wheel fails to load, use `QT_DEBUG_PLUGINS=1 localforge-gui` to identify missing distro libraries.
- Without a tray implementation (for example GNOME without an indicator extension), closing the window exits the GUI; the controller remains running.
- Headless operation needs only the CLI/controller, not a GUI display. GUI smoke testing can use `QT_QPA_PLATFORM=offscreen`.
- `pkexec` needs a desktop authentication agent. The helper is intentionally not launched from the `NoNewPrivileges` controller unit; the GUI/CLI launches it separately.
- Service reload does not reload the GUI or apply draft JSON settings. Save/apply intentionally.
- Nginx/Apache fail on missing PHP-FPM rather than silently serving PHP source. Nginx checks script existence and denies dotfiles. Apache permits project `.htaccess` for framework development; treat project code and overrides as trusted.

## Supervision and logs

```bash
systemctl --user status localforge.service
journalctl --user -u localforge.service
localforge tail database-error.log
localforge stop-all
localforge shutdown  # stop an existing standalone or systemd controller, without auto-starting it
systemctl --user stop localforge.service  # controller + cgroup clean shutdown
```

No system `nginx.service`, `httpd.service`, `mariadb.service` or `redis.service` is controlled. The manager deliberately refuses to kill a PID based on a stale file or occupied port. Do not use `killall nginx` to resolve a conflict: it may terminate a different workspace.

Logs are currently unbounded regular files; stop services and archive/truncate them when needed. There is no automatic log rotation, crash resume, runtime patching, health-based restart policy or password vault. The installed systemd unit supervises the controller's own failures, not individual applications. Standalone mode offers weaker descendant cleanup if the controller is SIGKILLed.

Back up database contents with a logical dump using the private socket and your chosen account; back up settings and PHP ini files separately. Filesystem copying a live datadir is not a consistent database backup.
