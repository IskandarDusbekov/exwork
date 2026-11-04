"""RES faylni o'qish (``resbase.resfile``): formatlar, tashlab ketiladigan qatorlar, kodlar, mazmun izi."""
import shutil
import tempfile
from pathlib import Path

from django.test import SimpleTestCase
from openpyxl import Workbook

from . import resfile

HEAD = [["N п.п.", "Шифр номера нормативов и коды ресурсов", "Наименование работ и затрат", "Единица измерения",
         "Количество", "Сметная стоимость", None],
        [None, None, None, None, None, "на.ед.изм.", "общая"],
        [1, 2, 3, 4, 5, 6, 7]]


def rs_sheet(ws, rows, title="ЛОКАЛЬНЫЙ РЕСУРСНЫЙ СМЕТНЫЙ РАСЧЕТ"):
    ws.append([None, None, title])
    ws.append([])
    for r in HEAD + rows:
        ws.append(r)


RS_ROWS = [
    [None, None, "ТРУДОВЫЕ РЕСУРСЫ"],
    [1, "1", "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ.-Ч", 100, 36030.1, 3603010],
    [2, "3", "ЗАТРАТЫ ТРУДА МАШИНИСТОВ", "ЧЕЛ.-Ч", 10, "--", "--"],
    [None, "ИТОГО ПО ТРУДОВЫМ РЕСУРСАМ:", None, "СУМ", None, None, 3603010],
    [None, None, "СТРОИТЕЛЬНЫЕ МАШИНЫ И МЕХАНИЗМЫ"],
    [3, "000258", "БУЛЬДОЗЕРЫ 79 КВТ", "МАШ.-Ч", 2, 207913, 415826],
    [4, "698", "КРАНЫ БАШЕННЫЕ 8 Т", "МАШ.-Ч", 5, 0, 0],
    [None, None, "МАТЕРИАЛЬНЫЕ РЕСУРСЫ"],
    [5, "9219", "ВОДА", "М3", 3, "6800,50", 20401.5],
    [6, None, "СВЕТИЛЬНИК АВАРИЙНЫЙ", "ШТ", 4, 135000, 540000],
    [7, None, "М2", "216,3", None, 32445000, None],  # ustun surilgan
    [None, None, None, None, None, None, None],
    [None, None, "ОБОРУДОВАНИЕ"],
    [8, None, "НАСОС ЦИРКУЛЯЦИОННЫЙ", "ШТ", 1, 9000000, 9000000],
]


class ResFileTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save(self, wb, name="res.xlsx") -> Path:
        path = self.tmp / name
        wb.save(path)
        return path

    def rs_file(self, name="res.xlsx", rows=RS_ROWS) -> Path:
        wb = Workbook()
        rs_sheet(wb.active, rows)
        return self.save(wb, name)

    def test_rs_sheet(self):
        res = resfile.parse(self.rs_file())
        got = [(r.code, r.name, r.price, r.category) for r in res.rows]
        self.assertEqual(got, [
            ("1", "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", 36030.1, "I"),
            ("258", "БУЛЬДОЗЕРЫ 79 КВТ", 207913, "J"),
            ("9219", "ВОДА", 6800.5, "K"),
            ("", "СВЕТИЛЬНИК АВАРИЙНЫЙ", 135000, "K"),
            ("", "НАСОС ЦИРКУЛЯЦИОННЫЙ", 9000000, "M"),
        ])
        self.assertEqual(res.skipped, {"no_price": 1, "total": 1, "zero_price": 1, "bad_unit": 1})
        self.assertEqual(res.rows[1].qty, 2)
        self.assertTrue(res.rows[3].key.startswith("n:"))

    def test_codes(self):
        for raw, code in (("258", "258"), ("000258", "258"), ("258 С", "258"), ("АВС 000001", "1"), (258.0, "258"),
                          ("С583", ""), ("Е0101-197-02", ""), ("R0004", ""), (None, "")):
            self.assertEqual(resfile.res_code(raw), code, raw)
        self.assertEqual(resfile.name_key("Вода ", "м3"), resfile.name_key("ВОДА", "М3"))
        self.assertNotEqual(resfile.name_key("ВОДА", "М3"), resfile.name_key("ВОДА", "Т"))

    def test_local_sheets_are_works_not_prices(self):
        """ЛРВ (narx ustuni yo'q) va narx ustuni bor, lekin norma shifrlari to'la lokal list — narx emas, ishlar."""
        wb = Workbook()
        ws = wb.active
        ws.title = "_ЛРВ"
        ws.append(["N п.п.", "Шифр", "Наименование работ и затрат", "Единица измерения", "Количество"])
        ws.append([None, None, None, None, "на. ед. измерения", "по проектным данным"])
        ws.append([1, 2, 3, 4, 5, 6])
        ws.append([1, "Е0101-197-02", "РАЗРАБОТКА ГРУНТА", "1000 М3", 1.5])
        ws.append([1.1, "1", "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ.-Ч", 5.86, 9.1])
        loc = wb.create_sheet("Локал")
        loc.append(["Т/р", "Шифр", "Наименование", "Ед. изм.", "Кол-во", "Цена"])
        for i in range(4):
            loc.append([i + 1, f"Е01-01-013-1{i}", "РАЗРАБОТКА", "1000 м3", 1, None])
            loc.append([None, "1", "ЗАТРАТЫ ТРУДА", "чел.-ч", 1, 36030])
        res = resfile.parse(self.save(wb))
        self.assertEqual(res.rows, [])
        self.assertEqual(res.lrv_sheets, ["_ЛРВ", "Локал"])
        self.assertEqual([(w.shifr, w.book) for w in res.works][:2], [("Е0101-197-02", "Е01"), ("Е01-01-013-10", "Е01")])

    def test_many_objects_in_one_sheet(self):
        """3.xlsx «РЕСУРС»: obyektlar ketma-ket, har birining sarlavhasi bor; resurs faylda bir marta olinadi."""
        wb = Workbook()
        ws = wb.active
        ws.append(["объект - ДЕМОНТАЖ"])
        rs_sheet(ws, [[None, None, "ТРУДОВЫЕ РЕСУРСЫ"], [1, "АВС 000001", "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ.-Ч", 1, 36030.1]])
        ws.append([])
        ws.append([None, None, "объект - МАФ"])
        rs_sheet(ws, [[None, None, "СТРОИТЕЛЬНЫЕ МАШИНЫ И МЕХАНИЗМЫ"],
                      [1, "258 С", "БУЛЬДОЗЕРЫ", "МАШ.-Ч", 1, 203442],
                      [2, "1", "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ.-Ч", 1, 36030.1]])
        res = resfile.parse(self.save(wb))
        self.assertEqual([(r.code, r.obj, r.category) for r in res.rows], [("1", "ДЕМОНТАЖ", "I"), ("258", "МАФ", "J")])
        self.assertEqual(res.skipped["duplicate"], 1)

    def test_one_code_for_different_resources_is_not_taken(self):
        """№10 НВК: «1360» — ЗАГЛУШКА 179 400, ОТВОД 25 704 … — qaysi biri haqiqiy kod ekanini bilib bo'lmaydi."""
        res = resfile.parse(self.rs_file(rows=[
            [None, None, "МАТЕРИАЛЬНЫЕ РЕСУРСЫ"],
            [1, "1360", "ЗАГЛУШКА СТ. Д=100ММ", "ШТ", 1, 179400],
            [2, "1360", "ОТВОД 90 ГР. Д=75ММ ПОЛИЭТИЛЕНОВИЕ", "ШТ", 4, 25704],
            [3, "1360", "ПОТРУБКА С ФЛАНСЫ Д=110", "ШТ", 2, 45000],
            [4, "9219", "ВОДА", "М3", 3, 6800],
            [5, "9219", "ВОДА", "М3", 1, 6800],                       # oddiy takror
            [6, "12699", "ЩЕБЕНЬ ИЗ ПРИРОДНОГО КАМНЯ", "М3", 1, 54000],
            [7, "12699", "ЩЕБЕНЬ ИЗ ПРИРОДНОГО КАМНЯ ФР. 20-40", "М3", 1, 56000],  # nomi o'xshash — takror
        ]))
        self.assertEqual([(r.code, r.price) for r in res.rows], [("9219", 6800), ("12699", 54000)])
        self.assertEqual(res.skipped, {"code_conflict": 3, "duplicate": 2})
        self.assertIn("1360", res.warnings[0])

    def test_svodno_and_uzbek_headers(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["СВОДНО-РЕСУРСНАЯ ВЕДОМОСТЬ"])
        ws.append(["№ п/п", "Обоснование (Код ресурса)", "Наименование ресурсов", "Ед. изм.", "Количество", "Цена",
                   "Стоимость"])
        ws.append([1, 2, 3, 4, 5, 6, 7])
        ws.append(["ТРУДОВЫЕ РЕСУРСЫ"])
        ws.append([1, "00001", "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ-ЧАС", "2717,7", "36030,10", "97919370,28"])
        ws.append(["ИТОГО ТРУДОВЫЕ РЕСУРСЫ:", None, None, None, None, None, "97919370,28"])
        uz = wb.create_sheet("RES")
        uz.append(["Т/р", "Бирл.", "Код", "Баҳоси, сўм", "Сана", "Изоҳ", "Ресурс тури ва номи"])
        uz.append([1, "чел.-ч", "R0004", 28500, "01.10.2026", "жорий", "Затраты труда рабочих-строителей"])
        uz.append([2, "м3", "R0011", 6800, "01.10.2026", "жорий", "Вода"])
        res = resfile.parse(self.save(wb))
        self.assertEqual([(r.sheet, r.code, r.price) for r in res.rows],
                         [("Sheet", "1", 36030.1), ("RES", "", 28500), ("RES", "", 6800)])
        self.assertEqual(res.rows[1].category, "I")

    def test_code_column_by_content(self):
        """RES.xls: «РЕСУРС» (milliy kod) va «ОБОСНОВАНИЕ» (katalog shifri) — kod qiymatlarga qarab tanlanadi,
        «№№» raqamlash ustuni kod emas."""
        wb = Workbook()
        ws = wb.active
        ws.append(["ЛОКАЛЬНАЯ РЕСУРСНАЯ СМЕТА № С-03"])
        ws.append(["№№", "РЕСУРС", "ОБОСНОВАНИЕ", "НАИМЕНОВАНИЕ РЕСУРСА", "ЕД.ИЗМ", "КОЛ-ВО", "ЦЕНА", "СУММА"])
        ws.append([1, 2, 3, 4, 5, 6, 7, 8])
        ws.append(["ТРУДОВЫЕ РЕСУРСЫ"])
        ws.append([1, "000001", None, "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ-Ч", 15102.27, 15807.93, 238735601.25])
        ws.append([None, None, None, "ИТОГО", None, None, None, 238735601.25])
        ws.append(["СТРОИТЕЛЬНЫЕ МАШИНЫ И МЕХАНИЗМЫ"])
        ws.append([1, "000112", "С203-101", "АВТОПОГРУЗЧИКИ 5 Т", "МАШ-Ч", 101.29, 54800, 5550505.15])
        ws.append([2, "000163", None, "АВТОМОБИЛИ-САМОСВАЛЫ ДО 10 Т", "МАШ-Ч", 2001.18, 45800, 91654088.85])
        ws.append([3, "000258", "С207-149", "БУЛЬДОЗЕРЫ", "МАШ-Ч", 100.97, 61720, 6231829.54])
        ws.append(["МЕСТНЫЕ МАТЕРИАЛЫ И КОНСТРУКЦИИ"])
        ws.append([1, None, "2ПК5910", "2ПК59.10-8А400-С9", "ШТ", 46, 915315, 42104490])
        res = resfile.parse(self.save(wb))
        self.assertEqual([(r.code, r.price, r.category) for r in res.rows],
                         [("1", 15807.93, "I"), ("112", 54800, "J"), ("163", 45800, "J"), ("258", 61720, "J"),
                          ("", 915315, "K")])

    def test_lrv_works_and_norms(self):
        """bv*.xls (Форма 5): ish — shifr, nom, birlik, hajm; ostida resurslar (1.1, 1.2 — norma va jami);
        ish ortidagi alohida material — shu ishga «qo'shimcha» (normasiz)."""
        wb = Workbook()
        ws = wb.active
        ws.title = "_ЛРВ"
        ws.append([None, None, "ЛОКАЛЬНАЯ РЕСУРСНАЯ ВЕДОМОСТЬ №"])
        ws.append(["N п.п.", "Шифр номера нормативов и коды ресурсов", "Наименование работ и затрат",
                   "Единица измерения", "Количество"])
        ws.append([None, None, None, None, "на. ед. измерения", "по проектным данным"])
        ws.append([1, 2, 3, 4, 5, 6])
        ws.append(["РАЗДЕЛ 2. ЗЕМЛЯНЫЕ РАБОТЫ"])
        ws.append([1, "Е0101-197-02 ДОП. 11 ГОСАРХИТЕКТСТРОЙ РУЗ", "РАЗРАБОТКА ГРУНТА", "1000 М3 ГРУНТА", 1.5549])
        ws.append([1.1, 1, "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ.-Ч", 5.86, 9.1117])
        ws.append([1.2, "258", "БУЛЬДОЗЕРЫ 79 КВТ", "МАШ.-Ч", 3.92, 6.0952])
        ws.append([2, "Е2701-005-01", "УСТРОЙСТВО ОСНОВАНИЯ", "1000М2", 0.882])
        ws.append([2.1, 1, "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ.-Ч", 34.7, 30.6054])
        ws.append([3, None, "ПЕСЧАНО-ГРАВИЙНАЯ СМЕСЬ", "М3", 88.2])
        ws.append([4, "Ц08-03-599-7", "ВВОДНОЕ УСТРОЙСТВО", "ШТ", None, 3])
        ws.append([5, "Е310-1015", "ПЕРЕВОЗКА ГРУЗОВ", "Т", 464.4])
        res = resfile.parse(self.save(wb))
        self.assertEqual([(w.shifr, w.book, w.unit, w.qty, w.section) for w in res.works], [
            ("Е0101-197-02", "Е01", "1000 М3 ГРУНТА", 1.5549, "РАЗДЕЛ 2. ЗЕМЛЯНЫЕ РАБОТЫ"),
            ("Е2701-005-01", "Е27", "1000М2", 0.882, "РАЗДЕЛ 2. ЗЕМЛЯНЫЕ РАБОТЫ"),
            ("Ц08-03-599-7", "Ц08", "ШТ", 3, "РАЗДЕЛ 2. ЗЕМЛЯНЫЕ РАБОТЫ"),
            ("Е310-1015", "ТР", "Т", 464.4, "РАЗДЕЛ 2. ЗЕМЛЯНЫЕ РАБОТЫ"),
        ])
        self.assertEqual([(x.code, x.norma, x.qty, x.extra) for x in res.works[0].resources],
                         [("1", 5.86, 9.1117, False), ("258", 3.92, 6.0952, False)])
        self.assertEqual([(x.code, x.name, x.norma, x.qty, x.extra) for x in res.works[1].resources],
                         [("1", "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", 34.7, 30.6054, False),
                          ("", "ПЕСЧАНО-ГРАВИЙНАЯ СМЕСЬ", None, 88.2, True)])

    def test_normbook(self):
        from . import normbook
        for raw, want in (("Е0101-197-02 ДОП. 11", ("Е0101-197-02", "Е01")), ("Е01-01-013-11", ("Е01-01-013-11", "Е01")),
                          ("E68-020-01", ("Е68-020-01", "Е68")), ("Е102-6-1", ("Е102-6-1", "Е01")),
                          ("Ц1009-003-11", ("Ц1009-003-11", "Ц10")), ("Е310-1015", ("Е310-1015", "ТР")),
                          ("258", None), ("С583", None), ("С203-101", None), ("", None)):
            self.assertEqual(normbook.parse(raw), want, raw)
        # тизим тўпламга ўзи ном қўймайди — номни админ ёзади
        self.assertEqual(normbook.book_label("Е01"), "")

    def test_fingerprint_ignores_file_name_and_order(self):
        a = resfile.parse(self.rs_file("a.xlsx"))
        b = resfile.parse(self.rs_file("b.xlsx", list(reversed(RS_ROWS[4:])) + RS_ROWS[:4]))
        self.assertEqual(a.fingerprint, b.fingerprint)
        changed = [r[:] for r in RS_ROWS]
        changed[5][5] = 210000
        self.assertNotEqual(a.fingerprint, resfile.parse(self.rs_file("c.xlsx", changed)).fingerprint)
        self.assertNotEqual(resfile.file_hash(self.tmp / "a.xlsx"), "")
