#!/usr/bin/env bash
set -euo pipefail
if (( EUID == 0 )); then echo "Run as your desktop user, not root (sudo is used only for packages/helper)." >&2; exit 1; fi
command -v dnf >/dev/null || { echo "This installer requires Fedora/dnf." >&2; exit 1; }
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Current Fedora releases may provide valkey rather than Redis. The compatible binary
# is selected below; this does not enable or stop any distribution system service.
CACHE_PACKAGE=redis
if ! dnf info redis >/dev/null 2>&1; then CACHE_PACKAGE=valkey; fi
sudo dnf install python3 python3-pip nginx httpd php-cli php-fpm php-mysqlnd php-mbstring php-xml php-opcache mariadb-server "$CACHE_PACKAGE" nodejs npm iproute polkit xcb-util-cursor libxkbcommon-x11 mesa-libGL
source "$ROOT/scripts/install-common.sh"
if ! command -v redis-server >/dev/null && command -v valkey-server >/dev/null; then
    "$DATA/venv/bin/python" - <<'PY'
import json
import shutil
from pathlib import Path
from localforge.client import call, ensure_daemon
ensure_daemon()
c = call('snapshot')['settings']
c['binaries']['redis'] = shutil.which('valkey-server')
call('apply', settings=c)
print('Configured Valkey as the Redis-compatible cache engine.')
PY
fi
