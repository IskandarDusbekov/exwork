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


def _convert_excel(path: Path, out_dir: Path) -> Path:
    if not excel_available():
        raise ConversionError("Excel orqali konvertatsiya faqat Windows'da, pywin32 o'rnatilgan bo'lsa ishlaydi")
    import pythoncom
    import win32com.client

    out = (out_dir / (path.stem + ".xlsx")).resolve()
    if out.exists():
        out.unlink()
    pythoncom.CoInitialize()
    excel = None
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        _com_retry(lambda: setattr(excel, "Visible", False))
        _com_retry(lambda: setattr(excel, "DisplayAlerts", False))
        wb = _com_retry(lambda: excel.Workbooks.Open(str(path.resolve()), ReadOnly=True))
        _com_retry(lambda: wb.SaveAs(str(out), FileFormat=51))  # xlOpenXMLWorkbook
        _com_retry(lambda: wb.Close(SaveChanges=False))
    except Exception as e:
        raise ConversionError(f".xls -> .xlsx (Excel) konvertatsiyada xato: {e}") from e
    finally:
        if excel is not None:
            try:
                _com_retry(lambda: excel.Quit())
            except Exception:
                pass
        pythoncom.CoUninitialize()
    if not out.is_file():
        raise ConversionError(".xls -> .xlsx (Excel): natija fayli yaratilmadi")
    return out
