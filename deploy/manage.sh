#!/usr/bin/env bash
# .env ni yuklab manage.py ni ishga tushiradi:  sudo -u narxlash /srv/narxlash/app/deploy/manage.sh migrate
set -euo pipefail
ROOT=/srv/narxlash
set -a; source "$ROOT/.env"; set +a
cd "$ROOT/app"
exec "$ROOT/venv/bin/python" manage.py "$@"
