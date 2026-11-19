# Tizim tekshiruvi — 2026-11-19

Kod, testlar, o'qish statistikasi (lokal bazadagi 729 obyekt, 921 smeta), xavfsizlik sozlamalari va
kutubxonalar ko'rib chiqildi. Ishlab turgan server (firewall, SSH, disk) va haqiqiy hujum sinovi (pentest)
**qilinmadi** — ular alohida.

## 1. Qisqa xulosa

**Odamlarga ko'rsatsa bo'ladi** — avval tanish smetachilar guruhiga (pilot), keyin keng. Asosiy narxlash
ishonchli, xavfsizlik asoslari yaxshi qilingan, zaxira va tiklash yo'li bor. To'liq ochishdan oldin
3-bo'limdagi «qilish kerak» ro'yxatini bajaring (asosan server sozlamasi, kod emas).

## 2. Nima yaxshi

* **Testlar:** 756 ta avtomatik test, hammasi o'tadi (3 tasi shu kompyuterda o'tkazib yuborildi) (yadro, sahifalar, panel, xavfsizlik, zaxira). Bitta buyruq:
  `venv/Scripts/python.exe -m pytest -q`.
* **Natija:** 921 smetadan faqat 1 tasi xato bilan to'xtagan (Windows'dagi fayl yo'li, serverda bo'lmaydi);
  471 398 qatordan 1,4 % narxlanmagan — ular smetachiga «Топилмаганлар» bosqichida ko'rsatiladi.
* **Har bir qadam qaytariladi:** smetachi qo'lda qilgani saqlanadi, «Ортга қайтариш», eslab qolish (SheetMemo).
* **Xavfsizlik** (5-bo'lim): kirishsiz hech narsa ochilmaydi, har kim faqat o'z ishini ko'radi, panelga 2FA,
  parol tanlashga cheklov, jurnal, arxiv bombalari va «../» hujumlaridan himoya.
* **Server:** gunicorn + nginx + PostgreSQL + Redis, xizmatlar alohida foydalanuvchi ostida, cheklangan
  huquqlar bilan (`NoNewPrivileges`, `ProtectSystem`), kunlik zaxira va tiklash sinovi.

## 3. Qilish kerak (muhimlik tartibida)

| # | Nima | Nega | Qancha vaqt |
|---|---|---|---|
| 1 | Serverda `.env`: `NARXLASH_PANEL_URL` / `NARXLASH_ADMIN_URL` ni tasodifiy qiling, `NARXLASH_TRUST_PROXY=1`, SMTP | Botlar panel manzilini topmasin; IP cheklovi to'g'ri ishlasin; parol tiklash xati borsin | 15 daqiqa |
| 2 | Serverda `manage.py check --deploy` — ogohlantirish qolmasin | Yuqoridagilarni o'zi tekshiradi | 5 daqiqa |
| 3 | Kutubxona versiyalarini qotirish (`pip freeze > requirements.lock`, serverga shundan o'rnatish) | Hozir `>=` — yangi serverda kutilmagan katta versiya (masalan Django 7) tushib, sayt buzilishi mumkin | 30 daqiqa |
| 4 | Oyda bir marta yangilash (6-bo'lim) | Ma'lum zaifliklar yopiladi | oyiga ~1 soat |
| 5 | Disk va xizmat kuzatuvi (disk 80 % dan oshsa, worker to'xtasa — xabar) | Eng ehtimolli to'xtash sababi — disk to'lishi | 1–2 soat bir marta |
| 6 | Zaxirani boshqa joyga ham saqlash (panel → media `.tar` + baza nusxasi) | Server butunlay yo'qolsa | haftasiga 10 daqiqa |

## 4. Tizim qanday holda to'xtashi mumkin

| Holat | Nima bo'ladi | Himoya bormi |
|---|---|---|
| Disk to'ldi (media va zaxiralar o'sadi) | Yuklash va narxlash xato beradi | Yo'q — kuzatuv qo'shish kerak (3-jadval, 5) |
| Fon ishchisi (worker) to'xtadi | Papka va qayta hisoblash navbatda qoladi | `Restart=always` — o'zi qayta turadi |
| `.xls` ni o'girishda LibreOffice osilib qoldi | Bitta fayl o'tkaziladi | Vaqt cheklovi bor |
| Juda katta fayl / arxiv | Rad etiladi, sabab yoziladi | Cheklovlar: 800 MB yuklash, Excel ichi 200 MB, arxiv ochilganda 1 GB, 4 qavat ichma-ich |
| PostgreSQL yoki Redis to'xtadi | Sayt ishlamaydi | Server darajasida — kuzatuv kerak |
| `.env` yo'qoldi | Sayt umuman ishga tushmaydi (DEBUG bilan ochilib qolmaydi) | Ha |
| Bitta server — server yo'qolsa | Hamma narsa to'xtaydi | Tiklash yo'riqnomasi bor (`deploy/README.md`), nusxa boshqa joyda bo'lsin |

## 5. Qanday smetalarni o'qiy olmaydi

Lokal bazadagi 4 381 list bo'yicha:

| Turi | Soni | Ulushi | Tizim nima qiladi |
|---|---|---|---|
| Lokal (narxlanadi) | 1 213 | 28 % | — |
| Resurs (RES) | 1 306 | 30 % | — |
| Svod / umumiy hisob | 1 322 | 30 % | O'tkazadi (to'g'ri) |
| Allaqachon narxlangan smeta | 320 | 7 % | O'tkazadi; kerak bo'lsa smetachi «Локал» qiladi |
| Narx katalogi (miqdorsiz) | 128 | 3 % | O'tkazadi; lokal resurslari ichida bo'lsa o'zi RES qiladi |
| **Tushunmagan** — «Наименование / Ед. изм.» sarlavhasi topilmadi | **49** | **1,1 %** | Sariq guruhda ko'rsatadi, smetachi «Белгилаш» bilan ko'rsatadi, **eslab qoladi** |
| Miqdor ustuni topilmadi | 7 | 0,2 % | «тузатиш» so'raydi |

Qiyin holatlar (aniq ma'lum):

* Sarlavhasi boshqacha so'z bilan yozilgan yoki umuman yo'q listlar (yuqoridagi 49 ta).
* Rasm/PDF dan ko'chirilgan, hamma narsa bitta katakda yozilgan smeta — o'qib bo'lmaydi.
* Parol bilan himoyalangan Excel — ochilmaydi.
* Narx formula bilan boshqa faylga havola qilsa (`[1]Лист!G5`) — saqlangan qiymat o'qiladi; fayl hech qachon
  Excel'da hisoblanmagan bo'lsa, narx bo'sh ko'rinadi.
* RES'da nom boshqacha yozilgan (xato, qisqartma) — taklif sifatida chiqadi (90 %+), avtomatik rejim o'zi
  qabul qiladi, qo'lda rejimda smetachi tasdiqlaydi.
* Ofyorta (texnazor) bo'yicha alohida ma'lum muammolar: resurs raqami xato, svodka formulasiz, summa qo'lda
  yozilgan — tizim ogohlantiradi.

## 6. Kiberxavfsizlik

**Bor himoyalar:**

* Hamma sahifa sukut bo'yicha yopiq (`LoginRequiredMiddleware`); ochiqlari alohida belgilangan.
* Har bir ish egasiga bog'langan; admin boshqaning ishini faqat ko'radi va bu jurnalga yoziladi.
* CSRF, `X-Frame-Options: DENY`, CSP, HSTS (1 yil), xavfsiz cookie, `nosniff`, `Referrer-Policy`.
* Parollar Argon2 bilan; 15 daqiqalik bloklash (parol tanlash); panel va admin uchun majburiy 2FA; IP bloklash.
* Yuklangan Excel'lar: XML hujumlariga qarshi `defusedxml`, «zip bomba» va «../» yo'l hujumlari cheklangan,
  tashqi dasturlar (LibreOffice, unrar, 7z) vaqt cheklovi va tozalangan muhit bilan ishlaydi.
* Excel'ni saytda ko'rsatishda katak matni HTML sifatida tozalanadi (XSS yo'q); blog va sahifa matnlari tozalanadi.
* Zaxira nusxasini yuklab olish — faqat superuser, qayta parol/2FA kodi bilan, jurnalga yoziladi.
* Kod ichida `eval`, `pickle`, `shell=True`, xom SQL (migratsiya vositasidan tashqari) yo'q.

**Zaifroq joylar:**

* CSP `unsafe-inline` ga ruxsat beradi (sahifalarda ichki skriptlar bor) — XSS bo'lib qolsa, himoya qatlami
  kuchsizroq. Uzoq muddatli ish: skriptlarni alohida fayllarga ko'chirish.
* Panel/admin manzili `.env` da o'zgartirilmasa, taxmin qilish oson (3-jadval, 1).
* Begona Excel'ni LibreOffice ochadi — LibreOffice'ni server bilan birga yangilab turish shart.

## 7. Kutubxonalar

`pip-audit` tekshiruvi: **ma'lum zaiflik topilmadi**. Yangi versiyasi chiqqanlar: Django 6.1.2 → 6.1.3,
gunicorn 26 → 27, psycopg 3.3.6 → 3.3.9, XlsxWriter 3.2 → 3.3, va mayda paketlar.

**Yangilab turish kerakmi — ha.** Tartib:

* **Django tuzatish versiyalari** (6.1.x) — chiqqanidan keyin 1–2 hafta ichida: ularda ko'pincha xavfsizlik
  tuzatishlari bo'ladi, xavf kam.
* **Qolgan paketlar** — oyda bir marta: `pip list --outdated` va `pip-audit`, testlar o'tsa — serverga.
* **Katta versiyalar** (Django 7, gunicorn 27) — shoshilmasdan, alohida sinab.
* **Server tizimi** (Ubuntu, LibreOffice, PostgreSQL, nginx) — avtomatik xavfsizlik yangilanishlari
  (`unattended-upgrades`).

Foydasi: ma'lum teshiklar yopiladi, xatolar tuzatiladi. Xavfi: yangilanish biror narsani buzishi mumkin —
shuning uchun avval testlar, keyin server.

## 8. Kod tuzilishi

Tuzilma umuman to'g'ri: Excel bilan ish `core/` da (Django'siz), sahifa mantiqi `services.py` larda.
O'qishni qiyinlashtiradigan joy — bir necha juda katta fayl: `jobs/views.py` (~2400 qator), `panel/views_narx.py`
(~1500), `core/pipeline.py`, `texnazor/sheet.py`. Xarita: `docs/kod-xaritasi.md`. Keyingi qadam —
`jobs/views.py` ni mavzular bo'yicha fayllarga bo'lish (yuklash, obyekt, smeta, papka), xatti-harakat
o'zgarmagan holda.
