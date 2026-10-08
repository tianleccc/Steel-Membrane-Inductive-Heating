#!/usr/bin/env bash
set -euo pipefail
if [[ $(id -u) != 0 ]]; then
  echo 'Run with sudo to configure the GPIO5/6 sensor bus.' >&2
  exit 1
fi
python3 - <<'PY'
from pathlib import Path
from datetime import datetime, timezone
import shutil
path = Path('/boot/firmware/config.txt')
text = path.read_text()
line = 'dtoverlay=i2c-gpio,bus=20,i2c_gpio_sda=5,i2c_gpio_scl=6,i2c_gpio_delay_us=5'
if line not in text.splitlines():
    conflicts = [s for s in text.splitlines() if s.strip().startswith('dtoverlay=i2c-gpio')]
    if conflicts:
        raise SystemExit('Existing software I2C overlay found; review it before adding another.')
    backup = path.with_name('config.txt.before-sensor-bus-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    shutil.copy2(path, backup)
    path.write_text(text.rstrip()+'\n\n[all]\n# Steel Membrane MLX90614: SDA GPIO5, SCL GPIO6\n'+line+'\n')
    print('Backup:', backup)
print('Sensor bus configured: SDA GPIO5 / SCL GPIO6 / bus 20. Reboot to activate.')
PY
