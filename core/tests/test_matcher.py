from openpyxl import Workbook

from core.matcher import AMBIGUOUS, EXACT, FUZZY, NOT_FOUND, ResourceIndex, best_two_matches
from core.normalize import norm_name, norm_unit


def _index(rows):
    """rows: (nom, birlik, narx) — resurs listining D, E, G ustunlari."""
    wb = Workbook()
    ws = wb.active
    for i, (name, unit, price) in enumerate(rows, start=1):
        ws.cell(row=i, column=4, value=name)
        ws.cell(row=i, column=5, value=unit)
        ws.cell(row=i, column=7, value=price)
    return ResourceIndex.from_sheet(ws, "D", "G")


def _match(index, name, unit, fuzzy=95):
    return index.match(norm_name(name), norm_unit(unit), fuzzy / 100)


def test_index_skips_empty_and_keeps_first_duplicate():
    idx = _index([
        ("Наименование", None, "x"),
        (None, "М3", 1),
        ("ВОДА", "М3", 1000),
        ("вода", "м3", 2000),
    ])
    assert idx.by_unit == {"м3": {"вода": 1000}}


def test_exact():
    idx = _index([("ВОДА", "М3", 1000)])
    m = _match(idx, " Вода ", "м3")
    assert (m.status, m.price, m.unit, m.score) == (EXACT, 1000, "м3", 1.0)


def test_exact_in_other_unit_wins():
    idx = _index([("ВОДА", "Т", 7)])
    m = _match(idx, "ВОДА", "М3")
    assert (m.status, m.unit, m.price) == (EXACT, "т", 7)


def test_fuzzy():
    idx = _index([("БЕТОН ТЯЖЕЛЫЙ КЛАССА В15 /М-200/ ФРАКЦИИ 5-20 ММ", "М3", 485994)])
    m = _match(idx, "БЕТОН ТЯЖЕЛЫЙ КЛАССА В15 /М-200/ ФРАКЦИИ 5-20ММ", "М3")
    assert m.status == FUZZY and m.price == 485994 and 0.95 <= m.score < 1


def test_ambiguous():
    idx = _index([
        ("КРАСКА МАСЛЯНАЯ МА-15А", "Т", 1),
        ("КРАСКА МАСЛЯНАЯ МА-15Б", "Т", 2),
    ])
    m = _match(idx, "КРАСКА МАСЛЯНАЯ МА-15", "Т")
    assert m.status == AMBIGUOUS and m.price is None
    assert m.second_name is not None and m.score - m.second_score < 0.01


def test_not_found_below_threshold():
    idx = _index([("ВОДА", "М3", 1000)])
    assert _match(idx, "ГВОЗДИ СТРОИТЕЛЬНЫЕ", "Т").status == NOT_FOUND


def test_best_two_matches_order():
    best, bs, second, ss = best_two_matches("abcd", ["abcx", "abcd", "zzzz"])
    assert best == "abcd" and bs == 1.0 and second == "abcx" and ss == 0.75
