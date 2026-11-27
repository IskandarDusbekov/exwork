""".xls -> .xlsx konvertatsiyasi.

Asosiy yo'l — LibreOffice (``soffice --headless``). Uning yo'li quyidagi tartibda aniqlanadi:
1. ``soffice`` argumenti;
2. ``NARXLASH_SOFFICE`` muhit o'zgaruvchisi;
3. ``PATH`` dagi ``soffice`` / ``libreoffice``;
4. Windows'dagi standart o'rnatish joylari.

Zaxira yo'l (faqat Windows, dev uchun) — Microsoft Excel COM orqali (pywin32 kerak). Eski
dastur ham .xls ni shunday ochardi. ``NARXLASH_CONVERTER`` bilan tanlanadi:
``auto`` (standart: LibreOffice, bo'lmasa Excel), ``libreoffice`` yoki ``excel``.
"""
import contextlib
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Optional
from django.utils.translation import gettext_noop as _noop

SUPPORTED = (".xls", ".xlsx", ".xlsm")
ENV_VAR = "NARXLASH_SOFFICE"
CONVERTER_ENV = "NARXLASH_CONVERTER"
MSO_AUTOMATION_FORCE_DISABLE = 3  # Excel: makroslar hech qachon ishlamaydi
_WINDOWS_PATHS = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)


class ConversionError(RuntimeError):
    pass


# Сервер бўйича LibreOffice чеклови (``jobs.lo_queue.slot``): ``set_limiter`` билан Django илова ўрнатади.
# core Django'сиз ҳам ишласин — ўрнатилмаса чеклов йўқ.
_LIMITER = None


def set_limiter(fn):
    global _LIMITER
    _LIMITER = fn


# ===================== ўгирилган файллар кеши =====================
# Бир хил .xls кўп марта келади (қайта нархлаш, папкани қайта юклаш, нарх базасига йиғиш): мазмуни (SHA-256)
# бўйича тайёр .xlsx сақланади — такрор келганда LibreOffice умуман ишга тушмайди. Django илова ``set_cache``
# билан йўлини ўрнатади; ўрнатилмаса — кешсиз (core Django'сиз ҳам ишласин).
CACHE_VERSION = "1"  # ўгириш усули ўзгарса оширилади — эски нусхалар ишлатилмайди
_CACHE_DIR: Optional[Path] = None
_CACHE_MAX = 2 * 1024 ** 3
_PRUNE_EVERY = 50
_cache_writes = 0


def set_cache(path, max_bytes: Optional[int] = None):
    """Кеш папкаси (``None`` — кешсиз) ва энг катта ҳажми: ошса, энг узоқ ишлатилмаганлари ўчирилади."""
    global _CACHE_DIR, _CACHE_MAX
    _CACHE_DIR = Path(path) if path else None
    if max_bytes:
        _CACHE_MAX = int(max_bytes)


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _cache_entry(path: Path, tag: str) -> Optional[Path]:
    if _CACHE_DIR is None:
        return None
    try:
        key = _file_hash(path)
    except OSError:
        return None
    return _CACHE_DIR / key[:2] / f"{key}-{tag}-v{CACHE_VERSION}.xlsx"


def _cache_prune():
    """Кеш ҳажми чегарадан ошса — энг узоқ ишлатилмаганлари (mtime) ўчирилади."""
    if _CACHE_DIR is None or not _CACHE_DIR.is_dir():
        return
    files = []
    for p in _CACHE_DIR.rglob("*.xlsx"):
        try:
            st = p.stat()
        except OSError:
            continue
        files.append((st.st_mtime, st.st_size, p))
    total = sum(s for _, s, _ in files)
    for _, size, p in sorted(files):
        if total <= _CACHE_MAX:
            break
        with contextlib.suppress(OSError):
            p.unlink()
            total -= size


def _cached(path: Path, out_dir: Path, tag: str, convert_fn) -> Path:
    """``convert_fn()`` натижаси мазмун бўйича кешланади. Кешдан олинганда ҳам натижа ``out_dir`` да."""
    global _cache_writes
    entry = _cache_entry(path, tag)
    out = out_dir / (path.stem + ".xlsx")
    if entry is not None and entry.is_file():
        try:
            shutil.copyfile(entry, out)
            if not zipfile.is_zipfile(out):
                raise ConversionError("кешдаги нусха бузуқ")
            os.utime(entry)  # яқинда ишлатилди — тозалашда охирида ўчади
            check_xlsx(out)
            return out
        except (OSError, ConversionError):
            with contextlib.suppress(OSError):
                entry.unlink()  # бузуқ нусха — қайта ўгирилади
    result = convert_fn()
    if entry is not None:
        try:
            if result.is_file() and result.stat().st_size > 0:
                entry.parent.mkdir(parents=True, exist_ok=True)
                tmp = entry.with_name(f".{entry.name}.{os.getpid()}.tmp")
                shutil.copyfile(result, tmp)
                os.replace(tmp, entry)  # бир вақтда икки жараён ёзса ҳам — бутун файл
                _cache_writes += 1
                if _cache_writes % _PRUNE_EVERY == 1:
                    _cache_prune()
        except OSError:
            pass  # кешга ёзиб бўлмади — ўгириш барибир тайёр
    return result


def find_soffice(soffice: Optional[str] = None) -> str:
    candidates = [soffice, os.environ.get(ENV_VAR)]
    for c in candidates:
        if c:
            if Path(c).is_file() or shutil.which(c):
                return c
            raise ConversionError(f"LibreOffice topilmadi: {c}")
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for p in _WINDOWS_PATHS:
        if Path(p).is_file():
            return p
    raise ConversionError(
        ".xls faylni ochish uchun LibreOffice kerak. O'rnating yoki yo'lini "
        f"{ENV_VAR} muhit o'zgaruvchisida ko'rsating."
    )


def excel_available() -> bool:
    return sys.platform == "win32" and importlib.util.find_spec("win32com") is not None


def converter_name(soffice: Optional[str] = None, converter: Optional[str] = None) -> str:
    """.xls ni qaysi dastur o'giradi: «libreoffice», «excel» yoki «» (hech biri yo'q) — panel statistikasi uchun."""
    mode = (converter or os.environ.get(CONVERTER_ENV) or "auto").lower()
    if mode in ("excel", "libreoffice"):
        return mode
    try:
        find_soffice(soffice)
        return "libreoffice"
    except ConversionError:
        return "excel" if excel_available() else ""


# .xlsx — ZIP: 30 MB fayl ichida gigabaytlab XML bo'lishi mumkin («bomba») — ochishdan oldin e'lon qilingan
# hajm tekshiriladi (o'qishda zipfile undan ortig'ini bermaydi). Haqiqiy smetalar: ochilganda ≤ 25 MB.
MAX_XLSX_UNPACKED = 200 * 1024 * 1024
MAX_XLSX_PARTS = 10000


def check_xlsx(path: Path):
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
    except zipfile.BadZipFile:
        return  # ZIP emas — openpyxl o'zi tushunarli xato beradi
    if len(infos) > MAX_XLSX_PARTS or sum(i.file_size for i in infos) > MAX_XLSX_UNPACKED:
        raise ConversionError(_noop("{0}: файл очилганда жуда катта ({1} МБ дан ортиқ) — бу смета эмас ёки файл бузилган").format(path.name, MAX_XLSX_UNPACKED // 2**20))


def ensure_xlsx(path: Path, out_dir: Path, soffice: Optional[str] = None, timeout: int = 180,
                converter: Optional[str] = None) -> Path:
    """``.xlsx``/``.xlsm`` ni o'zini qaytaradi, ``.xls`` ni ``out_dir`` ga konvertatsiya qiladi."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        check_xlsx(path)
        return path
    if ext != ".xls":
        raise ConversionError(f"Bu format qo'llanmaydi: {ext}. Faqat {', '.join(SUPPORTED)}")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mode = (converter or os.environ.get(CONVERTER_ENV) or "auto").lower()
    if mode == "excel":
        return _cached(path, out_dir, "excel", lambda: _convert_excel(path, out_dir))
    if mode == "libreoffice":
        exe = find_soffice(soffice)
        return _cached(path, out_dir, "lo", lambda: _convert_libreoffice(exe, path, out_dir, timeout))
    try:
        exe = find_soffice(soffice)
    except ConversionError:
        if excel_available():
            return _cached(path, out_dir, "excel", lambda: _convert_excel(path, out_dir))
        raise
    return _cached(path, out_dir, "lo", lambda: _convert_libreoffice(exe, path, out_dir, timeout))


# ===================== доимий LibreOffice (deploy/office/narx_office.py) =====================
# Сервердa 1–2 та LibreOffice доим ёқиқ туради (``narxlash-office@<порт>``) — ҳар файл учун ишга тушиб-ўчмайди.
# Файл мазмуни 127.0.0.1 даги XML-RPC орқали юборилади, .xlsx мазмуни қайтади (йўл эмас: хизмат бошқа
# фойдаланувчи номидан ишлайди ва сайт папкаларини кўрмайди). Хизмат жавоб бермаса — одатдаги усул (ҳар файлга
# алоҳида soffice): сайт ишлашда давом этади. Ўрнатилмаса (``set_office`` чақирилмаса) — фақат одатдаги усул.
OFFICE_ENV = "NARXLASH_OFFICE"
OFFICE_FAULT_FILE = 1  # хизмат жавоби: файл очилмади (бузуқ / смета эмас) — deploy/office/narx_office.py билан бир хил
OFFICE_FAULT_SERVICE = 2  # хизматнинг ўзи ишламаяпти (LibreOffice ўлган, қайта ёқилмоқда)
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_OFFICE: list = []


def parse_office(spec) -> list:
    """«127.0.0.1:2003,127.0.0.1:2004» → [("127.0.0.1", 2003), …]. Фақат шу компьютер (127.0.0.1) — хизматда
    пароль йўқ, бошқа манзил хавфли: ``ValueError``."""
    out = []
    for part in str(spec or "").replace(" ", "").split(","):
        if not part:
            continue
        host, _, port = part.rpartition(":")
        host = host.strip("[]") or "127.0.0.1"
        if host not in _LOOPBACK:
            raise ValueError(f"{OFFICE_ENV}: фақат 127.0.0.1 рухсат этилган, «{host}» эмас")
        if not port.isdigit() or not 0 < int(port) < 65536:
            raise ValueError(f"{OFFICE_ENV}: порт нотўғри — «{part}»")
        out.append((host, int(port)))
    return out


def set_office(spec):
    """Доимий LibreOffice манзиллари (``NARXLASH_OFFICE``); бўш — ўчиқ."""
    global _OFFICE
    _OFFICE = parse_office(spec)


class _TimeoutTransport:
    """xmlrpc Transport — уланиш ва жавоб учун вақт чегараси (хизмат қотса, сайт кутиб қолмасин)."""

    def __new__(cls, timeout):
        import xmlrpc.client

        class T(xmlrpc.client.Transport):
            def make_connection(self, host):
                conn = super().make_connection(host)
                conn.timeout = timeout
                return conn
        return T()


class OfficeUnavailable(Exception):
    """Доимий LibreOffice жавоб бермади — одатдаги усулга қайтилади."""


def _office_convert(path: Path, out_dir: Path, timeout: int, slot_no) -> Path:
    """``path`` ни доимий LibreOffice'да ўгиради. Хизмат йўқ / жавоб бермади — ``OfficeUnavailable``;
    хизмат файлни очолмади (бузуқ файл) — ``ConversionError`` (одатдаги усул ҳам очолмайди, қайта уринилмайди)."""
    import socket
    import xmlrpc.client
    host, port = _OFFICE[(slot_no or 0) % len(_OFFICE)]
    proxy = xmlrpc.client.ServerProxy(f"http://{host}:{port}/RPC2", transport=_TimeoutTransport(timeout),
                                      allow_none=True)
    try:
        data = proxy.convert(xmlrpc.client.Binary(path.read_bytes()), path.suffix.lower())
    except xmlrpc.client.Fault as e:
        if e.faultCode == OFFICE_FAULT_FILE:  # LibreOffice ишлаяпти, лекин бу файлни очолмади
            raise ConversionError(f".xls -> .xlsx konvertatsiyada xato: {e.faultString}") from e
        raise OfficeUnavailable(e.faultString) from e  # хизматнинг ўзида муаммо — одатдаги усул
    except (OSError, socket.timeout, xmlrpc.client.ProtocolError, EOFError) as e:
        raise OfficeUnavailable(str(e) or e.__class__.__name__) from e
    out = out_dir / (path.stem + ".xlsx")
    out.write_bytes(data.data if hasattr(data, "data") else bytes(data))
    if not zipfile.is_zipfile(out):
        out.unlink(missing_ok=True)
        raise OfficeUnavailable("жавоб .xlsx эмас")
    check_xlsx(out)
    return out


def _convert_libreoffice(exe: str, path: Path, out_dir: Path, timeout: int) -> Path:
    # Бўш ўрин келгунча кутилади (сервер бўйича бир вақтда кўпи билан N та soffice).
    limit = _LIMITER(path.name) if _LIMITER else contextlib.nullcontext()
    with limit as slot_no:
        if _OFFICE:
            try:
                return _office_convert(path, out_dir, timeout, slot_no)
            except OfficeUnavailable as e:
                import logging
                logging.getLogger(__name__).warning("доимий LibreOffice жавоб бермади (%s) — одатдаги усул: %s",
                                                    e, path.name)
        return _convert_cold(exe, path, out_dir, timeout)


def _convert_cold(exe: str, path: Path, out_dir: Path, timeout: int) -> Path:
    # Har bir chaqiruvga alohida profil: parallel ishga tushganda LibreOffice qulflanib qolmasin.
    with tempfile.TemporaryDirectory(prefix="narxlash_lo_") as profile:
        cmd = [
            exe,
            f"-env:UserInstallation={Path(profile).as_uri()}",
            "--headless", "--norestore", "--nolockcheck",
            "--convert-to", "xlsx:Calc MS Excel 2007 XML",
            "--outdir", str(out_dir),
            str(path),
        ]
        try:
            # begona fayl ochiladi: saytning sirlari (baza paroli, SECRET_KEY) LibreOffice'ga o'tmaydi
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=tool_env())
        except subprocess.TimeoutExpired as e:
            raise ConversionError(f"LibreOffice {timeout} soniyada javob bermadi") from e
    out = out_dir / (path.stem + ".xlsx")
    if proc.returncode != 0 or not out.is_file():
        raise ConversionError(
            f".xls -> .xlsx konvertatsiyada xato (kod {proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}"
        )
    check_xlsx(out)
    return out


_TOOL_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "WINDIR", "USERPROFILE",
             "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "COMSPEC")


def tool_env() -> dict:
    """Begona faylni ochadigan tashqi dasturlar (LibreOffice, unrar, 7z) uchun muhit — faqat kerakli
    o'zgaruvchilar. Dasturda zaiflik bo'lsa ham, zararli fayl saytning sirlarini (muhitdan) o'qiy olmaydi."""
    return {k: v for k, v in os.environ.items() if k.upper() in _TOOL_ENV}


_RPC_E_CALL_REJECTED = -2147418111


def _com_retry(fn, seconds: float = 60):
    """Excel band bo'lsa COM chaqiruvni rad etadi — biroz kutib qayta urinamiz."""
    import pythoncom
    import pywintypes

    t0 = time.time()
    while True:
        try:
            return fn()
        except pywintypes.com_error as e:
            if e.hresult != _RPC_E_CALL_REJECTED or time.time() - t0 > seconds:
                raise
            pythoncom.PumpWaitingMessages()
            time.sleep(0.3)


def _excel_pid(excel) -> Optional[int]:
    """Aynan biz ochgan Excel jarayonining raqami (keyin yopilganini tekshirish uchun)."""
    try:
        import win32process
        return win32process.GetWindowThreadProcessId(excel.Hwnd)[1]
    except Exception:
        return None


def _ensure_exited(pid: Optional[int], wait: float = 5.0):
    """Quit'dan keyin ham Excel ishlab tursa — o'sha jarayonni to'xtatamiz (ko'rinmas qoldiq bo'lmasin)."""
    if not pid:
        return
    import win32api
    import win32con
    import win32event
    try:
        handle = win32api.OpenProcess(win32con.SYNCHRONIZE | win32con.PROCESS_TERMINATE, False, pid)
    except Exception:
        return  # allaqachon yopilgan
    try:
        if win32event.WaitForSingleObject(handle, int(wait * 1000)) == win32event.WAIT_TIMEOUT:
            win32api.TerminateProcess(handle, 1)
    finally:
        win32api.CloseHandle(handle)


def _convert_excel(path: Path, out_dir: Path) -> Path:
    if not excel_available():
        raise ConversionError("Excel orqali konvertatsiya faqat Windows'da, pywin32 o'rnatilgan bo'lsa ishlaydi")
    import pythoncom
    import win32com.client

    out = (out_dir / (path.stem + ".xlsx")).resolve()
    if out.exists():
        out.unlink()
    pythoncom.CoInitialize()
    excel = wb = None
    pid = None
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        pid = _excel_pid(excel)
        _com_retry(lambda: setattr(excel, "Visible", False))
        _com_retry(lambda: setattr(excel, "DisplayAlerts", False))
        # yuklangan fayl begona: ochishdan OLDIN makroslar (Workbook_Open va h.k.) va hodisalar o'chiriladi —
        # COM orqali ochilganda Excel sukut bo'yicha makrosga ruxsat beradi (msoAutomationSecurityLow)
        _com_retry(lambda: setattr(excel, "AutomationSecurity", MSO_AUTOMATION_FORCE_DISABLE))
        _com_retry(lambda: setattr(excel, "EnableEvents", False))
        _com_retry(lambda: setattr(excel, "AskToUpdateLinks", False))
        wb = _com_retry(lambda: excel.Workbooks.Open(str(path.resolve()), UpdateLinks=0, ReadOnly=True))
        _com_retry(lambda: wb.SaveAs(str(out), FileFormat=51))  # xlOpenXMLWorkbook
        _com_retry(lambda: wb.Close(SaveChanges=False))
    except Exception as e:
        raise ConversionError(f".xls -> .xlsx (Excel) konvertatsiyada xato: {e}") from e
    finally:
        if excel is not None:
            try:
                _com_retry(lambda: excel.Quit(), seconds=15)
            except Exception:
                pass
        wb = excel = None  # COM havolalari bo'shatilmasa, Excel jarayoni yopilmay qoladi
        pythoncom.CoUninitialize()
        _ensure_exited(pid)
    if not out.is_file():
        raise ConversionError(".xls -> .xlsx (Excel): natija fayli yaratilmadi")
    check_xlsx(out)
    return out
