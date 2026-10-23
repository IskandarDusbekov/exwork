import json
import os
from copy import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from openpyxl.cell.cell import Cell

from core import convert
from core.writer import SheetWriter

GOLDEN_DIR = Path(os.environ.get("NARXLASH_GOLDEN_DIR", Path(__file__).parent / "golden"))
EXTS = (".xlsx", ".xlsm", ".xls")


@dataclass
class GoldenCase:
    name: str
    local: Path
    res: Path
    expected: Path
    options: dict = field(default_factory=dict)

    def needs_soffice(self) -> bool:
        return any(p.suffix.lower() == ".xls" for p in (self.local, self.res))


def _find(d: Path, stem: str) -> Optional[Path]:
    for ext in EXTS:
        p = d / f"{stem}{ext}"
        if p.is_file():
            return p
    return None


def golden_cases() -> List[GoldenCase]:
    """``golden/<nom>/`` ichida: input_local.*, input_res.*, expected_narxlandi.*, ixtiyoriy options.json."""
    if not GOLDEN_DIR.is_dir():
        return []
    cases = []
    for d in sorted(p for p in GOLDEN_DIR.iterdir() if p.is_dir()):
        local, res, expected = _find(d, "input_local"), _find(d, "input_res"), _find(d, "expected_narxlandi")
        if local and res and expected:
            opts_file = d / "options.json"
            options = json.loads(opts_file.read_text(encoding="utf-8")) if opts_file.is_file() else {}
            cases.append(GoldenCase(d.name, local, res, expected, options))
    return cases


def soffice_available() -> bool:
    try:
        convert.find_soffice()
        return True
    except convert.ConversionError:
        return False


# ===================== uslub imzosi =====================
def _color(c):
    if c is None:
        return None
    return (c.type, c.value, round(c.tint or 0, 6))


def _side(s):
    return (s.style, _color(s.color)) if s is not None else (None, None)


def effective_cell(ws, row: int, col: int):
    """Faylda bor katak yoki — yo'q bo'lsa — Excel ko'rsatadigan meros uslubli katak."""
    cell = ws._cells.get((row, col))
    if cell is not None:
        return cell
    tmp = Cell(ws, row=row, column=col)
    style = SheetWriter(ws)._inherited_style(row, col)
    if style is not None:
        tmp._style = copy(style)
    return tmp


def style_sig(cell) -> dict:
    f, fl, b, a = cell.font, cell.fill, cell.border, cell.alignment
    return {
        "font": (f.name, f.sz, bool(f.b), bool(f.i), f.u, bool(f.strike), _color(f.color)),
        "fill": (fl.fill_type, _color(fl.fgColor), _color(fl.bgColor)) if fl.fill_type else (None,),
        "border": {k: _side(getattr(b, k)) for k in ("left", "right", "top", "bottom", "diagonal")},
        "alignment": (a.horizontal, a.vertical, bool(a.wrap_text), a.indent, a.textRotation, bool(a.shrink_to_fit)),
        "number_format": cell.number_format,
    }
