from core.detect import pair_sheets

SHEETS = ["202611400_БВ", "202611400_БР", "202611410_БВ", "202611410_БР", "202611450_БВ"]
RES = SHEETS[:-1] + ["202611450_БР"]


def test_pairs_by_prefix():
    assert pair_sheets(SHEETS, RES) == ("202611400_БВ", "202611400_БР")
    assert pair_sheets(SHEETS, RES, "202611450_БВ") == ("202611450_БВ", "202611450_БР")


def test_same_file_for_both():
    assert pair_sheets(RES, RES, "202611410_БВ") == ("202611410_БВ", "202611410_БР")


def test_fallbacks():
    assert pair_sheets(["Лист1"], ["Лист1"]) == ("Лист1", "Лист1")
    assert pair_sheets(["Лист1"], ["Ресурс", "БР 3"]) == ("Лист1", "БР 3")
    assert pair_sheets([], ["a"]) == (None, "a")

