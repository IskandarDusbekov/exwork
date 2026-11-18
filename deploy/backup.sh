#!/usr/bin/env bash
# Kunlik zaxira nusxa (cron, narxlash foydalanuvchisi):
#   15 3 * * *  /srv/narxlash/app/deploy/backup.sh >> /srv/narxlash/logs/backup.log 2>&1
# Baza — har kuni alohida fayl (14 kun saqlanadi), olingach tekshiriladi (pg_restore --list).
# Media (yuklangan va natija fayllar) — har kuni alohida «surat» (7 kun): o'zgarmagan fayllar oldingi kunnikiga
# qattiq havola (hard link), shuning uchun joyni deyarli egallamaydi. Bitta nusxa (rsync --delete) bo'lsa,
# fayllar o'chib / buzilib qolsa ertasi kuni nusxadan ham o'chardi — suratlar oldingi kunlarni saqlaydi.
# BACKUP_REMOTE (.env, masalan «backup@10.0.0.5:/data/narxlash») berilsa — zaxira boshqa serverga ham ko'chiriladi
# (ssh kalit bilan, parolsiz). Disk buzilsa shu serverdagi nusxa ham yo'qoladi — boshqa joyda bo'lishi shart.
# U yerda baza nusxalari o'chirilmaydi (bu serverdagi zaxira adashib o'chsa ham saqlanib qolsin) — o'zi
# BACKUP_REMOTE_KEEP_DAYS (standart 60) kundan eskilarini ssh orqali tozalaydi.
# Xato bo'lsa — superfoydalanuvchilarga xat ketadi va panelda ko'rinadi (manage.py backup_check --failed).
set -euo pipefail
umask 077  # zaxirada parol xeshlari va ikki bosqichli kirish kalitlari bor — faqat egasi o'qiydi
ROOT=/srv/narxlash
on_err() {
    echo "$(date '+%F %T') XATO: backup.sh $1-qatorda to'xtadi" >&2
    "$ROOT/app/deploy/manage.sh" backup_check --failed "backup.sh $1-qatorda to'xtadi (logs/backup.log)" || true
}
trap 'on_err $LINENO' ERR
set -a; source "$ROOT/.env"; set +a
source "$ROOT/app/deploy/pgenv.sh"
pg_env "$DATABASE_URL"
DEST="$ROOT/backups"
STAMP=$(date +%Y-%m-%d_%H%M)
DAY=$(date +%Y-%m-%d)
mkdir -p "$DEST/db" "$DEST/media"

DUMP="$DEST/db/narxlash_$STAMP.dump"
pg_dump --format=custom --no-owner --file="$DUMP.part"
pg_restore --list "$DUMP.part" > /dev/null  # fayl butun va o'qiladi (aks holda set -e — xato bilan to'xtaydi)
mv "$DUMP.part" "$DUMP"
find "$DEST/db" -name 'narxlash_*.dump' -mtime +14 -delete

PREV=$(ls -1d "$DEST/media/"20* 2>/dev/null | grep -v "/$DAY\$" | tail -n 1 || true)
rsync -a --delete ${PREV:+--link-dest="$PREV"} "${NARXLASH_MEDIA_ROOT:-$ROOT/media}/" "$DEST/media/$DAY/"
ls -1d "$DEST/media/"20* | head -n -7 | xargs -r rm -rf  # 7 kundan eskilari

if [ -n "${BACKUP_REMOTE:-}" ]; then
    rsync -a "$DEST/db/" "$BACKUP_REMOTE/db/"                   # --delete yo'q: u yerda ko'proq kun saqlanadi
    rsync -a --delete -H "$DEST/media/" "$BACKUP_REMOTE/media/"  # suratlar (havolalar saqlanadi)
    RHOST="${BACKUP_REMOTE%%:*}" RPATH="${BACKUP_REMOTE#*:}"
    ssh -o BatchMode=yes "$RHOST" "find '$RPATH/db' -name 'narxlash_*.dump' -mtime +${BACKUP_REMOTE_KEEP_DAYS:-60} -delete" \
        || echo "!!! boshqa serverdagi eski nusxalar tozalanmadi (ssh) — joyni o'zingiz kuzating"
    REMOTE=" → $BACKUP_REMOTE"
fi

echo "$(date '+%F %T') zaxira tayyor: $(basename "$DUMP") ($(du -h "$DUMP" | cut -f1)), media/$DAY ($(du -sh "$DEST/media/$DAY" | cut -f1))${REMOTE:-}"
# Tiklash:  sudo /srv/narxlash/app/deploy/restore.sh narxlash_<sana>.dump   (README → «Zaxiradan tiklash»)
