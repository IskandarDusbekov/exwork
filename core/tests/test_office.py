"""Доимий LibreOffice: сайт томони (core/convert.py) ва хизмат скрипти (deploy/office/narx_office.py).

LibreOffice'нинг ўзи керак эмас: хизмат ўрнида — шу тестдаги XML-RPC сервер, скриптда эса UNO қисми
(``Office``) сохтаси билан алмаштирилади."""
import contextlib
import importlib.util
import io
import threading
import urllib.request
import xmlrpc.client
from pathlib import Path
from xmlrpc.server import SimpleXMLRPCServer

import pytest
from openpyxl import Workbook, load_workbook

from core import convert

ROOT = Path(__file__).resolve().parents[2]


def _xlsx_bytes(text="ВОДА") -> bytes:
    wb = Workbook()
    wb.active["A1"] = text
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@contextlib.contextmanager
def fake_office(handler):
    server = SimpleXMLRPCServer(("127.0.0.1", 0), allow_none=True, logRequests=False)
    server.register_function(handler, "convert")
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def lo(monkeypatch, tmp_path):
    """LibreOffice «бор», лекин эски усул (ҳар файлга soffice) фақат санайди."""
    cold = []
    monkeypatch.setattr(convert, "find_soffice", lambda s=None: "soffice")
    monkeypatch.setenv(convert.CONVERTER_ENV, "libreoffice")

    def cold_convert(exe, path, out_dir, timeout):
        cold.append(path)
        out = out_dir / (path.stem + ".xlsx")
        out.write_bytes(_xlsx_bytes("эски усул"))
        return out
    monkeypatch.setattr(convert, "_convert_cold", cold_convert)
    monkeypatch.setattr(convert, "_OFFICE", [])
    (tmp_path / "a.xls").write_bytes(b"xls-bytes")
    return cold


def test_parse_office_only_loopback():
    assert convert.parse_office("127.0.0.1:2003, localhost:2004") == [("127.0.0.1", 2003), ("localhost", 2004)]
    assert convert.parse_office("") == []
    with pytest.raises(ValueError, match="127.0.0.1"):
        convert.parse_office("10.0.0.5:2003")  # хизматда пароль йўқ — ташқи манзил тақиқланган
    with pytest.raises(ValueError, match="порт"):
        convert.parse_office("127.0.0.1:abc")


def test_converted_by_running_office(lo, tmp_path):
    got = []

    def handler(data, suffix):
        got.append((data.data, suffix))
        return xmlrpc.client.Binary(_xlsx_bytes("доимий"))
    with fake_office(handler) as port:
        convert.set_office(f"127.0.0.1:{port}")
        out = convert.ensure_xlsx(tmp_path / "a.xls", tmp_path / "out")
    assert got == [(b"xls-bytes", ".xls")]  # мазмуни юборилди (йўл эмас)
    assert out == tmp_path / "out" / "a.xlsx"
    assert load_workbook(out).active["A1"].value == "доимий"
    assert lo == []  # эски усул ишламади


def test_office_down_falls_back(lo, tmp_path):
    with fake_office(lambda d, s: None) as port:
        pass  # сервер ёпилди — порт бўш
    convert.set_office(f"127.0.0.1:{port}")
    out = convert.ensure_xlsx(tmp_path / "a.xls", tmp_path / "out")
    assert load_workbook(out).active["A1"].value == "эски усул"
    assert len(lo) == 1


def test_bad_file_not_retried_cold(lo, tmp_path):
    def handler(data, suffix):
        raise xmlrpc.client.Fault(convert.OFFICE_FAULT_FILE, "файлни очиб бўлмади")
    with fake_office(handler) as port:
        convert.set_office(f"127.0.0.1:{port}")
        with pytest.raises(convert.ConversionError, match="очиб бўлмади"):
            convert.ensure_xlsx(tmp_path / "a.xls", tmp_path / "out")
    assert lo == []  # бузуқ файл — эски усул ҳам очолмайди, вақт кетказилмайди


def test_service_fault_falls_back(lo, tmp_path):
    def handler(data, suffix):
        raise xmlrpc.client.Fault(convert.OFFICE_FAULT_SERVICE, "LibreOffice ишламаяпти")
    with fake_office(handler) as port:
        convert.set_office(f"127.0.0.1:{port}")
        convert.ensure_xlsx(tmp_path / "a.xls", tmp_path / "out")
    assert len(lo) == 1


def test_slot_picks_instance(lo, tmp_path, monkeypatch):
    """Навбат ўрни (0, 1) — нусха: ҳар нусхага бир вақтда биттадан файл."""
    hits = []
    monkeypatch.setattr(convert, "_LIMITER", lambda name: contextlib.nullcontext(1))
    with fake_office(lambda d, s: hits.append("A") or xmlrpc.client.Binary(_xlsx_bytes())) as pa, \
            fake_office(lambda d, s: hits.append("B") or xmlrpc.client.Binary(_xlsx_bytes())) as pb:
        convert.set_office(f"127.0.0.1:{pa},127.0.0.1:{pb}")
        convert.ensure_xlsx(tmp_path / "a.xls", tmp_path / "out")
    assert hits == ["B"]


def test_lo_queue_slot_yields_number(settings):
    from jobs import lo_queue
    settings.NARXLASH_LO_SLOTS = 2
    with lo_queue.slot("a.xls") as a, lo_queue.slot("b.xls") as b:
        assert {a, b} == {0, 1}


# ===================== хизмат скрипти =====================
def _script():
    spec = importlib.util.spec_from_file_location("narx_office", ROOT / "deploy" / "office" / "narx_office.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeOffice:
    def __init__(self, work, alive=True, error=None):
        self.work, self._alive, self.error, self.proc = work, alive, error, None
        (work / "tmp").mkdir(parents=True, exist_ok=True)

    def alive(self):
        return self._alive

    def convert(self, src, dst):
        if self.error:
            raise self.error
        dst.write_bytes(_xlsx_bytes(src.read_bytes().decode()))


def test_profile_disables_macros_history_and_recovery(tmp_path):
    m = _script()
    (tmp_path / "profile" / "user").mkdir(parents=True)
    (tmp_path / "profile" / "user" / "old.txt").write_text("олдинги файл изи")
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "left.xls").write_text("x")
    prof = m.fresh_profile(tmp_path)
    assert not (prof / "user" / "old.txt").exists() and not (tmp_path / "tmp" / "left.xls").exists()
    xcu = (prof / "user" / "registrymodifications.xcu").read_text(encoding="utf-8")
    for name, value in (("DisableMacrosExecution", "true"), ("MacroSecurityLevel", "3"), ("PickListSize", "0"),
                        ("CreateBackup", "false")):
        assert f'oor:name="{name}" oor:op="fuse"><value>{value}</value>' in xcu
    assert xcu.count('oor:name="Enabled" oor:op="fuse"><value>false</value>') == 2  # автосақлаш ва тиклаш
    assert m.HOST == "127.0.0.1" and m.NEVER_EXECUTE == 0


def test_service_convert_and_cleanup(tmp_path):
    m = _script()
    svc = m.Service(FakeOffice(tmp_path), timeout=30, max_conversions=0, max_mb=1)
    out = svc.convert(xmlrpc.client.Binary("ВОДА".encode()), ".XLS")
    assert load_workbook(io.BytesIO(out.data)).active["A1"].value == "ВОДА"
    assert svc.done == 1
    assert list((tmp_path / "tmp").iterdir()) == []  # файл ўгирилгач ўчирилди


def test_service_rejects_and_reports(tmp_path, monkeypatch):
    m = _script()
    svc = m.Service(FakeOffice(tmp_path), timeout=30, max_conversions=0, max_mb=1)
    for data, suffix in ((b"x", ".pdf"), (b"", ".xls"), (b"x" * (1024 * 1024 + 1), ".xls")):
        with pytest.raises(xmlrpc.client.Fault) as e:
            svc.convert(xmlrpc.client.Binary(data), suffix)
        assert e.value.faultCode == m.FAULT_FILE
    svc.office.error = RuntimeError("General Error")  # LibreOffice ишлаяпти, файл бузуқ
    with pytest.raises(xmlrpc.client.Fault) as e:
        svc.convert(xmlrpc.client.Binary(b"x"), ".xls")
    assert e.value.faultCode == m.FAULT_FILE
    died = []
    monkeypatch.setattr(svc, "_die", lambda why, code=3: died.append(why))
    svc.office._alive = False  # LibreOffice ўлган
    with pytest.raises(xmlrpc.client.Fault) as e:
        svc.convert(xmlrpc.client.Binary(b"x"), ".xls")
    assert e.value.faultCode == m.FAULT_SERVICE
    threading.Event().wait(0.4)
    assert died  # жараён чиқади — systemd тоза профил билан қайта ёқади


def test_cgroup_memory_read(tmp_path):
    m = _script()
    (tmp_path / "cgroup").write_text("0::/system.slice/narxlash-office@2003.service\n")
    d = tmp_path / "cg" / "system.slice" / "narxlash-office@2003.service"
    d.mkdir(parents=True)
    (d / "memory.current").write_text("927989760\n")
    assert m.cgroup_memory(tmp_path / "cgroup", tmp_path / "cg") == 927989760
    assert m.cgroup_memory(tmp_path / "yoq", tmp_path / "cg") is None


def test_restart_after_heavy_file(tmp_path, monkeypatch):
    """Оғир файлдан кейин LibreOffice хотирани қайтармайди — чегарадан ошса, хизмат қайта ёқилади."""
    m = _script()
    mem = [100 * 2 ** 20]
    svc = m.Service(FakeOffice(tmp_path), timeout=30, max_conversions=0, max_mb=1, restart_mb=600,
                    memory=lambda: mem[0])
    stops = []
    monkeypatch.setattr(svc, "shutdown", lambda: stops.append(1))
    svc.convert(xmlrpc.client.Binary(b"a"), ".xls")
    threading.Event().wait(0.7)
    assert stops == []  # 100 МБ — ишлашда давом этади
    mem[0] = 885 * 2 ** 20
    svc.convert(xmlrpc.client.Binary(b"b"), ".xls")
    svc.convert(xmlrpc.client.Binary(b"c"), ".xls")  # қайта ёқилишини кутаётганда — яна бир марта эмас
    threading.Event().wait(0.7)
    assert stops == [1]


def test_request_size_limited(tmp_path):
    m = _script()
    server = SimpleXMLRPCServer(("127.0.0.1", 0), requestHandler=m.make_handler(1000), allow_none=True,
                                logRequests=False)
    server.register_function(lambda: "ok", "ping")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/RPC2"
        assert xmlrpc.client.ServerProxy(url).ping() == "ok"
        req = urllib.request.Request(url, data=b"x" * 5000, headers={"Content-Type": "text/xml"})
        # 413 — танаси ўқилмай рад этилади: мижоз 413 ёки узилган уланишни кўради (ОТ га қараб)
        with pytest.raises((urllib.error.HTTPError, ConnectionError, OSError)) as e:
            urllib.request.urlopen(req, timeout=5)
        if isinstance(e.value, urllib.error.HTTPError):
            assert e.value.code == 413
    finally:
        server.shutdown()
        server.server_close()
