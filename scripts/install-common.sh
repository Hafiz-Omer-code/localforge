#!/usr/bin/env bash
# Sourced by install-{arch,fedora}.sh after distro package resolution.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/localforge"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/localforge"
mkdir -p "$DATA" "$STATE" "$HOME/.local/bin" "$CONFIG/systemd/user" "${XDG_DATA_HOME:-$HOME/.local/share}/applications" "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
chmod 700 "$DATA" "$STATE"
# Stop the old controller before upgrading its code (do not touch system services).
if systemctl --user is-active --quiet localforge.service 2>/dev/null; then
    systemctl --user stop localforge.service
elif [[ -x "$DATA/venv/bin/localforge" ]]; then
    echo "If a standalone controller is running, stop it before upgrading."
    mapfile -t RUNTIME_SOCKETS < <(python3 - <<'PY_SOCKET'
import os
from pathlib import Path
state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "localforge"
runtime = Path(os.environ.get("XDG_RUNTIME_DIR", state / "run")) / "localforge"
# Check legacy v0.1.1 and v0.1.2 locations before replacing installed code.
print(runtime / "manager.sock")
print(Path("/tmp") / f"lf-{os.getuid()}" / "manager.sock")
PY_SOCKET
)
    for RUNTIME_SOCKET in "${RUNTIME_SOCKETS[@]}"; do
        if [[ -S "$RUNTIME_SOCKET" ]]; then
            echo "Standalone controller detected at $RUNTIME_SOCKET. Run the installed localforge shutdown, then retry installation." >&2
            exit 1
        fi
    done
fi
python3 -m venv "$DATA/venv"
"$DATA/venv/bin/python" -m pip install --upgrade pip
"$DATA/venv/bin/python" -m pip install --upgrade "$ROOT"
ln -sfn "$DATA/venv/bin/localforge" "$HOME/.local/bin/localforge"
ln -sfn "$DATA/venv/bin/localforge-gui" "$HOME/.local/bin/localforge-gui"
install -m 644 "$ROOT/packaging/localforge.svg" "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/localforge.svg"
# This is the ONLY privileged component. GUI/controller code is never run with sudo.
sudo install -d -o root -g root -m 755 /usr/lib/localforge
sudo install -o root -g root -m 755 "$ROOT/packaging/hosts-helper" /usr/lib/localforge/hosts-helper
sudo install -o root -g root -m 644 "$ROOT/packaging/io.localforge.hosts.policy" /usr/share/polkit-1/actions/io.localforge.hosts.policy
# Restore policy defaults after copying user-staged files. Do not disable SELinux.
# Include sbin in discovery for desktops whose normal PATH omits it.
RESTORECON="$(PATH="$PATH:/usr/sbin:/sbin" command -v restorecon || true)"
if [[ -n "$RESTORECON" ]]; then
    sudo "$RESTORECON" -Rv /usr/lib/localforge /usr/share/polkit-1/actions
fi
export LOCALFORGE_INSTALL_DATA="$DATA" LOCALFORGE_INSTALL_CONFIG="$CONFIG"
python3 - <<'PY'
import os
from pathlib import Path
p = Path(os.environ['LOCALFORGE_INSTALL_DATA'])
c = Path(os.environ['LOCALFORGE_INSTALL_CONFIG'])
# systemd unit escaping, not shell quoting. % specifiers must also be escaped.
def unit_quote(x):
    return '"' + str(x).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
unit = f'''[Unit]
Description=LocalForge user development service controller
After=default.target

[Service]
Type=simple
ExecStart={unit_quote(p / 'venv/bin/python')} -m localforge.daemon
Restart=on-failure
RestartSec=3
TimeoutStopSec=100
KillMode=control-group
UMask=0077
NoNewPrivileges=yes

[Install]
WantedBy=default.target
'''
(c / 'systemd/user/localforge.service').write_text(unit)
# Desktop Exec syntax differs from systemd: % also needs doubling.
exe = str(p / 'venv/bin/localforge-gui').replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
entry = f'''[Desktop Entry]
Type=Application
Name=LocalForge
Comment=User-owned local web development environments
Exec="{exe}"
Icon=localforge
Terminal=false
Categories=Development;WebDevelopment;
StartupNotify=true
'''
(p.parent / 'applications/localforge.desktop').write_text(entry)
PY
if systemctl --user daemon-reload 2>/dev/null; then
    systemctl --user enable --now localforge.service
else
    echo "No systemd user bus. The GUI will start a standalone controller."
fi
command -v update-desktop-database >/dev/null && update-desktop-database "${XDG_DATA_HOME:-$HOME/.local/share}/applications" || true
printf '\nInstalled LocalForge. Launch from your application menu or:\n  %s\n\nOptional CLI PHP shim (does NOT replace /usr/bin/php):\n  export PATH="%s/bin:$PATH"\n\nNext: initialize the private database, scan ~/www, sync hosts, then Start all.\n' "$HOME/.local/bin/localforge-gui" "$DATA"
echo "Do not enable package-provided system services for this workspace. If ports 3306/6379 are already occupied, change LocalForge ports."
echo "PHP extensions are opt-in per runtime; see docs/RUNTIMES.md. DBeaver is an optional external dependency."
