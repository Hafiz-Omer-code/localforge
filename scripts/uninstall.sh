#!/usr/bin/env bash
set -euo pipefail
if (( EUID == 0 )); then echo 'Run as your normal desktop user.' >&2; exit 1; fi
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/localforge"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
if [[ -x "$DATA/venv/bin/localforge" ]]; then "$DATA/venv/bin/localforge" shutdown || true; fi
systemctl --user disable --now localforge.service 2>/dev/null || true
rm -f "$CONFIG/systemd/user/localforge.service" "$HOME/.local/bin/localforge" "$HOME/.local/bin/localforge-gui" "${XDG_DATA_HOME:-$HOME/.local/share}/applications/localforge.desktop" "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/localforge.svg"
systemctl --user daemon-reload 2>/dev/null || true
printf 'Application registrations removed. Data, runtimes, venv, configs and logs are preserved at:\n%s\n' "$DATA"
echo 'Before uninstalling, use Sync hosts with an empty project list to remove your hosts block.'
echo 'The machine-wide root helper is shared by all users and was NOT removed. An administrator may remove /usr/lib/localforge and /usr/share/polkit-1/actions/io.localforge.hosts.policy after all users uninstall.'
