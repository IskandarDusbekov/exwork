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


# ko'rinishi bir xil lotin/kirill harflari va belgilar: RES'da «ВОLIХ» (kirill В, О, Х), lokalda «BOLIX»;
# RES'da «Д±5», lokalda «Д=5» (dastur «=» ni «±» qilib yozadi). Faqat smart rejimda, aniq moslik uchun.
_FOLD = str.maketrans({"а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p", "с": "c",
                       "т": "t", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "±": "=", "×": "x"})


def fold_name(name: str) -> str:
    """``norm_name`` natijasidan solishtirish kaliti: «шт. тип "вolix"» = «шт. тип "bolix"»."""
    return name.translate(_FOLD)


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


_CODE = re.compile(r"^\d{1,6}$")


def norm_code(raw) -> str:
    """Milliy resurs kodi: «000258» / «258» / 258.0 → «258»; kod bo'lmasa (С1, Е0101-…, 15-104045-20) — ""."""
    s = str(raw if raw is not None else "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return str(int(s)) if _CODE.match(s) else ""


def unit_key(unit) -> str:
    """Birlikni solishtirish kaliti: «ЧЕЛ.-Ч» = «ЧЕЛ-ЧАС» = «челч»."""
    return re.sub(r"час$", "ч", norm_unit(unit))
