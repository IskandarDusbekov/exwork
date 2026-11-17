"""Нарх базасидан қидириш: тиниш белгиларисиз мослик (нарх ёзилади) ва ўхшаш ном (фақат таклиф)."""
from core.baselookup import BaseLookup, loose_name
from core.normalize import name_unit_key, unit_key

from .test_smart import _coded_local, _run


def _entry(name, unit, price):
    key = name_unit_key(name, unit)
    return key, {"price": price, "n": 3, "unit_key": unit_key(unit), "unit": unit, "category": "K",
                 "note": "ўрта нарх, 3 та нархдан", "name": name, "item": key}


def _base(*items):
    return dict(_entry(*it) for it in items)


def test_loose_name_ignores_only_punctuation():
    assert loose_name("КОЛЕНО/ОТВОД 90°/ PP-R ДН-25 ММ") == loose_name("КОЛЕНО(ОТВОД 90°) PP-R ДН-25 ММ")
    assert loose_name("ТРУБА 57Х3,5") == loose_name("ТРУБА 57 Х 3.5") != loose_name("ТРУБА 57Х3")


def test_lookup_rules():
    look = BaseLookup(_base(("КОЛЕНО(ОТВОД 90°) PP-R ДН-25 ММ", "ШТ", 946.0), ("МУФТА PP-R ДН-25 ММ", "ШТ", 500.0),
                            ("ТРУБА 32-40", "ШТ", 1.0), ("ТРУБА 3240", "ШТ", 2.0)))
    e, note = look.loose("КОЛЕНО/ОТВОД 90°/ PP-R ДН-25 ММ", "шт")
    assert e["price"] == 946.0 and "тиниш" in note
    assert look.loose("КОЛЕНО/ОТВОД 90°/ PP-R ДН-25 ММ", "м")[0] is None  # бирлиги бошқа
    e, note = look.loose("ТРУБА 32/40", "шт")  # тинишсиз иккита бир хил — танланмайди
    assert e is None and "2 та" in note
    assert look.near("МУФТЫ PP-R ДН-25 ММ", "шт", 0.9)["price"] == 500.0
    assert look.near("МУФТЫ PP-R ДН-32 ММ", "шт", 0.9) is None  # рақам бошқа — ўхшаш бўлса ҳам йўқ
    assert look.near("МУФТЫ PP-R ДН-25 ММ", "шт", 0) is None  # ўчиқ


def test_pipeline_prices_punctuation_and_suggests_near(tmp_path):
    base = _base(("КОЛЕНО(ОТВОД 90°) PP-R ДН-25 ММ", "ШТ", 946.0), ("МУФТА PP-R ДН-25 ММ", "ШТ", 500.0))
    local = _coded_local(tmp_path / "l.xlsx", [
        ("К1", "КОЛЕНО/ОТВОД 90°/ PP-R ДН-25 ММ", "ШТ", 490),
        ("М1", "МУФТЫ PP-R ДН-25 ММ", "ШТ", 4),
        ("М2", "МУФТЫ PP-R ДН-32 ММ", "ШТ", 4),
    ])
    r = _run(local, None, tmp_path, base_prices=base, base_near_min=0.9)
    rows = {x["row"]: x for x in r.rows}
    assert rows[3]["status"] == "BASE" and rows[3]["g"] == 946.0  # фақат тиниш белгилари бошқача — калити бир хил
    assert rows[4]["status"] == "NOT_FOUND" and rows[4]["suggested_price"] == 500.0  # фақат таклиф
    assert rows[4]["near"]["source"] == "base" and rows[4]["near"]["score"] >= 0.9
    assert rows[5]["status"] == "NOT_FOUND" and rows[5]["suggested_price"] is None
    off = _run(local, None, tmp_path, base_prices=base, base_loose=False)
    assert {x["row"]: x["status"] for x in off.rows}[3] == "BASE"  # аниқ мослик — «тинишсиз» созламасига боғлиқ эмас


def test_key_ignores_punctuation_and_letters_but_not_sizes():
    k = lambda n: name_unit_key(n, "шт")  # noqa: E731
    assert k("ПИЛОМАТЕРИАЛЫ ХВОЙНЫХ ПОРОД. ДОСКИ 4-6,5 М") == k("Пиломатериалы хвойных пород доски 4-6.5 м")
    assert k("Проволока ВР-1, 5 мм.") == k("проволока Bp1 5мм")  # лотин B, p
    assert k("Кабель ВВГ 3х2,5") == k("кабель ввг 3x2.5")
    assert k("Арматура класса A-III") == k("Арматура класса AIII")
    assert k("Арматура класса AII") != k("Арматура класса AIII")
    assert k("Лист 8 мм") != k("Лист 10 мм")
    assert k("Труба 32-40") != k("Труба 3240")
    assert k("Цемент 4,5") != k("Цемент 45")
