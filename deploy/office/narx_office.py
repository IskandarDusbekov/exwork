#!/usr/bin/env python3
"""Доимий LibreOffice: .xls → .xlsx хизмати (``narxlash-office@<порт>``).

Ҳар .xls учун LibreOffice'ни ишга тушириб-ўчириш ўрнига у доим ёқиқ туради; сайт файл **мазмунини** шу
компьютердаги (127.0.0.1) XML-RPC портга юборади, .xlsx мазмуни қайтади. Сайт томони — ``core/convert.py``
(``NARXLASH_OFFICE``); хизмат жавоб бермаса, сайт одатдаги усулга (ҳар файлга алоҳида soffice) қайтади.

Хавфсизлик:
* фақат 127.0.0.1 да тинглайди (бошқа манзил қабул қилинмайди) — хизматда пароль йўқ;
* файл макрослари ҳеч қачон ишламайди (``MacroExecutionMode=NEVER_EXECUTE``, профилда ҳам ўчиқ), ташқи
  ҳаволалар янгиланмайди (``UpdateDocMode=NO_UPDATE``), файл фақат ўқиш учун очилади;
* профил ҳар ишга тушганда ноль: «охирги файллар», автосақлаш, тиклаш нусхалари ва захира нусха ўчиқ;
  ҳар файл ўз вақтинча папкасида, ўгирилгач ўчирилади;
* алоҳида ҳуқуқсиз фойдаланувчи (systemd: ``narxlash-office``) — сайт папкалари, .env, база кўринмайди;
* битта ўгириш ``--timeout`` дан ошса ёки LibreOffice ўлса — жараён чиқади, systemd тоза профил билан
  қайта ёқади; ``--max-conversions`` тадан кейин ва оғир файлдан кейин хотира ``--restart-mb`` дан ошса ҳам
  (LibreOffice олган хотирасини қайтармайди — бўш турганда юзлаб МБ банд бўлиб қолмасин).

Тизим Python'и билан ишлайди (``python3-uno`` пакети — venv'да эмас)::

    /usr/bin/python3 narx_office.py --port 2003 --work /var/lib/narxlash-office/2003
    /usr/bin/python3 narx_office.py --port 2003 --selftest файл.xls   # текшириш: ўгириб, натижани --work га ёзади
"""
import argparse
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import xmlrpc.client
from pathlib import Path
from xmlrpc.server import SimpleXMLRPCRequestHandler, SimpleXMLRPCServer

log = logging.getLogger("narx_office")

FAULT_FILE = 1  # файл очилмади (бузуқ / смета эмас) — core/convert.py: OFFICE_FAULT_FILE
FAULT_SERVICE = 2  # хизматнинг ўзи ишламаяпти — сайт одатдаги усулга қайтади
FILTER = "Calc MS Excel 2007 XML"  # одатдаги усулдаги («--convert-to xlsx:…») фильтрнинг айнан ўзи
SUFFIXES = {".xls"}
HOST = "127.0.0.1"
NEVER_EXECUTE = 0  # com.sun.star.document.MacroExecMode.NEVER_EXECUTE
NO_UPDATE = 0  # com.sun.star.document.UpdateDocMode.NO_UPDATE

# профил созламалари: макрос ўчиқ, «охирги файллар», автосақлаш, тиклаш ва захира нусха йўқ
_ITEMS = [
    ("/org.openoffice.Office.Common/Security/Scripting", "MacroSecurityLevel", "3"),
    ("/org.openoffice.Office.Common/Security/Scripting", "DisableMacrosExecution", "true"),
    ("/org.openoffice.Office.Common/Security/Scripting", "BlockUntrustedRefererLinks", "true"),
    ("/org.openoffice.Office.Common/History", "PickListSize", "0"),
    ("/org.openoffice.Office.Common/History", "RecentDocsThumbnail", "false"),
    ("/org.openoffice.Office.Common/Save/Document", "CreateBackup", "false"),
    ("/org.openoffice.Office.Common/Save/Document", "AutoSave", "false"),
    ("/org.openoffice.Office.Recovery/AutoSave", "Enabled", "false"),
    ("/org.openoffice.Office.Recovery/RecoveryInfo", "Enabled", "false"),
]


def profile_xcu() -> str:
    items = "\n".join(f'<item oor:path="{p}"><prop oor:name="{n}" oor:op="fuse"><value>{v}</value></prop></item>'
                      for p, n, v in _ITEMS)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema"'
            ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n' + items + "\n</oor:items>\n")


def fresh_profile(work: Path) -> Path:
    """Профил ва вақтинча файллар — ҳар ишга тушганда нолдан (олдинги файллардан из қолмайди)."""
    for name in ("profile", "tmp"):
        shutil.rmtree(work / name, ignore_errors=True)
    profile = work / "profile"
    (profile / "user").mkdir(parents=True)
    (profile / "user" / "registrymodifications.xcu").write_text(profile_xcu(), encoding="utf-8")
    (work / "tmp").mkdir()
    return profile


def cgroup_memory(proc_cgroup="/proc/self/cgroup", root="/sys/fs/cgroup"):
    """Хизматнинг (systemd cgroup — LibreOffice билан бирга) ҳозирги хотираси, байт. Билиб бўлмаса — None."""
    try:
        for line in Path(proc_cgroup).read_text().splitlines():
            if line.startswith("0::"):
                return int((Path(root) / line[3:].strip().lstrip("/") / "memory.current").read_text().strip())
    except (OSError, ValueError):
        pass
    return None


def request_limit(max_mb: int) -> int:
    """XML-RPC сўрови энг кўпи: файл base64 да ~4/3 баравар катта + XML қобиғи."""
    return int(max_mb * 1024 * 1024 * 4 / 3) + 64 * 1024


class Office:
    """Битта LibreOffice жараёни ва унга UNO орқали уланиш."""

    def __init__(self, soffice: str, work: Path, uno_port: int):
        self.soffice, self.work, self.uno_port = soffice, work, uno_port
        self.proc = None
        self.desktop = None

    def start(self, wait: float = 60):
        profile = fresh_profile(self.work)
        cmd = [self.soffice, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--invisible", "--nologo",
               "--norestore", "--nolockcheck", "--nodefault", "--nocrashreport",
               f"--accept=socket,host={HOST},port={self.uno_port},tcpNoDelay=1;urp;StarOffice.ComponentContext"]
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL")}
        env.update(HOME=str(self.work), TMPDIR=str(self.work / "tmp"))
        self.proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        import uno  # тизим Python'идаги python3-uno
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
        url = f"uno:socket,host={HOST},port={self.uno_port};urp;StarOffice.ComponentContext"
        t0 = time.monotonic()
        while True:
            if self.proc.poll() is not None:
                raise RuntimeError(f"LibreOffice ишга тушмади (код {self.proc.returncode})")
            try:
                ctx = resolver.resolve(url)
                break
            except Exception:  # noqa: BLE001 — NoConnectException: ҳали тайёр эмас
                if time.monotonic() - t0 > wait:
                    raise RuntimeError(f"LibreOffice {wait:.0f} с ичида уланмади")
                time.sleep(0.3)
        self.desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        log.info("LibreOffice тайёр (pid %s, UNO порт %s, %.1f с)", self.proc.pid, self.uno_port, time.monotonic() - t0)

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.proc is None:
            return
        if self.proc.poll() is None:
            try:
                self.desktop.terminate()
            except Exception:  # noqa: BLE001
                pass
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def convert(self, src: Path, dst: Path):
        import uno
        from com.sun.star.beans import PropertyValue

        def prop(name, value):
            p = PropertyValue()
            p.Name, p.Value = name, value
            return p
        load = (prop("Hidden", True), prop("ReadOnly", True), prop("MacroExecutionMode", NEVER_EXECUTE),
                prop("UpdateDocMode", NO_UPDATE))
        doc = self.desktop.loadComponentFromURL(uno.systemPathToFileUrl(str(src)), "_blank", 0, load)
        if doc is None:
            raise xmlrpc.client.Fault(FAULT_FILE, "файлни очиб бўлмади")
        try:
            doc.storeToURL(uno.systemPathToFileUrl(str(dst)), (prop("FilterName", FILTER), prop("Overwrite", True)))
        finally:
            try:
                doc.close(True)
            except Exception:  # noqa: BLE001 — ҳужжат ёпилмаса ҳам натижа ёзилган
                pass


class Service:
    def __init__(self, office: Office, timeout: float, max_conversions: int, max_mb: int, restart_mb: int = 0,
                 memory=cgroup_memory):
        self.office, self.timeout, self.max_conversions, self.max_mb = office, timeout, max_conversions, max_mb
        self.restart_bytes = restart_mb * 1024 * 1024
        self.memory = memory
        self.done = 0
        self.server = None
        self.stopping = False

    def _die(self, why: str, code: int = 3):
        """LibreOffice қотди / ўлди: жараён чиқади — systemd тоза профил билан қайта ёқади."""
        log.error("%s — хизмат қайта ишга туширилади", why)
        try:
            if self.office.proc is not None:
                self.office.proc.kill()
        finally:
            os._exit(code)

    def ping(self) -> dict:
        return {"ok": self.office.alive(), "conversions": self.done, "pid": self.office.proc.pid if self.office.proc else None}

    def convert(self, data, suffix):
        suffix = str(suffix or "").lower()
        if suffix not in SUFFIXES:
            raise xmlrpc.client.Fault(FAULT_FILE, f"«{suffix}» қабул қилинмайди (фақат .xls)")
        raw = data.data if isinstance(data, xmlrpc.client.Binary) else bytes(data or b"")
        if not raw or len(raw) > self.max_mb * 1024 * 1024:
            raise xmlrpc.client.Fault(FAULT_FILE, "файл бўш ёки жуда катта")
        if not self.office.alive():
            threading.Timer(0.2, self._die, args=("LibreOffice ўлган",)).start()
            raise xmlrpc.client.Fault(FAULT_SERVICE, "LibreOffice ишламаяпти")
        watchdog = threading.Timer(self.timeout, self._die, args=(f"ўгириш {self.timeout:.0f} с дан ошди",))
        watchdog.daemon = True
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(dir=self.office.work / "tmp") as d:
            src, dst = Path(d) / f"in{suffix}", Path(d) / "out.xlsx"
            src.write_bytes(raw)
            watchdog.start()
            try:
                self.office.convert(src, dst)
            except xmlrpc.client.Fault:
                raise
            except Exception as e:  # noqa: BLE001 — UNO хатолари турли-туман
                if not self.office.alive() or "disposed" in str(e).lower():
                    threading.Timer(0.2, self._die, args=(f"LibreOffice уланиши узилди: {e}",)).start()
                    raise xmlrpc.client.Fault(FAULT_SERVICE, "LibreOffice ишламаяпти")
                raise xmlrpc.client.Fault(FAULT_FILE, f"файлни ўгириб бўлмади: {e}"[:300])
            finally:
                watchdog.cancel()
            if not dst.is_file() or dst.stat().st_size == 0:
                raise xmlrpc.client.Fault(FAULT_FILE, "натижа файли ёзилмади")
            out = dst.read_bytes()
        self.done += 1
        log.info("ўгирилди: %s КБ → %s КБ, %.2f с (жами %s)", len(raw) // 1024, len(out) // 1024,
                 time.monotonic() - t0, self.done)
        if self.max_conversions and self.done >= self.max_conversions:
            # хотира тўпланиб қолмасин: жавоб қайтгач тоза профил билан қайта ёқилади
            self._restart_soon(f"{self.done} та файл ўгирилди")
        elif self.restart_bytes:
            # оғир файлдан кейин LibreOffice олган хотирасини қайтармайди (юзлаб МБ) — бўш турганда ҳам банд
            # бўлмасин: чегарадан ошса, жавоб қайтгач қайта ёқилади (~4 с; шу орада сайт одатдаги усулда)
            mem = self.memory()
            if mem is not None and mem > self.restart_bytes:
                self._restart_soon(f"хотира {mem // 2 ** 20} МБ > {self.restart_bytes // 2 ** 20} МБ")
        return xmlrpc.client.Binary(out)

    def _restart_soon(self, why: str):
        if self.stopping:
            return
        self.stopping = True
        log.info("%s — тоза профил билан қайта ёқилади", why)
        threading.Timer(0.5, self.shutdown).start()

    def shutdown(self):
        log.info("тўхтатилмоқда (%s та ўгирилди)", self.done)
        if self.server is not None:
            threading.Thread(target=self.server.shutdown, daemon=True).start()


def make_handler(limit: int):
    class Handler(SimpleXMLRPCRequestHandler):
        rpc_paths = ("/RPC2",)

        def do_POST(self):  # noqa: N802
            try:
                n = int(self.headers.get("content-length", "-1"))
            except ValueError:
                n = -1
            if n < 0 or n > limit:
                self.send_response(413)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            super().do_POST()

        def log_message(self, fmt, *args):  # сўровлар журнали керак эмас — фақат ўгиришлар
            pass
    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=2003, help="XML-RPC порт (сайт шу портга уланади)")
    ap.add_argument("--uno-port", type=int, default=0, help="LibreOffice UNO порти (сукут: --port + 1000)")
    ap.add_argument("--work", default="", help="профил ва вақтинча файллар папкаси")
    ap.add_argument("--soffice", default=shutil.which("soffice") or "/usr/bin/soffice")
    ap.add_argument("--timeout", type=float, default=170, help="битта ўгириш энг кўпи (с)")
    ap.add_argument("--max-conversions", type=int, default=500, help="шунчадан кейин тоза қайта ёқилади (0 — чекловсиз)")
    ap.add_argument("--max-mb", type=int, default=40, help="энг катта файл (МБ)")
    ap.add_argument("--restart-mb", type=int, default=600,
                    help="ўгиришдан кейин хизмат хотираси шундан ошса — қайта ёқилади (0 — текширилмайди)")
    ap.add_argument("--selftest", default="", help="шу .xls ни ўгириб, натижани ёзади ва чиқади")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    work = Path(a.work or tempfile.mkdtemp(prefix="narx_office_")).resolve()
    work.mkdir(parents=True, exist_ok=True)
    office = Office(a.soffice, work, a.uno_port or a.port + 1000)
    office.start()
    svc = Service(office, a.timeout, a.max_conversions, a.max_mb, 0 if a.selftest else a.restart_mb)
    try:
        if a.selftest:
            src = Path(a.selftest)
            t0 = time.monotonic()
            out = svc.convert(xmlrpc.client.Binary(src.read_bytes()), src.suffix)
            dst = work / (src.stem + ".selftest.xlsx")  # манба папкасига ёзиш ҳуқуқи йўқ (бўлмаслиги керак)
            dst.write_bytes(out.data)
            print(f"OK: {dst} ({len(out.data)} байт, {time.monotonic() - t0:.2f} с)")
            return 0
        server = SimpleXMLRPCServer((HOST, a.port), requestHandler=make_handler(request_limit(a.max_mb)),
                                    allow_none=True, logRequests=False)
        svc.server = server
        server.register_function(svc.convert, "convert")
        server.register_function(svc.ping, "ping")
        signal.signal(signal.SIGTERM, lambda *_: svc.shutdown())
        log.info("тинглаяпти: %s:%s", HOST, a.port)
        server.serve_forever()
        server.server_close()
        return 0
    finally:
        office.stop()
        shutil.rmtree(work / "tmp", ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
