#!/usr/bin/env bash
# Zaxiradan tiklash (serverda, root):
#   sudo /srv/narxlash/app/deploy/restore.sh narxlash_2026-11-18_0315.dump            # baza + o'sha kungi media
#   sudo /srv/narxlash/app/deploy/restore.sh narxlash_2026-11-18_0315.dump 2026-11-17 # media boshqa kundan
#   sudo /srv/narxlash/app/deploy/restore.sh narxlash_2026-11-18_0315.dump --no-media # faqat baza
# Qo'shimcha: --yes — tasdiq so'ramaydi.
#
# Tartib: nusxa tekshiriladi → sayt va worker to'xtaydi → HOZIRGI baza nusxasi olinadi (adashsangiz qaytarish uchun)
# → baza tozalanib, nusxa bitta tranzaksiyada yoziladi (xato bo'lsa — hozirgi holat qaytariladi) → hozirgi media
# qattiq havola bilan saqlanadi (joy olmaydi) → media surati ko'chiriladi → migrate → xizmatlar yonadi.
set -euo pipefail
ROOT=/srv/narxlash
APP="$ROOT/app"
DEST="$ROOT/backups"
die() { echo "XATO: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die "root bilan ishga tushiring: sudo $0 $*"

DUMP="" DAY="" NOMEDIA=0 YES=0
for a in "$@"; do
    case "$a" in
        --yes) YES=1 ;;
        --no-media) NOMEDIA=1 ;;
        20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]) DAY="$a" ;;
        *) DUMP="$a" ;;
    esac
done
[ -n "$DUMP" ] || { sed -n '2,6p' "$0"; echo; echo "Nusxalar:"; ls -1t "$DEST/db/" | head -20; exit 1; }
[ -f "$DUMP" ] || DUMP="$DEST/db/$DUMP"
[ -f "$DUMP" ] || die "fayl topilmadi: $DUMP"

set -a; source "$ROOT/.env"; set +a
source "$APP/deploy/pgenv.sh"
pg_env "$DATABASE_URL"
MEDIA="${NARXLASH_MEDIA_ROOT:-$ROOT/media}"

[ -n "$DAY" ] || DAY=$(basename "$DUMP" | sed -n 's/^narxlash_\([0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}\)_.*/\1/p')
MEDIA_SRC=""
if [ "$NOMEDIA" = 0 ]; then
    if [ -n "$DAY" ] && [ -d "$DEST/media/$DAY" ]; then
        MEDIA_SRC="$DEST/media/$DAY"
    else
        echo "!!! media surati topilmadi ($DEST/media/${DAY:-?}) — faqat baza tiklanadi. Bor suratlar:"
        ls -1 "$DEST/media/" 2>/dev/null | grep '^20' || echo "    (yo'q)"
    fi
fi

echo "Nusxa tekshirilmoqda…"
pg_restore --list "$DUMP" > /dev/null || die "nusxa buzuq yoki o'qilmaydi: $DUMP"
echo
echo "  Baza:   $PGDATABASE ($PGHOST:$PGPORT)  ←  $(basename "$DUMP") ($(du -h "$DUMP" | cut -f1))"
if [ -n "$MEDIA_SRC" ]; then echo "  Media:  $MEDIA_SRC  →  $MEDIA"; else echo "  Media:  o'zgarmaydi"; fi
echo "  Sayt va worker shu vaqtda to'xtaydi. Hozirgi baza va media oldin saqlab qo'yiladi."
if [ "$YES" = 0 ]; then
    read -r -p "Davom etish uchun «ha» deb yozing: " ok
    [ "$ok" = "ha" ] || die "bekor qilindi"
fi

STAMP=$(date +%Y-%m-%d_%H%M%S)
systemctl stop narxlash-web narxlash-worker
started=1
on_exit() { [ "${started:-0}" = 1 ] && { echo "Xizmatlar qayta yoqilmoqda…"; systemctl start narxlash-web narxlash-worker || true; }; }
trap on_exit EXIT

SAFE="$DEST/db/narxlash_$STAMP.dump"
echo "1/5 Hozirgi baza saqlanmoqda: $SAFE"
( umask 077; pg_dump --format=custom --no-owner --file="$SAFE.part" )
mv "$SAFE.part" "$SAFE"
chown narxlash:narxlash "$SAFE"

echo "2/5 Baza tiklanmoqda (bitta tranzaksiya)…"
restore_db() {
    psql -q -v ON_ERROR_STOP=1 -c "$PG_CLEAN_SQL" &&
    pg_restore --no-owner --no-privileges --exit-on-error --single-transaction -d "$PGDATABASE" "$1"
}
if ! restore_db "$DUMP"; then
    echo "!!! Tiklash xato bilan to'xtadi — hozirgi baza qaytarilmoqda ($SAFE)…" >&2
    restore_db "$SAFE" || die "hozirgi holatni ham qaytarib bo'lmadi! Qo'lda: $0 $SAFE"
    die "nusxa tiklanmadi, baza avvalgi holatida"
fi

if [ -n "$MEDIA_SRC" ]; then
    KEEP="$DEST/pre-restore/$STAMP"
    echo "3/5 Hozirgi media saqlanmoqda (qattiq havola): $KEEP"
    sudo -u narxlash mkdir -p "$KEEP"
    sudo -u narxlash cp -al "$MEDIA/." "$KEEP/" 2>/dev/null \
        || { echo "    (havola bo'lmadi — boshqa disk; to'liq nusxa olinmoqda)"; sudo -u narxlash cp -a "$MEDIA/." "$KEEP/"; }
    echo "4/5 Media ko'chirilmoqda: $MEDIA_SRC"
    sudo -u narxlash rsync -a "$MEDIA_SRC/" "$MEDIA/"
else
    echo "3/5, 4/5 Media o'zgarmadi"
fi

echo "5/5 Migratsiya…"
sudo -u narxlash "$APP/deploy/manage.sh" migrate --noinput

trap - EXIT
systemctl start narxlash-web narxlash-worker
systemctl --no-pager --lines=0 status narxlash-web narxlash-worker || true
cat <<EOF

Tayyor. Tekshiring: saytga kiring, eski smeta faylini oching, Panel → Хатолар.
Adashib tiklangan bo'lsa — avvalgi holatga qaytarish:
    sudo $0 $(basename "$SAFE") --no-media${MEDIA_SRC:+
    sudo -u narxlash rsync -a --delete $KEEP/ $MEDIA/}
${MEDIA_SRC:+Hammasi joyida bo'lsa, bir necha kundan keyin o'chiring: sudo rm -rf $KEEP}
EOF
