"""LibreOffice навбати: сервер бўйича бир вақтда кўпи билан ``NARXLASH_LO_SLOTS`` (стандарт 2) та .xls ўгирилади.

Ҳар бир .xls учун алоҳида ``soffice`` жараёни очилади (~150–250 МБ RAM, 1 ядро). Чекловсиз 3–4 фойдаланувчи бир
вақтда юкласа, 2 ядро / 4 ГБ серверда ҳаммаси секинлашади. Ўрин — кешдаги калит (``lo:slot:<n>``): Redis'да
барча сайт жараёнлари ва фон ишчиси бир хил кўради. Жараён ўлса ҳам ўрин ``TTL`` дан кейин ўзи бўшайди
(ўгириш ``core.convert`` да 180 с билан чекланган).

Кутаётган кишига хабар: ``notify(cb)`` — шу оқимдаги ўгириш навбат кутса, ``cb(busy, ahead, waited)`` ҳар 2 с
чақирилади; навбат келганда ``cb(None, None, waited)``. Юклаш саҳифаси ва папка жараёни шундан фойдаланади.
"""
import contextlib
import logging
import os
import socket
import threading
import time
import uuid

from django.conf import settings
from django.core.cache import cache

from core.convert import ConversionError
from django.utils.translation import gettext_noop as _noop

log = logging.getLogger(__name__)
PREFIX = "lo:slot:"
WAITING = "lo:waiting"
TTL = 240  # ўгириш 180 с да тўхтатилади — ўрин ундан кейин албатта бўшайди
_local = threading.local()


def slots() -> int:
    return max(1, int(getattr(settings, "NARXLASH_LO_SLOTS", 2)))


@contextlib.contextmanager
def notify(cb, max_wait: float = None):
    """Шу оқимда ўгириш навбат кутса — ``cb`` орқали хабар; ``max_wait`` — кўпи билан қанча кутилади (с)."""
    old = getattr(_local, "cb", None), getattr(_local, "max_wait", None)
    _local.cb, _local.max_wait = cb, max_wait
    try:
        yield
    finally:
        _local.cb, _local.max_wait = old


def busy() -> list:
    """Ҳозир ўгирилаётган файллар: [{name, pid, host, t}]."""
    got = cache.get_many([f"{PREFIX}{i}" for i in range(slots())])
    return sorted(got.values(), key=lambda h: h.get("t", 0))


def waiting() -> int:
    return max(0, cache.get(WAITING) or 0)


def _try(name: str):
    holder = {"name": name[:120], "pid": os.getpid(), "host": socket.gethostname(), "t": time.time(),
              "id": uuid.uuid4().hex}
    for i in range(slots()):
        if cache.add(f"{PREFIX}{i}", holder, TTL):
            return f"{PREFIX}{i}", holder["id"]
    return None


def _count(delta: int):
    try:
        if delta > 0:
            cache.add(WAITING, 0, 3600)
            cache.incr(WAITING)
        else:
            cache.decr(WAITING)
    except ValueError:  # калит муддати ўтган — санаш муҳим эмас, фақат кўрсатиш учун
        if delta > 0:
            cache.set(WAITING, 1, 3600)


@contextlib.contextmanager
def slot(name: str = ""):
    """Бўш ўрин келгунча кутади, ўгириш тугагач бўшатади. Ўрин рақамини (0, 1, …) беради: доимий LibreOffice
    (``NARXLASH_OFFICE``) шу рақам бўйича танланади — ҳар нусхага бир вақтда биттадан файл."""
    got = _try(name)
    waited = 0.0
    if not got:
        cb = getattr(_local, "cb", None)
        max_wait = getattr(_local, "max_wait", None) or 900
        _count(+1)
        t0, said = time.monotonic(), -10.0
        log.info("LibreOffice банд (%s та ўрин) — %s навбатда", slots(), name)  # i18n: ok
        try:
            while not got:
                waited = time.monotonic() - t0
                if waited > max_wait:
                    raise ConversionError(_noop("Сервер жуда банд: {0} сония ичида .xls файлни очишга навбат келмади. Бир оздан кейин қайта юкланг ёки файлни .xlsx қилиб сақланг.").format(int(max_wait)))
                if cb and time.monotonic() - said >= 2:
                    said = time.monotonic()
                    _call(cb, len(busy()), max(0, waiting() - 1), waited)
                time.sleep(0.4)
                got = _try(name)
        finally:
            _count(-1)
        if cb:
            _call(cb, None, None, time.monotonic() - t0)
    key, token = got
    try:
        yield int(key[len(PREFIX):])
    finally:
        cur = cache.get(key)
        if cur and cur.get("id") == token:
            cache.delete(key)


def _call(cb, *args):
    try:
        cb(*args)
    except Exception:  # noqa: BLE001 — хабар ёзилмаса ҳам ўгириш давом этади
        log.debug("lo_queue notify failed", exc_info=True)


def wait_text(busy_n, ahead, waited) -> str:
    """Фойдаланувчига: нега кутяпти ва нима бўлади."""
    extra = _noop(", сиздан олдин яна {0} та").format(ahead) if ahead else ""
    return (_noop("Сервер банд: ҳозир {0} та .xls файл очилмоқда{1}. Навбатингиз келиши билан ўзи давом этади (кутилди {2} с)").format(busy_n, extra, int(waited)))
