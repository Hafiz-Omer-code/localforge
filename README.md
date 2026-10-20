# LocalForge

> **Fast, isolated, and native local web development environment for Linux.**  
> An authentic desktop alternative to Windows Laragon, tailored specifically for **Arch Linux** and **Fedora**.

[![License: MIT](https://img.shields.io/badge/License-MIT-emerald.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Arch%20%7C%20Fedora%20%7C%20Linux-blue.svg)]()
[![Python](https://img.shields.io/badge/Python-3.10%2B-informational.svg)]()
[![Qt](https://img.shields.io/badge/GUI-PySide6%20%2F%20Qt6-green.svg)]()

LocalForge delivers the speed and simplicity of Laragon to Linux developers without the resource tax of Docker, the bloat of heavy virtual machines, or the friction of polluting global system directories (`/etc`).

---

## ⚡ Highlights

- **Desktop GUI & Tray Controller:** Native PySide6 interface featuring one-click *Start All*, *Stop All*, and *Reload All*, complete with background system tray minimization.
- **Port Conflict Protection:** All web servers and caches default to unprivileged, userland ports (`8080`, `3306`, `6379`) with pre-flight live bind checks (`SO_REUSEADDR`) and `ss -ltnp` inspection.
- **Instant `.test` Virtual Hosts:** Subdirectories in `~/www/` are automatically discovered within 5 seconds; vhosts with `public/` fallback routes are generated and reloaded on the fly.
- **Scoped Polkit `/etc/hosts` Sync:** Safely appends and removes local `.test` domains inside an isolated user-delimited block via an unprivileged Polkit helper.
- **Multi-Version PHP Engine:** Switch the active workspace PHP or specify a `.php-version` file per project to route through independent PHP-FPM socket pools simultaneously.
- **Integrated Adminer Web DB:** Bundled single-file database manager available instantly at `http://localhost:8080/_adminer` with zero external dependencies.
- **Pure Native Execution:** Runs actual distro-native binaries directly as user-owned foreground processes with separate XDG config, data, and log paths.

---

## 🛠 System Architecture

```text
Desktop Session (User Space)
 ├── PySide6 Native GUI ──┐
 ├── LocalForge CLI ──────┤──> AF_UNIX Stream Socket (/run/user/<UID>/localforge/manager.sock)
                          │    ├── Nginx / Apache (Foreground, unprivileged ports)
                          │    ├── Switchable PHP-FPM pools (~/.local/share/localforge/runtimes/php/)
                          │    ├── MariaDB / MySQL (Private socket & datadir under ~/.local/share/)
                          │    ├── Redis / Valkey (Loopback cache daemon)
                          │    └── Adminer DB Web GUI (Mapped at /_adminer)
                          │
                          └──> Polkit Action (pkexec /usr/lib/localforge/hosts-helper)
                               └── Atomically synchronizes user-scoped block in /etc/hosts
