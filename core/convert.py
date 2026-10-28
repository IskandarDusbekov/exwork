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
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

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


def ensure_xlsx(path: Path, out_dir: Path, soffice: Optional[str] = None, timeout: int = 180,
                converter: Optional[str] = None) -> Path:
    """``.xlsx``/``.xlsm`` ni o'zini qaytaradi, ``.xls`` ni ``out_dir`` ga konvertatsiya qiladi."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        return path
    if ext != ".xls":
        raise ConversionError(f"Bu format qo'llanmaydi: {ext}. Faqat {', '.join(SUPPORTED)}")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mode = (converter or os.environ.get(CONVERTER_ENV) or "auto").lower()
    if mode == "excel":
        return _convert_excel(path, out_dir)
    if mode == "libreoffice":
        return _convert_libreoffice(find_soffice(soffice), path, out_dir, timeout)
    try:
        exe = find_soffice(soffice)
    except ConversionError:
        if excel_available():
            return _convert_excel(path, out_dir)
        raise
    return _convert_libreoffice(exe, path, out_dir, timeout)


def _convert_libreoffice(exe: str, path: Path, out_dir: Path, timeout: int) -> Path:
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
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise ConversionError(f"LibreOffice {timeout} soniyada javob bermadi") from e
    out = out_dir / (path.stem + ".xlsx")
    if proc.returncode != 0 or not out.is_file():
        raise ConversionError(
            f".xls -> .xlsx konvertatsiyada xato (kod {proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return out


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
    return out
