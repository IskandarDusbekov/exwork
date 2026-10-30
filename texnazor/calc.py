"""Svodka zanjiri: turlar bo'yicha to'g'ridan-to'g'ri summalar -> foizlar -> QQS bilan jami.

Ofyortaning pastidagi svodka bilan bir xil (40-maktab: 32 235 755 495 so'm aynan chiqadi):

    material + transport %, kabel + transport %, ish haqi, mashina   -> to'g'ridan-to'g'ri
    + pudratchi boshqa xarajatlari %                                  -> ...
    + buyurtmachi uskunalari (+ transport %), pudratchi uskunalari (+ transport %)
    + sug'urta %  + qo'shimcha qatorlar (X)                           -> QQS siz
    + QQS                                                              -> jami

Buyurtmachi uskunalari (N) o'zi beradi — pudratchiga to'lanmaydi: ``contractor`` — pudratchiga tegishli qism.
"""
from typing import Dict

STEPS = [
    ("J", "Қурувчи-ишчилар иш ҳақи"),
    ("K", "Қурилиш машиналари ва механизмлари"),
    ("L", "Қурилиш материаллари (пудратчи)"),
    ("tr_L", "Транспорт харажатлари (материал)"),
    ("O", "Кабель маҳсулотлари (пудратчи)"),
    ("tr_O", "Транспорт харажатлари (кабель)"),
    ("direct", "Жами тўғридан-тўғри харажатлар"),
    ("overhead", "Пудратчи бошқа харажатлари"),
    ("direct_overhead", "Тўғридан-тўғри ва бошқа харажатлар"),
    ("N_full", "Ускуналар (буюртмачи) транспорт билан"),
    ("M_full", "Ускуналар (пудратчи) транспорт билан"),
    ("base", "Жами тўғридан-тўғри харажатлар ускуналар билан"),
    ("insurance", "Объектни суғурталаш"),
    ("X", "Қўшимча қаторлар"),
    ("no_vat", "Жами объект қиймати ҚҚС сиз"),
    ("vat", "ҚҚС"),
    ("total", "Жами объект қиймати ҚҚС билан"),
    ("customer", "Буюртмачи жиҳозлари ҚҚС билан"),
    ("contractor", "Пудратчи харажатлари ҚҚС билан"),
]


def chain(cat: Dict[str, float], rates: Dict[str, float]) -> Dict[str, float]:
    """``cat`` — turlar bo'yicha summa (J..O va X — qo'shimcha), ``rates`` — ofyorta foizlari."""
    g = lambda k: float(cat.get(k) or 0.0)  # noqa: E731
    out = {k: g(k) for k in ("J", "K", "L", "M", "N", "O", "X")}
    out["tr_L"] = out["L"] * rates["tr_material"]
    out["tr_O"] = out["O"] * rates["tr_cable"]
    out["direct"] = out["J"] + out["K"] + out["L"] + out["tr_L"] + out["O"] + out["tr_O"]
    out["overhead"] = out["direct"] * rates["overhead"]
    out["direct_overhead"] = out["direct"] + out["overhead"]
    out["N_full"] = out["N"] * (1 + rates["tr_eq_customer"])
    out["M_full"] = out["M"] * (1 + rates["tr_eq_contractor"])
    out["base"] = out["direct_overhead"] + out["N_full"] + out["M_full"]
    out["insurance"] = out["base"] * rates["insurance"]
    out["no_vat"] = out["base"] + out["insurance"] + out["X"]
    out["vat"] = out["no_vat"] * rates["vat"]
    out["total"] = out["no_vat"] + out["vat"]
    # buyurtmachi o'zi beradigan uskunalar (sug'urtasi bilan) — pudratchiga to'lanmaydi
    out["customer"] = out["N_full"] * (1 + rates["insurance"]) * (1 + rates["vat"])
    out["contractor"] = out["total"] - out["customer"]
    return out
