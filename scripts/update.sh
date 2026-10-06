#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -n $(git status --porcelain --untracked-files=normal) ]]; then
  echo 'Working tree has local changes. Commit or review them before updating.' >&2
  exit 1
fi
git fetch origin main
if [[ $(git rev-list --count origin/main..HEAD) != 0 ]]; then
  echo 'Local commits are not on origin/main. Review before updating.' >&2
  exit 1
fi
sudo systemctl stop steel-membrane.service
git merge --ff-only origin/main
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
sudo systemctl start steel-membrane.service
sudo systemctl --no-pager status steel-membrane.service
