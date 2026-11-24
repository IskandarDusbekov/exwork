"""Тайёр файлларда mhisobot.uz белгиси — ҳужжатнинг ўзига (смета, Ф-2, Ф-3 жадвалларига) тегмасдан.

* ``stamp_file`` / ``stamp_bytes`` — Excel файл хусусиятлари (Файл → Сведения): «Организация» = mhisobot.uz,
  «Кем изменено» = mhisobot.uz, «Теги»га mhisobot.uz қўшилади, изоҳ бўш бўлса — «… тайёрланди».
  Жадвалда кўринмайди. Муаллиф (смета­чининг ўз файлидаги исми) ўзгармайди. Доим ёзилади.
* ``file_name`` — юклаб олинадиган файл номига « (mhisobot.uz)» (созламадаги калит билан).
* ``LOG_NOTE`` — LOG листининг ўнг томонидаги ёзув (``log_sheet.write_log(brand=…)``).

Файл ичидаги қолган ҳамма нарса байтма-байт ўзгармайди: фақат ``docProps/core.xml`` ва ``docProps/app.xml``
қайта ёзилади, бошқа қисмлар сиқилган ҳолича кўчирилади."""
import os
import shutil
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

BRAND = "mhisobot.uz"
URL = "https://mhisobot.uz"
LOG_NOTE = "mhisobot.uz тизимида тайёрланди"  # i18n: ok — Excel'да доим кирилл

CORE = "docProps/core.xml"
APP = "docProps/app.xml"
NS = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcmitype": "http://purl.org/dc/dcmitype/",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
APP_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
EXCEL_EXT = (".xlsx", ".xlsm")


def file_name(name: str) -> str:
    """«Смета_narxlandi.xlsx» → «Смета_narxlandi (mhisobot.uz).xlsx»; иккинчи марта қўшилмайди."""
    p = Path(name)
    if BRAND in p.stem:
        return name
    return f"{p.stem} ({BRAND}){p.suffix}"


def _register():
    for k, v in NS.items():
        ET.register_namespace(k, v)
    ET.register_namespace("vt", VT_NS)


def _xml(root) -> bytes:
    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + ET.tostring(root, encoding="utf-8")


def _core(data: bytes) -> bytes:
    _register()
    root = ET.fromstring(data)

    def el(tag):
        pre, name = tag.split(":")
        e = root.find(tag, NS)
        if e is None:
            e = ET.SubElement(root, f"{{{NS[pre]}}}{name}")
        return e

    el("cp:lastModifiedBy").text = BRAND
    kw = el("cp:keywords")
    if BRAND not in (kw.text or ""):
        kw.text = f"{kw.text.strip()}; {BRAND}" if (kw.text or "").strip() else BRAND
    desc = el("dc:description")
    if not (desc.text or "").strip():
        desc.text = LOG_NOTE
    return _xml(root)


def _app(data: bytes) -> bytes:
    ET.register_namespace("", APP_NS)
    ET.register_namespace("vt", VT_NS)
    root = ET.fromstring(data)
    company = root.find(f"{{{APP_NS}}}Company")
    if company is None:
        company = ET.SubElement(root, f"{{{APP_NS}}}Company")
    company.text = BRAND
    return _xml(root)


def stamped(z: zipfile.ZipFile) -> bool:
    try:
        return f"Company>{BRAND}<".encode() in z.read(APP)
    except KeyError:
        return False


def _rewrite(src: zipfile.ZipFile, dst) -> None:
    repl = {}
    names = set(src.namelist())
    if CORE in names:
        repl[CORE] = _core(src.read(CORE))
    if APP in names:
        repl[APP] = _app(src.read(APP))
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out:
        for item in src.infolist():
            out.writestr(item, repl.get(item.filename) or src.read(item.filename))


def stamp_bytes(data: bytes) -> bytes:
    """Хотирадаги xlsx (xlsxwriter / openpyxl): белги қўйилган нусха. Ўқиб бўлмаса — ўзи қайтади."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            if stamped(z):
                return data
            buf = BytesIO()
            _rewrite(z, buf)
            return buf.getvalue()
    except (zipfile.BadZipFile, ET.ParseError, KeyError):
        return data


def stamp_file(path) -> None:
    """Дискдаги .xlsx / .xlsm га белги (вақтинча файл орқали, ярим ёзилган файл қолмайди). Бошқа тур — тегилмайди."""
    path = Path(path)
    if path.suffix.lower() not in EXCEL_EXT or not path.is_file():
        return
    try:
        with zipfile.ZipFile(path) as z:
            if stamped(z):
                return
            fd, tmp = tempfile.mkstemp(suffix=path.suffix, dir=path.parent)
            try:
                with open(fd, "wb") as fh:
                    _rewrite(z, fh)
            except BaseException:
                os.unlink(tmp)
                raise
    except (zipfile.BadZipFile, ET.ParseError):
        return
    shutil.move(tmp, path)
