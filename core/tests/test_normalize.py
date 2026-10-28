import pytest

from core.classify import (
    MACHINES, OTHER, WORKERS,
    is_chel_ch, is_mash_ch, is_machinists_labor, is_workers_labor, is_workers_row, row_kind,
)
from core.normalize import norm_name, norm_unit


@pytest.mark.parametrize("raw, expected", [
    ("ЧЕЛ.-Ч", "челч"),
    ("МАШ.-Ч", "машч"),
    ("100М3", "100м3"),
    ("10М2", "10м2"),
    ("100 м3", "100м3"),
    ("М3", "м3"),
    ("М2", "м2"),
    ("м²", "м2"),
    ("м^3", "м3"),
    ("кв.м", "м2"),
    ("кв. метров", "м2"),
    ("куб. м", "м3"),
    ("m3", "м3"),
    ("kg", "кг"),
    ("Т", "т"),
    ("тн", "т"),
    ("ШТ", "шт"),
    ("шт.", "шт"),
    ("ед.", "шт"),
    ("компл.", "компл"),
    ("п.м.", "пм"),
    ("пог. м", "пм"),
    (" КГ ", "кг"),
    ("1-6", "16"),
    ("%", ""),
    ("", ""),
    (None, ""),
])
def test_norm_unit(raw, expected):
    assert norm_unit(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("  Бетон  ТЯЖЁЛЫЙ   КЛАССА ", "бетон тяжелый класса"),
    ("ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "затраты труда рабочих-строителей"),
    (3, "3"),
    (None, ""),
])
def test_norm_name(raw, expected):
    assert norm_name(raw) == expected


@pytest.mark.parametrize("unit, expected", [
    ("ЧЕЛ.-Ч", True), ("чел-час", True), ("чел.ч", True), ("Чел/час", True),
    ("МАШ.-Ч", False), ("чел", False), (None, False),
])
def test_is_chel_ch(unit, expected):
    assert is_chel_ch(unit) is expected


@pytest.mark.parametrize("unit, expected", [
    ("МАШ.-Ч", True), ("маш.час", True), ("маш-ч", True),
    ("ЧЕЛ.-Ч", False), ("маш", False), (None, False),
])
def test_is_mash_ch(unit, expected):
    assert is_mash_ch(unit) is expected


def test_labor_names():
    assert is_workers_labor("ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ")
    assert is_workers_labor("Затраты труда рабочих строит.")
    assert not is_workers_labor("ЗАТРАТЫ ТРУДА МАШИНИСТОВ")
    assert is_machinists_labor("ЗАТРАТЫ ТРУДА МАШИНИСТОВ")
    assert not is_machinists_labor("БЕТОН ТЯЖЕЛЫЙ")
    assert not is_workers_labor(None) and not is_machinists_labor(None)


@pytest.mark.parametrize("name, unit, kind", [
    ("ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ.-Ч", WORKERS),
    ("ЗАТРАТЫ ТРУДА МАШИНИСТОВ", "ЧЕЛ.-Ч", MACHINES),
    ("КРАНЫ НА АВТОМОБИЛЬНОМ ХОДУ", "МАШ.-Ч", MACHINES),
    ("БЕТОН ТЯЖЕЛЫЙ КЛАССА В15", "М3", OTHER),
    ("УСТРОЙСТВО ПРОЛЕТНЫХ СТРОЕНИЙ", "100М3", OTHER),
    # Eski dastur xususiyati: «маш…» bilan boshlanuvchi so'z birlikdan qat'i nazar J ga tushadi
    ("МАСЛО ДИЗЕЛЬНОЕ", "Т", OTHER),
    ("МАШИНЫ ШЛИФОВАЛЬНЫЕ", "Т", MACHINES),
    # Ishchilar nomi, lekin birlik чел-ч emas -> boshqa
    ("ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "РУБ", OTHER),
    (None, None, OTHER),
])
def test_row_kind(name, unit, kind):
    assert row_kind(name, unit) == kind


def test_is_workers_row_excludes_machinists():
    assert is_workers_row("ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ.-Ч")
    assert not is_workers_row("РАБОЧИЕ-СТРОИТЕЛИ И МАШИНИСТЫ", "ЧЕЛ.-Ч")
