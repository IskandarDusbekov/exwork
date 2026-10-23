"""Narxlash jarayoni: ``run(local, res, opts) -> Result`` va CLI.

python -m core.pipeline local.xlsx res.xlsx -o out.xlsx --fuzzy 95
"""
import argparse
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from openpyxl import load_workbook

from . import convert
from .classify import is_workers_row, row_kind
from .log_sheet import log_row, write_log
from .matcher import AMBIGUOUS, EXACT, FUZZY, NOT_FOUND, ResourceIndex
from .normalize import norm_name, norm_unit
from .svodka import XlError, make_evaluator, write_svodka
from .writer import (
    SheetWriter, restore_header_footer, restore_merged_styles, write_priced_rows, write_sum_row,
)

NOT_FOUND_TEXT = "ТОПИЛМАДИ"
AMBIGUOUS_TEXT = "ШАБҲАЛИ"

LOCAL_NAME_COL = 3  # C, birlik D
RES_NAME_COL = "D"  # birlik E
RES_PRICE_COL = "G"


@dataclass
class Options:
    fuzzy_percent: int = 95
    ambiguity_delta: float = 0.01
    mode: str = "compat"            # hozircha faqat "compat"
    local_sheet: Optional[str] = None  # None -> 1-list (eski dastur kabi)
    res_sheet: Optional[str] = None
    soffice: Optional[str] = None      # LibreOffice yo'li (.xls uchun)
    output_path: Optional[Path] = None


@dataclass
class Result:
    output_path: Path
    stats: Dict[str, int]
    problems: List[dict]
    totals: Dict[str, object]  # son yoki XlError ("#VALUE!" ...)
    local_sheet: str = ""
    start_row: int = 0
    end_row: int = 0
    sum_row: int = 0
    svodka_start: int = 0
    log: List[list] = field(default_factory=list)
    rows: List[dict] = field(default_factory=list)  # start..end: g, h (hisoblangan), kind, status


def default_output_path(local_path: Path) -> Path:
    ext = ".xlsm" if local_path.suffix.lower() == ".xlsm" else ".xlsx"
    return local_path.with_name(f"{local_path.stem}_narxlandi{ext}")


def _pick_sheet(wb, name: Optional[str]):
    if name is None:
        return wb.worksheets[0]
    if name not in wb.sheetnames:
        raise ValueError(f"'{name}' listi topilmadi. Bor listlar: {', '.join(wb.sheetnames)}")
    return wb[name]


def _peek(ws, row: int, col: int):
    """Katak qiymatini o'qiydi, lekin ``ws.cell()`` kabi yangi katak yaratmaydi
    (aks holda writer uni «faylda bor» deb o'ylab, qator uslubini bermay qo'yadi)."""
    cell = ws._cells.get((row, col))
    return cell.value if cell is not None else None


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def run(local_path, res_path, opts: Optional[Options] = None) -> Result:
    opts = opts or Options()
    if opts.mode != "compat":
        raise NotImplementedError("Hozircha faqat 'compat' rejimi bor")
    local_path, res_path = Path(local_path), Path(res_path)
    out_path = Path(opts.output_path) if opts.output_path else default_output_path(local_path)

    with tempfile.TemporaryDirectory(prefix="narxlash_") as tmp:
        local_xlsx = convert.ensure_xlsx(local_path, Path(tmp) / "local", opts.soffice)
        res_xlsx = convert.ensure_xlsx(res_path, Path(tmp) / "res", opts.soffice)
        return _run_xlsx(local_xlsx, res_xlsx, out_path, opts)


def _run_xlsx(local_xlsx: Path, res_xlsx: Path, out_path: Path, opts: Options) -> Result:
    # --- resurs indeksi ---
    wb_res = load_workbook(res_xlsx, data_only=True)
    index = ResourceIndex.from_sheet(_pick_sheet(wb_res, opts.res_sheet), RES_NAME_COL, RES_PRICE_COL)
    wb_res.close()

    # --- lokal ---
    keep_vba = local_xlsx.suffix.lower() == ".xlsm"
    wb = load_workbook(local_xlsx, keep_vba=keep_vba)
    restore_merged_styles(wb, local_xlsx)
    ws = _pick_sheet(wb, opts.local_sheet)
    sheet_titles = list(wb.sheetnames)
    ln, lu = LOCAL_NAME_COL, LOCAL_NAME_COL + 1

    threshold = int(max(80, min(100, opts.fuzzy_percent))) / 100.0
    max_l = ws.max_row

    g_values: Dict[int, object] = {}
    log_rows: List[list] = []
    zero_rows = set()
    first_priced_row: Optional[int] = None
    last_data_row = 1

    for r in range(1, max_l + 1):
        name_raw = _peek(ws, r, ln)
        unit_raw = _peek(ws, r, lu)

        if name_raw is not None and str(name_raw).strip() != "":
            last_data_row = r
        # Nol qoidasi: ishchilar mehnati qatoridan oldingi qator (ish sarlavhasi) = 0
        if r > 1 and is_workers_row(name_raw, unit_raw):
            zero_rows.add(r - 1)

        local_name = norm_name(name_raw)
        if not local_name:
            continue
        m = index.match(local_name, norm_unit(unit_raw), threshold, opts.ambiguity_delta)
        if m.status in (EXACT, FUZZY):
            value = m.price
        elif m.status == AMBIGUOUS:
            value = f"{AMBIGUOUS_TEXT} ({round(m.score, 3)})"
        else:
            value = NOT_FOUND_TEXT
        g_values[r] = value
        if first_priced_row is None:
            first_priced_row = r
        log_rows.append(log_row(r, name_raw, unit_raw, m, value))

    for rr in zero_rows:
        g_values[rr] = 0
        if first_priced_row is None:
            first_priced_row = rr

    if first_priced_row is None:
        first_priced_row = 2 if max_l >= 2 else 1
    start_r = first_priced_row
    end_r = last_data_row if last_data_row >= start_r else max_l

    kinds = {
        r: row_kind(_peek(ws, r, ln), _peek(ws, r, lu))
        for r in range(start_r, end_r + 1)
    }

    # --- yozish ---
    w = SheetWriter(ws)
    write_priced_rows(w, start_r, end_r, g_values, kinds)
    sum_row = end_r + 1
    write_sum_row(w, sum_row, start_r, end_r)
    svodka_start = sum_row + 1
    svodka_cells = write_svodka(w, svodka_start, sum_row)
    write_log(wb, log_rows)

    # --- svodka summalari (Excel saqlagan qiymatlar + biz yozgan formulalar) ---
    wb_vals = load_workbook(local_xlsx, data_only=True)
    ws_vals = wb_vals[ws.title]
    ev = make_evaluator(w.written, lambda a: ws_vals[a].value)
    totals = {key: ev.value(addr) for key, addr in svodka_cells.items()}

    # Sahifada ko'rsatish uchun: har bir qator Excel'da qanday chiqishi
    log_by_row = {row[0]: row for row in log_rows}
    rows = []
    for r in range(start_r, end_r + 1):
        log = log_by_row.get(r)
        rows.append({
            "row": r,
            "name": _peek(ws, r, ln),
            "unit": _peek(ws, r, lu),
            "g": w.written.get(f"G{r}"),
            "h": ev.value(f"H{r}") if f"H{r}" in w.written else None,
            "kind": kinds[r],
            "status": log[3] if log else "",
            "picked_name": log[5] if log else "",
            "score": log[6] if log else "",
            "zero": r in zero_rows,
        })
    wb_vals.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    wb.close()
    restore_header_footer(local_xlsx, out_path, sheet_titles)

    # --- statistika va muammolar ---
    stats = Counter({EXACT: 0, FUZZY: 0, AMBIGUOUS: 0, NOT_FOUND: 0})
    stats.update(row[3] for row in log_rows)
    stats["ZERO_RULE"] = len(zero_rows)
    problems = []
    for row in log_rows:
        r = row[0]
        final = g_values.get(r)
        if not _is_number(final):
            problems.append({
                "row": r, "name": row[1], "unit": row[2], "status": row[3],
                "picked_name": row[5], "score": row[6], "written": final,
            })
    stats["NON_NUMERIC"] = len(problems)

    return Result(
        output_path=out_path, stats=dict(stats), problems=problems, totals=totals,
        local_sheet=ws.title, start_row=start_r, end_row=end_r, sum_row=sum_row,
        svodka_start=svodka_start, log=log_rows, rows=rows,
    )


# ===================== CLI =====================
def _fmt(v) -> str:
    if isinstance(v, XlError):
        return str(v)
    if _is_number(v):
        return f"{v:,.2f}".replace(",", " ")
    return repr(v)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m core.pipeline", description="Смета нархлаш (compat rejimi)")
    p.add_argument("local", type=Path, help="Lokal (БВ) fayl: .xls/.xlsx/.xlsm")
    p.add_argument("res", type=Path, help="Resurs (БР) fayl: .xls/.xlsx/.xlsm")
    p.add_argument("-o", "--output", type=Path, help="Chiqish fayli (standart: <lokal>_narxlandi.xlsx)")
    p.add_argument("--fuzzy", type=int, default=95, help="Fuzzy chegarasi, 80–100 (standart 95)")
    p.add_argument("--local-sheet", help="Lokal list nomi (standart: 1-list)")
    p.add_argument("--res-sheet", help="Resurs list nomi (standart: 1-list)")
    p.add_argument("--soffice", help=f"LibreOffice yo'li (yoki {convert.ENV_VAR})")
    args = p.parse_args(argv)

    opts = Options(
        fuzzy_percent=args.fuzzy, local_sheet=args.local_sheet, res_sheet=args.res_sheet,
        soffice=args.soffice, output_path=args.output,
    )
    try:
        res = run(args.local, args.res, opts)
    except (convert.ConversionError, ValueError, FileNotFoundError) as e:
        print(f"Xato: {e}", file=sys.stderr)
        return 1

    print(f"Tayyor: {res.output_path}")
    print(f"List: {res.local_sheet}, qatorlar {res.start_row}..{res.end_row}, "
          f"sum qatori {res.sum_row}, svodka {res.svodka_start}")
    print("Statistika: " + ", ".join(f"{k}={v}" for k, v in res.stats.items()))
    for key, value in res.totals.items():
        print(f"  {key:16} {_fmt(value)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
