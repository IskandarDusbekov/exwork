"""Базанинг захира нусхаси (панелдан): рўйхат, ҳозир олиш, юклаб олиш. Ҳар кунги нусхани серверда
``deploy/backup.sh`` (cron) олади — у ``media/`` файлларини ҳам сақлайди; панел уни кўрсатади ва зарур бўлса
базадан қўшимча нусха олади (масалан, катта ўзгаришдан олдин)."""
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from django.conf import settings
from django.db import close_old_connections, connection
from django.utils import timezone

log = logging.getLogger(__name__)
KEEP_DAYS = 14  # backup.sh ҳам шунча сақлайди
STATE_KEY = "_backup_state"
NAME_RE = re.compile(r"^narxlash_\d{4}-\d{2}-\d{2}_\d{4,6}\.(dump|sqlite3)$")
STALE_HOURS = 26  # кунлик нусха шундан эски бўлса — огоҳлантириш (cron ишламаяпти)


def root() -> Path:
    return Path(settings.NARXLASH_BACKUP_DIR)


def dumps() -> List[dict]:
    d = root() / "db"
    out = []
    if d.is_dir():
        for p in d.iterdir():
            if p.is_file() and NAME_RE.match(p.name):
                st = p.stat()
                out.append({"name": p.name, "size": st.st_size,
                            "time": datetime.fromtimestamp(st.st_mtime, tz=timezone.get_current_timezone())})
    return sorted(out, key=lambda x: x["time"], reverse=True)


def media_snapshots() -> List[dict]:
    d = root() / "media"
    if not d.is_dir():
        return []
    return [{"name": p.name} for p in sorted(d.iterdir(), reverse=True) if p.is_dir() and re.match(r"^20\d\d-\d\d-\d\d$", p.name)]


def path_of(name: str) -> Optional[Path]:
    """Фақат рўйхатдаги ном (йўл ўйини — ``../`` — бўлмайди)."""
    if not NAME_RE.match(name or ""):
        return None
    p = root() / "db" / name
    return p if p.is_file() else None


def state() -> dict:
    from .models import SiteSetting
    s = SiteSetting.objects.filter(key=STATE_KEY).first()
    return s.value if s else {}


def _set_state(**kw):
    from .models import SiteSetting
    SiteSetting.objects.update_or_create(key=STATE_KEY, defaults={"value": kw})


def summary() -> dict:
    items = dumps()
    last = items[0] if items else None
    total, _used, free = shutil.disk_usage(root() if root().exists() else settings.BASE_DIR)
    stale = last is None or (timezone.now() - last["time"]).total_seconds() > STALE_HOURS * 3600
    return {"dir": root(), "dumps": items, "last": last, "stale": stale, "media": media_snapshots(),
            "free": free, "total": total, "state": state(), "engine": connection.vendor}


def start(user) -> bool:
    """Фонда нусха олиш. Аввалгиси тугамаган бўлса — False."""
    st = state()
    if st.get("running") and time.time() - st.get("started", 0) < 3600:
        return False
    _set_state(running=True, started=time.time(), by=user.email)
    threading.Thread(target=_run, args=(user.email,), daemon=True, name="panel-backup").start()
    return True


def _run(by: str):
    try:
        name = make()
        _set_state(running=False, finished=time.time(), file=name, by=by)
    except Exception as e:  # noqa: BLE001 — сабаби панелда кўринади
        log.exception("захира нусха олинмади")
        _set_state(running=False, finished=time.time(), error=str(e)[:500], by=by)
    finally:
        close_old_connections()


def make() -> str:
    """Базанинг нусхаси ``backups/db/`` га (PostgreSQL — pg_dump, ишлаб чиқишда SQLite — backup API)."""
    d = root() / "db"
    d.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(d, 0o700)
    stamp = timezone.localtime().strftime("%Y-%m-%d_%H%M%S")
    if connection.vendor == "postgresql":
        dest = d / f"narxlash_{stamp}.dump"
        tmp = dest.with_suffix(".part")
        subprocess.run(["pg_dump", "--format=custom", "--no-owner", f"--file={tmp}", settings.DATABASE_URL],
                       check=True, timeout=3600, capture_output=True)
        subprocess.run(["pg_restore", "--list", str(tmp)], check=True, timeout=600, capture_output=True)
    else:
        dest = d / f"narxlash_{stamp}.sqlite3"
        tmp = dest.with_suffix(".part")
        name = str(connection.settings_dict["NAME"])  # тестда — «file:memorydb_…?mode=memory» (URI)
        src = sqlite3.connect(name, uri=name.startswith("file:"))
        out = sqlite3.connect(tmp)
        try:
            src.backup(out)
        finally:
            out.close()
            src.close()
    os.replace(tmp, dest)
    if os.name != "nt":
        os.chmod(dest, 0o600)  # ичида парол хешлари ва икки босқичли кириш калитлари
    cutoff = time.time() - KEEP_DAYS * 86400
    for x in d.iterdir():
        if NAME_RE.match(x.name) and x.stat().st_mtime < cutoff:
            x.unlink(missing_ok=True)
    return dest.name
