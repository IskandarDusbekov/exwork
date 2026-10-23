"""Nom va birlikni normallashtirish (reja §2.2). Eski dasturdan o'zgarishsiz ko'chirilgan."""
import re

_UNIT_SYNONYMS = [
    (r"^(квм|квметр|квметра|квметров|квм2|кв\.?м|кв\.?метр|кв\.?метра|кв\.?метров|м2)$", "м2"),
    (r"^(кубм|кубметр|кубметра|кубметров|куб\.?м|м3)$", "м3"),
    (r"^(пм|погм|погонм|погонныйм|пог\.?м|п\.?м)$", "пм"),
    (r"^(шт|штука|штук|ед|единица|pcs)$", "шт"),
    (r"^(компл|комплект|кмп)$", "компл"),
    (r"^(уп|упак|упаковка|пачка|пак)$", "уп"),
    (r"^(т|тн|тонна|тонн)$", "т"),
    (r"^(кг|килограмм|килограм|килограммов)$", "кг"),
    (r"^(г|гр|грамм|граммов)$", "г"),
    (r"^(м|метр|метра|метров)$", "м"),
    (r"^(см|сантиметр|сантиметра|сантиметров)$", "см"),
    (r"^(мм|миллиметр|миллиметра|миллиметров)$", "мм"),
    (r"^(л|литр|литра|литров)$", "л"),
    (r"^(ч|час|часа|часов)$", "ч"),
]


def norm_name(s) -> str:
    if s is None:
        return ""
    s = str(s).replace(" ", " ")
    s = s.lower().strip()
    s = s.replace("ё", "е")
    s = re.sub(r"\s+", " ", s)
    return s


def norm_unit(u) -> str:
    if u is None:
        return ""
    u = str(u).replace(" ", " ").strip().lower()
    u = u.replace(",", ".")
    u = u.replace("²", "2").replace("³", "3")
    u = u.replace("^2", "2").replace("^3", "3")
    u = re.sub(r"\s+", "", u)
    u = u.replace(".", "").replace("-", "").replace("_", "")
    u = u.replace("\\", "").replace("/", "")
    u = u.replace("(", "").replace(")", "")
    u = u.replace("[", "").replace("]", "")
    u = u.replace("{", "").replace("}", "")
    u = u.replace("·", "").replace(":", "").replace(";", "")
    u = u.replace("'", "").replace('"', "").replace("`", "")

    u = u.replace("m2", "м2").replace("m3", "м3")
    u = u.replace("kg", "кг").replace("l", "л")

    for pat, repl in _UNIT_SYNONYMS:
        if re.match(pat, u):
            return repl

    return re.sub(r"[^0-9а-яa-z]+", "", u)
