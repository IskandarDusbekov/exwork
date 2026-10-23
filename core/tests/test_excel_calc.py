"""Python hisobi = Excel hisobi.

``excel_calc/<nom>/`` ichida: ``input_local.xlsx``, ``input_res.xlsx`` va pipeline chiqargan faylni
Excel (rus lokali) ochib, to'liq qayta hisoblab saqlagan nusxalari:
``excel_recalc.xlsx`` (compat rejimi) va/yoki ``excel_recalc_smart.xlsx`` (smart rejimi).
Har bir narxlangan qatorning H qiymati (I/J/K shundan olinadi) va svodka Python baholovchisi
bilan solishtiriladi. Bu, masalan, E/F dagi «50,27» kabi matn-sonlarni va ISNUMBER'ni tekshiradi.

* ``bolnitsa7_kr_blok2`` — «7-БОЛНИЦА, КР БЛОК 2» smetasi (LRV + RES);
* ``most_202611450`` — «МОСТ НА ПК 75+34», 202611450_БВ / 202611450_БР.
"""
from pathlib import Path

import pytest
from openpyxl import load_workbook

from core.pipeline import Options, run
from core.svodka import XlError

from .test_golden import _main

CASES_DIR = Path(__file__).parent / "excel_calc"
FILES = {"compat": "excel_recalc.xlsx", "smart": "excel_recalc_smart.xlsx"}
CASES = sorted(
    (d, mode) for d in (CASES_DIR.iterdir() if CASES_DIR.is_dir() else []) if d.is_dir()
    for mode, f in FILES.items() if (d / f).is_file()
)


def _same(python_value, excel_value) -> bool:
    if isinstance(python_value, XlError):
        return excel_value == str(python_value)
    if python_value is None:
        return excel_value in (None, 0)
    if isinstance(excel_value, (int, float)):
        return abs(python_value - excel_value) <= 1e-6 * max(1.0, abs(excel_value))
    return False


@pytest.fixture(scope="module", params=CASES, ids=lambda c: f"{c[0].name}-{c[1]}")
def case(request, tmp_path_factory):
    d, mode = request.param
    result = run(d / "input_local.xlsx", d / "input_res.xlsx",
                 Options(mode=mode, output_path=tmp_path_factory.mktemp(d.name) / "out.xlsx"))
    excel = _main(load_workbook(d / FILES[mode], data_only=True), result.local_sheet)
    return result, excel


def test_every_row_amount(case):
    result, excel = case
    diffs = [(row["row"], row["h"], excel[f"H{row['row']}"].value)
             for row in result.rows if not _same(row["h"], excel[f"H{row['row']}"].value)]
    assert not diffs, f"H farqlari (qator, python, excel): {diffs[:20]}"


def test_totals(case):
    result, excel = case
    s = result.svodka_start
    diffs = [(k, v, excel[f"H{s + i}"].value) for i, (k, v) in enumerate(result.totals.items())
             if not _same(v, excel[f"H{s + i}"].value)]
    assert not diffs, f"svodka farqlari: {diffs}"


def test_salary_matches_resource_file(case):
    result, _ = case
    if result.local_sheet == "LRV":
        assert result.totals["zarplata"] == pytest.approx(238735601.25, abs=0.01)
