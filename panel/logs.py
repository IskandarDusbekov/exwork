"""Сервер хатолари панелда: ``logging`` WARNING ва ундан юқорисини ``ServerLog`` жадвалига ёзади.

Хато ёзишнинг ўзи ҳеч қачон асосий ишни бузмаслиги керак: база банд ёки транзакция бузилган бўлса — ўтказиб
юборилади (файл ва консолга барибир чиқади). 404 ва бегона Host сўровлари (сканерлар) бу ерга тушмайди."""
import logging
import threading
import traceback

_local = threading.local()
KEEP_DAYS = 30
_CLEAN_EVERY = 200  # шунча ёзувдан кейин эскилари ўчирилади


class DBLogHandler(logging.Handler):
    _written = 0

    def emit(self, record):
        if getattr(_local, "busy", False) or record.name.startswith("django.db.backends"):
            return  # ёзув вақтида чиққан хабар — чексиз айланишга тушмасин
        _local.busy = True
        try:
            from django.db import connection

            if connection.in_atomic_block and connection.needs_rollback:
                return  # бузилган транзакция ичида ёзиб бўлмайди
            from .models import ServerLog

            request = getattr(record, "request", None)
            user = getattr(request, "user", None) if request is not None else None
            tb = ""
            if record.exc_info and record.exc_info[0] is not None:
                tb = "".join(traceback.format_exception(*record.exc_info))
            elif record.stack_info:
                tb = record.stack_info
            ServerLog.objects.create(
                level=record.levelno,
                logger=record.name[:100],
                message=self._message(record)[:4000],
                traceback=tb[-20000:],
                path=(request.get_full_path() if request is not None else "")[:500],
                method=(getattr(request, "method", "") or "")[:10],
                status=getattr(record, "status_code", None),
                user=user if user is not None and getattr(user, "is_authenticated", False) else None,
                ip=_ip(request),
            )
            DBLogHandler._written += 1
            if DBLogHandler._written % _CLEAN_EVERY == 0:
                cleanup()
        except Exception:  # noqa: BLE001
            pass
        finally:
            _local.busy = False

    @staticmethod
    def _message(record):
        try:
            return record.getMessage()
        except Exception:  # noqa: BLE001
            return str(record.msg)


def _ip(request):
    if request is None:
        return None
    try:
        from accounts.throttle import client_ip
        return client_ip(request)
    except Exception:  # noqa: BLE001
        return None


def cleanup(days=KEEP_DAYS):
    from datetime import timedelta

    from django.utils import timezone

    from .models import ServerLog
    ServerLog.objects.filter(created__lt=timezone.now() - timedelta(days=days)).delete()


class SkipNoise(logging.Filter):
    """Панелга фақат ҳақиқий муаммолар: 4xx жавоблар (404, 403…) ва бегона Host (сканерлар) — йўқ."""

    def filter(self, record):
        if record.name.startswith("django.security.DisallowedHost"):
            return False
        status = getattr(record, "status_code", None)
        return not (record.name == "django.request" and status is not None and status < 500)
