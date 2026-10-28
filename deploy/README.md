# Narxlash — serverga qo'yish (Ubuntu 22.04 / 24.04)

Tuzilma (hamma narsa `/srv/narxlash` ichida):

```
/srv/narxlash/
├── app/        ← git repozitoriy (shu kod)
├── venv/       ← Python muhiti
├── .env        ← sozlamalar va parollar (deploy/.env.example dan)
├── media/      ← yuklangan smetalar va natijalar (zaxiralanadi!)
├── static/     ← CSS va admin fayllari (collectstatic)
├── logs/       ← narxlash.log (panelda ham: Панел → Хатолар)
└── backups/    ← kunlik zaxira (boshqa joyga ham ko'chiring)
```

Ishlaydigan qismlar:

| Xizmat | Nima qiladi |
|---|---|
| `nginx` | HTTPS, katta fayl yuklash, `/static/` |
| `narxlash-web` | Sayt (gunicorn, 127.0.0.1:8000) |
| `narxlash-worker` | Fon ishi: papka ochish va narxlash |
| PostgreSQL | Baza |
| Redis | Umumiy kesh (IP bloklar, onlayn holat) |

---

## 1. Tizim paketlari

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-dev build-essential git nginx rsync \
    postgresql-client libreoffice-calc-nogui 7zip unrar certbot python3-certbot-nginx
```

- `libreoffice-calc-nogui` — `.xls` fayllarni `.xlsx` ga o'girish uchun (majburiy).
- `7zip` / `unrar` — RAR va 7z arxivlarni ochish uchun. `unrar` Ubuntu'da `multiverse` omborida; topilmasa, faqat `7zip` yetadi.
- Ubuntu 22.04 da `7zip` paketi bo'lmasa, o'rniga `p7zip-full` o'rnating.

## 2. Foydalanuvchi va kod

```bash
sudo adduser --system --group --home /srv/narxlash narxlash
sudo -u narxlash git clone <repo-manzili> /srv/narxlash/app
sudo -u narxlash python3 -m venv /srv/narxlash/venv
sudo -u narxlash /srv/narxlash/venv/bin/pip install -r /srv/narxlash/app/requirements.txt
sudo -u narxlash mkdir -p /srv/narxlash/{media,static,logs,backups}
```

## 3. PostgreSQL va Redis

**Docker bilan** (tavsiya — `deploy/docker-compose.yml`):

```bash
sudo apt install -y docker.io docker-compose-v2
cd /srv/narxlash/app/deploy
sudo docker compose --env-file /srv/narxlash/.env up -d
```

Portlar faqat `127.0.0.1` da ochiq, tashqaridan ulanib bo'lmaydi.

## 4. Sozlamalar (`.env`)

```bash
sudo -u narxlash cp /srv/narxlash/app/deploy/.env.example /srv/narxlash/.env
sudo -u narxlash nano /srv/narxlash/.env
sudo chmod 600 /srv/narxlash/.env
```

`<…>` belgili hamma joyni to'ldiring. `DJANGO_SECRET_KEY` uchun:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(60))"
```

> `.env` 3-qadamdagi docker'dan **oldin** tayyor bo'lishi kerak: `POSTGRES_PASSWORD` va `REDIS_PASSWORD` shu yerdan olinadi.

## 5. Baza, static va birinchi admin

```bash
cd /srv/narxlash/app
sudo -u narxlash deploy/manage.sh migrate
sudo -u narxlash deploy/manage.sh collectstatic --noinput
sudo -u narxlash deploy/manage.sh createsuperuser
sudo -u narxlash deploy/manage.sh check --deploy
```

`check --deploy` faqat haqiqatan ham hal qilinmagan narsalar haqida ogohlantirishi kerak. Masalan:
- `narxlash.W005` — email sozlanmagan;
- `security.W021` — HSTS preload (ixtiyoriy).

**Kompyuterdagi ma'lumotni ko'chirish** (ixtiyoriy):

1. Kompyuterdagi `db.sqlite3` ni serverga `/srv/narxlash/app/db.sqlite3` qilib nusxalang.
2. `media/` papkani `/srv/narxlash/media/` ga nusxalang.
3. Ko'chiring:

   ```bash
   sudo -u narxlash deploy/manage.sh copy_to_postgres
   ```

4. Tekshirib bo'lgach, `db.sqlite3` ni serverdan o'chiring.

## 6. Xizmatlar (systemd)

```bash
sudo cp /srv/narxlash/app/deploy/systemd/narxlash-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now narxlash-web narxlash-worker
sudo systemctl status narxlash-web narxlash-worker
```

## 7. nginx va HTTPS

```bash
sudo cp /srv/narxlash/app/deploy/nginx/narxlash.conf /etc/nginx/sites-available/narxlash
sudo nano /etc/nginx/sites-available/narxlash        # narxlash.uz → o'z domeningiz
sudo ln -s ../sites-available/narxlash /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo certbot --nginx -d narxlash.uz                   # sertifikat va 443 sozlamasi
sudo nginx -t && sudo systemctl reload nginx
```

> **Muhim:** saytga faqat HTTPS orqali kirish mumkin — production'da cookie'lar «faqat HTTPS» rejimida. Sertifikat olinmaguncha kirish ishlamaydi.

## 8. Kunlik zaxira

```bash
sudo crontab -u narxlash -e
```

Ochilgan faylga quyidagi qatorni qo'shing:

```
15 3 * * *  /srv/narxlash/app/deploy/backup.sh >> /srv/narxlash/logs/backup.log 2>&1
```

Har kuni soat 03:15 da baza (`backups/db/`, 14 kun saqlanadi) va `media/` nusxasi olinadi.

> `backups/` ni haftada kamida bir marta **boshqa joyga** ko'chiring (boshqa server, bulut, tashqi disk).

## 9. Tekshirish

1. Saytni oching, admin bilan kiring.
2. **Панел → Хатолар → «Синов хатоси»**. Yozuv chiqsa, logging ishlayapti.
3. Kichik papka yuklab, «Ҳисоблашни бошлаш»ni bosing. Worker uni olishi kerak (Панел → Жараёнлар).
4. «Паролни унутдингизми?» orqali o'zingizga xat yuboring. Email ishlayotganini tekshirasiz.

## Yangi versiyani qo'yish

```bash
sudo /srv/narxlash/app/deploy/update.sh
```

Skript ketma-ket bajaradi:
1. `git pull`;
2. kutubxonalarni o'rnatish;
3. `migrate`;
4. `collectstatic`;
5. sayt va worker'ni qayta ishga tushirish.

Worker boshlangan ishni tugatib, keyin qayta yonadi.

## Loglar

| Qayerda | Nima |
|---|---|
| **Панел → Хатолар** | Xatolar va ogohlantirishlar, traceback bilan (30 kun) |
| `/srv/narxlash/logs/narxlash.log` | Hammasi (10 MB × 5 fayl, aylanib turadi) |
| `journalctl -u narxlash-web -f` | Gunicorn: so'rovlar va ishga tushish |
| `journalctl -u narxlash-worker -f` | Fon ishi |

## CSS o'zgarsa (faqat dasturchi kompyuterida)

Shablonlarga yangi Tailwind klasslari qo'shilsa:

```bash
cd frontend && npm install && npm run build
```

Natija `web/static/css/app.css` ga yoziladi. Uni ham commit qiling. Serverga Node kerak emas.
