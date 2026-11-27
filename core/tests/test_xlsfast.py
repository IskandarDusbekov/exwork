""".xls → .xlsx LibreOffice'сиз (xlrd) — нарх базасига йиғиш учун."""
import datetime as dt
from pathlib import Path

import pytest
from openpyxl import load_workbook

from core import convert, xlsfast

SAMPLE = Path(__file__).parent / "xls" / "sample.xls"  # Excel'да ясалган: бирлашган катак, яширин лист, формула

pytestmark = pytest.mark.skipif(not xlsfast.available(), reason="xlrd ўрнатилмаган")


def test_values_merges_and_hidden_sheet(tmp_path):
    out = xlsfast.xls_to_xlsx(SAMPLE, tmp_path / "a.xlsx")
    wb = load_workbook(out, data_only=True)
    assert sorted(wb.sheetnames) == ["Смета", "Яширин"]
    ws = wb["Смета"]
    assert [ws.cell(3, c).value for c in (1, 2, 3)] == ["ЦЕМЕНТ ПЦ 400", "т", 750000]  # бутун сон — бутун
    assert ws["C4"].value == 760.5
    assert ws["C5"].value == 1500000  # формуланинг файлдаги қиймати
    assert ws["A5"].value == "=не формула"  # «=» билан бошланган матн — формула бўлиб қолмайди
    # сана — сана бўлиб қолади (сон эмас); намуна Excel COM'да ёзилган — соат минтақаси сурилган, шунинг учун фақат кун
    assert isinstance(ws["D3"].value, dt.datetime) and ws["D3"].value.date() >= dt.date(2026, 11, 19)
    assert "A1:C1" in {str(r) for r in ws.merged_cells.ranges}
    assert ws["A1"].font.bold
    assert ws.column_dimensions["A"].width > 30
    assert wb["Яширин"].sheet_state == "hidden"


def test_same_rows_as_converted_file(tmp_path):
    """Нарх базаси ўқийдиган қаторлар — LibreOffice ўгиргани билан бир хил (бу ерда — xlrd'нинг ўзи)."""
    from narxbaza import services as S
    rows = S.file_rows(xlsfast.xls_to_xlsx(SAMPLE, tmp_path / "a.xlsx"), "excel")
    got = [(r["name"], r["unit"], r["price"]) for r in rows]
    assert ("ЦЕМЕНТ ПЦ 400", "т", 750000) in got and ("ВОДА", "м3", 760.5) in got
    assert all(r["sheet"] == "Смета" for r in rows)  # яширин лист ўқилмайди


def test_unreadable_xls_falls_back_to_converter(tmp_path, monkeypatch):
    fake = tmp_path / "html.xls"
    fake.write_text("<html><table><tr><td>ВОДА</td></tr></table></html>", encoding="utf-8")  # «.xls» номли HTML
    with pytest.raises(xlsfast.FastXlsError):
        xlsfast.xls_to_xlsx(fake, tmp_path / "x.xlsx")
    called = []
    monkeypatch.setattr(convert, "ensure_xlsx", lambda p, d, **kw: called.append(p) or d / "conv.xlsx")
    assert xlsfast.to_xlsx_values(fake, tmp_path / "out").name == "conv.xlsx"
    assert called == [fake]
    assert not (tmp_path / "out" / "html.xlsx").exists()


def test_xlsx_passthrough_and_no_converter_for_xls(tmp_path, monkeypatch):
    monkeypatch.setattr(convert, "ensure_xlsx", lambda *a, **k: pytest.fail("LibreOffice чақирилмаслиги керак"))
    out = xlsfast.to_xlsx_values(SAMPLE, tmp_path / "out")
    assert out == tmp_path / "out" / "sample.xlsx" and out.is_file()
