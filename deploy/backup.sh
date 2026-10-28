#!/usr/bin/env bash
# Kunlik zaxira nusxa (cron, narxlash foydalanuvchisi):
#   15 3 * * *  /srv/narxlash/app/deploy/backup.sh >> /srv/narxlash/logs/backup.log 2>&1
# Baza — har kuni alohida fayl (14 kun saqlanadi); media (yuklangan va natija fayllar) — nusxasi yangilanadi.
# MUHIM: backups/ papkasini vaqti-vaqti bilan BOSHQA joyga (boshqa server, bulut) ko'chiring —
# disk buzilsa, shu serverdagi nusxa ham yo'qoladi.
set -euo pipefail
ROOT=/srv/narxlash
set -a; source "$ROOT/.env"; set +a
DEST="$ROOT/backups"
STAMP=$(date +%Y-%m-%d_%H%M)
mkdir -p "$DEST/db" "$DEST/media"

pg_dump --format=custom --no-owner --file="$DEST/db/narxlash_$STAMP.dump" "$DATABASE_URL"
find "$DEST/db" -name 'narxlash_*.dump' -mtime +14 -delete

rsync -a --delete "${NARXLASH_MEDIA_ROOT:-$ROOT/media}/" "$DEST/media/"

echo "$(date '+%F %T') zaxira tayyor: db/narxlash_$STAMP.dump, media/ ($(du -sh "$DEST/media" | cut -f1))"
# Tiklash:  pg_restore --clean --no-owner -d "$DATABASE_URL" backups/db/narxlash_<sana>.dump
#           rsync -a backups/media/ /srv/narxlash/media/
