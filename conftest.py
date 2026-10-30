import pytest


@pytest.fixture(autouse=True)
def _recalc_inline(request, monkeypatch):
    """Testlarda smetani qayta hisoblash shu jarayonda, darhol (fondagi oqim test tranzaksiyasini ko'rmaydi).
    Navbatning o'zini tekshiradigan test ``@pytest.mark.recalc_queue`` bilan belgilanadi."""
    if request.node.get_closest_marker("recalc_queue"):
        return
    try:
        from jobs import recalc
    except Exception:  # noqa: BLE001 — Django sozlanmagan (core testlari)
        return
    monkeypatch.setattr(recalc, "start", recalc.run_now)
    from texnazor import tasks as tn_tasks
    monkeypatch.setattr(tn_tasks, "start", lambda obj_id: None)  # ofyorta testda ``tasks.run_now`` bilan o'qiladi
