# Ofyorta qanday o'qiladi va dalolatnoma qanday hisoblanadi

Bu hujjat «Офёрта ва далолатнома» bo'limini boshidan oxirigacha tushuntiradi:
fayl yuklanganidan to narxlangan Excel hisobot yuklab olinguncha nima bo'ladi.

Asosiy fikr bitta: **tizim ofyortani qayta yozmaydi va o'zidan narx o'ylab topmaydi.**
U fayldagi jadvalni, fayldagi **formulalar bilan birga** o'qiydi. Dalolatnomada faqat
**hajm kataklarini** almashtiradi, qolganini fayldagi formulalar o'zi hisoblaydi.
Natija Excel faylni qo'lda to'ldirgandagidek chiqadi.

---

## 1. Umumiy jarayon (qisqacha)

```
Fayl yuklandi (.xls / .xlsx)
   │
   ├─ 1. .xls bo'lsa → .xlsx ga o'giriladi (LibreOffice yoki Excel)
   ├─ 2. Hamma listlar ko'rib chiqiladi → eng ofyortaga o'xshaganini tizim taklif qiladi
   ├─ 3. Foydalanuvchi listni tasdiqlaydi («Тасдиқлаш — шу листни ўқиш»)
   ├─ 4. List o'qiladi: sarlavha → ustunlar → har bir qatorning turi
   ├─ 5. Formulasi yo'qolgan kataklar tiklanadi (qiymat bo'lib ko'chirilgan fayllar)
   ├─ 6. Ogohlantirishlar tuziladi (qo'lda qo'shilgan son, boshqa listga havola …)
   ├─ 7. O'z-o'zini tekshirish (bo'sh / yarim / to'liq hajm sinovi)
   └─ 8. Bazaga yoziladi → ofyorta sahifasi ochiladi

Keyin: dalolatnomalar (№1, №2 …) → hajm yoziladi → hisob → Excel hisobot / Ф-2 / Ф-3
```

Fondagi ish (`services_acts.prepare` → `import_raw`) har bir qadamni sahifada ko'rsatadi:
«Файл очилмоқда», «Листлар кўрилмоқда», «Офёрта ўқилмоқда», «Ҳисоб текширилмоқда», «Базага ёзилмоқда».

---

## 2. Qaysi list ofyorta: tanish (`sheet.list_sheets`)

Bitta faylda ko'p list bo'ladi: Дефектный акт, ЛРВ, Свод, Ф-2, Ф-3 va boshqalar. Tizim har bir listni ko'radi.

1. **Sarlavha qatori bormi?** Birinchi 200 qator ichidan shunday qator qidiriladi:
   - unda «**Наименование**» so'zi bor;
   - yonida «единиц», «шифр» yoki «кол» ham bor.

   Topilmasa, bu list ofyorta emas.
2. **Nechta ish bor?** Sarlavha topilgan bo'lsa, qatorlar turlarga ajratiladi
   (4-bo'limdagidek) va ishlar sanaladi.
3. **Ball qo'yiladi:**

   | Belgi | Ball |
   |---|---|
   | Sarlavha topildi | +1 000 000 |
   | Har bir ish / material qatori | +1 |
   | List nomida «офёрта / oferta / ofyorta» | +100 000 |
   | List nomida «Ф-2, Ф-3, свод, акт, остатка, ҳисобот …» | −500 000 |
   | List yashirin | −1 000 000 |

4. Eng katta ball olgan list **«★ тизим таклифи»** bo'ladi. Foydalanuvchi uni tasdiqlaydi yoki
   boshqa listni tanlaydi. Har bir list uchun birinchi 12 ta ish ko'rsatiladi: nima ichida ekanini ko'rib tanlash uchun.

---

## 3. Ustunlarni topish (`sheet.find_layout`)

Ustunlar harf bo'yicha qotirilmagan, sarlavhadagi so'zlardan topiladi. Shuning uchun har xil
tartibdagi ofyortalar o'qiladi.

| Ustun (ichki nomi) | Sarlavhada qidiriladigan so'zlar | Odatda |
|---|---|---|
| `num` — tartib raqami | «№», «п/п», «п.п», «N п» | A |
| `name` — nomi | «Наименование» | C |
| `unit` — o'lchov birligi | «Единица», «ед.изм», «ед.» («на единицу …» bundan mustasno) | D |
| `qty` — hajm / me'yor | «Количество», «кол-во», «кол.» | E |
| `total` — loyiha bo'yicha jami | «по проектным данным» (bo'lmasa `qty` dan keyingi ustun) | F |
| `price` — narx | «Стоимость», «Цена» | G |
| `amount` — summa | «Сумма» | H |

- **Sarlavha 2–3 qator bo'lishi mumkin.** Uning ostidagi «1 | 2 | 3 | 4 …» raqamlash qatori ham sarlavhaga kiradi.
- **Sarlavha har doim to'g'ri yozilmaydi.** Masalan, «Цена | Стоимость» yoki «сумма | обший».
  Shuning uchun narx va summa ustunlari **ish qatorlarining formulalaridan** qayta tekshiriladi
  (`_money_cols`):
  - qaysi ustunda `=E14*G14` (hajm × narx) kabi formula ko'p bo'lsa, o'sha **summa** ustuni;
  - formuladagi ikkinchi ustun **narx** ustuni.
- **Formula yo'q bo'lsa** (fayl qiymat sifatida ko'chirilgan): qaysi ustun aniq «hajm × boshqa ustun» ga
  teng bo'lsa, o'sha summa deb olinadi.
- **Oddiy narx ro'yxati / Ф-2** (summa `=D6*E6`, ya'ni «hajm × hajm yonidagi ustun»): bu holda
  «jami» deb o'ylangan ustun aslida **narx** bo'ladi. Tizim buni taniydi. Oldin bunday fayllarda yarim
  hajm 25% chiqardi, endi tuzatilgan.
- Summadan o'ngdagi ustunlar (ЧЕЛ-Ч, МАШ-Ч, материал, кабель, оборудование …) **tur ustunlari** deb
  olinadi. Ular hisobda qatnashadi, lekin hisobotda yashiriladi.

---

## 4. Qatorlarni turlarga ajratish (`sheet._classify`)

Har bir qatorga bitta tur beriladi:

| Tur | Nima | Qanday taniladi |
|---|---|---|
| `head` | Sarlavha | Sarlavha qatorlari |
| `text` | Bo'lim nomi, izoh, bo'sh qator | Raqami yo'q yoki nomi yo'q |
| `work` | **Ish** (narx lokal smetadagi ish) | A ustunida `1`, `2`, `3` … va tagida resurslari bor |
| `res` | **Ishning resursi** | A ustunida `1.1`, `1.2` … (ota ish: `1`) |
| `free` | **Alohida ish / material** (resurssiz) | A da `5` bo'lib tagida `5.1` yo'q; yoki raqamsiz, lekin birligi, hajmi va `=E*G` formulasi bor |
| `svod` | **Svodka** (pastdagi hisob) | Ishlardan keyingi «ИТОГО», «ВСЕГО», «ЗАРПЛАТА», «НДС», foizlar … |

**Tartib:** jadval yuqoridan pastga bir marta o'qiladi.

1. **Svodka qayerdan boshlanadi** (`svod_start`). Buni ikki belgi ko'rsatadi:
   - butun jadvalni oladigan `SUM`, masalan `=SUM(H11:H1250)`;
   - «JAMI / ИТОГО / ВСЕГО / ЗАРПЛАТА / ИШ ҲАҚИ» bilan boshlanadigan nom.

   Ishning o'z resurslarini yig'adigan `=SUM(H11:H21)` svodka **hisoblanmaydi**, chunki u qatorda
   ish raqami va birligi bor.
2. **Raqam o'qiladi.** `3`, `3.1`, `3,1`, `3. 1` bir xil tushuniladi.
3. **Resurs bog'lanadi.** `N.k` raqamli qator tepadagi `N` ishga bog'lanadi (`parent`).
   Tepasida ishi yo'q `N.k` alohida material (`free`) bo'ladi.
4. **Svodkadan keyin ham ishlar bo'lishi mumkin.** Ba'zi ofyortalar bo'limma-bo'lim boradi: har bir
   bo'limning o'z svodkasi bor, keyin raqamlash yana 1 dan boshlanadi. Raqami, nomi va **pul bo'lmagan
   birligi** («м3», «шт», «т») bor qator ish deb olinadi. Birligi «сум» yoki «%» bo'lsa, bu svodka qatori.
5. **Sarlavha qayta uchrasa, o'qish to'xtaydi.** Svodkadan keyin yana «Наименование …» sarlavhasi
   chiqsa, bu boshqa jadval (masalan, Ф-2 nusxasi). Undan pastdagi hamma narsa izoh (`text`) deb olinadi.

---

## 5. O'qishdagi tuzatishlar (`_restore_formulas`, `_relink_svod`)

Ko'p fayl qo'lda ko'chirilgan bo'ladi: formulalar o'rnida faqat sonlar qoladi. Shunday faylda
dalolatnoma hajmi o'zgarsa ham summa o'zgarmasdi. Tizim formulani **faqat aniq mos kelsa** tiklaydi:

- **Summa = narx × hajm.** Agar bu yaxlitlash farqigacha (0,5 so'm) to'g'ri bo'lsa, summa katagiga
  `=G×E` formulasi qo'yiladi.
- **Tur ustuni** (ЧЕЛ-Ч, материал …) summaga teng bo'lsa, `=H` (summaga havola) qilinadi.
  Narxga xato bog'langan bo'lsa ham shunday qilinadi.
- **Svodkada qo'lda yozilgan son** bir-ikki qator tepadagi formula natijasiga teng bo'lsa, o'sha
  katakka havola qilinadi. Aks holda bu son har bir dalolatnomaga tushib qolardi.

---

## 6. Formulalar qanday ishlaydi (`formula.py`)

Tizimda **Excel formulalarining o'z hisoblagichi** bor. Excel kerak emas, server o'zi hisoblaydi.

- **Har bir katak uchun ikki narsa saqlanadi:**
  - Excel saqlagan **qiymat** (kesh);
  - **formula** matni.
- **Formula bir marta tahlil qilinib**, tez ishlaydigan funksiyaga aylantiriladi (`compile_formula`).
- **Qo'llab-quvvatlanadi:**
  - amallar: `+ − * / ^ & = <> < > <= >=`, foiz (`12%`);
  - oraliqlar (`H11:H250`);
  - funksiyalar: `SUM`, `SUBTOTAL`, `MIN`, `MAX`, `AVERAGE`, `ROUND`, `ROUNDUP`, `ROUNDDOWN`, `ABS`, `INT`, `IF`.
- **Excel qoidalariga mos:**
  - bo'sh katak hisobda 0, matn bilan solishtirilganda `""` bo'ladi;
  - matnlar katta-kichik harfga qaramay solishtiriladi;
  - `«0,0028»` kabi matn bo'lib yozilgan son ham son deb olinadi.
- **Hisob tartibi** (`Evaluator`):
  1. Katakka **dalolatnoma hajmi** berilgan bo'lsa (override), o'sha olinadi.
  2. Aks holda katakda formula bo'lsa, u hisoblanadi. Formula ishlatgan kataklar ham o'z navbatida
     hisoblanadi. Natija xotirada saqlanadi, shuning uchun har katak bir marta hisoblanadi.
  3. Formula bo'lmasa, fayldagi qiymat olinadi.

  Aylanma havola `#REF` bo'ladi. Boshqa listga havola qilgan formula fayldagi qiymatni oladi
  (ogohlantirish chiqadi).

**Misol.** Ish `3`, uning resursi `3.2`, svodka:

```
E14 (ish hajmi)        = dalolatnomadagi hajm        ← tizim almashtiradi
F15 (resurs jami)      = E15 × E14                   ← fayldagi formula, o'zi hisoblanadi
H15 (resurs summasi)   = F15 × G15                   ← fayldagi formula
H1250 (ИТОГО)          = SUM(H11:H1249)              ← fayldagi formula
H1252 (НДС)            = H1250 × 12%                 ← fayldagi formula
```

Dalolatnoma uchun tizim faqat `E14` ni almashtiradi. Qolgan zanjirni fayldagi formulalar o'zi hisoblaydi.

---

## 7. Dalolatnoma hisobi (`sheet.Book`)

### Hajm qayerga yoziladi (`Book.override`)

Foydalanuvchi hajmni faqat **ish** (`work`) va **alohida** (`free`) qatorlarga yozadi:
`0,5`, `12` yoki `60%` (ofyortadagi hajmdan).

| Katak | Dalolatnomada nima bo'ladi |
|---|---|
| Ishning hajmi (E) | = dalolatnoma hajmi |
| Ishning F ustuni (formulasiz son bo'lsa) | F × (dalolatnoma hajmi / shartnoma hajmi) |
| Resursning F ustuni (formula bo'lsa) | O'zi hisoblanadi |
| Resursning F ustuni (formulasiz son) | F × (dalolatnoma hajmi / shartnoma hajmi) |
| Resurs me'yori (summa E dan hisoblansa) | Hajmga mutanosib |
| Svodka | Fayldagi formulalar bilan o'zi hisoblanadi |

### Jami summa qayerdan olinadi (`final_cell`, `grand`)

Svodkada «ИТОГО / ВСЕГО / JAMI» yozilgan qatorlar ichidan eng muhimi tanlanadi:

1. «НДС / ҚҚС» bilan bo'lgani;
2. teng bo'lsa, summasi kattasi (bir nechta bo'lim bo'lsa, umumiy jami);
3. undan keyin pastroqdagisi.

Bunday qator bo'lmasa, ishlar summasi yig'iladi. Resurslar ikki marta qo'shilmaydi.

### Shartnoma, dalolatnoma, qoldiq (`services_acts.totals`)

- **Shartnoma summasi** fayldagi son emas, **to'liq hajm bilan qayta hisoblanadi**. Shuning uchun
  hamma dalolatnomalar 100% bo'lganda qoldiq **aniq 0** bo'ladi.
  - Fayldagi «ВСЕГО» bundan farq qilsa, ogohlantirish chiqadi. Odatda sabab svodkada qo'lda yozilgan son.
- **Har bir dalolatnoma** o'z hajmlari bilan alohida hisoblanadi.
- **«Остатка»** = shartnoma hajmi − hamma dalolatnomalar hajmi. U ham o'sha formulalar bilan hisoblanadi.
- **Cheklovlar:**
  - hajm qoldiqdan oshib ketishi mumkin emas;
  - yopilgan dalolatnomani o'zgartirib bo'lmaydi.

---

## 8. O'z-o'zini tekshirish (`Book.self_check`)

Har bir yuklashda tizim o'zini 3 ta sinovdan o'tkazadi:

| Sinov | Kutiladi | Yiqilsa nima degani |
|---|---|---|
| **Bo'sh dalolatnoma** (hamma hajm 0) | ≈ 0 so'm | Hajmga bog'lanmagan pul bor (qo'lda yozilgan son, «narx × me'yor»). Tizim qaysi katak ekanini ko'rsatadi |
| **Yarim hajm** (hamma ish 50%) | ≈ 50% | Ustunlar noto'g'ri tanilgan (masalan, narx hajm deb olingan → 25%) |
| **To'liq hajm** | ≈ fayldagi jami | Tizim fayldagi hisobni boshqacha tushungan |

Yana bitta sinov bor: jami **0** chiqsa, ishlar topilmagan bo'ladi.

Sinovlardan biri yiqilsa, ofyorta sahifasida **qizil banner** chiqadi:
«Тизим бу офёртани тўлиқ тушунмади — далолатнома суммалари нотўғри бўлиши мумкин.»
Bannerda sabab va o'sha qatorga o'tish tugmasi bor.

### Ko'p faylni birdan tekshirish

```bash
venv/Scripts/python manage.py tn_check "C:/papka/ofyortalar" --ignore "Ф-3,Ф_3,Буйруқ"
```

Har bir fayl uchun quyidagilar chiqadi:
- OK yoki FAIL;
- jami, bo'sh dalolatnoma summasi, yarim hajm foizi, fayldagi jami.

Oxirida «N / M ўтди» chiqadi. O'qish qoidalari o'zgarganda shu buyruq bilan
avvalgi fayllar buzilmaganini tekshirish mumkin
(test: `TEXNAZOR_CORPUS=<papka> manage.py test texnazor.tests_corpus`).

---

## 9. Excel hisobot (`hisobot.py`)

**Narxlangan ofyorta** («Офёрта (нархланган) — …xlsx»):

1. **Asos** — yuklangan faylning o'zi: ofyorta listi uslublari, kengliklari, birlashtirilgan kataklari va
   **formulalari** bilan. Boshqa listlar o'chiriladi.
2. **Bloklar.** O'ng tomonga har bir dalolatnoma (№1, №2 …) va «Остатка» uchun blok qo'shiladi. Blok —
   ofyortaning E…oxirgi ustunlari nusxasi.
3. **Formulalar ko'chiriladi.** Formulalar blok ustunlariga surilib ko'chiriladi
   (`=G14*F14` → `=P14*O14`). Faqat hajm kataklari dalolatnomadagi bilan almashadi.
4. **Excel o'zi hisoblaydi.** Fayl ochilganda Excel hammasini qayta hisoblaydi. Natija saytdagi bilan bir xil.
5. **Ko'rinish:**
   - har bir blok o'z rangida;
   - hajm va narx ustunlari Excel guruhida («свернуть»);
   - tur ustunlari yashirin;
   - jamidan pastdagi tekshiruv qatorlari bloklarga ko'chirilmaydi.

**Ф-2 №N** — faqat shu dalolatnomada bajarilgan ishlar (resurslari, bo'lim sarlavhalari va svodka bilan).
Qatorlar yuqoriga suriladi, formulalar yangi qator raqamlariga moslanadi.

---

## 10. O'qish qoidalari o'zgarsa

`sheet.READER` raqami oshiriladi (hozir **4**). Eski versiyada o'qilgan obyekt ochilganda **o'zi qayta
o'qiladi**. Dalolatnomalardagi hajmlar saqlanib qoladi, chunki ular Excel qator raqamiga bog'langan.

---

## 11. Tizim hozir nimani o'qiy olmaydi

- **«Наименование» sarlavhasi yo'q** list (masalan, faqat Ф-3, buyruq, ro'yxat). Bu to'g'ri: ular ofyorta emas.
- **Butunlay boshqa shakldagi smeta.** Masalan, jamisi 0 chiqadigan ba'zi lokal smetalar
  («ТОШКЕНТ-ЗАНГИОТА», «Нишонбой» kabi). Ularni alohida ko'rib chiqish kerak.
- **Fayldagi hisobning o'zida xato bo'lsa**, tizim uni faylidagidek qoldiradi va **ogohlantiradi**,
  o'zicha tuzatmaydi. Masalan:
  - svodkada qo'lda `+124,25` qo'shilgan;
  - jami qo'lda yozilgan.

Bunday fayl yuklanganda qizil banner chiqadi. Demak, natijani ishlatishdan oldin tekshirish kerak.

---

## Fayllar xaritasi

| Fayl | Vazifasi |
|---|---|
| `texnazor/sheet.py` | List tanlash, ustunlar, qator turlari, formulalarni tiklash, `Book` (dalolatnoma hisobi, o'z-o'zini tekshirish) |
| `texnazor/formula.py` | Excel formulalari hisoblagichi, formulalarni ko'chirish (`move_refs`) |
| `texnazor/services_acts.py` | Yuklash jarayoni, dalolatnomalar, hajm yozish, jami / qoldiq |
| `texnazor/hisobot.py` | Narxlangan ofyorta va Ф-2 Excel fayllari |
| `texnazor/f3.py` | Ф-3 (справка-счёт-фактура) |
| `texnazor/corpus.py`, `management/commands/tn_check.py` | Ko'p faylni birdan tekshirish |
