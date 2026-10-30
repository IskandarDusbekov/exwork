#!/usr/bin/env bash
# Yangi versiyani qo'yish (serverda, root):  sudo /srv/narxlash/app/deploy/update.sh
# git pull → kutubxonalar → migratsiya → static → sayt va worker qayta ishga tushadi.
set -euo pipefail
ROOT=/srv/narxlash
cd "$ROOT/app"
sudo -u narxlash git pull --ff-only
sudo -u narxlash "$ROOT/venv/bin/pip" install -q -r requirements.txt
sudo -u narxlash "$ROOT/app/deploy/manage.sh" migrate --noinput
sudo -u narxlash "$ROOT/app/deploy/manage.sh" collectstatic --noinput
# nginx (www-data) static'ni o'qiy olsin: /srv/narxlash ga faqat o'tish huquqi (ro'yxat emas), static — o'qish
chmod o+x "$ROOT"
chmod -R o+rX "$ROOT/static"
# tekshiruv faqat xabar beradi: xato bo'lsa ham (masalan, email hali sozlanmagan) yangi kod ishga tushadi
sudo -u narxlash "$ROOT/app/deploy/manage.sh" check --deploy || echo "!!! check --deploy muammo topdi — yuqoriga qarang"
systemctl restart narxlash-web
systemctl restart narxlash-worker   # boshlangan ish tugatilib, keyin qayta yonadi
systemctl --no-pager --lines=0 status narxlash-web narxlash-worker
