# i18n: ok-file — ички маълумот / тил номлари (ҳар бири ўз ёзувида)
"""Ўзбек кирилл → лотин (1995 йилги расмий алифбо): «Иш столи» → «Ish stoli».

Фақат кирилл ҳарфлар ўзгаради: HTML, %(name)s, {name} ва лотин матн тегилмайди.
Қоидалар:
* е — сўз бошида, унлидан ва ъ/ь дан кейин «ye» («ечим» → «yechim», «объект» → «obʼyekt»), қолган ҳолда «e»;
* ц — сўз бошида «s» («цемент» → «sement»), қолган ҳолда «ts» («процент» → «protsent»);
* ў, ғ → oʻ, gʻ (U+02BB); ъ → ʼ (U+02BC, тутуқ белгиси); ь — тушириб қолдирилади;
* катта ҳарф: сўз бутунлай катта бўлса «SH», бўлмаса «Sh».
"""
OKINA = "ʻ"   # ʻ — oʻ, gʻ
TUTUQ = "ʼ"   # ʼ — тутуқ белгиси

_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "ё": "yo", "ж": "j", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "x", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": TUTUQ, "ы": "i", "ь": "", "э": "e",
    "ю": "yu", "я": "ya", "ў": "o" + OKINA, "қ": "q", "ғ": "g" + OKINA, "ҳ": "h",
}
_VOWELS = set("аеёиоуўэюяыaeiou")
_CYR = set(_MAP) | {"е", "ц"}


def _is_cyr(ch: str) -> bool:
    return ch.lower() in _CYR


def to_latin(text: str) -> str:
    out = []
    n = len(text)
    for i, ch in enumerate(text):
        low = ch.lower()
        if low not in _CYR:
            out.append(ch)
            continue
        prev = text[i - 1].lower() if i else ""
        word_start = not prev or not (prev.isalpha() or prev in "ъь'")
        if low == "е":
            lat = "ye" if word_start or prev in _VOWELS or prev in "ъь" else "e"
        elif low == "ц":
            lat = "s" if word_start else "ts"
        else:
            lat = _MAP[low]
        if ch != low and lat:  # катта ҳарф
            nxt = text[i + 1] if i + 1 < n else ""
            prv = text[i - 1] if i else ""
            all_caps = (nxt.isalpha() and nxt.isupper()) or (not (nxt.isalpha() and nxt.islower())
                                                             and prv.isalpha() and prv.isupper())
            lat = lat.upper() if all_caps else lat[0].upper() + lat[1:]
            if lat.endswith(OKINA.upper()):  # ʻ нинг каттаси йўқ
                pass
        out.append(lat)
    return "".join(out)


def has_cyrillic(text: str) -> bool:
    return any("Ѐ" <= ch <= "ӿ" for ch in text)
