#!/usr/bin/env bash
# Kunlik zaxira nusxa (cron, narxlash foydalanuvchisi):
#   15 3 * * *  /srv/narxlash/app/deploy/backup.sh >> /srv/narxlash/logs/backup.log 2>&1
# Baza — har kuni alohida fayl (14 kun saqlanadi), olingach tekshiriladi (pg_restore --list).
# Media (yuklangan va natija fayllar) — har kuni alohida «surat» (7 kun): o'zgarmagan fayllar oldingi kunnikiga
# qattiq havola (hard link), shuning uchun joyni deyarli egallamaydi. Bitta nusxa (rsync --delete) bo'lsa,
# fayllar o'chib / buzilib qolsa ertasi kuni nusxadan ham o'chardi — suratlar oldingi kunlarni saqlaydi.
# BACKUP_REMOTE (.env, masalan «backup@10.0.0.5:/data/narxlash») berilsa — zaxira boshqa serverga ham ko'chiriladi
# (ssh kalit bilan, parolsiz). Disk buzilsa shu serverdagi nusxa ham yo'qoladi — boshqa joyda bo'lishi shart.
set -euo pipefail
umask 077  # zaxirada parol xeshlari va ikki bosqichli kirish kalitlari bor — faqat egasi o'qiydi
ROOT=/srv/narxlash
set -a; source "$ROOT/.env"; set +a
DEST="$ROOT/backups"
STAMP=$(date +%Y-%m-%d_%H%M)
DAY=$(date +%Y-%m-%d)
mkdir -p "$DEST/db" "$DEST/media"

DUMP="$DEST/db/narxlash_$STAMP.dump"
pg_dump --format=custom --no-owner --file="$DUMP" "$DATABASE_URL"
pg_restore --list "$DUMP" > /dev/null  # fayl butun va o'qiladi (aks holda set -e — xato bilan to'xtaydi)
find "$DEST/db" -name 'narxlash_*.dump' -mtime +14 -delete

PREV=$(ls -1d "$DEST/media/"20* 2>/dev/null | grep -v "/$DAY\$" | tail -n 1 || true)
rsync -a --delete ${PREV:+--link-dest="$PREV"} "${NARXLASH_MEDIA_ROOT:-$ROOT/media}/" "$DEST/media/$DAY/"
ls -1d "$DEST/media/"20* | head -n -7 | xargs -r rm -rf  # 7 kundan eskilari

if [ -n "${BACKUP_REMOTE:-}" ]; then
    rsync -a --delete -H "$DEST/" "$BACKUP_REMOTE/"
    REMOTE=" → $BACKUP_REMOTE"
fi

echo "$(date '+%F %T') zaxira tayyor: $(basename "$DUMP") ($(du -h "$DUMP" | cut -f1)), media/$DAY ($(du -sh "$DEST/media/$DAY" | cut -f1))${REMOTE:-}"
# Tiklash:  pg_restore --clean --no-owner -d "$DATABASE_URL" backups/db/narxlash_<sana>.dump
#           rsync -a backups/media/<kun>/ /srv/narxlash/media/
