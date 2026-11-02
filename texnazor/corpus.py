"""Ofyortalar to'plamini tekshirish: har bir fayl tizimdagidek o'qiladi va ``Book.self_check`` dan o'tkaziladi.
Yangi qoida qo'shilganda avval o'qiganlari buzilmaganini bitta buyruq bilan ko'rish uchun
(``manage.py tn_check <papka>``, test: ``TEXNAZOR_CORPUS=<papka> manage.py test texnazor.tests_corpus``)."""
import tempfile
from pathlib import Path
from typing import Iterable, List

from core import convert

from .sheet import Book, list_sheets, read

EXT = (".xlsx", ".xlsm", ".xls")


def files(folder: Path, ignore: Iterable[str] = ()) -> List[Path]:
    """Papkadagi Excel fayllari (ichki papkalar bilan); nomida ``ignore`` dagi so'z bo'lsa — tashlanadi."""
    ignore = [s.lower() for s in ignore if s]
    out = [p for p in Path(folder).rglob("*") if p.suffix.lower() in EXT and not p.name.startswith("~$")
           and not any(s in p.name.lower() for s in ignore)]
    return sorted(out)


def check_file(path: Path, work: Path = None) -> dict:
    """{"file", "sheet", "ok", "error", "full", "empty", "half", "file_total", "fails"} — bitta fayl."""
    res = {"file": Path(path).name, "sheet": "", "ok": False, "error": "", "fails": []}
    with tempfile.TemporaryDirectory() as tmp:
        try:
            x = convert.ensure_xlsx(Path(path), Path(work or tmp))
            sheets = list_sheets(x)
            guess = next((s["name"] for s in sheets if s.get("guess")), None)
            if not guess:
                res["error"] = "офёрта листи топилмади"
                return res
            res["sheet"] = guess
            grid = read(x, guess)
            c = Book(grid.layout.as_dict(), grid.rows).self_check()
        except Exception as e:  # noqa: BLE001 — har qanday xato — natijada
            res["error"] = f"{type(e).__name__}: {e}"
            return res
    res.update(ok=c["ok"], full=c["full"], empty=c["empty"], half=c["half"], file_total=c["file"], fails=c["fails"])
    return res
