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
- `unrar` — RAR arxivlar uchun (**majburiy**: Ubuntu'dagi `7zip` paketi RAR modulisiz, u faqat ZIP/7z ochadi).
  `unrar` `multiverse` omborida: `Unable to locate package` chiqsa — avval
  `sudo add-apt-repository -y multiverse && sudo apt update`. Tekshirish: `check --deploy` da `narxlash.W007` bo'lmasin.
- Ubuntu 22.04 da `7zip` paketi bo'lmasa, o'rniga `p7zip-full` o'rnating.

## 2. Foydalanuvchi va kod

```bash
sudo adduser --system --group --home /srv/narxlash narxlash
sudo -u narxlash git clone -b narxlash-mvp https://github.com/IskandarDusbekov/exwork.git /srv/narxlash/app
sudo -u narxlash python3 -m venv /srv/narxlash/venv
sudo -u narxlash /srv/narxlash/venv/bin/pip install -r /srv/narxlash/app/requirements.txt
sudo -u narxlash mkdir -p /srv/narxlash/{media,static,logs,backups}
```

Repozitoriy yopiq (private) bo'lsa, `git clone` login so'raydi: parol o'rniga **GitHub token** kiriting
(GitHub → Settings → Developer settings → Fine-grained tokens → faqat shu repo, `Contents: Read-only`).

## 3. PostgreSQL va Redis

**Serverda allaqachon bor bo'lsa** (boshqa saytlar bilan umumiy — tekshirish: `ss -tlnp | grep -E ':(5432|6379)\b'`),
Docker kerak emas. Narxlash uchun alohida baza va foydalanuvchi oching:

```bash
sudo -u postgres psql -c "CREATE USER narxlash WITH PASSWORD '<postgres-paroli>';"
sudo -u postgres psql -c "CREATE DATABASE narxlash OWNER narxlash;"
redis-cli ping      # PONG — Redis parolsiz; NOAUTH — parol /etc/redis/redis.conf dagi requirepass
```

`.env` da: `DATABASE_URL=postgres://narxlash:<postgres-paroli>@127.0.0.1:5432/narxlash`,
`REDIS_URL=redis://127.0.0.1:6379/5` (`/5` — boshqa saytlar kalitlari bilan aralashmaydi).
`pg_dump` (kunlik zaxira) server bilan bir xil versiyada bo'lishi kerak: `psql --version` va
`sudo -u postgres psql -tAc "SHOW server_version;"` bir xil chiqsin.

**Server bo'sh bo'lsa — Docker bilan** (`deploy/docker-compose.yml`):

```bash
sudo apt install -y docker.io docker-compose-v2
cd /srv/narxlash/app/deploy
sudo docker compose --env-file /srv/narxlash/.env up -d
```

Portlar faqat `127.0.0.1` da ochiq, tashqaridan ulanib bo'lmaydi. 5432 yoki 6379 band bo'lsa, Docker ishga tushmaydi —
unda yuqoridagi usulni tanlang.

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

`NARXLASH_ADMIN_URL` va `NARXLASH_PANEL_URL` uchun (har biriga alohida ishga tushiring):

```bash
python3 -c "import secrets; print('boshqaruv-' + secrets.token_hex(8) + '/')"
```

Panel va admin shu manzillarda ochiladi (`https://DOMEN/boshqaruv-…/`). Ularni yozib qo'ying — saytda havola yo'q.

> Docker usulida `.env` 3-qadamdagi docker'dan **oldin** tayyor bo'lishi kerak: `POSTGRES_PASSWORD` va `REDIS_PASSWORD` shu yerdan olinadi.

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

Domen DNS'i serverga ulangan bo'lishi kerak (tekshirish: `ping DOMEN` server IP sini ko'rsatsin).
`DOMEN` o'rniga o'z domeningizni yozing:

```bash
sudo certbot certonly --nginx -d DOMEN               # 1) avval sertifikat (nginx config'iga tegmaydi)
sudo cp /srv/narxlash/app/deploy/nginx/narxlash.conf /etc/nginx/sites-available/narxlash
sudo sed -i 's/narxlash\.uz/DOMEN/g' /etc/nginx/sites-available/narxlash
sudo ln -s ../sites-available/narxlash /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx         # 2) nginx -t xato bersa — reload QILINMAYDI
```

> **Serverda boshqa saytlar bo'lsa:** `/etc/nginx/sites-enabled/default` ni **o'chirmang** va `certbot --nginx`
> (certonly'siz) ishlatmang — u boshqa saytlar config'iga ham tegishi mumkin.

Keyin `/srv/narxlash/.env` da: `DJANGO_ALLOWED_HOSTS=DOMEN`, `DJANGO_SSL_REDIRECT=1`, so'ng
`sudo systemctl restart narxlash-web narxlash-worker`.

> **Muhim:** saytga faqat HTTPS orqali kirish mumkin — production'da cookie'lar «faqat HTTPS» rejimida.
> Domen olinguncha — pastdagi «Domen olinguncha» bo'limi.

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

## Domen olinguncha (SSH tunnel)

Domen faollashguncha sayt xavfsiz holatda, faqat sizga ochiq ishlaydi: tashqaridan (IP orqali) kirib bo'lmaydi,
siz esa o'z kompyuteringizdan SSH tunnel orqali kirasiz. 1–6 qadamlarni bajaring, 7-qadamni (nginx) keyinga qoldiring.

1. `/srv/narxlash/.env` da vaqtincha:

   ```
   DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
   DJANGO_SSL_REDIRECT=0
   ```

   va `sudo systemctl restart narxlash-web narxlash-worker`.

2. **O'z kompyuteringizda** (serverda emas) terminal oching va tunnelni ulang — oyna ochiq turguncha ishlaydi:

   ```bash
   ssh -N -L 8800:127.0.0.1:8000 root@SERVER_IP
   ```

3. Brauzerda (Chrome, Edge yoki Firefox) oching: `http://localhost:8800/`, panel — `http://localhost:8800/boshqaruv-…/`.

`check --deploy` bu vaqtda `security.W008` (SSL redirect o'chiq) deb ogohlantiradi — bu kutilgan holat.
Domen faollashgach — 7-qadam, va `.env` ni qaytaring (`DJANGO_ALLOWED_HOSTS=DOMEN`, `DJANGO_SSL_REDIRECT=1`).

> Saytni IP orqali oddiy `http://` da ochmang: parol va cookie'lar shifrlanmasdan yuboriladi, xavfsizlik
> sozlamalari (faqat HTTPS cookie) tufayli kirish baribir ishlamaydi.

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
