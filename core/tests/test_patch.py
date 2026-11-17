"""Excel'ni yamash (``core.patch``) to'liq qayta narxlash bilan aynan bir xil fayl beradi."""
import shutil

import pytest
from openpyxl import load_workbook

from core.patch import NotPatchable, apply_prices
from core.pipeline import Options, run
from core.svodka import parse_text_number

from .test_smart import TESTS

CASE = TESTS / "excel_calc/bolnitsa7_kr_blok2"


def _full(tmp_path, name, prices):
    return run(CASE / "input_local.xlsx", CASE / "input_res.xlsx",
               Options(mode="smart", output_path=tmp_path / name, prices=prices or None))


def _cells(path, sheet, data_only):
    ws = load_workbook(path, data_only=data_only)[sheet]
    return {c.coordinate: c.value for row in ws.iter_rows() for c in row if c.value is not None}


def test_patched_file_equals_full_reprice(tmp_path):
    base = _full(tmp_path, "base.xlsx", {})
    red = [x for x in base.rows if x["suggested_price"] is not None]
    first = {red[0]["row"]: 1_234_567.0}  # аввалги қўлда нарх — кейин ўзгартирилади
    before = _full(tmp_path, "before.xlsx", first)
    patched = tmp_path / "patched.xlsx"
    shutil.copyfile(before.output_path, patched)

    change = {x["row"]: float(parse_text_number(str(x["suggested_price"]))) for x in red[1:6]}
    change[red[6]["row"]] = 987_654_321.0  # катта сон — устун кенгаяди
    change[red[0]["row"]] = 4_500_000.0
    notes = {r: "нарх қўлда киритилди" for r in change}
    done = apply_prices(patched, before.calc_kit, change, notes)

    full = _full(tmp_path, "full.xlsx", {**first, **change})
    sheet = full.local_sheet
    assert _cells(before.output_path, sheet, True) != _cells(full.output_path, sheet, True)
    # формулалар ва қийматлар (Excel очганда кўрадигани)
    assert _cells(patched, sheet, False) == _cells(full.output_path, sheet, False)
    # формулалар натижаси (сайт, телефон, «Защищённый просмотр»)
    got, want = _cells(patched, sheet, True), _cells(full.output_path, sheet, True)
    assert got.keys() == want.keys()
    for addr, v in want.items():
        assert got[addr] == (pytest.approx(v) if isinstance(v, float) else v), addr
    assert done.totals == pytest.approx(full.totals)
    rows = {x["row"]: x for x in full.rows}
    for r in change:
        assert done.values[f"H{r}"] == pytest.approx(rows[r]["h"])
    # устун эни — тўлиқ ҳисобдагидан тор эмас
    wp, wf = load_workbook(patched)[sheet], load_workbook(full.output_path)[sheet]
    for col in "GHIJK":
        assert (wp.column_dimensions[col].width or 0) >= (wf.column_dimensions[col].width or 0) - 0.01, col
    # LOG: ҳолат MANUAL, ёзилган нарх, изоҳ
    log = {row[0]: row for row in load_workbook(patched)["LOG"].iter_rows(min_row=2, values_only=True)}
    for r, p in change.items():
        assert log[r][3] == "MANUAL" and log[r][9] == p and log[r][10] == "нарх қўлда киритилди"
    # кейинги ямаш — янги тўплам билан (ҳисоб занжири узилмайди)
    again = apply_prices(patched, done.kit, {red[7]["row"]: 100.0}, {})
    full2 = _full(tmp_path, "full2.xlsx", {**first, **change, red[7]["row"]: 100.0})
    assert again.totals == pytest.approx(full2.totals)


def test_not_a_priced_row_is_not_patched(tmp_path):
    r = _full(tmp_path, "base.xlsx", {})
    with pytest.raises(NotPatchable):
        apply_prices(r.output_path, r.calc_kit, {r.sum_row: 5.0}, {})
