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

Domen: **mhisobot.uz** va **www.mhisobot.uz**. DNS serverga ulangan bo'lishi kerak — domen panelida
ikkita `A` yozuv: `@` → server IP, `www` → server IP (tekshirish: `ping mhisobot.uz` va
`ping www.mhisobot.uz` server IP sini ko'rsatsin; DNS 5 daqiqadan bir necha soatgacha yangilanadi).

```bash
sudo certbot certonly --nginx -d mhisobot.uz -d www.mhisobot.uz   # 1) avval sertifikat (nginx config'iga tegmaydi)
sudo cp /srv/narxlash/app/deploy/nginx/narxlash.conf /etc/nginx/sites-available/narxlash
sudo ln -s ../sites-available/narxlash /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx         # 2) nginx -t xato bersa — reload QILINMAYDI
```

> **Serverda boshqa saytlar bo'lsa:** `/etc/nginx/sites-enabled/default` ni **o'chirmang** va `certbot --nginx`
> (certonly'siz) ishlatmang — u boshqa saytlar config'iga ham tegishi mumkin.

Keyin `/srv/narxlash/.env` da: `DJANGO_ALLOWED_HOSTS=mhisobot.uz,www.mhisobot.uz`, `DJANGO_SSL_REDIRECT=1`,
`NARXLASH_TRUST_PROXY=1` (bo'lmasa sayt cheksiz yo'naltirishga tushadi), so'ng
`sudo systemctl restart narxlash-web narxlash-worker`.

**Panel «Тизим» uchun nginx jurnali** (javob kodlari, 429, javob vaqti): config `narxlash.mon.log` ga yozadi,
sayt uni o'qishi uchun bir marta:

```bash
sudo usermod -aG adm narxlash && sudo systemctl restart narxlash-web
```

Yangilashda `pip install -r requirements.txt` — `psutil` (RAM, protsessor, jarayonlar) qo'shilgan.

LibreOffice (.xls → .xlsx): server bo'yicha bir vaqtda ko'pi bilan `NARXLASH_LO_SLOTS` (standart 2) ta fayl ochiladi, qolganlari
navbat kutadi — foydalanuvchi «Сервер банд: …» xabarini ko'radi (`jobs/lo_queue.py`). Navbat Redis orqali barcha jarayonlarga umumiy;
server kuchaytirilsa (4+ yadro) `.env` da `NARXLASH_LO_SLOTS=3` qilish mumkin.

> **Muhim:** saytga faqat HTTPS orqali kirish mumkin — production'da cookie'lar «faqat HTTPS» rejimida.
> Domen olinguncha — pastdagi «Domen olinguncha» bo'limi.

## 8. Kunlik zaxira

Bir marta — tiklash sinovi uchun alohida bo'sh baza (asosiy bazaga tegilmaydi; Docker'da o'zi ochiladi):

```bash
sudo -u postgres psql -c "CREATE DATABASE narxlash_restore_test OWNER narxlash;"
```

```bash
sudo crontab -u narxlash -e
```

Ochilgan faylga quyidagi ikki qatorni qo'shing:

```
15 3 * * *  /srv/narxlash/app/deploy/backup.sh >> /srv/narxlash/logs/backup.log 2>&1
30 5 * * 0  /srv/narxlash/app/deploy/manage.sh backup_check --restore-test >> /srv/narxlash/logs/backup.log 2>&1
```

Har kuni soat 03:15 da baza (`backups/db/`, 14 kun saqlanadi, olingach tekshiriladi) va `media/` ning shu kungi
«surati» (`backups/media/<sana>/`, 7 kun) olinadi. O'zgarmagan fayllar oldingi kunnikiga qattiq havola bo'ladi —
7 ta surat deyarli bitta nusxa joyini egallaydi, lekin kecha o'chib ketgan fayl bugungi suratda ham bor.
Har yakshanba 05:30 da oxirgi nusxa `narxlash_restore_test` bazasiga **haqiqatan tiklanib** tekshiriladi
(jadvallar, migratsiyalar, foydalanuvchilar), keyin sinov bazasi tozalanadi. Natija — Панел → Захира нусха.

Tekshirish: `sudo crontab -u narxlash -l` (qatorlar bor), `tail /srv/narxlash/logs/backup.log` (har kuni «zaxira tayyor»).

**Ogohlantirish.** Muammo bo'lsa — superfoydalanuvchilarga kuniga bir marta xat keladi va panelda sariq ro'yxat
chiqadi: nusxa 26 soatdan eski (cron ishlamayapti yoki umuman o'rnatilmagan — worker soatda tekshiradi),
`backup.sh` xato bilan tugadi (darhol xat), media surati olinmayapti, boshqa joyda nusxa yo'q, tiklash sinovi
o'tmadi yoki 35 kundan beri o'tkazilmadi. Qo'lda: `sudo -u narxlash /srv/narxlash/app/deploy/manage.sh backup_check`.

> **Boshqa joyga ko'chirish shart** (disk buzilsa, shu serverdagi nusxa ham yo'qoladi). Eng osoni — `.env` ga
> `BACKUP_REMOTE=backup@ZAXIRA-SERVER:/data/narxlash` yozing va `narxlash` foydalanuvchisining ssh kalitini o'sha
> serverga qo'shing (`ssh-copy-id`). Skript har kuni nusxani o'sha yerga ham yuboradi. U yerda baza nusxalari
> bu serverdagi o'chirilganda o'chmaydi — `BACKUP_REMOTE_KEEP_DAYS` (standart 60) kundan eskilari tozalanadi.
>
> Eski tartibdan o'tganda: `backups/media/` ichida sanasiz qolgan fayllarni birinchi yangi zaxiradan keyin o'chirsa
> bo'ladi (`backups/media/20*` papkalaridan boshqasini).

**Zaxirada nima yo'q:** `/srv/narxlash/.env` (parollar, `DJANGO_SECRET_KEY`, maxfiy manzillar), nginx sozlamasi va
sertifikat. `.env` ni bir marta parol menejeri yoki boshqa xavfsiz joyga saqlang va o'zgartirganda yangilang:
`DJANGO_SECRET_KEY` yo'qolsa, panelda yozilgan pochta (SMTP) paroli o'qilmay qoladi va hamma qayta kiradi.
Zaxira shifrlanmagan (ichida parol xeshlari va ikki bosqichli kirish kalitlari) — yuklab olingan nusxani ham
ochiq joyda qoldirmang.

### Zaxiradan tiklash

```bash
sudo /srv/narxlash/app/deploy/restore.sh                                  # nusxalar ro'yxati
sudo /srv/narxlash/app/deploy/restore.sh narxlash_2026-11-18_0315.dump    # baza + o'sha kungi media
```

Skript: nusxani tekshiradi → sayt va worker'ni to'xtatadi → **hozirgi** bazani `backups/db/` ga, hozirgi media'ni
`backups/pre-restore/<vaqt>/` ga saqlaydi (adashsangiz qaytarish uchun) → bazani tozalab nusxani bitta tranzaksiyada
yozadi (xato bo'lsa — hozirgi holat qaytariladi) → o'sha kungi media suratini ko'chiradi → `migrate` → xizmatlarni
yoqadi. Oxirida qanday qaytarish mumkinligini yozib beradi. Media boshqa kundan: oxiriga `2026-11-17`; faqat baza:
`--no-media`.

**O'zingizdagi nusxadan** (server yo'qolganda): Panel → Захира нусха'dan baza (`narxlash_<sana>.dump`) va
o'sha kungi fayllarni (`media_<kun>.tar`) yuklab oling va server tashqarisida saqlang. Tiklashda ikkalasini
serverga yuklang (`scp narxlash_….dump media_….tar user@SERVER:/tmp/`), keyin:

```bash
sudo tar xf /tmp/media_<kun>.tar -C /srv/narxlash && sudo chown -R narxlash:narxlash /srv/narxlash/media
sudo /srv/narxlash/app/deploy/restore.sh /tmp/narxlash_<sana>.dump --no-media
```

### Yangi serverga ko'chish

1. Yangi serverni shu yo'riqnoma bo'yicha tayyorlang (1–7), lekin saytni hali ochmang. PostgreSQL versiyasi
   eskisidan past bo'lmasin. Domen sozlamasida TTL'ni 1–2 kun oldin 300 soniyaga tushiring.
2. Eski serverdan `/srv/narxlash/.env` ni ko'chiring. **O'zgarmasin:** `DJANGO_SECRET_KEY`, `NARXLASH_ADMIN_URL`,
   `NARXLASH_PANEL_URL`, email va `DJANGO_ALLOWED_HOSTS`. Yangilang: `DATABASE_URL`/`REDIS_URL` (yangi parol/manzil),
   `BACKUP_REMOTE` (agar o'zgarsa).
3. Eski serverda: `sudo systemctl stop narxlash-web narxlash-worker`, keyin
   `sudo -u narxlash /srv/narxlash/app/deploy/backup.sh` — yakuniy nusxa.
4. Eski serverdan ko'chiring (yangi serverda `narxlash` egasi bo'lsin — keyin `chown -R narxlash:narxlash`):
   `rsync -a /srv/narxlash/backups/db/<oxirgi>.dump YANGI:/srv/narxlash/backups/db/` va
   `rsync -a /srv/narxlash/media/ YANGI:/srv/narxlash/media/`.
5. Yangi serverda: `sudo /srv/narxlash/app/deploy/restore.sh <oxirgi>.dump --no-media`.
6. DNS'ni almashtirmasdan tekshiring (SSH tunnel yoki kompyuterdagi `hosts` fayli): kirish, panel, eski smeta
   fayli, yangi narxlash, parol tiklash xati, Панел → Хатолар.
7. DNS A yozuvini yangi IP'ga o'zgartiring, sertifikat oling (6-bo'lim), yangi serverda cron'ni qo'shing (8-bo'lim).
8. Ertasi kuni Панел → Захира нусха sariq ro'yxatsiz bo'lsin. Eski serverni 1–2 hafta to'xtatilgan holda saqlang.

## Panel: ikki bosqichli kirish

Panel va Django admin'ga (`is_staff`) faqat ikki bosqichli kirish bilan kiriladi: birinchi kirishda sayt
Google Authenticator / Microsoft Authenticator ilovasiga kalit qo'shishni so'raydi, keyin har kirishda ilova kodi
so'raladi. Telefon yo'qolsa — serverda:

```bash
sudo -u narxlash /srv/narxlash/app/deploy/manage.sh reset_2fa email@manzil
```

Favqulodda (masalan, kodni hech kim kirita olmasa) `.env` ga `NARXLASH_STAFF_2FA=0` yozib, xizmatni qayta ishga
tushirish mumkin — muammo hal bo'lgach albatta qaytaring.

nginx so'rovlarni cheklaydi (`deploy/nginx/narxlash.conf`): kirish / ro'yxatdan o'tish / parol tiklash — bitta IP
dan daqiqasiga 10 ta, qolgan sahifalar — soniyasiga 20 ta. Oshsa — 429. Config yangilanganda:
`sudo cp … /etc/nginx/sites-available/narxlash && sudo nginx -t && sudo systemctl reload nginx`.

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
Domen faollashgach — 7-qadam, va `.env` ni qaytaring (`DJANGO_ALLOWED_HOSTS=mhisobot.uz,www.mhisobot.uz`, `DJANGO_SSL_REDIRECT=1`).

> Saytni IP orqali oddiy `http://` da ochmang: parol va cookie'lar shifrlanmasdan yuboriladi, xavfsizlik
> sozlamalari (faqat HTTPS cookie) tufayli kirish baribir ishlamaydi.

## Xavfsizlik

Sayt begona Excel va arxiv fayllarni ochadi. Himoya qatlamlari (testlari — `jobs/tests_security.py`):

| Xavf | Himoya |
|---|---|
| Excel ichidagi kod (XSS) admin sessiyasida | fayldan kelgan shrift nomi va ranglar tozalanadi, katak matni escape qilinadi |
| «Arxiv bombasi» (kichik fayl gigabaytga ochiladi) | arxivdagi Excel ≤ 100 MB, ichki arxiv ≤ 700 MB, jami ≤ 1 GB; `.xlsx` ochilganda ≤ 200 MB |
| XML hujumlari (XXE) | `defusedxml` (openpyxl o'zi ishlatadi) |
| LibreOffice/unrar zaifligi orqali sirlarni o'qish | dasturga saytning muhiti (parollar) berilmaydi; systemd: `NoNewPrivileges`, `ProtectSystem`, `ProtectHome` |
| Bitta foydalanuvchi serverni to'ldirishi | soatiga 30 yuklash, diskda 5 GB (`.env`); ro'yxatdan o'tishni yopish — `NARXLASH_SIGNUP=closed` |

systemd fayllari o'zgarganda (`update.sh` ularni nusxalamaydi):

```bash
sudo cp /srv/narxlash/app/deploy/systemd/narxlash-*.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl restart narxlash-web narxlash-worker
```

**Server:** `root` ga parol bilan kirishni o'chirib, SSH kalitdan foydalaning (`/etc/ssh/sshd_config`:
`PasswordAuthentication no`, `PermitRootLogin prohibit-password`) — avval kalit bilan kirish ishlashini tekshiring.

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
