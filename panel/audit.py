"""Журналга ёзиш. Журнал хатоси асосий ишни ҳеч қачон бузмаслиги керак."""
import logging

from accounts.throttle import client_ip

from .models import Event

log_ = logging.getLogger(__name__)


def log(request, kind, *, actor=None, target=None, email="", work=None, work_kind="", detail=""):
    """``work`` — Project/Batch/Job (``work_kind``: folder/object/smeta). ``actor=False`` — номаълум."""
    from .sessions import device_text
    try:
        user = actor if actor is not None else getattr(request, "user", None)
        Event.objects.create(
            kind=kind,
            actor=user if user and user.is_authenticated else None,
            device=device_text(request) if request is not None else "",
            target=target, email=(email or "")[:254],
            ip=client_ip(request) if request is not None else None,
            work_kind=work_kind, work_id=getattr(work, "pk", None), detail=(detail or "")[:500],
        )
    except Exception:  # noqa: BLE001
        log_.exception("audit log yozilmadi: %s", kind)
