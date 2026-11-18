# Ekran videosini yozish — to'liq qo'llanma

Bu fayl — **qanday yozish** (dastur, sozlama, tartib) va **har bir darsda nima deyish** (so'zma-so'z matn).
Darslar ro'yxati va qisqa rejasi: [video-darslar.md](video-darslar.md).

Tugma nomlari ekrandagidek kirill harfida yozilgan — ovozda ham aynan shu so'zlarni ayting, tomoshabin ekranda shu yozuvni qidiradi.

---

## 1-qism. Nima kerak

| Nima | Qaysi | Izoh |
|------|-------|------|
| Ekran yozish | **OBS Studio** (bepul, obsproject.com) | Asosiy tavsiya. Sifatli, suv belgisi yo'q. |
| Oddiyroq yo'l | Windows **Xbox Game Bar** — `Win + Alt + R` | Faqat bitta oynani yozadi. Sozlash shart emas, lekin ovozni boshqarish qiyin. |
| Montaj (kesish) | **Clipchamp** (Windows'da bor yoki Microsoft Store'dan bepul) | 1080p eksport bepul. Xatolarni kesish, boshida/oxirida ozgina qo'shish uchun yetadi. |
| Mikrofon | Quloqchin mikrofoni yoki USB mikrofon | Noutbukning ichki mikrofoni — oxirgi chora (shovqin ko'p). |
| Brauzer | Chrome yoki Edge, **alohida profil** | Xatchilar, kengaytmalar, boshqa yorliqlar ko'rinmasin. |

---

## 2-qism. Bir marta tayyorgarlik (30–40 daqiqa)

### 2.1. Sinov hisobi va namunaviy fayllar

1. Saytda **alohida sinov hisobi** oching (masalan «Намуна смечи»). Haqiqiy mijoz ismi, email, obyekt nomi videoda ko'rinmasligi shart.
2. Bitta papka tayyorlang, masalan `Desktop\video-fayllar\`:
   - `01-bitta-smeta\` — bitta lokal + bitta RES fayl (2-dars uchun);
   - `02-obyekt\` — har bir smeta o'z ichki papkasida bo'lgan obyekt papkasi + uning ZIP'i (3–4-darslar);
   - `03-tushunmaydi\` — tizim «Тизим тушунмади» deydigan list bor fayl (6-dars);
   - `04-tuzatilgan\` — 7-dars uchun bitta faylning tuzatilgan nusxasi;
   - `05-narxlanmagan\` — narxlanmagan qatorlari ko'p smeta (8-dars).
3. Fayllar ichida ham obyekt nomi, buyurtmachi, manzil — **o'zgartirilgan** bo'lsin.
4. Har bir darsni yozishdan oldin sinov hisobida o'sha holatni **bir marta oldindan o'tib chiqing** — natija kutilgandek ekanini tekshiring. Keyin o'sha ishni o'chirib, toza holatdan yozing.

### 2.2. Kompyuter

- Ekran o'lchami **1920×1080**, Windows masshtabi **100%** (Параметры → Дисплей → Масштаб). 125% bo'lsa, videoda hamma narsa katta va kesilgan chiqadi.
- Brauzer masshtabi **100%** (`Ctrl + 0`). Brauzer oynasi to'liq ekranga (`F11` emas — oddiy kattalashtirilgan oyna, manzil satri ko'rinsin).
- Yoqing: **Фокусировка внимания / Не беспокоить** — Telegram, pochta bildirishnomalari chiqmasin.
- Telegram, WhatsApp, pochta — yopiq. Ish stolidagi shaxsiy fayllar ko'rinmasin (fayl tanlash oynasida ko'rinadi!).
- Sichqoncha ko'rsatkichini kattaroq qiling (Параметры → Специальные возможности → Указатель мыши → 2-o'lcham). Tomoshabin qayerga bosilganini oson ko'radi.

### 2.3. OBS Studio sozlash

1. O'rnating, birinchi ochilganda sehrgar so'raydi → **«Оптимизировать для записи»** → 1920×1080, 30 FPS.
2. **Настройки → Вывод (Output)**:
   - Режим вывода: *Простой*;
   - Путь записи: masalan `Desktop\video-yozuvlar`;
   - Качество записи: *Высокое качество, средний размер файла*;
   - Формат записи: **MP4** (yoki MKV, keyin «Файл → Ремультиплексировать» bilan MP4 ga);
   - Кодировщик: kompyuterda NVIDIA bo'lsa *NVENC*, bo'lmasa *x264*.
3. **Настройки → Видео**: Базовое и выходное разрешение — 1920×1080, FPS — 30.
4. **Настройки → Горячие клавиши**: «Начать запись» — `F9`, «Остановить запись» — `F10`, «Приостановить» — `F8`. Shunda OBS oynasiga o'tish shart bo'lmaydi.
5. **Источники (Sources)** → «+» → **Захват экрана** (Display Capture) → monitoringizni tanlang.
6. **Микшер аудио**:
   - Mikrofon/Aux — sizning mikrofoningiz;
   - «Звук рабочего стола» — **o'chiring** (karnay belgisini bosing), aks holda Windows ovozlari yoziladi.
7. Mikrofon yonidagi ⚙ → **Фильтры** → «+»:
   - **Подавление шума** (RNNoise) — fon shovqinini olib tashlaydi;
   - **Шумовой порог** (Noise Gate) — gapirmaganda jimlik;
   - kerak bo'lsa **Компрессор** — ovoz bir tekis.
8. **Sinov yozuvi:** 20 soniya yozing, biror narsani bosing, gapiring. Keyin ochib eshiting:
   - ovoz aniqmi, juda past/baland emasmi (mikser ko'rsatkichi gapirganda **sariq** zonaga yetsin, qizilga emas);
   - matn o'qiladimi, sichqoncha ko'rinadimi.

---

## 3-qism. Har bir darsni yozish tartibi

1. **Matnni 2 marta ovoz chiqarib o'qing** (pastdagi 5-qism). Yod olish shart emas — o'z so'zingiz bilan ayting, lekin tugma nomlari aynan bo'lsin.
2. Brauzerda kerakli sahifani oching, sinov hisobi, toza holat.
3. Matnni ikkinchi monitorda yoki telefonda oching (yozuvga tushmasin).
4. Xonada jimlik, telefon ovozsiz.
5. `F9` — yozuv boshlandi. **3 soniya jim turing** (keyin montajda kesish oson).
6. Darsni gapiring. Qoidalar:
   - Har bosishdan oldin sichqonchani tugma ustiga olib boring, **1 soniya to'xtang**, keyin bosing.
   - Avval ayting, keyin bosing: "Endi «Бошлаш»ni bosamiz" → bosish.
   - Sahifa yuklanayotganda jim turmang: "Tizim fayllarni o'qiyapti, bir necha soniya kutamiz."
   - Sichqonchani bekordan aylantirmang, tinch qo'ying.
7. **Xato qilsangiz to'xtamang.** 2 soniya jim turing, o'sha gapni boshidan qayta ayting. Montajda xato joyni kesasiz. Juda chalkashib ketsa — `F10`, qaytadan.
8. Oxirgi gapdan keyin 2 soniya jim → `F10`.
9. Faylni darhol nomlang: `02-bitta-smeta-yuklash.mp4`.

**Davomiylik:** bitta dars — bitta ish, **2–4 daqiqa**. 5 daqiqadan oshsa — ikkiga bo'ling.

**Yozish ketma-ketligi (qulay tartib):**
2 → 3 → 4 → 6 → 5 → 7 → 8 → 9 → 10 → 11 → 13 → **1** (eng oxirida).
2-dars eng oson — birinchi o'shandan qo'lingiz ochiladi. 1-dars (tanishuv) hammasini ko'rib bo'lgach yaxshi chiqadi.

---

## 4-qism. Montaj va joylash

### 4.1. Clipchamp'da kesish (har dars 5–10 daqiqa)

1. «Создать видео» → videoni sudrab tashlang → pastdagi lentaga qo'ying.
2. Boshidagi 3 soniya jimlikni va xato joylarni kesing: kursorni joyga qo'ying → **S** (bo'lish) → keraksiz bo'lakni tanlang → `Delete`.
3. Uzoq kutishlar (fayl yuklanishi 15 soniya) — kesing yoki tezlashtiring (bo'lakni tanlang → «Скорость» → 2×–4×).
4. Ixtiyoriy: muhim tugma ustiga matn yozuvi («Текст») — masalan «Белгилаш».
5. Boshiga/oxiriga musiqa **qo'ymang** — darsda kerak emas, YouTube ham mualliflik huquqi bo'yicha embed'ni taqiqlashi mumkin.
6. **Экспорт → 1080p.**

### 4.2. YouTube'ga yuklash

1. YouTube Studio → «Создать» → «Загрузить видео».
2. Nomi: saytdagi bilan bir xil, masalan «Бир смета юклаш».
3. Tavsif: 1–2 gap, nimani o'rgatadi.
4. «Для детей» — *Нет*.
5. Ko'rinish: **«Доступ по ссылке» (Unlisted)** — qidiruvda chiqmaydi, faqat sayt orqali ko'rinadi.
6. Muhim: «Дополнительно» → **«Разрешить встраивание»** yoqilgan bo'lsin, aks holda saytda «Видео недоступно» chiqadi.
7. Havolani nusxalang (`https://youtu.be/...`).

### 4.3. Saytga qo'yish

1. Panel → **«Блог: мақола ва видео»** → «Видео қўлланма» yorlig'i → **«Видео қўшиш»**.
2. Nomi, YouTube havolasi, **«Бўлим»** — jadvaldagi «Saytda sahifa» ustunidagi qiymat (shu sahifadagi «▶ Видео» tugmasida chiqadi).
3. Tavsif — uzun matn bo'lishi mumkin, video ostida chiqadi.
4. Saqlang → o'sha sahifaga kiring → tepada «▶ Видео» chiqqanini va ochilishini tekshiring.
5. Tartibni ↑↓ bilan to'g'rilang (1-dars eng tepada).

| # | Dars | «Бўлим» | Fayl nomi |
|---|------|---------|-----------|
| 1 | Tizim bilan tanishuv | Умумий | `01-tanishuv.mp4` |
| 2 | Bitta smetani yuklash | Юклаш | `02-bitta-smeta.mp4` |
| 3 | Butun obyektni yuklash | Юклаш | `03-obyekt-papka.mp4` |
| 4 | Papkani tekshirish | Папка | `04-papka-tekshirish.mp4` |
| 5 | Juftliklar, resursni almashtirish | Объект | `05-juftliklar.mp4` |
| 6 | Tushunmagan listni belgilash | Объект | `06-belgilash.mp4` |
| 7 | Qayta yuklash va «Ортга» | Папка | `07-qayta-yuklash.mp4` |
| 8 | Narxlanmaganlarni to'g'rilash | Смета | `08-togrilash.mp4` |
| 9 | Yuklab olish va LOG | Смета | `09-yuklab-olish.mp4` |
| 10 | Иш столи | Иш столи | `10-ish-stoli.mp4` |
| 11 | Созламалар | Умумий | `11-sozlamalar.mp4` |
| 13 | Калькулятор | Калькулятор | `13-kalkulyator.mp4` |

(12 — Офёрта: bo'lim tayyor bo'lgach yoziladi.)

---

## 5-qism. Darslar matni — nima deyish

Jadvalda chapda — ekranda nima qilasiz, o'ngda — nima deysiz. Qavs ichidagi `[…]` — o'zingizning haqiqiy soningiz / nomingiz bilan almashtiring.

### 1-dars. Tizim bilan tanishuv (2–3 daq) — «Умумий»

| Ekranda | Aytiladi |
|---------|----------|
| Иш столи sahifasi ochiq | "Assalomu alaykum. Bu darsda tizim bilan qisqacha tanishamiz: qayerda nima bor va smeta qanday narxlanadi." |
| Chap menyuda «Иш столи» ustiga | "Chapda asosiy menyu. «Иш столи» — barcha ishlaringiz shu yerda: oxirgi yuklanganlar, qaysi biri tayyor, qaysi birida ish qolgan." |
| «Смета нархлаш» ustiga | "«Смета нархлаш» — asosiy bo'lim. Smetani yuklaysiz, tizim har bir resursga narx qo'yib beradi." |
| «Офёрта ва далолатнома» ustiga | "«Офёрта ва далолатнома» — bajarilgan ishlar bo'yicha hujjatlar." |
| «Калькулятор» ustiga | "«Калькулятор» — tez hisob-kitoblar uchun." |
| «Смета нархлаш»ni bosing | "«Смета нархлаш»ni ochamiz." |
| Tepadagi qadamlar chizig'i ustida sichqoncha chapdan o'ngga | "Tepada to'rt qadam: **Юклаш, Текшириш, Тўғирлаш, Юклаб олиш**. Har bir sahifada shu chiziq bor — hozir qaysi qadamda ekaningizni ko'rsatadi." |
| «Йўриқнома»ni bosing, oching, yoping | "«Йўриқнома» tugmasi — shu sahifaning qisqa qo'llanmasi. Adashsangiz, shu yerdan o'qing." |
| Biror «?» belgisini bosing | "Ba'zi yozuvlar yonida so'roq belgisi bor. Bossangiz — bu nima degani ekanini tushuntiradi." |
| «▶ Видео» tugmasi ustiga | "«▶ Видео» — shu sahifa bo'yicha video dars. Har bir sahifada o'zining darsi." |
| — | "Keyingi darsda bitta smetani yuklab, narxlab ko'ramiz." |

### 2-dars. Bitta smetani yuklash (3 daq) — «Юклаш»

**Tayyor:** `01-bitta-smeta\` papkasi Explorer'da ochiq, brauzer yonida.

| Ekranda | Aytiladi |
|---------|----------|
| «Смета нархлаш» sahifasi | "Bu darsda bitta smetani yuklaymiz. Menda ikki fayl bor: lokal smeta — ish hajmlari, va RES — resurslar ro'yxati. Ikkalasi bitta faylda bo'lsa ham bo'ladi." |
| 1-qadam ustiga | "Birinchi qadam — fayl tanlash." |
| Ikki faylni maydonga sudrang, maydon ko'k bo'lganda to'xtang | "Fayllarni shunchaki shu maydonga sudrab tashlaymiz. Ko'rib turibsiz — maydon ko'k bo'ldi, demak qabul qiladi." *(yoki: "«Файл танлаш» tugmasi orqali ham tanlash mumkin.")* |
| Progress, o'ng pastdagi jarayon oynasi | "Fayllar o'zi yuklanadi. O'ng pastda jarayon ko'rinadi — tizim fayllarni o'qiyapti." |
| 2-qadam — listlar va juftliklar | "Ikkinchi qadam shu sahifaning o'zida ochildi. Chapda — lokal, ya'ni ish hajmlari. O'ngda — uning resursi, ya'ni narxlar shu yerdan olinadi. Tizim ularni o'zi juftladi." |
| Foiz ustiga | "Bu foiz — lokal va resurs qanchalik mos kelishi. Yuqori bo'lsa — hammasi joyida." |
| «Созламалар»ni oching | "«Созламалар»da usul — Автоматик yoki Қўлда — va narx manbai. Odatda bunga tegmaysiz, tizim o'zi to'g'ri tanlaydi." |
| «Созламалар»ni yoping, yashil «Бошлаш»ga | "Hammasi tayyor. Pastdagi yashil «Бошлаш»ni bosamiz." |
| Bosing, jarayon | "Tizim narxlayapti. Tugagach smeta sahifasi ochiladi — u yerda natijani tekshirasiz. Bu haqida alohida darsda." |

### 3-dars. Butun obyektni yuklash — papka yoki ZIP (3 daq) — «Юклаш»

| Ekranda | Aytiladi |
|---------|----------|
| «Смета нархлаш» sahifasi | "Obyektda smetalar ko'p bo'lsa, ularni bittalab yuklash shart emas. Butun papkani bir martada yuklaymiz." |
| «Папка танлаш»ni bosing, `02-obyekt` papkasini tanlang | "«Папка танлаш»ni bosamiz va obyekt papkasini tanlaymiz. Ichida har bir smeta o'z papkasida." |
| Brauzer "yuklashga ruxsat" so'rasa — «Загрузить» | "Brauzer ruxsat so'raydi — «Загрузить»ni bosamiz. Fayllar faqat shu saytga yuboriladi." |
| Sariq karta | "Mana: [N] ta Excel fayl, [M] ta papka topildi." |
| «Бошлаш» | "«Бошлаш»ni bosamiz." |
| Papka sahifasi, fayllar ochilmoqda | "Papka sahifasi ochildi. Tizim fayllarni ochyapti — sahifa o'zi yangilanadi, hech narsa bosish shart emas." |
| (Qisqa) qaytib, «ZIP / RAR» bilan tanlang | "Agar obyekt arxivda bo'lsa — ZIP yoki RAR — xuddi shunday, shu tugma orqali yuklanadi." |
| — | "Eslatma: fayl nomi muhim emas. PDF, rasmlar va oldingi natijalar — «нархланди» deb nomlangan fayllar — o'zi o'tkazib yuboriladi. Papkani boricha tashlayvering." |

### 4-dars. Papkani tekshirish va «Ҳаммасини бошлаш» (3–4 daq) — «Папка»

| Ekranda | Aytiladi |
|---------|----------|
| Papka sahifasi | "Obyekt yuklandi. Endi boshlashdan oldin tekshiramiz." |
| Chapdagi daraxt, o'ngdagi qismlar | "Chapda — papkalar daraxti, xuddi kompyuteringizdagidek. O'ngda — har bir smeta qismi." |
| Tepadagi ikki qadam | "Tepada ikki qadam yozilgan: birinchi — «Қисмларни текширинг», ikkinchi — pastdagi «Ҳаммасини бошлаш»." |
| «Биринчисини очиш» | "Agar biror qismga e'tibor kerak bo'lsa, tizim ko'rsatadi. «Биринчисини очиш»ni bosamiz — o'sha qism ochildi va miltilladi." |
| Juftlikdagi foizni bosing | "Foizni bossak — lokal va resurs yonma-yon ochiladi. To'g'ri juftlanganini ko'z bilan tekshirish mumkin." |
| Daraxtdan boshqa papkani bosing | "Daraxtdan papkani bossangiz — o'sha qism ochiladi." |
| «Сводка фоизлари»ni oching | "«Сводка фоизлари» — qo'shimcha xarajatlar foizlari. Odatda tegilmaydi, kerak bo'lsa shu yerda o'zgartirasiz." |
| Yashil «Ҳаммасини бошлаш» | "Hammasi tekshirildi. Pastdagi yashil «Ҳаммасини бошлаш»ni bosamiz." |
| Jarayon, «Тўхтатиш» | "Barcha smetalar navbat bilan narxlanadi. Kerak bo'lsa «Тўхтатиш» bilan to'xtatish mumkin." |

### 5-dars. Listlar va juftliklar: resursni almashtirish (3 daq) — «Объект»

| Ekranda | Aytiladi |
|---------|----------|
| Juftlik kartasi | "Bu darsda tizim noto'g'ri juftlagan holatni to'g'rilaymiz." |
| «Кўриш» | "«Кўриш» — faylni Excel ko'rinishida ochadi. Ichida nima borligini shu yerda ko'rasiz." |
| Foizi past juftlik | "Bu juftlikda foiz past — demak lokal va resurs bir-biriga yaxshi mos kelmayapti." |
| «Ресурсни алмаштириш» → boshqasini tanlang | "«Ресурсни алмаштириш»ni bosamiz va ro'yxatdan to'g'ri resursni tanlaymiz. Foiz o'zgardi." |
| Локал / Ресурс / Ўтказиш tugmalari | "Bu tugmalar list turini bir bosishda o'zgartiradi: lokal, resurs yoki o'tkazib yuborish." |
| «Керак эмас деб топилганлар»ni oching | "Pastda «Керак эмас деб топилганлар» — tizim keraksiz deb hisoblagan listlar. Agar bu yerda kerakli list bo'lsa…" |
| Listni «Локал»ga o'tkazing | "…«Локал»ni bosamiz — list ishga qaytdi." |

### 6-dars. Tizim tushunmagan listni belgilash (3–4 daq) — «Объект»

Bu — eng muhim dars. Sekin, aniq gapiring.

| Ekranda | Aytiladi |
|---------|----------|
| Sariq guruh «Тизим тушунмаган листлар — белгиланг» | "Ba'zan smeta g'ayrioddiy tuzilgan bo'ladi va tizim uni o'qiy olmaydi. Shunda sariq guruhda chiqadi: «Тизим тушунмаган листлар — белгиланг»." |
| «Белгилаш» | "Katta «Белгилаш» tugmasini bosamiz. Tizim qadamma-qadam so'raydi." |
| 1. List turi | "Birinchi: bu qanday list? Lokal — ish hajmlari, Ресурс — narxlar, yoki Керак эмас. Men «Локал»ni tanlayman." |
| 2. Birinchi qator — raqam miltillaydi, bosing | "Ikkinchi: ma'lumot qaysi qatordan boshlanadi. Qator raqamlari miltillayapti — birinchi ish yozilgan qatorni bosaman." |
| 3. Ustunlar — nom, birlik, miqdor | "Uchinchi: ustunlar. Ish nomi qaysi ustunda — bosaman. Birlik — bosaman. Miqdor — bosaman." |
| 4. Oxirgi qator | "To'rtinchi: oxirgi qator. Shu yerda tugaydi." |
| «Сақлаш ва қайта жуфтлаш» | "«Сақлаш ва қайта жуфтлаш»ni bosamiz." |
| Xabar | "[N] ta qator o'qildi." |
| — | "Eng muhimi: **tizim eslab qoldi.** Keyingi safar shunday tuzilishdagi list kelsa — nomi boshqacha bo'lsa ham — o'zi o'qiydi. Bir marta belgilaysiz, keyin u o'zi ishlaydi." |

### 7-dars. Xato faylni qayta yuklash va «Ортга» (2 daq) — «Папка»

| Ekranda | Aytiladi |
|---------|----------|
| Daraxtda fayl ustiga keling | "Faylda xato topdingiz va Excel'da tuzatdingiz. Obyektni boshidan yuklash shart emas." |
| Uch belgi ko'rinadi | "Fayl ustiga kelsangiz uchta belgi chiqadi: yuklab olish, qayta yuklash va ortga." |
| Qayta yuklash → `04-tuzatilgan` faylini tanlang | "Qayta yuklashni bosamiz va tuzatilgan faylni tanlaymiz." |
| «… ўрнига … юклансинми?» → «Ҳа, юклаш» | "Tizim so'raydi: eski fayl o'rniga yangisi yuklansinmi? «Ҳа, юклаш»." |
| O'ng pastda jarayon | "Jarayon o'ng pastda. Sahifa o'zi yangilanadi, ochiq turgan qism yana ochiladi." |
| «Ортга» ustiga | "Agar yangisi yoqmasa — «Ортга» oldingi nusxani qaytaradi. Qo'lda qilgan o'zgarishlaringiz saqlanib qoladi." |

### 8-dars. Narxlanmagan qatorlarni to'g'rilash (4 daq) — «Смета»

| Ekranda | Aytiladi |
|---------|----------|
| Smeta sahifasi, qizil sonlar | "Smeta narxlandi. Qizil sonlar — tizim narx topa olmagan qatorlar. Ularni to'g'rilaymiz." |
| «Топилмаганлар» | "«Топилмаганлар»ni ochamiz. Har bir qator uchun tizim taklif beradi." |
| Taklifni qabul qiling | "Taklif to'g'ri bo'lsa — qabul qilamiz." |
| Narxni qo'lda yozing | "Taklif yo'q yoki noto'g'ri bo'lsa — narxni o'zim yozaman." |
| Bir nechta qatorni belgilab, birdaniga narx | "Bir xil resurs ko'p qatorda bo'lsa — hammasini belgilab, birdaniga narx qo'yamiz." |
| Kabel/uskunani L / M ga o'tkazish | "Kabel va uskunalar — alohida ustunga, L yoki M ga o'tkaziladi. Mana shunday." |
| «Сводка» — foizni o'zgartiring | "«Сводка»da foizlarni o'zgartirsangiz — natija darrov qayta hisoblanadi." |
| ТОПИЛМАДИ / ТЕКШИРИНГ yonidagi «?» | "Har bir belgi yonidagi so'roq — uning ma'nosini aytadi. Masalan «ТЕКШИРИНГ» — narx bor, lekin bir ko'z tashlang degani." |

### 9-dars. Natijani yuklab olish va LOG listi (2 daq) — «Смета»

| Ekranda | Aytiladi |
|---------|----------|
| «Юклаб олиш» | "Hammasi tayyor. «Юклаб олиш»ni bosamiz. Bitta smeta — Excel, papka bo'lsa — ZIP, papkalar tuzilishi o'zgarmaydi." |
| Excel'da oching | "Ochamiz. Asl faylingiz o'zgarmagan — natija «нархланди» qo'shimchasi bilan alohida fayl." |
| LOG listi | "Oxirida LOG listi bor. Bu yerda har bir resursga qaysi narx olingani, qayerdan olingani, qaysilari qo'lda kiritilgani yozilgan. Tekshiruvchi so'rasa — shu listni ko'rsatasiz." |

### 10-dars. Иш столи (2 daq) — «Иш столи»

| Ekranda | Aytiladi |
|---------|----------|
| «Иш столи» | "«Иш столи» — barcha ishlaringiz." |
| Holat belgilari | "Har bir ish yonida holati: «Тайёр — бошлаш», yoki nechta qator narxlanmagani. Qaysi ishda nima qolganini bir qarashda ko'rasiz." |
| Ishni oching | "Ishni bossangiz — qolgan joyidan davom etasiz." |
| Keraksizini o'chirish | "Keraksiz ishni shu yerda o'chirish mumkin." |

### 11-dars. Созламалар (2 daq) — «Умумий»

| Ekranda | Aytiladi |
|---------|----------|
| Chap menyu → «Созламалар» | "«Созламалар» — tizimni o'zingizga moslash." |
| Ish usuli, narx manbai | "Ish usuli — Автоматик yoki Қўлда. Narx manbai va davri — narxlar qaysi bazadan olinadi." |
| «Йўриқнома ўзи очилсин» | "Yo'riqnoma har sahifada o'zi ochilsinmi — o'rganib olgach o'chirib qo'yishingiz mumkin." |
| «Изоҳ белгилари» | "So'roq belgilari ko'rinsinmi." |
| «Ихчам жадваллар» → «Сақлаш» → boshqa sahifa | "«Ихчам жадваллар» — jadvallar zichroq, ekranga ko'proq sig'adi. Saqlaymiz… mana, jadval ixcham bo'ldi." |

### 13-dars. Калькулятор (2 daq) — «Калькулятор»

Kalkulyator sahifasini ochib, har bir maydonni ko'rsating: "Bu maydonga … yoziladi", bitta misolni boshidan oxirigacha hisoblang, natija qayerda chiqishini ko'rsating. Matnni yozishdan oldin sahifaga qarab o'zingiz to'ldiring.

---

## 6-qism. Tez-tez uchraydigan xatolar

| Muammo | Sabab / yechim |
|--------|----------------|
| Videoda matn xira | Windows masshtabi 100% emas yoki OBS'da chiqish o'lchami 1280×720. 2.2 va 2.3 ni tekshiring. |
| Ovoz yo'q | OBS mikserida mikrofon o'chiq yoki noto'g'ri qurilma. Har gal 5 soniyalik sinov yozing. |
| Ovozda shovqin/aks-sado | Mikrofonga yaqinroq; yumshoq narsalar ko'p xonada yozing; «Подавление шума» filtri. |
| Saytda «Видео недоступно» | YouTube'da «Разрешить встраивание» o'chiq. |
| «▶ Видео» chiqmayapti | Panelda «Бўлим» noto'g'ri tanlangan. |
| Videoda shaxsiy ma'lumot ko'rinib qoldi | Yuklamang, qayta yozing. Clipchamp'da xiralashtirish ham mumkin, lekin qayta yozish ishonchliroq. |
| Dizayn o'zgardi | Faqat o'sha sahifaning darsini qayta yozasiz — darslar shuning uchun sahifa bo'yicha bo'lingan. |
