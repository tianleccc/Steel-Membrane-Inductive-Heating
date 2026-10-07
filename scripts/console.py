#!/usr/bin/env python3
"""Open the existing local service in a dedicated desktop application window."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


def main():
    config = json.loads((ROOT / 'config.example.json').read_text())
    if (ROOT / 'config.json').exists():
        config.update(json.loads((ROOT / 'config.json').read_text()))
    url = f"http://127.0.0.1:{int(config['port'])}/"
    browser = shutil.which('chromium') or shutil.which('chromium-browser')
    if not browser:
        raise RuntimeError('Chromium is missing. Run: sudo apt-get install chromium')
    # Ignore proxy settings: the instrument is always on this machine.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(30):
        try:
            with opener.open(url + 'api/status', timeout=1) as response:
                json.load(response)
            break
        except (OSError, ValueError):
            time.sleep(1)
    else:
        raise RuntimeError('The local instrument service is unavailable. '
                           'Run: sudo systemctl start steel-membrane')
    profile = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'steel-membrane-console'
    os.execv(browser, [browser, '--app=' + url, '--start-maximized',
                      '--user-data-dir=' + str(profile), '--no-first-run',
                      '--no-default-browser-check', '--no-proxy-server'])


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        message = str(error)
        print(message, file=sys.stderr)
        if shutil.which('zenity'):
            subprocess.run(['zenity', '--error', '--title=Steel Membrane Console', '--text=' + message])
        elif shutil.which('xmessage'):
            subprocess.run(['xmessage', '-center', message])
        elif shutil.which('notify-send'):
            subprocess.run(['notify-send', 'Steel Membrane Console', message])
        sys.exit(1)
