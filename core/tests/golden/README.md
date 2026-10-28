# Etalon to'plamlar

Har bir to'plam alohida papkada:

```
golden/<nom>/
├── input_local.xls|xlsx|xlsm     # lokal (БВ) — eski dasturga berilgan fayl
├── input_res.xls|xlsx|xlsm       # resurs (БР), C oldiga bo'sh ustun qo'shilgan
├── expected_narxlandi.xlsx       # eski dastur chiqargan natija
└── options.json                  # ixtiyoriy: {"fuzzy": 95, "local_sheet": "...", "res_sheet": "..."}
```

Eski dastur faqat 1-listni o'qiydi, shuning uchun kirish fayllarida kerakli list birinchi bo'lsin
(yoki `options.json` da `local_sheet`/`res_sheet` ni ko'rsating).

`.xls` kirish uchun LibreOffice kerak (`NARXLASH_SOFFICE`), aks holda to'plam skip qilinadi.
Boshqa papkadagi etalonlar bilan ishlatish: `NARXLASH_GOLDEN_DIR=<yo'l> pytest`.

`202611450/` — «МОСТ НА ПК 75+34» smetasining 202611450_БВ / 202611450_БР listlari; natija eski
`.pyw` dasturni shu kompyuterda (Excel + pywin32) ishga tushirib olingan.
