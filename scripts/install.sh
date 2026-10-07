#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $(id -u) == 0 ]]; then
  echo 'Run as your normal Pi user, not sudo. This script requests sudo where needed.' >&2
  exit 1
fi
sudo apt-get update
sudo apt-get install -y git python3-venv python3-picamera2 python3-gpiozero python3-lgpio i2c-tools
sudo raspi-config nonint do_i2c 0
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
if [[ ! -f config.json ]]; then cp config.example.json config.json; fi
mkdir -p data
sudo usermod -aG gpio,i2c,video,render "$(id -un)"
SCRIPT_ROOT=$(pwd)
PANEL_USER=$(id -un)
sed -e "s|@ROOT@|$SCRIPT_ROOT|g" -e "s|@USER@|$PANEL_USER|g" deploy/steel-membrane.service.in | sudo tee /etc/systemd/system/steel-membrane.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now steel-membrane.service
if command -v chromium >/dev/null || command -v chromium-browser >/dev/null; then
  bash scripts/install-desktop.sh
fi
echo 'Installed. Open http://<Pi-IP>:8080 . Review config.json before enabling heating.'
