#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $(id -u) == 0 ]]; then
  echo 'Run as the desktop user, without sudo.' >&2
  exit 1
fi
if ! command -v chromium >/dev/null && ! command -v chromium-browser >/dev/null; then
  echo 'Install Chromium first: sudo apt-get install chromium' >&2
  exit 1
fi
python3 - <<'PY'
import os
from pathlib import Path
import shutil
import subprocess

root = Path.cwd()
applications = Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))/'applications'
desktop = Path.home()/'Desktop'
if shutil.which('xdg-user-dir'):
    result = subprocess.check_output(['xdg-user-dir', 'DESKTOP'], text=True).strip()
    if result:
        desktop = Path(result)
# Desktop Entry Exec has its own quoting rules, separate from shell quoting.
def quote(value):
    value = str(value).replace('%', '%%')
    for char in ('\\', '"', '`', '$'):
        value = value.replace(char, '\\'+char)
    return '"'+value.replace('\\', '\\\\')+'"'

entry = '\n'.join([
    '[Desktop Entry]', 'Version=1.0', 'Type=Application',
    'Name=Steel Membrane Console',
    'Comment=Local offline camera, heating and assay control',
    'Exec=/usr/bin/python3 '+quote(root/'scripts/console.py'),
    'Icon='+str(root/'deploy/steel-membrane.svg'),
    'Terminal=false', 'Categories=Science;Education;',
    'StartupNotify=true', 'StartupWMClass=steel-membrane-console', '',
])
for directory in (applications, desktop):
    directory.mkdir(parents=True, exist_ok=True)
    target = directory/'steel-membrane-console.desktop'
    target.write_text(entry, encoding='utf-8')
    target.chmod(0o755)
    if shutil.which('desktop-file-validate'):
        subprocess.run(['desktop-file-validate', str(target)], check=True)
    if shutil.which('gio'):
        subprocess.run(['gio', 'set', str(target), 'metadata::trusted', 'true'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print('Installed:', target)
PY
echo 'Open Steel Membrane Console from the desktop or application menu. No network is required.'
