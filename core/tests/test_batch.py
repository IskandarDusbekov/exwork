"""Bir/ko'p faylli yuklash: list turi, nusxalar, juftlash (haqiqiy obyekt fayllarida)."""
from pathlib import Path

from openpyxl import Workbook, load_workbook

from core.batch import LOCAL, RESOURCE, UNKNOWN, apply_roles, inspect_workbook, mark_duplicates, plan_pairs

TESTS = Path(__file__).parent
OBJ_LOCAL = TESTS / "batch/obekt_lokal_7list.xlsx"   # 400/410/420 БВ+БР, 450_БВ
OBJ_RES = TESTS / "batch/obekt_res_7list.xlsx"       # o'sha listlar + 450_БР (C oldiga ustun qo'shilgan)


def _infos(*paths):
    infos = []
    for i, p in enumerate(paths):
        infos += inspect_workbook(load_workbook(p), i, Path(p).name)
    return mark_duplicates(infos)


def test_roles_numbers_and_names_from_content():
    infos = {i.sheet: i for i in _infos(OBJ_LOCAL)}
    assert {s: i.role for s, i in infos.items()} == {
        "202611400_БВ": LOCAL, "202611400_БР": RESOURCE, "202611410_БВ": LOCAL, "202611410_БР": RESOURCE,
        "202611420_БВ": LOCAL, "202611420_БР": RESOURCE, "202611450_БВ": LOCAL}
    m = infos["202611450_БВ"]
    assert (m.number, m.obj) == ("1-6", "МОСТ НА ПК 75+34")
    assert m.stroyka.startswith('IL "URGUT TUMANIDAGI')
    assert infos["202611410_БВ"].obj == "YANGI-DARGOM KANALI РВТ 80-100"


def test_one_object_file_pairs_by_content_not_only_number():
    # 400 va 410 ikkalasi ham «№ 1-1» — mazmun bo'yicha ajratiladi
    pairs, unused = plan_pairs(_infos(OBJ_LOCAL))
    got = {p.local: p.res for p in pairs}
    assert got == {"0:202611400_БВ": "0:202611400_БР", "0:202611410_БВ": "0:202611410_БР",
                   "0:202611420_БВ": "0:202611420_БР", "0:202611450_БВ": None}
    assert unused == []


def test_two_files_duplicates_and_cross_file_pair():
    infos = _infos(OBJ_LOCAL, OBJ_RES)
    dups = {i.key: i.duplicate_of for i in infos if i.duplicate_of}
    assert len(dups) == 6 and dups["1:202611400_БВ"] == "0:202611400_БВ"
    pairs, unused = plan_pairs(infos)
    assert len(pairs) == 4 and unused == []
    most = next(p for p in pairs if p.local == "0:202611450_БВ")
    assert most.res == "1:202611450_БР" and most.overlap > 0.9
    assert most.label == "МОСТ НА ПК 75+34 · № 1-6"
    assert any("смета № 1-6" in r for r in most.reasons)


def test_names_do_not_matter_lrv_res():
    d = TESTS / "excel_calc/bolnitsa7_kr_blok2"
    infos = _infos(d / "input_local.xlsx", d / "input_res.xlsx")   # listlar: «LRV», «RES»
    assert [i.role for i in infos] == [LOCAL, RESOURCE]
    assert infos[0].stroyka == "7-БОЛНИЦА 2019" and infos[0].obj == "БЛОК 1,2,3,4,5"
    pairs, _ = plan_pairs(infos)
    assert [(p.local, p.res) for p in pairs] == [("0:LRV", "1:RES")]
    assert pairs[0].label == "КОНСТРУКТИВНЫЕ РЕШЕНИЕ (КР) БЛОК 2 · № С-03"


def test_unknown_sheet_and_manual_roles(tmp_path):
    wb = Workbook()
    wb.active.title = "Свод"
    wb.active["A1"] = "СВОДНЫЙ СМЕТНЫЙ РАСЧЕТ"
    wb.save(tmp_path / "s.xlsx")
    infos = _infos(OBJ_LOCAL, tmp_path / "s.xlsx")
    svod = next(i for i in infos if i.sheet == "Свод")
    assert svod.role == UNKNOWN
    apply_roles(infos, {"0:202611400_БР": UNKNOWN})
    pairs, _ = plan_pairs(infos)
    assert next(p for p in pairs if p.local == "0:202611400_БВ").res != "0:202611400_БР"


# ---------------- tasnif: resurs/lokal ko'rinishidagi, lekin smeta bo'lmagan listlar ----------------
def _sheet(wb, title, header, rows, top=()):
    ws = wb.create_sheet(title)
    for r, text in enumerate(top, start=1):
        ws.cell(r, 1, text)
    h = len(top) + 2
    for c, text in enumerate(header, start=1):
        ws.cell(h, c, text)
        ws.cell(h + 1, c, c)  # «1 2 3 …» raqamlash qatori
    for i, row in enumerate(rows, start=h + 2):
        for c, v in enumerate(row, start=1):
            ws.cell(i, c, v)
    return ws


def test_catalog_priced_lrv_act_and_summary_are_skipped():
    wb = Workbook()
    wb.remove(wb.active)
    items = [(n, f"МАТЕРИАЛ {n}", "М3") for n in range(1, 9)]
    _sheet(wb, "РС", ["№ п/п", "Наименование ресурсов", "Ед. изм.", "Количество", "Цена"],
           [(n, name, unit, 2, 1000) for n, name, unit in items], top=["Форма N 6a", "ЛОКАЛЬНЫЙ РЕСУРСНЫЙ СМЕТНЫЙ РАСЧЕТ"])
    _sheet(wb, "Каталог", ["№ п/п", "Наименование выпускаемой продукции", "Ед. изм", "Отпускная текущая цена"],
           [(n, name, unit, 1000) for n, name, unit in items])
    lrv = []
    for n in range(1, 5):  # ish + uning resurslari «1.1, 1.2», narx yozilgan
        lrv += [(str(n), f"ИШ {n}", "100 М2", 1, 1), (f"{n}.1", "ЗАТРАТЫ ТРУДА РАБОЧИХ", "ЧЕЛ-Ч", 2, 3),
                (f"{n}.2", f"МАТЕРИАЛ {n}", "М3", 1, 1000)]
    _sheet(wb, "ЛРВ нархланган", ["№ п/п", "Наименование работ и затрат", "Единица измерения", "Количество",
                                   "Сметная стоимость"], lrv, top=["ЛОКАЛЬНО-РЕСУРСНАЯ ВЕДОМОСТЬ"])
    _sheet(wb, "Дефектный акт", ["№ п/п", "Наименование работ", "Ед. изм.", "Количество"],
           [(n, f"ИШ {n}", "100 М2", 1) for n in range(1, 6)], top=["ДЕФЕКТНЫЙ АКТ"])
    wb.create_sheet("СВОД").cell(2, 2, "СВОДНЫЙ СМЕТНЫЙ РАСЧЕТ СТОИМОСТИ СТРОИТЕЛЬСТВА")
    wb.create_sheet("Бўш")
    infos = {i.sheet: i for i in mark_duplicates(inspect_workbook(wb))}
    assert infos["РС"].role == RESOURCE
    assert {s: (infos[s].role, infos[s].kind) for s in ("Каталог", "ЛРВ нархланган", "Дефектный акт", "СВОД", "Бўш")} == {
        "Каталог": (UNKNOWN, "catalog"), "ЛРВ нархланган": (UNKNOWN, "priced"), "Дефектный акт": (UNKNOWN, "act"),
        "СВОД": (UNKNOWN, "summary"), "Бўш": (UNKNOWN, "empty")}


def test_visible_copy_preferred_over_hidden_original(tmp_path):
    """Smetachi asl listlarni yashirib, nusxasi bilan ishlagan: ko'rinadigan nusxa olinadi va juftlanadi."""
    wb = load_workbook(OBJ_LOCAL)
    src = wb["202611410_БВ"]
    src.sheet_state = "hidden"
    wb.copy_worksheet(src).title = "Лист5"
    wb.active = wb["202611400_БВ"]
    p = tmp_path / "hidden.xlsx"
    wb.save(p)
    infos = _infos(p)
    by = {i.sheet: i for i in infos}
    assert by["202611410_БВ"].hidden and by["202611410_БВ"].duplicate_of == "0:Лист5"
    assert by["Лист5"].duplicate_of is None
    pairs, _unused = plan_pairs(infos)
    got = {p.local: p.res for p in pairs}
    assert got["0:Лист5"] == "0:202611410_БР"
    assert "0:202611410_БВ" not in got
    # qo'lda tanlash uchun: har bir resurs listi mosligi bilan, eng mosi birinchi
    opts = next(p for p in pairs if p.local == "0:Лист5").options
    assert opts[0]["res"] == "0:202611410_БР" and opts[0]["same_file"]
    assert len(opts) == sum(1 for i in infos if i.role == RESOURCE and not i.duplicate_of)


def test_res_without_qty_header_promoted_when_local_matches(tmp_path):
    """ABC «vr_…» каби RES: нарх бор, миқдор сарлавҳаси бошқача — каталог эмас, локал ресурслари шу ерда."""
    from core.batch import KIND_CATALOG, promote_catalogs
    d = TESTS / "excel_calc/bolnitsa7_kr_blok2"
    wb = load_workbook(d / "input_res.xlsx")
    ws = wb["RES"]
    hits = 0
    for row in ws.iter_rows(max_row=40):
        for c in row:
            if isinstance(c.value, str) and any(w in c.value.lower() for w in ("колич", "кол-во", "кол.", "объем", "объём")):
                c.value = "Показатель"  # «Потребность» энди миқдор сўзи (ABC) — бошқа номаълум сўз
                hits += 1
    assert hits
    wb.save(tmp_path / "vr.xlsx")
    infos = _infos(d / "input_local.xlsx", tmp_path / "vr.xlsx")
    res = next(i for i in infos if i.file == 1)
    assert (res.role, res.kind) == (UNKNOWN, KIND_CATALOG) and res.names  # аввал — «нарх каталоги»
    promote_catalogs(infos)
    assert res.role == RESOURCE and res.kind == ""
    pairs, _ = plan_pairs(infos)
    assert [(p.local, p.res) for p in pairs] == [("0:LRV", "1:RES")]
    # смета­чи қўлда «Ўтказиш» деса — тегилмайди
    infos = _infos(d / "input_local.xlsx", tmp_path / "vr.xlsx")
    promote_catalogs(infos, {"1:RES": UNKNOWN})
    assert next(i for i in infos if i.file == 1).role == UNKNOWN


# ===================== номга боғланмаслик: тузилма бўйича жуфтлаш ва эслаб қолиш =====================
def _form(path, rows, title_no="7", res=False):
    """Битта файлда Форма 5 / 6а кўринишидаги лист (устун кенгликлари — шаблондагидек)."""
    from openpyxl import Workbook
    wb = Workbook() if not Path(path).exists() else load_workbook(path)
    ws = wb.create_sheet("92960_БР" if res else "92960_БВ")
    for c, w in zip("ABCDEFG", (6, 16, 97, 12, 11, 11, 11)):
        ws.column_dimensions[c].width = w
    ws["C5"] = ("ЛОКАЛЬНЫЙ РЕСУРСНЫЙ СМЕТНЫЙ РАСЧЕТ № " if res else "ЛОКАЛЬНАЯ РЕСУРСНАЯ ВЕДОМОСТЬ № ") + title_no
    for c, t in zip("ABCDE", ("N п.п.", "Шифр номера нормативов и коды ресурсов", "Наименование работ и затрат",
                              "Единица измерения", "Количество")):
        ws[f"{c}12"] = t
    ws["F12"] = "в текущем уровне цен" if res else "по проектным данным"
    ws["F13"] = "на.ед.изм." if res else None
    for i, c in enumerate("ABCDEF", start=1):
        ws[f"{c}14"] = i
    for r, (code, name, unit, qty, price) in enumerate(rows, start=16):
        ws[f"A{r}"], ws[f"B{r}"], ws[f"C{r}"], ws[f"D{r}"], ws[f"E{r}"] = r - 15, code, name, unit, qty
        if res:
            ws[f"F{r}"] = price
    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]
    wb.save(path)
    return path


def test_pairs_by_structure_when_names_differ(tmp_path):
    """Смета­чи RES'да ускуна тавсифини ўзгартирган — номлар мос эмас, лекин битта файл, лист ва смета рақами бир хил."""
    p = tmp_path / "solar.xlsx"
    _form(p, [("2347-81Т", "УСТАНОВКА СОЛНЕЧНОЙ СИСТЕМЫ: Инвертор 4 кВт-5шт", "ШТ", 1, None)])
    _form(p, [("2347-81T", "УСТАНОВКА СОЛНЕЧНОЙ СИСТЕМЫ: Сетевой инвертор 20 кВт-1шт", "ШТ", 1, 90720000)], res=True)
    pairs, _ = plan_pairs(_infos(p))
    assert [(x.local, x.res) for x in pairs] == [("0:92960_БВ", "0:92960_БР")]
    assert any("рақами бўйича жуфтланди" in r for r in pairs[0].reasons)


def test_no_structural_pair_across_files_or_numbers(tmp_path):
    a, b = tmp_path / "a.xlsx", tmp_path / "b.xlsx"
    _form(a, [("1", "ЦЕМЕНТ", "Т", 1, None)])
    _form(b, [("1", "ПЕСОК", "М3", 1, 5)], res=True)
    pairs, _ = plan_pairs(_infos(a, b))  # бошқа файл — фақат номлар мослиги билан
    assert pairs[0].res is None


def test_cipher_suggests_price_when_name_differs(tmp_path):
    from core import pipeline
    p = tmp_path / "solar.xlsx"
    _form(p, [("2347-81Т", "УСТАНОВКА СОЛНЕЧНОЙ СИСТЕМЫ: Инвертор 4 кВт-5шт", "ШТ", 1, None)])
    _form(p, [("2347-81T", "УСТАНОВКА СОЛНЕЧНОЙ СИСТЕМЫ: Сетевой инвертор 20 кВт-1шт", "ШТ", 1, 90720000)], res=True)
    r = pipeline.run(p, p, pipeline.Options(mode="smart", local_sheet="92960_БВ", res_sheet="92960_БР",
                                            output_path=tmp_path / "out.xlsx"))
    row = next(x for x in r.rows if x["row"] == 16)
    # нарх ўзи ёзилмайди — «ТЕКШИРИНГ» ва таклиф (смета­чи тасдиқлайди)
    assert (row["status"], row["g"], row["suggested_price"]) == ("FUZZY", "ТЕКШИРИНГ", 90720000)
    assert "шифр бир хил" in row["note"]


def test_cipher_ignored_when_unit_differs_or_code_ambiguous(tmp_path):
    from core import pipeline
    p = tmp_path / "x.xlsx"
    _form(p, [("2347-81Т", "ПАНЕЛЬ А", "ШТ", 1, None), ("1111-22", "КАБЕЛЬ А", "М", 5, None)])
    _form(p, [("2347-81Т", "ПАНЕЛЬ Б", "КОМПЛ", 1, 100), ("1111-22", "КАБЕЛЬ Б", "М", 5, 7),
              ("1111-22", "КАБЕЛЬ В", "М", 5, 8)], res=True)
    r = pipeline.run(p, p, pipeline.Options(mode="smart", local_sheet="92960_БВ", res_sheet="92960_БР",
                                            output_path=tmp_path / "out.xlsx"))
    rows = {x["row"]: x for x in r.rows}
    assert "шифр бир хил" not in (rows[16]["note"] or "")  # бирлиги бошқа
    assert "шифр бир хил" not in (rows[17]["note"] or "")  # битта шифрда икки хил ном


def test_fingerprint_ignores_text_but_not_structure(tmp_path):
    from core.batch import sheet_fingerprint
    a, b, c = tmp_path / "a.xlsx", tmp_path / "b.xlsx", tmp_path / "c.xlsx"
    _form(a, [("1", "ЦЕМЕНТ", "Т", 1, None)])
    _form(b, [("9", "ПЕСОК ҚУРИЛИШ", "М3", 4, None), ("8", "ГВОЗДИ", "КГ", 2, None)], title_no="12")
    _form(c, [("1", "ЦЕМЕНТ", "Т", 1, None)])
    wc = load_workbook(c)
    wc.active.column_dimensions["C"].width = 60  # бошқа шаблон
    wc.save(c)
    fa, fb, fc = (sheet_fingerprint(load_workbook(x).active) for x in (a, b, c))
    assert fa == fb and fa[0]  # матни бошқа — изи бир хил
    assert fa[0] != fc[0] and fa[1] != fc[1]


def test_numbering_row_with_missing_column():
    from openpyxl import Workbook
    from core.detect import _is_numbering_row
    ws = Workbook().active
    for c, v in zip("ADEFGHIJ", (1, 3, 4, 5, 6, 7, 8, 9)):
        ws[f"{c}1"] = v
    ws["A2"], ws["D2"], ws["E2"], ws["F2"] = 1, "ЦЕМЕНТ", 3, 4  # маълумот қатори (матн бор)
    ws["A3"], ws["B3"], ws["C3"], ws["D3"] = 1, 2, 3, 4
    assert _is_numbering_row(ws, 1) and not _is_numbering_row(ws, 2) and _is_numbering_row(ws, 3)


def test_abc_price_header_and_uzbek_headers():
    from openpyxl import Workbook
    from core.detect import detect_layout
    ws = Workbook().active
    for c, t in zip("ABCDEFG", ("N п/п", "Шифр (обоснование)", "Код АВС", "Наименование ресурса", "Единица измерения",
                                "Сметная потреб-ность", "Сметная стоимость, СУМ")):
        ws[f"{c}14"] = t
    ws["G15"], ws["G16"], ws["H16"] = "в базисном уровне", "единицы", "всего"
    for i, c in enumerate("ABCDEFGH", start=1):
        ws[f"{c}17"] = i
    lay = detect_layout(ws, need_price=True, stop_at_total=False)
    assert (lay.name_col, lay.unit_col, lay.price_col, lay.data_start) == (4, 5, 7, 18)

    uz = Workbook().active
    for c, t in zip("ABCD", ("№", "Номи", "Бирл.", "Баҳоси, сўм")):
        uz[f"{c}3"] = t
    lay = detect_layout(uz, need_price=True, stop_at_total=False)
    assert (lay.name_col, lay.unit_col, lay.price_col) == (2, 3, 4)
