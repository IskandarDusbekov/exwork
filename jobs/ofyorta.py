"""Офёрта: папкадаги нархланган сметалар (``Job.output_path`` — смета­чининг қўлда ўзгаришлари билан) смета­чи
белгилаган тартибда битта «Офёрта» варағида.

Ҳар смета — блок:

* **сариқ сарлавҳа** — бу қайси иш экани (стандарт: сметадаги «на …» қатори, смета­чи ўзгартиради);
* **жадвал** — сметанинг рақамлаш қаторидан («1 2 3 …») кейинги қатордан сумма қаторигача: қиймат, формула
  (қатор силжишига мослаб), шрифт, ранг, чегара, бирлашган катаклар, қатор баландлиги — асл файлдагидек;
* **ўз сводкаси** — асл файлдаги матн ва фоизлар билан; формулалари шу блокнинг сумма қаторига боғланади.
  Асл файлдаги имзолар («Составил / Проверил») олинмайди.

Охирида — **умумий жами**: ҳар смета «Всего с НДС» билан ва барча сводкалар йиғиндиси. Ҳаммаси формула —
Excel'да бирор нарх ўзгарса, блок сводкаси ҳам, умумий жами ҳам ўзи қайта ҳисобланади.

Сметалар тартиби — папкалар дарахти бўйича (``tree``): смета­чи папка ичида сметаларни, папкалар ичида
папкаларни суради; офёрта — дарахтнинг юқоридан пастга ўқилиши.
"""
import re
from copy import copy
from pathlib import Path
from typing import Dict, List, Optional

from django.utils import timezone
from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.formula.translate import Translator
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.formula import ArrayFormula
from openpyxl.worksheet.merge import MergedCellRange

from core.svodka import OLD_SVODKA_TEXTS, SVODKA_ROWS, make_evaluator, svodka_formulas, svodka_layout
from core.writer import write_cached_values

from .models import Job, Project, ProjectOfyorta

YELLOW = PatternFill("solid", fgColor="FFFFFF00")
GREY = PatternFill("solid", fgColor="FFD9D9D9")
CYAN = PatternFill("solid", fgColor="FFCCFFFF")  # смета сарлавҳаси — смета­чи офёртасидагидек
BAND_COLS = 8  # сариқ ва сарлавҳа: A..H (жадвалнинг асосий қисми)
THIN = Side(style="thin")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
MAX_COLS = 30  # асл варақда ўнгда тасодифий форматлар бўлса — шунгача
MIN_COLS = 16  # A..P (нарх, сумма ва турлар)
FILE = "ofyorta/ofyorta.xlsx"
NUM_FMT = "#,##0.00"
_LEAD = re.compile(r"^\s*на\b[\s:,]*", re.I)
_SPACES = re.compile(r"\s+")


class OfyortaError(Exception):
    pass


# ===================== сметалар ва тартиб =====================
def _natural(path: str):
    """«10. Благаустройство» «2. Блок-А» дан кейин (рақамлар сон сифатида)."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path)]


def priced_jobs(project: Project) -> List[Job]:
    """Папканинг нархланган сметалари — папкалар тартибида (рақамлари бўйича), папка ичида — жуфтлик тартибида."""
    jobs = list(Job.objects.filter(batch__project=project, status=Job.Status.DONE).exclude(output_path="")
                .select_related("batch"))
    return sorted(jobs, key=lambda j: (_natural(j.batch.folder or ""), j.batch_order, j.created))


def _sheet(wb, job: Job):
    return wb[job.local_sheet] if job.local_sheet in wb.sheetnames else wb.worksheets[0]


def _line(row) -> str:
    return " ".join(_SPACES.sub(" ", str(v)).strip() for v in row if v not in (None, "") and str(v).strip())


def _top_lines(job: Job) -> List[str]:
    """Сметанинг жадвалгача бўлган қаторлари (матн)."""
    start = (job.result or {}).get("start_row") or 20
    try:
        wb = load_workbook(job.abs(job.output_path), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 — файл бузилган бўлса ҳам рўйхат очилсин
        return []
    try:
        ws = _sheet(wb, job)
        return [_line(r) for r in ws.iter_rows(min_row=1, max_row=min(start, 40), max_col=12, values_only=True)]
    finally:
        wb.close()


def _named_above(lines: List[str], marker: str) -> str:
    """«(наименование работ…)» каби изоҳ қаторидан олдинги матн."""
    for i, text in enumerate(lines):
        if i and text.lower().lstrip("(").startswith(marker):
            return next((t for t in reversed(lines[:i]) if t), "")
    return ""


def work_title(job: Job, lines: Optional[List[str]] = None) -> str:
    """Сариқ сарлавҳа: сметадаги «на ФЭС» → «ФЭС»; топилмаса — қисм ёки файл номи."""
    lines = _top_lines(job) if lines is None else lines
    name = _LEAD.sub("", _named_above(lines, "наименование работ")).strip(" ,.;")
    return (name or job.batch_label or Path(job.local_name).stem).strip()[:300]


def stroyka_name(lines: List[str]) -> str:
    for marker in ("наименование стройки", "наименование (объекта) стройки"):
        name = _named_above(lines, marker)
        if name:
            return name[:500]
    return ""


def tree(parts: List[dict], jobs: Dict[str, Job]) -> List[dict]:
    """Тартибланган рўйхатдан папкалар дарахти: папка тугуни ``{"kind": "folder", "name", "path", "children"}``,
    смета ``{"kind": "file", "part", "job"}``. Тугунлар биринчи учраган жойида туради."""
    root = {"children": [], "index": {}}
    for p in parts:
        job = jobs.get(p["job"])
        if job is None:
            continue
        node, path = root, ""
        for seg in [s for s in (job.batch.folder or "").split("/") if s.strip()]:
            path = f"{path}/{seg}" if path else seg
            child = node["index"].get(seg)
            if child is None:
                child = {"kind": "folder", "name": seg, "path": path, "children": [], "index": {}}
                node["index"][seg] = child
                node["children"].append(child)
            node = child
        node["children"].append({"kind": "file", "part": p, "job": job})
    return root["children"]


def flatten(nodes: List[dict]) -> List[dict]:
    out = []
    for n in nodes:
        out += [n["part"]] if n["kind"] == "file" else flatten(n["children"])
    return out


def sync_parts(project: Project, saved: Optional[List[dict]]) -> List[dict]:
    """Сақланган тартиб + янги нархланганлар (ўз папкасининг охирига); ўчирилган ёки нархи йўқолганлари олиб
    ташланади. Тартиб дарахт бўйича (папка бўлаклари ёнма-ён)."""
    jobs = {str(j.pk): j for j in priced_jobs(project)}
    out, seen = [], set()
    for p in saved or []:
        key = str(p.get("job", ""))
        if key in jobs and key not in seen:
            seen.add(key)
            out.append({"job": key, "title": " ".join(str(p.get("title") or "").split())[:300] or work_title(jobs[key]),
                        "include": bool(p.get("include", True))})
    for key, job in jobs.items():
        if key not in seen:
            out.append({"job": key, "title": work_title(job), "include": True})
    return flatten(tree(out, jobs))


def for_project(project: Project, save: bool = True) -> ProjectOfyorta:
    """Папканинг офёртаси (биринчи очилганда — стандарт тартиб ва сарлавҳалар билан)."""
    doc = ProjectOfyorta.objects.filter(project=project).first()
    if doc is None:
        doc = ProjectOfyorta(project=project, owner=project.owner, name=project.name[:300])
    parts = sync_parts(project, doc.parts)
    if parts != doc.parts:
        doc.parts = parts
        if save:
            doc.save()
    elif save and doc.pk is None:
        doc.save()
    return doc


# ===================== Excel =====================
def _int(v) -> Optional[int]:
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else None


def numbering_row(ws, start: int) -> Optional[int]:
    """Жадвал устунлари рақамланган қатор («1 2 3 4 …») — блок ундан кейинги қатордан бошланади."""
    for r in range(start - 1, max(0, start - 16), -1):
        if [_int(ws.cell(r, c).value) for c in (1, 2, 3)] == [1, 2, 3]:
            return r
    return None


def header_top(ws, num_row: int) -> int:
    """Жадвал сарлавҳасининг биринчи қатори («N п.п.», «№ п/п»)."""
    for r in range(num_row - 1, max(0, num_row - 6), -1):
        a = str(ws.cell(r, 1).value or "").lower()
        if "п.п" in a or "п/п" in a or a.startswith(("n ", "№")):
            return r
    return num_row


class _Writer:
    def __init__(self, ws):
        self.ws = ws
        self.r = 1
        self.width = MIN_COLS
        self._styles = {}

    # --- асл варақдан нусха ---
    def _style(self, src, dst):
        if not src.has_style:
            return
        key = id(src.parent.parent), tuple(src._style)
        known = self._styles.get(key)
        if known is not None:
            dst._style = copy(known)
            return
        dst.font, dst.fill, dst.border = copy(src.font), copy(src.fill), copy(src.border)
        dst.alignment, dst.protection, dst.number_format = copy(src.alignment), copy(src.protection), src.number_format
        self._styles[key] = copy(dst._style)

    def copy_rows(self, src, r1: int, r2: int, dest: int, cols: int, cached=None) -> int:
        """``src`` нинг r1..r2 қаторлари ``dest`` дан; формулалар силжишга мослашади. Қайтади: силжиш."""
        delta = dest - r1
        # бирлашмалар аввал: ўрнидаги MergedCell'лар кейин асл услубни олади (merge_cells + format()
        # катта сметада секин — чегаралар барибир ҳар катакдан нусха)
        for mr in list(src.merged_cells.ranges):
            if mr.min_row >= r1 and mr.max_row <= r2 and mr.min_col <= cols:
                self._merge(mr.min_row + delta, mr.min_col, mr.max_row + delta, min(mr.max_col, cols))
        for row in src.iter_rows(min_row=r1, max_row=r2, max_col=cols):
            for c in row:
                t = self.ws.cell(row=c.row + delta, column=c.column)
                self._style(c, t)
                if isinstance(c, MergedCell):
                    continue
                v = c.value
                if isinstance(v, ArrayFormula):
                    v = v.text
                if isinstance(v, str) and v.startswith("="):
                    v = self._formula(v, c, t, cached)
                if v is not None:
                    t.value = v
        for r in range(r1, r2 + 1):
            d = src.row_dimensions.get(r) if hasattr(src.row_dimensions, "get") else None
            if d is not None and (d.height or d.hidden):
                nd = self.ws.row_dimensions[r + delta]
                nd.height, nd.hidden = d.height, d.hidden
        return delta

    def _merge(self, r1: int, c1: int, r2: int, c2: int):
        if r1 == r2 and c1 == c2:
            return
        ws = self.ws
        ws.merged_cells.ranges.add(MergedCellRange(ws, f"{get_column_letter(c1)}{r1}:{get_column_letter(c2)}{r2}"))
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                if (r, c) != (r1, c1):
                    ws._cells[r, c] = MergedCell(ws, r, c)

    def _formula(self, v: str, c, t, cached):
        """Ўз варағидаги формула — силжитилади; бошқа варақ ёки файлга боғланган — асл файлдаги қиймати
        (у варақлар офёртада йўқ)."""
        if "!" in v or "[" in v:
            return cached(c.coordinate) if cached else None
        try:
            return Translator(v, origin=c.coordinate).translate_formula(t.coordinate)
        except Exception:  # noqa: BLE001
            return cached(c.coordinate) if cached else None

    # --- ўзимиз ёзадиган қаторлар ---
    def band(self, text: Optional[str], fill=YELLOW, size=12, height=24, cols=None):
        cols = cols or self.width
        self._merge(self.r, 1, self.r, cols)  # аввал: ўрнидаги катаклар кейин ранг олади
        for col in range(1, cols + 1):
            cell = self.ws.cell(self.r, col)
            cell.fill, cell.border = fill, BOX
        if text:
            a = self.ws.cell(self.r, 1, text)
            a.font = Font(name="Times New Roman", size=size, bold=True)
            a.alignment = Alignment(vertical="center", wrap_text=True)
        self.ws.row_dimensions[self.r].height = height
        self.r += 1

    def title(self, text: str, cols=None, size=12, height=24, fill=CYAN):
        """Блок сарлавҳаси (бу қайси иш) — сариқ қатор остида: оч ҳаворанг, қалин, тагига чизилган."""
        cols = cols or self.width
        self._merge(self.r, 1, self.r, cols)
        for col in range(1, cols + 1):
            cell = self.ws.cell(self.r, col)
            cell.border = BOX
            if fill:
                cell.fill = fill
        a = self.ws.cell(self.r, 1, text)
        a.font = Font(name="Times New Roman", size=size, bold=True, underline="single")
        a.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        self.ws.row_dimensions[self.r].height = height
        self.r += 1

    def text(self, text: str, size=11, bold=False, italic=False, center=True, height=None, cols=8):
        a = self.ws.cell(self.r, 1, text)
        a.font = Font(name="Times New Roman", size=size, bold=bold, italic=italic)
        a.alignment = Alignment(horizontal="center" if center else "left", vertical="center", wrap_text=True)
        self.ws.merge_cells(start_row=self.r, end_row=self.r, start_column=1, end_column=cols)
        if height:
            self.ws.row_dimensions[self.r].height = height
        self.r += 1


def _cached(path: Path, job: Job):
    """Асл файлдаги ҳисобланган қийматлар — керак бўлгандагина очилади."""
    state = {}

    def value(coord):
        if "ws" not in state:
            wb = load_workbook(path, data_only=True)
            state["wb"], state["ws"] = wb, _sheet(wb, job)
        return state["ws"][coord].value

    def close():
        if "wb" in state:
            state["wb"].close()

    return value, close


_RANGE = re.compile(r"(\$?[A-Z]{1,3}\$?)(\d+):(\$?[A-Z]{1,3}\$?)(\d+)")


def _clamp_sums(ws, row: int, top: int, cols: int):
    """Сумма қаторидаги диапазон блок бошидан юқорига чиқмасин (тепадаги бўш қаторлар олинмаганда
    ``SUM(H15:…)`` олдинги блок сводкасини қўшиб юбормасин)."""
    for c in range(1, cols + 1):
        cell = ws.cell(row, c)
        v = cell.value
        if isinstance(v, str) and v.startswith("="):
            cell.value = _RANGE.sub(lambda m: f"{m[1]}{max(int(m[2]), top)}:{m[3]}{m[4]}"
                                    if int(m[2]) < top <= int(m[4]) else m[0], v)


def _block(w: _Writer, n: int, title: str, src, job: Job, cached) -> dict:
    res = job.result or {}
    start, sum_row, svod = res.get("start_row"), res.get("sum_row"), res.get("svodka_start")
    if not (start and sum_row and svod):
        raise OfyortaError(f"«{title}»: нархлаш натижаси эски — сметани қайта нархланг")
    smart = (res.get("mode") or job.mode) == "smart"
    extra = [tuple(x) for x in (res.get("extra_columns") or [])] if smart else []
    layout = svodka_layout(extra or None)
    norm = lambda t: _SPACES.sub(" ", str(t or "")).strip().upper()  # noqa: E731
    if norm(src.cell(svod, 3).value) not in {norm(layout[0][1])} | {norm(t) for t in OLD_SVODKA_TEXTS[layout[0][0]]}:
        raise OfyortaError(f"«{title}»: сводка жойи ўзгарган — сметани қайта нархланг")
    num = numbering_row(src, start)
    first = num + 1 if num else start
    cols = max(MIN_COLS, min(src.max_column, MAX_COLS))
    if n > 1:
        w.band(None, cols=BAND_COLS, height=12)  # бўш сариқ қатор — блоклар ажратувчиси (биринчисида йўқ)
        # сарлавҳа — сметанинг ўзиники («РАЗДЕЛ 1. …»): сариқ остида бўш жойсиз (смета­чи офёртасидагидек)
        while first < sum_row and all(src.cell(first, c).value in (None, "") for c in range(1, cols + 1)):
            first += 1
    top = w.r
    delta = w.copy_rows(src, first, sum_row, w.r, cols, cached)
    new_sum = sum_row + delta
    _clamp_sums(w.ws, new_sum, top, cols)
    w.r = new_sum + 1
    s = w.r
    formulas = svodka_formulas(s, new_sum, nds_from_cell=smart, extra=extra or None)
    cells = {}
    for k, (key, _text, _pct) in enumerate(layout):
        w.copy_rows(src, svod + k, svod + k, s + k, 8)
        if key in OLD_SVODKA_TEXTS:  # эски файлда «Зарплата» — офёртада янги номи
            w.ws.cell(s + k, 3).value = _text
        w.ws.cell(s + k, 8).value = formulas[k]
        cells[key] = f"H{s + k}"
    w.r = s + len(layout)
    return {"title": title, "cells": cells, "fmt": w.ws.cell(s, 8).number_format or NUM_FMT, "cols": cols}


def _grand(w: _Writer, blocks: List[dict]):
    """Охирида: бўш сариқ қатор, «Умумий сводка» сарлавҳаси ва ҳамма сметалар сводкаларининг йиғиндиси (формула)."""
    fmt = blocks[0]["fmt"]
    w.band(None, cols=BAND_COLS, height=12)
    w.title(f"УМУМИЙ СВОДКА ({len(blocks)} та смета)", cols=BAND_COLS, size=13, height=26)

    def row(text, h, bold=True):  # смета сводкасидек — ҳамма қатор қалин
        w._merge(w.r, 3, w.r, 7)
        w.ws.cell(w.r, 3, text)
        w.ws.cell(w.r, 8, h)
        for col in range(1, 9):
            cell = w.ws.cell(w.r, col)
            cell.border, cell.fill = BOX, GREY
            cell.font = Font(name="Times New Roman", size=11, bold=bold)
        w.ws.cell(w.r, 8).number_format = fmt
        w.ws.cell(w.r, 3).alignment = Alignment(wrap_text=True, vertical="center")
        w.r += 1

    extra = [c for b in blocks for k, c in b["cells"].items() if k.startswith("extra_")]
    for key, text, _pct in SVODKA_ROWS:
        if key == "pryamye" and extra:
            row("Қўшимча устунлар", "=" + "+".join(extra))
        if key == "s_nds":
            text = "Всего с НДС"
        cells = [b["cells"][key] for b in blocks if key in b["cells"]]
        row(text, "=" + "+".join(cells))


def write(doc: ProjectOfyorta, parts: List[dict], jobs: Dict[str, Job], step=None) -> dict:
    wb = Workbook()
    ws = wb.active
    ws.title = "Офёрта"
    w = _Writer(ws)
    blocks, notes = [], []
    for i, p in enumerate(parts, 1):
        job = jobs.get(p["job"])
        if job is None:
            notes.append(f"«{p['title']}» — смета ўчирилган ёки нархи йўқ, олинмади")
            continue
        if step:
            step(f"Смета қўшилмоқда: {p['title']}", i - 1, len(parts))
        path = job.abs(job.output_path)
        if not path.is_file():
            raise OfyortaError(f"«{p['title']}»: нархланган файл топилмади — сметани қайта нархланг")
        src_wb = load_workbook(path)
        cached, close = _cached(path, job)
        try:
            src = _sheet(src_wb, job)
            if not blocks:
                _head(w, doc, src, job, cached)
            blocks.append(_block(w, len(blocks) + 1, p["title"], src, job, cached))
        finally:
            close()
            src_wb.close()
    if not blocks:
        raise OfyortaError("Битта ҳам смета олинмади")
    _grand(w, blocks)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    out = doc.project.abs(FILE)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.xlsx")
    wb.save(tmp)
    write_cached_values(tmp, {ws.title: _values(ws)})
    tmp.replace(out)
    return {"blocks": len(blocks), "notes": notes}


def _values(ws) -> dict:
    """Ҳамма формулалар натижаси (нархлаш файли билан бир хил баҳолагич)."""
    written = {c.coordinate: c.value for row in ws.iter_rows() for c in row if c.value is not None}
    ev = make_evaluator(written, lambda a: None)
    out = {}
    for addr, v in written.items():
        if isinstance(v, str) and v.startswith("="):
            try:
                out[addr] = ev.value(addr)
            except Exception:  # noqa: BLE001 — қўлда ёзилган тушунилмаган формула: Excel ўзи ҳисоблайди
                pass
    return out


def _head(w: _Writer, doc: ProjectOfyorta, src, job: Job, cached=None):
    """Бош қисм — биринчи сметанинг ўз тепаси ўзгаришсиз («Форма №5», қурилиш номи, «ЛОКАЛЬНАЯ РЕСУРСНАЯ
    ВЕДОМОСТЬ», «на …», «Основание», жадвал сарлавҳаси ва 1 2 3 … қатори) — смета­чи офёртасидагидек."""
    cols = max(MIN_COLS, min(src.max_column, MAX_COLS))
    start = (job.result or {}).get("start_row") or 1
    num = numbering_row(src, start) or start - 1
    if num >= 1:
        w.copy_rows(src, 1, num, 1, cols, cached)
        w.r = num + 1
        top = header_top(src, num)
        w.ws.freeze_panes = f"A{w.r}"
        w.ws.print_title_rows = f"{top}:{num}"
    else:
        w.text(doc.name, size=12, bold=True, height=20)
    for col in range(1, cols + 1):
        letter = get_column_letter(col)
        d = src.column_dimensions.get(letter) if hasattr(src.column_dimensions, "get") else None
        if d is not None and d.width:
            w.ws.column_dimensions[letter].width = d.width
    w.width = cols


def make(doc: ProjectOfyorta, progress=None) -> ProjectOfyorta:
    """Танланган сметаларни белгиланган тартибда ўқиб, офёрта файлини ёзади."""
    def step(text, done=0, total=0):
        doc.progress = {"step": text, "done": done, "total": total}
        ProjectOfyorta.objects.filter(pk=doc.pk).update(progress=doc.progress)
        if progress:
            progress()

    try:
        parts = [p for p in doc.parts if p.get("include", True)]
        if not parts:
            raise OfyortaError("Битта ҳам смета танланмаган")
        jobs = {str(j.pk): j for j in Job.objects.filter(
            pk__in=[p["job"] for p in parts], batch__project=doc.project, status=Job.Status.DONE).exclude(output_path="")
            .select_related("batch")}
        out = write(doc, parts, jobs, step)
        doc.file = FILE
        doc.total = sum(jobs[p["job"]].sum_total or 0 for p in parts if p["job"] in jobs)
        doc.status, doc.error, doc.built = ProjectOfyorta.Status.READY, "\n".join(out["notes"]), timezone.now()
        doc.progress = {"step": "Тайёр", "done": len(parts), "total": len(parts)}
        doc.save()
    except Exception as e:  # noqa: BLE001 — сабаб саҳифада кўринади
        doc.status = ProjectOfyorta.Status.FAILED
        doc.error = str(e) if isinstance(e, OfyortaError) else f"{type(e).__name__}: {e}"
        doc.save(update_fields=["status", "error", "updated"])
    return doc
