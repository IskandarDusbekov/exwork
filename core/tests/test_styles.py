"""Dizayn bir xilligi (reja §5): kirish va chiqish faylda A1:G{oxirgi} oralig'ida uslub bir xil.

«Oxirgi» — narxlangan oxirgi qator (``Result.end_row``); undan keyin compat rejimi sum
qatori va svodkani yozadi. Oraliq ichida faqat spetsifikatsiyadagi o'zgarishlar kutiladi:
* ``start..end`` qatorlarida A–G kataklariga yupqa ramka (§2.8);
* ramka ustidagi qatorning pastki chizig'i olib tashlanadi (Excel umumiy chegarani bir joyda saqlaydi);
* G ustunda son formati ``#,##0``.
Qolgan hamma narsa — shrift, fon, tekislash, son formati, birlashtirish, ustun kengligi,
qator balandligi va header/footer — o'zgarmasligi kerak.
"""
from pathlib import Path

import pytest
from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string, get_column_letter

from core import convert
from core.pipeline import Options, run
from core.writer import NUM_FMT, column_width, read_header_footer, restore_merged_styles

from .helpers import effective_cell, golden_cases, soffice_available, style_sig

THIN = ("thin", None)
NONE = (None, None)


def _expected_sig(ws_in, r, c, start, end):
    sig = style_sig(effective_cell(ws_in, r, c))
    if start <= r <= end:
        sig["border"].update(left=THIN, right=THIN, top=THIN, bottom=THIN)
    if r == start - 1:
        sig["border"]["bottom"] = NONE
    if r == end + 1:
        sig["border"]["top"] = NONE
    if c == column_index_from_string("G") and start <= r <= end:
        sig["number_format"] = NUM_FMT
    return sig


def _load(path: Path, sheet: str):
    # openpyxl birlashgan kataklar uslubini yuklashda buzadi; faylda aslida nima borligini
    # ko'rish uchun XML'dagi uslubni qaytaramiz.
    wb = load_workbook(path)
    restore_merged_styles(wb, path)
    return wb[sheet]


def assert_styles_preserved(local_xlsx: Path, out: Path, sheet: str, start: int, end: int, last_col: str = "G",
                            last_row: int = None, growable: str = ""):
    """``last_row`` — qaysi qatorgacha tekshirish (standart: ``end``). Smart rejimda svodka
    smeta pastidan keyin yoziladi, shuning uchun pastki «СОСТАВИЛ» qatorlari ham tekshiriladi.
    ``growable`` — kengligi faqat oshishi mumkin bo'lgan ustunlar (smart: G..Q, sonlar sig'ishi uchun)."""
    ws_in = _load(local_xlsx, sheet)
    ws_out = _load(out, sheet)
    last_row = last_row or end

    diffs = []
    for r in range(1, last_row + 1):
        for c in range(1, column_index_from_string(last_col) + 1):
            want = _expected_sig(ws_in, r, c, start, end)
            got = style_sig(effective_cell(ws_out, r, c))
            for key in want:
                if want[key] != got[key]:
                    diffs.append(f"{get_column_letter(c)}{r} {key}: {want[key]} != {got[key]}")
    assert not diffs, "Uslub o'zgargan:\n" + "\n".join(diffs[:40])

    assert sorted(map(str, ws_in.merged_cells.ranges)) == sorted(map(str, ws_out.merged_cells.ranges))

    def widths(ws):
        return {k: (d.min, d.max, d.width, d.hidden) for k, d in ws.column_dimensions.items()}
    if not growable:
        assert widths(ws_in) == widths(ws_out)
    else:
        for col in range(1, 60):
            before, after = column_width(ws_in, col), column_width(ws_out, col)
            if get_column_letter(col) in growable:
                assert after >= before, f"{get_column_letter(col)} toraydi: {before} -> {after}"
            else:
                assert after == before, f"{get_column_letter(col)} kengligi o'zgargan: {before} -> {after}"

    for r in range(1, max(ws_in.max_row, end) + 1):
        assert ws_in.row_dimensions[r].height == ws_out.row_dimensions[r].height, f"qator {r} balandligi"

    assert read_header_footer(local_xlsx, sheet) == read_header_footer(out, sheet)


def test_styles_sample(sample_files, tmp_path):
    local, res = sample_files
    out = tmp_path / "out.xlsx"
    result = run(local, res, Options(output_path=out))
    assert read_header_footer(local, result.local_sheet) is not None  # fixture haqiqatan header/footer'li
    assert_styles_preserved(local, out, result.local_sheet, result.start_row, result.end_row)


@pytest.mark.parametrize("case", golden_cases(), ids=lambda c: c.name)
def test_styles_golden(case, tmp_path):
    if case.needs_soffice() and not soffice_available():
        pytest.skip("LibreOffice yo'q: .xls kirishni konvertatsiya qilib bo'lmaydi")
    out = tmp_path / "out.xlsx"
    result = run(case.local, case.res, Options(output_path=out, fuzzy_percent=case.options.get("fuzzy", 95)))
    local_xlsx = convert.ensure_xlsx(case.local, tmp_path / "conv")
    assert_styles_preserved(local_xlsx, out, result.local_sheet, result.start_row, result.end_row)


@pytest.mark.parametrize("case", golden_cases(), ids=lambda c: c.name)
def test_styles_smart_keeps_footer(case, tmp_path):
    """Smart: ramka faqat ma'lumot qatorlarida, svodka «СОСТАВИЛ»dan keyin — pastki qism o'zgarmaydi."""
    if case.needs_soffice() and not soffice_available():
        pytest.skip("LibreOffice yo'q: .xls kirishni konvertatsiya qilib bo'lmaydi")
    out = tmp_path / "out.xlsx"
    result = run(case.local, case.res, Options(output_path=out, mode="smart"))
    local_xlsx = convert.ensure_xlsx(case.local, tmp_path / "conv")
    last_input_row = load_workbook(local_xlsx)[result.local_sheet].max_row
    assert result.svodka_start > last_input_row
    assert_styles_preserved(local_xlsx, out, result.local_sheet, result.start_row, result.end_row,
                            last_row=last_input_row, growable="GHIJKLMNOPQ")
