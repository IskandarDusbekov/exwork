# To'lov tizimi — to'liq qo'llanma

Bu fayl o'qib o'rganish uchun: to'lov tizimi qanday ishlaydi, qaysi kod nima qiladi, Click'ni qanday
ulash kerak va serverda nima qilish kerak. Kod ichidagi izohlar kirill yozuvida, bu fayl — lotinda.

---

## 1. Qisqacha: tizim nima qiladi

- Har bir foydalanuvchining **balansi** bor (so'mda), u **ikki hamyondan** iborat:
  **haqiqiy pul** (Click yoki admin «bonus»siz qo'shgan — qaytarish mumkin) va **bonus** (sovg'a — qaytarilmaydi).
  Ular hech qachon aralashmaydi (2.6-bo'lim).
- **Tariflar** — oylik paketlar: nechta smeta qatori va nechta texnazor obyekti. Tarif balansdan sotib olinadi,
  **1, 3, 6 yoki 12 oyga** — uzoq muddatga chegirma bilan (2.3-bo'lim).
- **Pul faqat tayyor faylni yuklab olganda yechiladi.** Narxlash, natijani ko'rish, tuzatish — bepul.
  Har bir fayl uchun **bir marta**: qayta yuklab olish bepul.
- Yuklab olishda avval **tarifdagi qatorlardan** olinadi, tugasa — **balansdan** (har qator `N` so'm).
  Texnazor obyekti — oyiga bir marta (tarifdagi obyektlar sonidan oshsa — balansdan).
- **Xodimlar (`is_staff`) to'lamaydi** — ularda sanash ham ko'rinmaydi.
- **Pulli rejim** panelda yoqiladi/o'chiriladi. O'chiq bo'lsa — hamma narsa bepul (hozirgidek).

```
                ┌─────────────── Click ───────────────┐
foydalanuvchi ──┤ balansni to'ldirish (10 000 so'mdan) ├──► BALANS
                └─────────────────────────────────────┘        │
                                                               ├──► tarif sotib olish (oylik)
                                                               │
         tayyor faylni yuklab olish ──► avval TARIF qatorlari ──┴──► yetmasa BALANSDAN
```

---

## 2. Qoidalar (biznes mantiq)

### 2.1. Nima hisoblanadi

| Nima | Qanday hisoblanadi |
|---|---|
| Narxlangan smeta (Excel) | Ishlov berilgan resurs qatorlari soni (`Job.rows_done`) |
| Obyekt / papka ZIP | Ichidagi barcha narxlangan smetalar qatorlari yig'indisi (avval to'langanlari sanalmaydi) |
| Ofyorta fayli | Smeta kabi (qatorlar) |
| Texnazor Ф-2 / Ф-3 / dalolatnomalar | Obyekt × oy: shu obyekt uchun shu oyda bir marta |

Kalit (`Unlock.key`) — bir fayl ikki marta to'lanmasligi uchun:
`job:<id>` — smeta, `obj:<id>:2026-11` — texnazor obyekti, shu oy uchun.

### 2.2. Yuklab olish tugmasini bosganda

1. Pulli rejim o'chiq, xodim yoki avval to'langan → **fayl darrov** beriladi.
2. Tarif qatorlari yetadi → **fayl darrov** beriladi, qatorlar tarifdan ayriladi.
3. Balansdan pul yechilishi kerak → **tasdiqlash sahifasi**: «Balansdan 41 600 so'm yechiladi»
   (nechta qator, qanchasi tarifdan, qanchasi balansdan, balans qanchaga tushadi). «Tasdiqlash» → fayl.
4. Balansda yetmaydi → **«pul yetmaydi» sahifasi**: qancha kerak, qancha bor, qancha yetmaydi +
   «Balansni to'ldirish» tugmasi. Natija saqlanib qoladi.

Yuklab olish tugmalari yonida kichik yorliq: «тўланган», «тарифдан», «41 600 сўм», «пул етмайди».

### 2.3. Tarif

- Muddat: **1, 3, 6 yoki 12 oy** (`services.PERIODS`), sotib olingan kundan. Qator va obyekt chegarasi
  **har oyga alohida** — 3 oylik tarifda ham har oy yangidan (`services._window`). Ortib qolgani o'tmaydi.
- **Narx** (`services.price_for(tariff, months)`):
  `narx × oylar × (1 − muddat chegirmasi %) × (1 − tarif aksiyasi %)`, 100 so'mga yaxlitlanadi.
  - **Muddat chegirmasi** — panel «Созламалар»: `billing_disc_3/6/12` (standart 10 / 15 / 25 %).
  - **Tarif aksiyasi** — har tarifda o'zi: `Tariff.discount` (%) va `discount_until` (qachongacha; bo'sh — muddatsiz).
    Muddati o'tsa o'zi o'chadi (`Tariff.discount_now`). Eng ko'pi 90 %.
  - Misol: Старт 100 000, 12 oy, 25 % → 1 200 000 × 0,75 = **900 000 so'm** (oyiga 75 000).
- Tarif kartasida **«Батафсил»** tugmasi — nima kiradi, har muddat narxi, almashtirish qoidalari;
  admin `Tariff.details` ga o'z matnini yozsa — tepasida chiqadi.
- **Bepul tarif** (narxi 0) — tarifsiz har bir foydalanuvchi shu bilan ishlaydi, kalendar oy bo'yicha
  (har oy 1-kuni yangilanadi). Panelda **albatta bitta faol bepul tarif** bo'lishi kerak.
- **O'zi yangilanish** (`auto_renew`): tarif tugaganda balansda pul bo'lsa — yana bir oyga o'zi olinadi.
  Pul yetmasa — bepul tarifga tushadi. Foydalanuvchi profilda o'chirishi mumkin.
- **Tarif almashtirish** (tarif tugamasdan):
  - **Qimmatrog'iga** — **darrov**. Hozirgisining ishlatilmagan qismi hisobga olinadi, faqat farqi yechiladi.
    Ishlatilmagan qism = `oylik narx × (to'liq qolgan oylar + min(shu oyning qolgan kun ulushi, qolgan qator ulushi))`.
    Hisobga olingan pul yangi narxdan ko'p bo'lsa — ortgani **balansga qaytadi** (hamyonlarga bonus ulushiga qarab).
    Misol: Старт (100 000) olgan, 30 000 qatordan 9 605 tasi ishlatilgan, 1 kun o'tgan →
    qatorlar ulushi 68%, kunlar ~97% → kichigi 68% → 67 983 so'm hisobga olinadi → Про uchun
    250 000 − 67 983 = **182 017 so'm** yechiladi. Yangi davr bugundan, qatorlar yangidan.
    *Nega kichigi:* 2 kunda hamma qatorni ishlatgan odam pulini qaytarib ololmasin.
  - Bu **bitta qadam**: eski tarif «bekor bo'lib, pul qaytib, keyin yangisi to'liq yechilmaydi». Daftarga bitta
    yozuv tushadi — faqat farqi. Tasdiqlash oynasida hisob qator-qator: yangi narx, hisobga olingan qism, yechiladigan farq.
  - **Arzonrog'iga** — **keyingi davrdan**. Hozirgisi oxirigacha ishlaydi, keyin yangisiga o'tadi,
    pul o'sha kuni yechiladi (`Account.next_tariff`). Bekor qilish mumkin.
  - **Shu tarifning o'zi** — mumkin emas (muddati tugagach o'zi yangilanadi).
- **Sovg'a** (admin beradi) — bepul, o'zi yangilanmaydi, almashtirganda pul hisobga olinmaydi.
- Tarif narxi/qatori keyin panelda o'zgartirilsa, **sotib olinganlarga ta'sir qilmaydi**
  (shartlar `Subscription` ga ko'chirib yoziladi).

### 2.4. Balansni to'ldirish

- Eng kam va eng ko'p summa — panelda (hozir 10 000 / 5 000 000). **Server tekshiradi**:
  «9 999», «12.5», «-100», «1e6» — qabul qilinmaydi.
- Pul balansga **faqat Click «to'landi» deganda** tushadi (Complete). Foydalanuvchi sahifaga qaytishi muhim emas.
- Bir to'lov **ikki marta tushmaydi** (Click so'rovni qayta yuborsa ham).

### 2.4a. Email tasdiqlanmagan bo'lsa

Balansni to'ldirish va tarif sotib olish **yopiq** (`billing.views.need_verify`): parol esdan chiqsa, hisob va undagi
pul faqat email orqali tiklanadi. Profilda ogohlantirish chiqadi, tugmalar «avval tasdiqlang» oynasini ochadi,
server ham tekshiradi. Xodimlarga tegishli emas.

### 2.5. Qaytarish

Ishlatilmagan **haqiqiy** pulni qaytarish — admin panelda qo'lda (minus yozuv, sababi bilan) + Click kabinetidan
pulni qaytarish. Bonus qaytarilmaydi. Avtomatik qaytarish hozircha yo'q. Shartlar sahifasida (`/shartlar/`,
4-bo'lim) shu yozilgan.

### 2.6. Ikki hamyon: haqiqiy pul va bonus

| | Haqiqiy pul (`Account.balance`) | Bonus (`Account.bonus`) |
|---|---|---|
| Qayerdan | Click; admin «bonus» belgisisiz qo'shgan (naqd, bank) | admin «bonus» belgisi bilan; sovg'a |
| Sarflash | bonusdan **keyin** | **birinchi** |
| Qaytarish | mumkin | yo'q |

- Daftarda har yozuvda: `amount` (jami o'zgarish), `bonus` (shundan bonus qismi), `real = amount − bonus`,
  qoldiq — `balance_after` (haqiqiy) va `bonus_after`.
- Foydalanuvchiga **jami** ko'rinadi (`Account.total`), profilda ostida «haqiqiy / bonus» ajratilgan.
- Panel «Умумий» bo'limida Click tushumi va bonuslar **alohida bloklarda** — aralashmaydi.
- Admin haqiqiy puldan ayirsa, bonus hisobiga minusga tusha olmaydi (`post` ikkalasini alohida tekshiradi).

---

## 3. Fayllar xaritasi

```
billing/
├── models.py          ← jadvallar: Tariff, Account, Subscription, Entry, Unlock, Payment
├── services.py        ← BUTUN MANTIQ: balans, tarif, davr, yechish, tarif almashtirish
├── gate.py            ← yuklab olish «darvozasi»: tasdiqlash / pul yetmaydi sahifalari
├── click.py           ← Click SHOP API: to'lov havolasi, Prepare, Complete, imzo
├── views.py           ← foydalanuvchi sahifalari: profil, tarif tanlash, to'ldirish, Click manzillari
├── urls.py            ← /balance/... manzillari
├── templatetags/billing_tags.py   ← {% bill_hint work as bh %} — tugma yonidagi yorliq
├── templates/billing/
│   ├── profile.html   ← «Баланс ва тариф» sahifasi (to'ldirish — modal oynada)
│   ├── confirm.html   ← «Balansdan X so'm yechiladi» (402)
│   ├── blocked.html   ← «Balansda pul yetmaydi» (402)
│   ├── _gate_css.html ← ikkalasining CSS + JS
│   └── _tag.html      ← tugma yonidagi yorliq
├── management/commands/billing_check.py   ← serverda tekshiruv buyrug'i
├── migrations/        ← 0001 jadvallar, 0002 boshlang'ich tariflar, 0003 next_tariff
└── tests.py           ← 33 ta test

panel/views_billing.py              ← panel: tariflar, balanslar, foydalanuvchi balansi/sovg'a
panel/templates/panel/billing.html  ← panel «Тўлов ва тарифлар» sahifasi
panel/conf.py                       ← panel sozlamalari: billing_on, narxlar, min/max summa
web/settings.py                     ← standart qiymatlar va CLICK_* (.env dan)
deploy/.env.example                 ← CLICK_* namunasi
deploy/README.md  (10-bo'lim)       ← serverda yoqish tartibi
```

Darvoza ulangan joylar (yuklab olish view'lari):

| Fayl | Funksiya | Nima |
|---|---|---|
| `jobs/views.py` | `job_download`, `batch_download`, `project_download` | smeta, obyekt ZIP, papka ZIP |
| `jobs/views_ofyorta.py` | `ofyorta_file` | ofyorta |
| `texnazor/views.py` | `f2_download`, `f3_download` | Ф-2, Ф-3 |
| `texnazor/views_acts.py` | `report_download`, `f2_download` | dalolatnomalar hisoboti |

---

## 4. Jadvallar (`billing/models.py`)

| Model | Nima saqlaydi | Muhim maydonlar |
|---|---|---|
| `Tariff` | Tarif (panelda tahrirlanadi) | `name`, `price` (0 = bepul, oyiga), `rows`, `objects_limit` (oyiga), `discount`, `discount_until`, `details`, `active`, `order` |
| `Account` | Foydalanuvchi hisobi (1:1) | `balance` (haqiqiy), `bonus`, `total`, `auto_renew`, `next_tariff`, `next_months` |
| `Subscription` | Sotib olingan tarif davri | `tariff`, `months`, `price` (to'liq qiymati), `paid_bonus`, `rows`, `objects_limit` (oyiga), `start`, `end`, `gift` |
| `Entry` | **Daftar**: har bir kirim/chiqim | `kind`, `amount` (+/−), `bonus` (shundan bonus), `balance_after`, `bonus_after`, `note`, `ref` (noyob), `actor` |
| `Unlock` | To'langan (yuklab olingan) fayl | `key` (noyob user bilan), `rows`, `quota_rows`, `amount`, `free` |
| `Payment` | Click to'lovi | `amount`, `status` (created → prepared → paid / cancelled), `provider_id` (click_trans_id) |

**Asosiy qoida:** `Account.balance` va `Account.bonus` faqat `services.post()` orqali o'zgaradi. Har o'zgarish
`Entry` ga yoziladi. Shuning uchun haqiqiy pul = `Σ(amount − bonus)`, bonus = `Σ bonus` —
`billing_check` ikkalasini tekshiradi (bonus manfiy bo'lmasligini ham).

`Entry.kind`: `topup` (Click), `bonus`, `admin` (tuzatish), `subscription` (tarif), `rows` (smeta),
`object` (texnazor), `refund`.

`Entry.ref` — **idempotentlik kaliti**: `click:<payment_id>`, `sub:<id>`, `unlock:<id>`. Shu `ref` bilan
yozuv bor bo'lsa — ikkinchi marta yozilmaydi (pul ikki marta tushmaydi/yechilmaydi).

---

## 5. Kod: muhim funksiyalar (`billing/services.py`)

### Sozlamalar
- `enabled()` — pulli rejim yoqiqmi (panel `billing_on`).
- `exempt(user)` — xodimmi (to'lamaydi).
- `row_price()`, `object_price()`, `topup_limits()` — panel sozlamalaridan.

### Daftar
```python
post(user, amount, kind, note="", ref=None, actor=None, allow_negative=False, bonus=None) -> Entry
```
Balansga yozadi. `bonus=None` — chiqimda avval bonusdan o'zi bo'ladi; kirimda 0 (haqiqiy). `select_for_update` bilan hisobni qulflaydi (bir vaqtda ikki so'rov — navbat bilan).
Chiqim balansdan katta bo'lsa — `NotEnough` (adminda `allow_negative`). `ref` takrorlansa — eski yozuv qaytadi.

### Tarif va davr
- `plan(user)` → `Plan`: hozirgi tarif, davr (`start`–`end`), `rows`, `rows_used`, `rows_left`, obyektlar.
  Ishlatilgani = shu davrdagi `Unlock` lardagi `quota_rows` yig'indisi.
  Tugagan obuna bo'lsa — shu yerda `renew()` chaqiriladi (alohida cron kerak emas, «dangasa» yangilanish).
- `renew(user)` — tugagan tarifni yangilash: `next_tariff` bo'lsa — shuni, bo'lmasa `auto_renew` bo'lsa — o'shani.
- `price_for(tariff, months)` → `Price`: `total`, `base`, `per_month`, `saved`, `pct` (jami chegirma %).
- `buy(user, tariff, months=1, gift=False)` — sotib olish: balansdan yechadi, `credit()` ni ayiradi, eski obunani tugatadi.
- `credit(user)` — hozirgi tarifning ishlatilmagan qismi (so'mda).
- `offer(user, tariff, months=1)` → `Offer(kind, pay, credit)` — tarif kartasi uchun: `current`, `upgrade`, `next`,
  `scheduled`, `buy`, `free`.
- `change(user, tariff, months=1)` — foydalanuvchi tanladi: `("now", sub)` yoki `("next", None)`.
- `cancel_next(user)` — keyingi davrdagi almashishni bekor qilish.

### Yechish (yuklab olish)
- `quote_items(user, items)` → `Quote`: `paid` (avval to'langan), `free` (rejim o'chiq / xodim),
  `rows`, `quota_rows`, `amount` (balansdan), `balance`, `ok` (yetadimi).
- `charge_items(user, items)` — hammasi yoki hech biri: yetmasa `NotEnough`, hech narsa yozilmaydi.
- `items` — `[(key, kind, title, rows), …]`, `gate.py` dagi `job_items()`, `object_items()` yasaydi.

### Darvoza (`billing/gate.py`)
```python
resp = gate.check(request, gate.job_items([job]), back=reverse("job", args=[job.pk]))
if resp:
    return resp          # tasdiqlash yoki «pul yetmaydi» sahifasi (HTTP 402)
return FileResponse(...)  # fayl
```
- Tasdiqlash havolasi `?pay=<token>` — **imzolangan** (`django.core.signing`, 15 daqiqa): kim, qaysi
  fayllar, qancha. Begona havola boshqa odamning pulini yecholmaydi; narx o'zgarsa token yaroqsiz.
- 402 kodi — texnazordagi `fetch` yuklash shuni ko'rib, faylning o'rniga sahifani ochadi.

---

## 6. Click: to'lov qanday o'tadi

Click'ning **SHOP API** si ishlatilgan (eng keng tarqalgan usul: foydalanuvchi Click sahifasida to'laydi,
Click serverimizga ikki marta xabar beradi).

### 6.1. Ketma-ketlik

```
Foydalanuvchi          Bizning sayt                         Click
     │  «Click orqali to'lash 50 000»                         │
     ├──────────────► POST /balance/topup/                    │
     │                 summa tekshiriladi (min/max)           │
     │                 Payment #17 (created) yaratiladi       │
     │ ◄───────────── 302 → my.click.uz/services/pay?         │
     │                  service_id&merchant_id&amount=50000   │
     │                  &transaction_param=17&return_url=…    │
     ├──────────────────────────────────────────────────────► │ karta / Click hisobi
     │                                                        │
     │                 POST /balance/click/prepare/  ◄────────┤ 1) Prepare (action=0)
     │                 imzo ✓, Payment #17 bor, summa ✓       │
     │                 → prepared, javob error=0  ───────────►│
     │                                                        │ pul yechiladi
     │                 POST /balance/click/complete/ ◄────────┤ 2) Complete (action=1)
     │                 imzo ✓ → balansga +50 000 (Entry)      │
     │                 → paid, javob error=0  ───────────────►│
     │ ◄──────────────────────────────────────────────────────┤ return_url ga qaytaradi
     ├──────────────► GET /balance/?paid=17                   │
     │ ◄───────────── «To'lov o'tdi: balansga 50 000 so'm tushdi»
```

Click karta rad etilsa (pul yo'q va h.k.) — Complete'ni `error < 0` bilan yuboradi → biz to'lovni
`cancelled` qilamiz, balansga hech narsa yozilmaydi.

### 6.2. Imzo (sign_string)

Click har so'rovga `sign_string` qo'shadi — MD5. Biz o'zimiz hisoblab solishtiramiz (`click._sign`):

```
Prepare:  md5(click_trans_id + service_id + SECRET_KEY + merchant_trans_id + amount + action + sign_time)
Complete: md5(click_trans_id + service_id + SECRET_KEY + merchant_trans_id + merchant_prepare_id
              + amount + action + sign_time)
```

`SECRET_KEY` faqat bizda va Click'da bor → begona odam soxta «to'landi» xabarini yubora olmaydi.
Solishtirish `hmac.compare_digest` bilan (vaqt bo'yicha hujumdan himoya).

### 6.3. Tekshiruvlar tartibi (`click._common`)

1. Hamma maydon bormi → yo'q: `-8`
2. `.env` da kalitlar bormi → yo'q: `-8`
3. Imzo to'g'rimi → yo'q: `-1`
4. `service_id` bizniki emasmi → `-8`
5. `action` to'g'rimi (0 / 1) → yo'q: `-3`
6. `merchant_trans_id` (Payment.pk) bormi → yo'q: `-5`
7. Summa mosmi (±0.01) → yo'q: `-2`
8. Allaqachon to'langanmi → `-4`; bekor qilinganmi → `-9`

Complete'da qo'shimcha: `merchant_prepare_id` mosmi, Prepare bo'lganmi, `click_trans_id` o'shami → yo'q: `-6`.
Balansga yozib bo'lmasa (baza xatosi) → `-7` (Click keyin qayta yuboradi).

### 6.4. Click xato kodlari

| Kod | Ma'nosi |
|---|---|
| 0 | Muvaffaqiyatli |
| -1 | Imzo noto'g'ri (SIGN CHECK FAILED) |
| -2 | Summa noto'g'ri |
| -3 | Action topilmadi |
| -4 | Allaqachon to'langan |
| -5 | Buyurtma (foydalanuvchi) topilmadi |
| -6 | Tranzaksiya topilmadi |
| -7 | Yangilab bo'lmadi |
| -8 | So'rovda xato |
| -9 | Tranzaksiya bekor qilingan |

### 6.5. Xavfsizlik

- `/balance/click/prepare/` va `/complete/` — **kirishsiz va CSRF'siz** (Click server-serverga yuboradi),
  lekin **imzo** bilan himoyalangan.
- Pul **faqat** Complete'da va **faqat** `services.post(..., ref="click:<id>")` orqali tushadi →
  qayta yuborilgan so'rov pulni ikkinchi marta qo'shmaydi; `select_for_update` — bir vaqtda kelgan
  ikki so'rov ham.
- `return_url` dagi `?paid=17` faqat **xabar** ko'rsatadi, hech narsa o'zgartirmaydi. Boshqa odamning
  to'lov raqamini yozsa — hech narsa ko'rinmaydi.
- Kalitlar **panelda emas, `.env` da** — panelga kirgan odam (yoki o'g'irlangan sessiya) ko'ra olmasin.

---

## 7. Click bilan ishlashni boshlash — nima qilish kerak

### 7.1. Click bilan shartnoma

1. **Yuridik shaxs yoki YaTT** bo'lishi kerak va bank hisob raqami.
2. Click'ning biznes bo'limiga ariza: **click.uz → «Бизнесга» / «Подключить оплату»** (yoki
   ular telefoni orqali). Odatda so'raladi:
   - guvohnoma (yuridik shaxs / YaTT), STIR (INN), bank rekvizitlari, direktor pasporti;
   - **sayt manzili** va sayt ishlayotgan bo'lishi;
   - saytda **ommaviy oferta** (foydalanish shartlari): xizmat nima, narxlar, to'lov va qaytarish tartibi,
     kompaniya rekvizitlari va aloqa. Bizda `/terms/` sahifa bor — unga to'lov va qaytarish bo'limini
     qo'shish kerak;
   - xizmat turi va taxminiy aylanma.
3. Shartnomada **komissiya foizi** belgilanadi (har bir to'lovdan Click oladi) — shuni so'rang.
4. Click **merchant kabinet** (merchant.click.uz) beradi va servis yaratadi. Sizga keladi:
   - `SERVICE_ID`
   - `MERCHANT_ID`
   - `SECRET_KEY`
   - `MERCHANT_USER_ID`

### 7.2. Click'dan albatta so'rang

- **Fiskal chek (OFD / ИКПУ, МХИК kodi).** O'zbekistonda onlayn to'lovga fiskal chek kerak bo'lishi mumkin.
  Click buni ba'zan o'zi qiladi, ba'zan merchantdan alohida API (fiskalizatsiya) talab qiladi.
  Xizmatimizning ИКПУ kodi qaysi va chekni kim chiqaradi — so'rang. **Bu hozircha qilinmagan.**
- Komissiya foizi va pul hisobga necha kunda tushishi.
- Test rejim bormi (test kalitlar bilan sinab ko'rish).
- Prepare/Complete so'rovlari qaysi IP lardan keladi (xohlasangiz, nginx'da cheklash mumkin; shart emas —
  imzo yetarli).

### 7.3. Click kabinetida sozlash

Servis sozlamalarida ikkita manzil yoziladi:

```
Prepare URL:   https://mhisobot.uz/balance/click/prepare/
Complete URL:  https://mhisobot.uz/balance/click/complete/
```

Bu manzillar panel → «Тўлов ва тарифлар» sahifasida ham ko'rinadi (nusxa olish mumkin).
Sayt **https** bilan ishlashi shart.

### 7.4. Serverda

1. Yangi versiya: `sudo /srv/narxlash/app/deploy/update.sh` (migratsiya o'zi bajariladi: jadvallar
   va 4 ta boshlang'ich tarif paydo bo'ladi, pulli rejim o'chiq qoladi).
2. `/srv/narxlash/.env` ga:
   ```
   CLICK_SERVICE_ID=...
   CLICK_MERCHANT_ID=...
   CLICK_SECRET_KEY=...
   CLICK_MERCHANT_USER_ID=...
   ```
   va `sudo systemctl restart narxlash-web`.
3. Tekshiruv (pul harakatlanmaydi):
   ```
   sudo -u narxlash /srv/narxlash/app/deploy/manage.sh billing_check --ping
   ```
   «имзо ўтди» chiqsa — manzil tashqaridan ochiladi va kalit to'g'ri.
4. Panelda: tariflar narxini tekshiring, Созламалар → Тўлов: qator/obyekt narxi, eng kam/eng ko'p summa.
5. **Haqiqiy sinov:** oddiy (xodim emas) hisob bilan kiring → «Баланс ва тариф» → 10 000 so'm to'lang →
   balansga tushdimi? → biror smetani yuklab oling → tarifdan / balansdan hisoblandimi?
6. Hammasi joyida bo'lsa: Созламалар → «Пулли режим» → yoqish.

---

## 8. Panel (admin uchun)

**«Тўлов ва тарифлар»** (`panel/views_billing.py`, `panel/templates/panel/billing.html`) — to'lovga oid
hammasi bitta sahifada, bo'limlarga (`?tab=`) ajratilgan:

| Bo'lim | Nima bor |
|---|---|
| Умумий | 3 blok: **haqiqiy pul** (Click tushumi, sarflangan, balanslarda), **bonus** (berilgan, sarflangan, sovg'a tariflar), boshqa; Click holati; oxirgi to'lovlar |
| Тарифлар | har tarif — karta-forma: narx, qator, obyekt, aksiya % va muddati, «Батафсил» matni; 1/3/6/12 oy narxlari oldindan ko'rinadi |
| Созламалар | pulli rejim, ortiqcha narxlar, to'ldirish min/max, muddat chegirmalari (faqat superuser saqlaydi) |
| Дафтар | barcha yozuvlar: tur, hamyon (haqiqiy / bonus), email yoki izoh bo'yicha filtr; sahifalab |
| Тўловлар | Click to'lovlari, holat bo'yicha filtr |
| Юклаб олинганлар | pul to'langan fayllar |
| Журнал | admin harakatlari (tarif, sozlama, balans tuzatish, sovg'a) — `Event` dan |

**Foydalanuvchi sahifasi → «Баланс ва тариф» tab:**
- Balansga qo'shish / ayirish (sababi majburiy, daftarga yoziladi; «bonus» belgisi).
- Tarif sovg'a qilish.
- Obuna muddatini o'zgartirish.
- Hammasi audit logga (`Event.Kind.ADMIN_BILLING`) yoziladi.

Sozlamalar (`panel/conf.py`, guruh `BILLING_GROUP`): `billing_on`, `billing_row_price`, `billing_object_price`,
`billing_topup_min`, `billing_topup_max`, `billing_disc_3`, `billing_disc_6`, `billing_disc_12`. Ular umumiy
«Созламалар» sahifasida emas — faqat shu yerda.

**Сайт саҳифалари** (`panel/views_pages.py`): «Фойдаланиш шартлари» (`/shartlar/`, to'lov va qaytarish bo'limi
bilan) va **«Махфийлик сиёсати»** (`/maxfiylik/`) — matni panelda tahrirlanadi, bo'sh bo'lsa standart
(`jobs/pages.py`: `TERMS_DEFAULT`, `PRIVACY_DEFAULT`). Ro'yxatdan o'tishda ikkalasiga havola bor.

---

## 9. Muammolar bo'lsa

| Belgi | Qayerga qarash |
|---|---|
| Foydalanuvchi to'ladi, balans tushmadi | Panel → To'lovlar: holati `prepared` bo'lsa — Complete kelmagan, Click kabinetida tranzaksiyani qidiring. Server logida `billing.click` yozuvlari. |
| `billing_check` «имзо ўтмади» | `.env` dagi `CLICK_SECRET_KEY` Click'dagidan farq qiladi yoki xizmat qayta ishga tushirilmagan |
| `billing_check` «HTTP 4xx/5xx» | nginx yoki sayt so'rovni o'tkazmadi; https ishlayaptimi |
| Balans yozuvlar yig'indisiga teng emas | `billing_check` qaysi foydalanuvchi ekanini aytadi — kimdir bazani qo'lda o'zgartirgan |
| Pulni qaytarish kerak | Panel → foydalanuvchi → balansdan ayirish (sababi bilan) + Click kabinetidan qaytarish |

---

## 10. Testlar

```
venv/Scripts/python.exe -X utf8 manage.py test billing
```

43 ta test, guruhlar:
- `OffTests` — rejim o'chiq: hamma narsa bepul;
- `RowsTests` — avval tarif, keyin balans, keyin yopiq;
- `SubscriptionTests` — sotib olish, o'zi yangilanish, sovg'a;
- `TariffChangeTests` — qimmatrog'iga (credit), arzonrog'iga (keyingi davr), bekor qilish, min/max summa;
- `LedgerTests` — daftar, `ref` takrorlanmasligi;
- `PanelTests`, `ProfileTests` — sahifalar;
- `GateUnitTests`, `GateTexnazorTests` — yuklab olish darvozasi;
- `ClickTests` — to'liq oqim, qayta yuborish, noto'g'ri imzo/summa/buyurtma, Click xatosi, qaytish sahifasi;
- `BillingCheckTests` — tekshiruv buyrug'i va `--ping`;
- `WalletTests` — ikki hamyon: avval bonus, haqiqiy pul minusga tushmasligi;
- `PeriodTests` — 3/6/12 oy narxi, chegirmalar, oyma-oy qatorlar.

---

## 11. Hali qilinmagan

- **Fiskal chek** (ИКПУ / OFD) — Click'dan so'rash kerak.
- **Payme** — keyin (`Payment.provider` maydoni tayyor, `click.py` kabi `payme.py` yoziladi).
- **Avtomatik qaytarish** — hozir admin qo'lda.
- Tarif nomlari va izohlari bazada — tarjima qilinmaydi (rus tilida ham o'sha nom).
- Huquqiy matnlarni (shartlar, maxfiylik siyosati) yurist bilan bir ko'rib chiqish — standart matn umumiy.
