import pytest

from core.svodka import VALUE, Evaluator, XlError, compute_totals, svodka_formulas


def _ev(cells):
    return Evaluator(lambda a: cells.get(a))


def test_amount_formula_like_excel():
    f = '=IF(F1="",E1*G1,F1*G1)'
    assert _ev({"H1": f, "E1": 2, "G1": 10}).value("H1") == 20      # F bo'sh -> E*G
    assert _ev({"H1": f, "E1": 2, "F1": 3, "G1": 10}).value("H1") == 30
    assert _ev({"H1": f, "E1": 2}).value("H1") == 0                  # G bo'sh = 0
    assert _ev({"H1": f, "E1": 2, "G1": "--"}).value("H1") == VALUE
    assert _ev({"H1": f, "E1": 2, "G1": "ТОПИЛМАДИ"}).value("H1") == VALUE
    assert _ev({"H1": f, "E1": "5", "G1": 2}).value("H1") == 10      # raqamli matn


@pytest.mark.parametrize("text, value", [
    ("50,2741706", 50.2741706), ("4614.23", 4614.23), ("1 234,5", 1234.5), ("1 234", 1234),
    (" 12 ", 12), ("-3,5", -3.5), ("12%", 0.12), ("1,5E3", 1500),
    ("--", VALUE), ("ТОПИЛМАДИ", VALUE), ("1,2,3", VALUE), ("12 34", VALUE),
])
def test_text_numbers_like_russian_excel(text, value):
    f = '=IF(F1="",E1*G1,F1*G1)'
    got = _ev({"H1": f, "F1": text, "G1": 10}).value("H1")
    assert got == (VALUE if value is VALUE else pytest.approx(value * 10))


def test_sum_ignores_text_but_propagates_errors():
    cells = {"A1": 1, "A2": "matn", "A3": None, "A4": 2.5, "B1": "=SUM(A1:A4)"}
    assert _ev(cells).value("B1") == 3.5
    cells["A5"] = "=A2*1"
    cells["B1"] = "=SUM(A1:A5)"
    assert _ev(cells).value("B1") == VALUE


def test_reference_to_blank_is_zero_and_cached_errors():
    assert _ev({"A1": "=B1"}).value("A1") == 0
    assert isinstance(_ev({"A1": "=B1*2", "B1": "#DIV/0!"}).value("A1"), XlError)


def test_nested_and_parentheses():
    cells = {"A1": 10, "A2": 4, "A3": 1, "A4": 0.19, "B1": "=(A1-A2-A3)*A4", "B2": "=B1+A1*1.12"}
    assert _ev(cells).value("B2") == pytest.approx(5 * 0.19 + 11.2)


def test_svodka_formulas_match_old_program():
    # eski dasturdagi formulalar: s=77, sum=76
    assert svodka_formulas(77, 76) == [
        "=I76", "=J76", "=K76", "=H79*D80", "=L76", "=H81*D82", "=M76", "=H83*D84",
        "=SUM(H77:H84)", "=(H85-H84-H83)*D86", "=(H86+H85)*D87", "=H87+H86+H85", "=H88*1.12",
    ]


def test_compute_totals_numbers():
    s, sm = 10, 9
    written = {}
    cells = {}
    keys = ["zarplata", "mashina", "materialy", "transport_mat", "kabel", "transport_kabel",
            "oborudovanie", "transport_obor", "pryamye", "prochie", "strahovanie", "bez_nds", "s_nds"]
    pct = {3: 0.03, 5: 0.02, 7: 0.02, 9: 0.19, 10: 0.0032, 12: 0.12}
    for i, (k, f) in enumerate(zip(keys, svodka_formulas(s, sm))):
        written[f"H{s + i}"] = f
        cells[k] = f"H{s + i}"
        if i in pct:
            written[f"D{s + i}"] = pct[i]
    cached = {"I9": 100.0, "J9": 50.0, "K9": 1000.0, "L9": 0, "M9": None}
    t = compute_totals(written, cached.get, cells)

    direct = 100 + 50 + 1000 + 30
    prochie = direct * 0.19
    strah = (prochie + direct) * 0.0032
    bez = strah + prochie + direct
    assert t["pryamye"] == pytest.approx(direct)
    assert t["prochie"] == pytest.approx(prochie)
    assert t["bez_nds"] == pytest.approx(bez)
    assert t["s_nds"] == pytest.approx(bez * 1.12)
