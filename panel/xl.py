"""Панел: фойдаланувчи юклаган асл файлларни Excel кўринишида очиш ва ҳаммасини битта ZIP қилиб бериш.

Асл архив (ZIP/RAR) юклангандан кейин очилади ва ўзи сақланмайди — шунинг учун «бутун юклаш» ZIP'и
ичидаги ҳамма Excel'лардан, папкадаги ўз йўли билан қайта йиғилади (смета­чи кўрган тартибда).
"""
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from jobs import xlview

from . import data

VIEW_ROWS = 1500  # биринчи очилишда шунча қатор (тез); «Ҳаммасини кўрсатиш» — xlview.MAX_ROWS гача
XLSX = (".xlsx", ".xlsm")


def _ok(p) -> bool:
    return p is not None and p.suffix.lower() in XLSX and p.is_file() and data._inside_media(p)


def sources(kind, w, files) -> list:
    """Ҳар бир асл файл (``data.originals`` тартибида) учун кўрсатса бўладиган .xlsx ва кеш папкаси:
    [(path, cache_dir) ёки None]. .xls файллар очилгандаги нусхадан кўрсатилади (асли openpyxl'га ўқилмайди)."""
    out = []
    if kind == "folder":
        conv = {}
        for part in w.parts.only("id", "files"):
            for f in part.files:
                if f.get("path") and f.get("src"):
                    conv[f["src"]] = (part.abs(f["path"]), part.dir / "preview")
        for f in files:
            p, cache = conv.get(f["path"], (None, None))
            out.append((p, cache) if _ok(p) else (f["abs"], w.dir / "preview") if _ok(f["abs"]) else None)
    elif kind == "object":
        for i, f in enumerate(files):
            rel = w.files[i].get("path") if i < len(w.files) else ""
            p = w.abs(rel) if rel else None
            out.append((p, w.dir / "preview") if _ok(p) else (f["abs"], w.dir / "preview") if _ok(f["abs"]) else None)
    else:
        # смета: 0 — локал, 1 — ресурс (data.originals тартиби); ишда сақланган .xlsx нусхаси
        for f in files:
            rel = w.res_path if f["path"].startswith("Ресурс") else w.local_path
            p = w.abs(rel) if rel else None
            out.append((p, w.dir / "preview") if _ok(p) else None)
    return out


def render(path: Path, cache_dir: Path, sheet=None, full=False) -> dict:
    """Листни HTML жадвал қилиб (кешланади — файл ўзгармагунча қайта ҳисобланмайди)."""
    n = xlview.MAX_ROWS if full else VIEW_ROWS
    return xlview.cached(cache_dir, f"panel|{sheet}|{n}", path, lambda: xlview.render(path, sheet, max_rows=n))


def _unique(name: str, seen: set) -> str:
    stem, dot, ext = name.rpartition(".") if "." in PurePosixPath(name).name else (name, "", "")
    out, i = name, 2
    while out.lower() in seen:
        out = f"{stem} ({i}).{ext}" if dot else f"{name} ({i})"
        i += 1
    seen.add(out.lower())
    return out


def originals_zip(kind, w, files):
    """Ҳамма асл файллар битта ZIP'да (вақтинча файл — юборилгач ўчади): (файл, нечта қўшилди).
    Папкада — ўз йўли билан («Папка/2. Блок-А/…»), объект ва сметада — асл номи билан."""
    tmp = tempfile.TemporaryFile()
    seen, n = set(), 0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            if f["abs"] is None:
                continue
            arc = f"{w.name}/{f['path']}" if kind == "folder" else f["name"]
            z.write(f["abs"], _unique(arc, seen))
            n += 1
    tmp.seek(0)
    return tmp, n
