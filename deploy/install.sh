#!/bin/sh
# Installeert p1meter als systemd-service op een Raspberry Pi.
# Uitvoeren vanuit de map van de repository:  sh deploy/install.sh
set -eu

DIR=$(cd "$(dirname "$0")/.." && pwd)
RUN_AS=${SUDO_USER:-$(id -un)}

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    echo "Python 3.11 of nieuwer is nodig (Raspberry Pi OS Bookworm of nieuwer)." >&2
    exit 1
fi

cd "$DIR"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --quiet pyserial
[ -f config.toml ] || cp config.example.toml config.toml

# Toegang tot de seriële poort
sudo usermod -a -G dialout "$RUN_AS"

sed -e "s|__DIR__|$DIR|g" -e "s|__USER__|$RUN_AS|g" deploy/p1meter.service |
    sudo tee /etc/systemd/system/p1meter.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now p1meter.service

PORT=$(sed -n 's/^port *= *\([0-9]*\).*/\1/p' config.toml)
echo "Klaar. Dashboard: http://$(hostname -I | cut -d' ' -f1):${PORT:-8080}"
echo "Logboek bekijken: journalctl -u p1meter -f"
