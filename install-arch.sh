#!/usr/bin/env bash
set -euo pipefail
if (( EUID == 0 )); then echo "Run as your desktop user, not root (sudo is used only for packages/helper)." >&2; exit 1; fi
command -v pacman >/dev/null || { echo "This installer requires Arch Linux/pacman." >&2; exit 1; }
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# A full upgrade avoids unsupported Arch partial-upgrade dependency combinations.
sudo pacman -Syu --needed python python-pip nginx apache php php-fpm mariadb redis nodejs npm iproute2 polkit xcb-util-cursor libxkbcommon-x11 mesa
source "$ROOT/scripts/install-common.sh"
