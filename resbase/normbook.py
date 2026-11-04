"""Норма шифри ва иш тури (тўплам): «Е0101-197-02 ДОП. 11 …» → шифр «Е0101-197-02», тўплам «Е01».
Тўплам номини админ ёзади (``resbase.models.BookName``) — тизимда тайёр ном йўқ.

Шифр кўринишлари (смета файлларида учрагани):
* ``Е0101-197-02`` — 4 рақам: биринчи иккитаси тўплам (01), кейингиси жадвал;
* ``Е01-01-013-11``, ``Е68-020-01``, ``Ц08-03-599-7`` — 2 рақам: тўпламнинг ўзи;
* ``Е102-6-1``, ``Е101-33-1`` — эски ёзув: биринчи рақам тўплам (1), кейингиси жадвал;
* ``Е310-1015`` — юк ташиш (перевозка грузов) нархлари.
«Е» — қурилиш ишлари (51–69 — таъмирлаш), «Ц» — ускуна монтажи. Ҳарф лотинча ёзилган бўлса ҳам (E, C) бир хил.
"""
import re
from typing import Optional, Tuple

_SHIFR = re.compile(r"^\s*([ЕЦEC])\s?(\d{1,4})((?:-\d+)+)", re.I)

TRANSPORT = "ТР"  # «Е310-1015» — юк ташиш


def parse(raw) -> Optional[Tuple[str, str]]:
    """Норма шифри бўлса — (шифр, тўплам калити), бўлмаса None. «Е0101-197-02 ДОП. 11 …» → («Е0101-197-02», «Е01»)."""
    m = _SHIFR.match(str(raw or "").upper())
    if not m:
        return None
    letter = {"E": "Е", "C": "Ц"}.get(m.group(1), m.group(1))
    digits, rest = m.group(2), m.group(3)
    shifr = f"{letter}{digits}{rest}"
    if letter == "Е" and len(digits) == 3 and digits.startswith("31"):
        return shifr, TRANSPORT
    if len(digits) == 4:
        book = digits[:2]
    elif len(digits) == 3:
        book = digits[0].zfill(2)
    else:
        book = digits.zfill(2)
    return shifr, f"{letter}{book}"


def book_label(key: str) -> str:
    """Тўплам номини тизим ўзи ёзмайди (тахминий ном чалғитади) — номни админ ёзади (``BookName``). Бу ерда — бўш."""
    return ""


def book_sort(key: str):
    return (key == TRANSPORT, key[:1] != "Е", key)
