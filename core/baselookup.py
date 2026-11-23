"""Нарх базасидан қидириш (RES'сиз смета): аниқ калит → тиниш белгиларисиз → ўхшаш ном (фақат таклиф).

* **Аниқ** — номи + бирлиги калити (``name_unit_key``) ёки миллий код (эски база).
* **Тиниш белгиларисиз** — «КОЛЕНО/ОТВОД 90°/ PP-R ДН-25 ММ» = «КОЛЕНО(ОТВОД 90°) PP-R ДН-25 ММ»: ҳарф ва рақамлар
  айнан бир хил, фақат ``( ) / , . - °`` ва бўшлиқлар бошқача. Нарх ёзилади — лекин базада шундай калитга
  **битта** ресурс тушса (иккита ҳар хил ресурс тушса — ҳеч бири олинмайди, смета­чи танлайди).
* **Ўхшаш ном** — панелдаги фоиздан ўхшаш, бирлиги бир хил ва номидаги **барча рақамлар бир хил** (ДН-25 ≠ ДН-32:
  бир рақам фарқи ҳам 95% ўхшаш чиқади, шунинг учун фоиз ёлғиз ишончли эмас). Нарх ёзилмайди — «яқин ресурс»
  таклифи; «Автоматик» усулда панелдаги «Таклифни ўзи олиш» қоидалари билан олинади.
"""
import re
from typing import Dict, List, Optional, Tuple

from rapidfuzz import fuzz, process

from .normalize import fold_name, name_unit_key, norm_code, norm_name, unit_key
from django.utils.translation import gettext_noop as _noop

_PUNCT = re.compile(r"[\W_]+")
_NUMBERS = re.compile(r"\d+(?:[.,]\d+)?")


def loose_name(name) -> str:
    """Тиниш белгилари ва бўшлиқларсиз (ҳарф ва рақамлар қолади)."""
    return _PUNCT.sub("", fold_name(norm_name(name)))


def numbers_of(name) -> List[str]:
    return [n.replace(",", ".") for n in _NUMBERS.findall(fold_name(norm_name(name)))]


class BaseLookup:
    def __init__(self, base: Dict[str, dict]):
        self.base = base or {}
        self._loose: Dict[str, Dict[str, dict]] = {}   # «ном|бирлик» (тинишсиз) -> {ресурс: ёзуви}
        self._by_unit: Dict[str, List[Tuple[str, str]]] = {}  # бирлик -> [(ном, калит)]
        self._near_memo: Dict[tuple, Optional[dict]] = {}
        for key, e in self.base.items():
            if "|" not in key:
                continue  # эски база — миллий код
            name, unit = key.rsplit("|", 1)
            item = e.get("item") or key
            self._loose.setdefault(f"{_PUNCT.sub('', name)}|{unit}", {})[item] = e
            if key == item:  # эски номлар (alias) ўхшаш қидирувда такрорланмасин
                # калитда бўшлиқ ва белгилар йўқ (name_core) — ўхшашликни асл номи бўйича ҳисоблаймиз
                self._by_unit.setdefault(unit, []).append((fold_name(norm_name(e.get("name") or name)), key))

    def exact(self, code, name, unit) -> Optional[dict]:
        return (self.base.get(norm_code(code)) if code is not None else None) or self.base.get(name_unit_key(name, unit))

    def loose(self, name, unit) -> Tuple[Optional[dict], str]:
        """``(ёзув, изоҳ)``: битта ресурс — ёзув; бир нечта — None ва изоҳ (смета­чи танлайди)."""
        hits = self._loose.get(f"{loose_name(name)}|{unit_key(unit)}") or {}
        if len(hits) == 1:
            e = next(iter(hits.values()))
            return e, _noop("тиниш белгилари бошқача: базада «{0}»").format(e.get('name') or '')
        if hits:
            listed = ", ".join(f"«{e.get('name') or ''}» ({e['price']})" for e in list(hits.values())[:3])
            return None, _noop("базада тиниш белгисиз {0} та ресурс: {1} — қайсиниси, смета­чи танлайди").format(len(hits), listed)
        return None, ""

    def near(self, name, unit, min_score: float) -> Optional[dict]:
        """Энг ўхшаш (``min_score`` 0..1 дан кам эмас), бирлиги ва рақамлари бир хил ресурс: ёзуви + ``score``."""
        if not min_score:
            return None
        u = unit_key(unit)
        query = fold_name(norm_name(name))
        memo = (query, u)
        if memo in self._near_memo:
            return self._near_memo[memo]
        found = None
        names = self._by_unit.get(u) or []
        if names and query:
            nums = numbers_of(name)
            for cand, score, i in process.extract(query, [n for n, _k in names], scorer=fuzz.ratio,
                                                  score_cutoff=min_score * 100, limit=5):
                if numbers_of(cand) == nums:
                    found = {**self.base[names[i][1]], "score": round(score / 100, 3)}
                    break
        self._near_memo[memo] = found
        return found
