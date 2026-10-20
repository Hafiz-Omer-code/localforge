# Runtime configuration

## PHP: system runtime and independent versions

The installer installs the distro's **current** CLI and FPM; initial discovery registers the pair as `system`. It does not promise that the current distribution carries PHP 7.4, 8.1, 8.2 or 8.3 side by side. LocalForge can manage those versions once supplied as compatible native builds.

Recommended layout for each registered runtime:

```text
~/.local/share/localforge/runtimes/php/8.3/
├── bin/php
├── sbin/php-fpm
├── lib/php/extensions/...
└── ... associated libraries/data files required by that build ...
```

Use **Settings → Register PHP** and provide the two **absolute executable paths** and a label, then Apply. Or place the conventional prefix above and select **Discover runtimes**. All services must be stopped to discover/apply. Do not copy only `php` from another distribution: glibc, OpenSSL, libicu and PHP extension ABI versions must match. Build and maintain your own trusted prefixes, or use a vendor whose provenance, signature/checksums and distro compatibility you have verified. This release does not ship a PHP binary downloader or silently add third-party package repositories.

A source build into a version-specific prefix is another option. Configure with `--prefix=/absolute/prefix --enable-fpm` and the needed database/extension flags; consult that PHP release's build requirements. Old PHP may not compile against modern OpenSSL/ICU without maintenance patches. No automated obsolete-runtime compiler is included.

Registry example (inside complete settings JSON):

```json
{
  "php_versions": {
    "system": {"cli": "/usr/bin/php", "fpm": "/usr/sbin/php-fpm"},
    "7.4": {"cli": "/home/you/.local/share/localforge/runtimes/php/7.4/bin/php", "fpm": "/home/you/.local/share/localforge/runtimes/php/7.4/sbin/php-fpm"},
    "8.1": {"cli": "/home/you/.local/share/localforge/runtimes/php/8.1/bin/php", "fpm": "/home/you/.local/share/localforge/runtimes/php/8.1/sbin/php-fpm"},
    "8.2": {"cli": "/home/you/.local/share/localforge/runtimes/php/8.2/bin/php", "fpm": "/home/you/.local/share/localforge/runtimes/php/8.2/sbin/php-fpm"},
    "8.3": {"cli": "/home/you/.local/share/localforge/runtimes/php/8.3/bin/php", "fpm": "/home/you/.local/share/localforge/runtimes/php/8.3/sbin/php-fpm"}
  },
  "active_php": "8.3"
}
```

This is an illustrative registry fragment, **not** a complete settings file. On Arch the package FPM is typically in `/usr/bin`; use actual discovered paths rather than copying the example blindly.

### Extensions and isolation

For both the CLI shim and FPM, LocalForge sets `PHP_INI_SCAN_DIR` to the selected version's private `conf.d` directory and passes its private `php.ini` with `-c`. It does not reuse `/etc/php-fpm.d` pools or load another runtime's extension configuration.

The initial ini is intentionally minimal. In v0.1.2 the `system` label gets a one-time private `20-localforge-database.ini`: the matching CLI reports its own extension directory under `-n`, and only existing mysqlnd/PDO/MySQLi/pdo_mysql/session modules not already built in are added in dependency order. This never edits system ini files or configures other labels. An existing private defaults file is preserved. The `.database-seeded` marker prevents re-enabling modules after a user intentionally removes the ini. If no matching drivers are installed, or for custom runtime labels, configure extensions **explicitly**, for example:

```ini
; ~/.config/localforge/php/system/php.ini
; Check php -i for the runtime's actual extension_dir first.
extension=pdo_mysql
extension=mysqli
extension=mbstring
```

Some distributions build an extension in; loading it again causes warnings. Fedora modules often have ordered ini dependencies (`mysqlnd` before `mysqli`/`pdo_mysql`, `xml` before related XML modules). Read package-provided `/etc/php.d/*.ini` snippets and copy only the relevant verified directives into the matching **private** version's `conf.d`. Do not bulk copy extension files or ini files between PHP ABIs. `extension_dir` may need an explicit absolute path for custom builds. The GUI's php.ini editor persists across full regeneration; edit `conf.d` using your regular editor.

Inspect the selected CLI:

```bash
~/.local/share/localforge/bin/php --ini
~/.local/share/localforge/bin/php -m
~/.local/share/localforge/bin/php --version
```

There is one global PHP CLI/default web runtime, plus independently supervised FPM instances for project-root `.php-version` overrides. Matching labels use `php-<label>.sock` even when the label equals the global selection; absent or unknown labels use `php.sock`. Each master has a private PID/log/config, and each label retains its own ini/extensions. Global switching preserves pinned project selections. Containers, extension compilation and PHP patch management are outside this release. PHP 7.4 and 8.1 are obsolete for new development by the time of this delivery; even loopback services can be reached by other local users and browser-origin attacks. Prefer maintained runtimes.

## MariaDB

The default datadir is `~/.local/share/localforge/database`. The first initialization uses `mariadb-install-db --no-defaults`, socket authentication and a second privileged account matching your OS username. Never point it at an existing system server's datadir.

After Start database, connect without a TCP root password:

```bash
mariadb --no-defaults --protocol=socket \
  --socket="${XDG_RUNTIME_DIR}/localforge/mysql.sock" --user="$(id -un)"
```

Always find the actual resolved socket in `localforge status` → `paths.mysql_socket`, especially when XDG runtime is absent or too long; do not assume the example runtime directory.

Create a dedicated development account for your app and DBeaver (choose your own password):

```sql
CREATE DATABASE app CHARACTER SET utf8mb4;
CREATE USER 'app'@'127.0.0.1' IDENTIFIED BY 'replace-with-your-own-password';
GRANT ALL PRIVILEGES ON app.* TO 'app'@'127.0.0.1';
```

For bundled Adminer, open `http://localhost:8080/_adminer` (your configured HTTP port), choose server `127.0.0.1:3306` (your configured database port), and use the password-protected application account. Adminer intentionally does not permit passwordless login; do not try to use the OS socket-auth account from PHP. The endpoint is localhost-Host-restricted, uses the global FPM and is not a database credential vault. Matching PHP MySQLi or PDO/MySQL modules are still required.

Connect DBeaver to `127.0.0.1`, the configured database port and this account. The launcher opens installed native DBeaver or its existing Flatpak installation and copies non-secret connection details; it does **not** configure a connection automatically, install JDBC drivers or store database passwords. Flatpak TCP access is simpler than private Unix-socket access. `localhost` may resolve differently from `127.0.0.1`, so use the latter consistently with the example grant.

The database directory is never automatically deleted, upgraded, downgraded or reinitialized. Back up databases before changing engine versions. A failed bootstrap can leave partial data; inspect `database-init.log` and recover manually rather than retrying over existing files.

## MySQL alternative

Not installed by default and not native-integration-tested in this delivery. Stop services, supply a compatible `mysqld` binary, set `database_kind` to `mysql`, change `binaries.database`, and use a **fresh** datadir (archive the entire existing private datadir out of the way after a verified backup). Never open MariaDB data with MySQL or vice versa.

Initialize uses `--initialize-insecure`. **MySQL's initial root password is empty**. Immediately start the local instance and set a root password using its private Unix socket:

```bash
mysql --no-defaults --protocol=socket --socket="${XDG_RUNTIME_DIR}/localforge/mysql.sock" -u root
```

```sql
ALTER USER 'root'@'localhost' IDENTIFIED BY 'your-unique-strong-password';
```

Generated MySQL config disables the secondary X Protocol listener with `mysqlx=OFF`. The same MySQL socket/path caveat applies when `XDG_RUNTIME_DIR` is absent or too long. The software does not automate credential setup; use MariaDB's socket-authentication default if an insecure bootstrap window is unacceptable. MySQL vendors/releases may require different option names and must be validated before use.

## Node.js

One optional foreground Node entry point is managed at a time. Edit settings:

```json
"node": {
  "cwd": "/home/you/www/my-node-app",
  "entry": "server.js",
  "args": []
}
```

You may set `binaries.node` to an absolute version-managed Node executable. `cwd` is the process working directory. The supervisor supplies `PORT`, `HOST=127.0.0.1`, `NODE_ENV=development` and the active PHP shim on PATH. The app **must honor** HOST and PORT; these variables cannot force an arbitrary Node app to bind securely. No `npm install` lifecycle script is run by the manager.

```javascript
const http = require('node:http');
http.createServer((req, res) => res.end('Hello from Node\n'))
  .listen(Number(process.env.PORT), process.env.HOST);
```

Node's direct URL is `http://127.0.0.1:3000`. Automatic PHP/static project vhosts do not reverse-proxy Node. Add an explicit project-specific proxy in your persistent Nginx HTTP include if desired; avoid duplicate `server_name` blocks. Per-project process matrices and framework-specific Vite/HMR launchers are future work.
