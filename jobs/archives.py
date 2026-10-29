"""ZIP / RAR / 7z arxivlarni ochish: ichma-ich (ZIP ichida RAR, uning ichida yana RAR), faqat Excel fayllar.

Smetalar odatda arxivda keladi: ichida PDF, chizmalar, rasm va Excel'lar. Bu yerda faqat Excel'lar
(va keyingi arxivlar) chiqariladi, qolgani tashlanadi. Fayl nomlari:

* ZIP — eski arxivlarda UTF-8 belgisi yo'q, nomlar DOS kodlashida (cp866) — shunday o'qiladi;
* RAR — ``unrar``/``UnRAR.exe`` (WinRAR) yoki ``7z`` orqali; ro'yxat UTF-8 da (``-scfr``: aks holda
  DOS kodlashida «қ, ҳ, ў» «?» bo'lib qoladi), har bir fayl ``p`` (stdout) bilan o'qiladi — Windows'dagi
  260 belgilik yo'l cheklovi va diskka chuqur papkalar yozilmaydi.

Arxiv ichidagi yo'llar tozalanadi («..», disk, bosh «/») — arxiv tashqarisiga yozib bo'lmaydi.
"""
import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator, List, Optional, Tuple

log = logging.getLogger(__name__)

ARCHIVE_EXT = (".zip", ".rar", ".7z")
EXCEL_EXT = (".xls", ".xlsx", ".xlsm")
MAX_DEPTH = 4            # ichma-ich arxivlar chuqurligi
MAX_MEMBERS = 5000       # bitta arxivda ko'rib chiqiladigan fayllar
TOOL_ENV = "NARXLASH_UNRAR"
_WINDOWS_TOOLS = (
    ("unrar", r"C:\Program Files\WinRAR\UnRAR.exe"),
    ("unrar", r"C:\Program Files (x86)\WinRAR\UnRAR.exe"),
    ("7z", r"C:\Program Files\7-Zip\7z.exe"),
    ("7z", r"C:\Program Files (x86)\7-Zip\7z.exe"),
)


class ArchiveError(Exception):
    pass


def is_archive(name: str) -> bool:
    return PurePosixPath(str(name)).suffix.lower() in ARCHIVE_EXT


_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')


def safe_part(part: str) -> str:
    """Papka/fayl nomi Windows'da ham yaratiladigan bo'lsin: taqiqlangan belgilar «_», oxiridagi nuqta/bo'shliq yo'q."""
    return _BAD_CHARS.sub("_", part).strip().rstrip(". ")


def safe_rel(name: str) -> Optional[PurePosixPath]:
    """Arxiv ichidagi nom -> xavfsiz nisbiy yo'l (yoki None): «..», disk va taqiqlangan belgilarsiz."""
    parts = [p.strip() for p in str(name).replace("\\", "/").split("/")]
    parts = [safe_part(p) for p in parts if p and p not in (".", "..") and not p.endswith(":")]
    parts = [p for p in parts if p]
    return PurePosixPath(*parts) if parts else None


def find_tool() -> Optional[Tuple[str, str]]:
    """RAR/7z ochadigan dastur: ``("unrar"|"7z", yo'l)``."""
    env = os.environ.get(TOOL_ENV)
    if env:
        return ("7z" if "7z" in Path(env).name.lower() else "unrar", env)
    for kind, name in (("unrar", "unrar"), ("unrar", "UnRAR"), ("7z", "7z"), ("7z", "7za")):
        found = shutil.which(name)
        if found:
            return kind, found
    if sys.platform == "win32":
        for kind, p in _WINDOWS_TOOLS:
            if Path(p).is_file():
                return kind, p
    return None


def rar_support() -> Optional[str]:
    """RAR ocholmasa — sababi (``check --deploy`` uchun), ochsa — None. Ubuntu/Debian'dagi ``7zip`` paketi
    RAR modulisiz yig'ilgan: 7z bor, lekin RAR ochilmaydi — ``unrar`` (yoki ``7zip-rar``) kerak."""
    tool = find_tool()
    if tool is None:
        return "RAR/7z дастури топилмади"
    kind, exe = tool
    if kind == "7z" and b"rar" not in _run([exe, "i"], timeout=30).lower():
        return f"{exe}: RAR модулисиз (Ubuntu/Debian'даги 7zip пакети)"
    return None


def _zip_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp866")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


Member = Tuple[str, Callable[[], bytes]]


def _zip_members(path: Path) -> Iterator[Member]:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise ArchiveError(f"ZIP очилмади: {e}") from e
    with zf:
        for info in zf.infolist()[:MAX_MEMBERS]:
            if not info.is_dir():
                yield _zip_name(info), (lambda i=info: zf.read(i))


def _run(cmd: List[str], timeout: int = 300, check: bool = False) -> bytes:
    """``check`` — ro'yxat olishda: dastur xato qaytarsa (masalan, 7z'da RAR moduli yo'q) arxiv «bo'sh» emas, xato."""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ArchiveError(f"архив дастури ишламади: {e}") from e
    if check and r.returncode > 1:  # 1 — ikkala dasturda ham ogohlantirish (arxiv baribir o'qiladi)
        msg = (r.stderr or r.stdout).decode("utf-8", "replace").strip().splitlines()
        raise ArchiveError(f"архив очилмади ({Path(cmd[0]).name}: {msg[-1] if msg else r.returncode})")
    return r.stdout


def _rar_members(path: Path) -> Iterator[Member]:
    """RAR (yoki 7z) — ``unrar lb`` / ``7z l -slt`` ro'yxati, har bir fayl stdout orqali."""
    tool = find_tool()
    if tool is None:
        raise ArchiveError("RAR/7z очиш учун серверда WinRAR (UnRAR) ёки 7-Zip ўрнатилиши керак")
    kind, exe = tool
    if kind == "unrar":
        names = _run([exe, "lb", "-scfr", "-p-", str(path)], check=True).decode("utf-8", "replace").splitlines()
        read = lambda n: _run([exe, "p", "-inul", "-p-", str(path), n])  # noqa: E731
    else:
        out = _run([exe, "l", "-slt", "-ba", "-sccUTF-8", "-p", str(path)], check=True).decode("utf-8", "replace")
        names = [line[7:] for line in out.splitlines() if line.startswith("Path = ")]
        read = lambda n: _run([exe, "e", "-so", "-p", "-sccUTF-8", str(path), n])  # noqa: E731
    names = [n.strip() for n in names if n.strip()]
    if not names:  # unrar buzuq/RAR bo'lmagan faylda ham 0 qaytaradi — bo'sh ro'yxat «ochilmadi» deb olinadi
        raise ArchiveError(f"архив бўш ёки очилмади ({Path(exe).name})")
    for n in names[:MAX_MEMBERS]:
        yield n, (lambda n=n: read(n))


def members(path: Path) -> Iterator[Member]:
    ext = path.suffix.lower()
    if ext == ".zip":
        return _zip_members(path)
    if ext in (".rar", ".7z"):
        return _rar_members(path)
    raise ArchiveError(f"Архив тури қўллаб-қувватланмайди: {path.name}")


def _prefix(archive_name: str, rels: List[PurePosixPath]) -> PurePosixPath:
    """Arxiv mazmuni qaysi papkaga: ichida yagona «<arxiv nomi>/» papka bo'lsa — shuning o'zi
    («Жамолоғага.zip» -> «Жамолоғага/…», «Жамолоғага/Жамолоғага/…» emas), aks holda arxiv nomli papka."""
    stem = PurePosixPath(archive_name).stem
    tops = {r.parts[0] for r in rels}
    if len(tops) == 1 and all(len(r.parts) > 1 for r in rels) and next(iter(tops)).strip().lower() == stem.strip().lower():
        return PurePosixPath()
    return PurePosixPath(stem)


def extract_excel(archive: Path, staging: Path, base: PurePosixPath = PurePosixPath(), depth: int = 0,
                  out: Optional[List[Tuple[PurePosixPath, Path]]] = None,
                  name: str = "") -> List[Tuple[PurePosixPath, Path]]:
    """Arxivdagi Excel'lar ``staging`` ga yoziladi: ``[(yuklangan papka ichidagi yo'l, fayl), …]``.
    ``base`` — arxivning o'zi yuklangan papkadagi joyi (masalan «Блок-А»), ``name`` — arxivning asl nomi."""
    out = [] if out is None else out
    if depth > MAX_DEPTH:
        return out
    wanted = []  # arxiv ochiqligida darhol diskka (ZIP o'qilgandan keyin yopiladi)
    for member, read in members(archive):
        rel = safe_rel(member)
        if rel is None or rel.suffix.lower() not in EXCEL_EXT + ARCHIVE_EXT:
            continue
        data = read()
        if not data:
            continue  # papka yoki o'qilmadi
        target = staging / f"{uuid.uuid4().hex}{rel.suffix.lower()}"  # qisqa nom: chuqur yo'l diskka yozilmaydi
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        wanted.append((rel, target))
    prefix = base / _prefix(name or archive.name, [r for r, _ in wanted]) if wanted else base
    for rel, target in wanted:
        if rel.suffix.lower() in ARCHIVE_EXT:
            try:
                extract_excel(target, staging, prefix / rel.parent, depth + 1, out, name=rel.name)
            except ArchiveError as e:
                # ichki arxiv ochilmasa — qolganlari olinadi; sabab panelda (Хатолар) ko'rinadi
                log.warning("ички архив очилмади: %s — %s", prefix / rel, e)
            target.unlink(missing_ok=True)
        else:
            out.append((prefix / rel, target))
    return out
