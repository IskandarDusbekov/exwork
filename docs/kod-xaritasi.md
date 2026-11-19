# Kod xaritasi — qaysi narsa qayerda

Bu fayl tizimni o'qib tushunish uchun: har bir papka nima qiladi, asosiy funksiyalar qayerda, bir smeta
yuklangandan yuklab olingunicha qaysi fayllardan o'tadi. Kod ichidagi izohlar kirill yozuvida.

## 1. Umumiy ko'rinish

> Jonli, rasmli variant: **panel → «Тизим қандай ишлайди»** (`/panel/tizim/`, faqat superuser) — har bir
> jarayon qadamma-qadam, kodi bilan (faqat o'qish), va serverning hozirgi holati. Darslar matni:
> `panel/tizim_lessons.py`; funksiya nomi o'zgarsa `panel/tests_tizim.py` yiqiladi — shu faylni yangilang.

```
brauzer ──► web/urls.py ──► ilova (jobs, panel, texnazor, …) views ──► services ──► core (Excel bilan ish)
                                                              │
                                                              └──► jobs/worker (fonda narxlash)
```

* **core/** — Django'siz «yadro»: Excel'ni o'qish, ustunlarni topish, RES bilan solishtirish, natijani yozish.
  Saytsiz ham ishlaydi (`python -m core.pipeline …`). Eng muhim va eng ko'p tekshirilgan qism.
* **jobs/** — smeta narxlash sayti: yuklash, listlarni ajratish, narxlash, natija sahifasi, papka/arxiv.
* **panel/** — admin paneli: foydalanuvchilar, statistika, xavfsizlik, zaxira, sozlamalar, blog.
* **accounts/** — kirish, ro'yxatdan o'tish, parol, ikki bosqichli kirish (2FA), urinishlarni cheklash.
* **resbase/** — eski resurs bazasi (kod + narx, viloyat/davr bo'yicha median), ускуна/кабель bazasi.
* **narxbaza/** — yangi narx bazasi (kodsiz: nom + birlik, past/o'rta/yuqori narx).
* **texnazor/** — ofyorta va dalolatnomalar (Ф-2, Ф-3, qoldiq).
* **support/**, **news/** — yordam chati va e'lonlar.
* **web/** — sozlamalar (`settings.py`), asosiy URL'lar.
* **deploy/** — server: gunicorn, nginx, systemd, zaxira (`backup.sh`, `restore.sh`), yo'riqnoma `README.md`.

## 2. Bitta smetaning yo'li (yuklashdan yuklab olishgacha)

| Qadam | Nima bo'ladi | Fayl / funksiya |
|---|---|---|
| 1. Yuklash | Fayl saqlanadi, `.xls` → `.xlsx` | `jobs/views.py: index` → `jobs/services.py: create_batch` → `core/convert.py` (LibreOffice навбати: `jobs/lo_queue.py`, бир вақтда 2 та) |
| 2. Listlarni ajratish | Har list: lokal / resurs / keraksiz; juftlash | `jobs/services.py: analyze_batch` → `core/batch.py: inspect_workbook, plan_pairs` |
| 2a. «Қўлда» usuli | Fayl smetachi belgilaguncha o'qilmaydi | `analyze_batch(lazy=True)`; tugmalar `jobs/views.py: batch_pick` |
| 2b. Belgilash sahifasi | Tizim tushunmagan listni smetachi ko'rsatadi, eslab qolinadi | `jobs/views.py: batch_mark`, `jobs/models.py: SheetMemo` |
| 3. Boshlash | Tanlov navbatga qo'yiladi (so'rov darhol qaytadi), fon ishchisi har juftlik → alohida `Job` | `jobs/views.py: batch_confirm` → `jobs/pricing.py` (navbat) → `jobs/services.py: confirm_batch` |
| 4. Narxlash | Ustunlar, RES indeksi, solishtirish, yozish, svodka | `core/pipeline.py: run` → `core/detect.py`, `core/columns.py`, `core/matcher.py`, `core/writer.py`, `core/svodka.py` |
| 5. Smetachi tuzatishlari | Narx yozish, taklifni qabul qilish, L/M ga o'tkazish | `jobs/views.py: job_*` → `jobs/fastpatch.py` (tez yamash) yoki `jobs/recalc.py` (qayta hisoblash) |
| 6. Yuklab olish | Natija Excel + LOG listi | `jobs/views.py: job_download`, `core/log_sheet.py` |

Papka yoki ZIP/RAR yuklansa: `jobs/folders.py` (qismlarga ajratish, fonda ochish), `jobs/archives.py` (arxivni
xavfsiz ochish, hajm va chuqurlik cheklovlari).

## 3. Katta fayllar ichida nima bor

**`jobs/views.py`** (~2400 qator, 110 funksiya) — eng katta fayl, bo'limlari:

| Qatorlar (taxminan) | Bo'lim |
|---|---|
| 1–540 | Yuklash sahifasi, sozlama tugmalari, ishlar ro'yxati |
| 540–760 | Breadcrumb, papka (loyiha) sahifalari |
| 760–1420 | Obyekt: listlar jadvali (`_picker_ctx`), `batch_pick`, juftlash, belgilash (`batch_mark`) |
| 1420–2330 | Smeta sahifasi (`job`) va undagi amallar: narx, topilmaganlar, L/M, foizlar, ustunlar, Excel ko'rinishi |
| 2330–2400 | Kalkulyator, yo'riqnoma, smetachi sozlamalari |

**`core/pipeline.py`** — narxlashning o'zi: `run()` bitta funksiya hamma bosqichni chaqiradi; `Options` — barcha
sozlamalar bir joyda.

**`jobs/services.py`** — sahifalar chaqiradigan ish mantiqi: yuklash, tahlil (`analyze_batch`), narxlash
(`confirm_batch`), foydalanuvchi sozlamalari (`nf_auto_of`, `remembered_percents` …), ortga qaytarish tarixi.

**`panel/views_narx.py`** — narx bazasi bo'yicha admin sahifalari (yuklash, tekshirish, ro'yxat).

## 4. Ma'lumot qayerda saqlanadi

* **Baza** (production'da PostgreSQL): foydalanuvchilar, ishlar (`Batch`, `Job`, `Project`), sozlamalar
  (`Setting` — har foydalanuvchiniki, `SiteSetting` — butun sayt), narx bazalari, jurnal (`panel.Event`).
* **media/** — fayllar: `batches/<id>/` (yuklangan, o'girilgan, `scan/` — listlar tahlili keshi,
  `pick_undo.json` — «Ортга қайтариш» tarixi), `jobs/<id>/` (natija), `projects/<id>/`.
* **backups/** — kunlik baza nusxasi va media suratlari (`deploy/backup.sh`).

## 5. Testlar

Hammasi bitta buyruq bilan (pytest — `manage.py test` emas: panel testlari uchun `conftest.py` kerak):

```bash
venv/Scripts/python.exe -m pytest -q
```

* `core/tests/` — yadro (Excel namunalari bilan).
* `<ilova>/tests*.py` — har ilova sahifalari va mantiqi.

## 6. Yangi narsa qo'shganda qoidalar

* Excel bilan ishlash — `core/` ga (Django'siz), sahifa mantiqi — `<ilova>/services.py` ga, `views.py` faqat
  so'rovni qabul qilib, natijani ko'rsatadi.
* Listni ajratish va juftlashda nomlarga emas, tuzilmaga tayaning (fayl, list nomi, smeta raqami, ustun joyi).
* Smetachi qo'lda qilgan har narsa saqlanadi va qayta tahlilda o'chmaydi; adashsa — qaytarish yo'li bo'lsin.
