"""Ускуна ва кабель базаси (луғат): ресурс номи / коди → L (кабель), M (ускуна) ёки «ускуна эмас» (K).

Django'га боғлиқ эмас: ``resbase.lm`` базадан ёзувларни олиб ``LMBase`` тузади, нархлаш (``core.pipeline``)
L/M таклифи учун шундан сўрайди. Қидириш тартиби: миллий код → аниқ ном → ўхшаш ном (``FUZZY_MIN``% ва
юқори). Натижа фақат ТАКЛИФ — қаторни ўзи кўчирмайди (``Автоматик`` усулда ишончлиси олинади).
"""
from dataclasses import dataclass
from typing import Dict, Iterable, Optional

from rapidfuzz import fuzz, process

from .classify import CABLE, EQUIPMENT, OTHER, _tokenize_ru
from .normalize import norm_code

FUZZY_MIN = 93  # ўхшаш ном: шундан паст бўлса — базада йўқ деб ҳисобланади
KINDS = {"L": CABLE, "M": EQUIPMENT, "K": OTHER}


def lm_key(name) -> str:
    """Ном калити: кичик ҳарф, ё→е, тиниш белгиларсиз («Щит  силовой, ЩРВ-48» → «щит силовой щрв 48»)."""
    return " ".join(_tokenize_ru(str(name or "")))[:400]


@dataclass
class Hit:
    kind: str     # CABLE | EQUIPMENT | OTHER
    reason: str   # смета­чига: «базада: 12 та RES'да ускуна бўлимида»
    strong: bool  # админ ўргатган ёки бир неча манба бир хил — «Автоматик»да ўзи олинади
    how: str      # code | name | fuzzy


class LMBase:
    """``entries``: [{"key", "code", "category" (L/M/K), "reason", "strong"}]."""

    def __init__(self, entries: Iterable[dict]):
        self.by_key: Dict[str, dict] = {}
        self.by_code: Dict[str, dict] = {}
        for e in entries:
            if e.get("category") not in KINDS or not e.get("key"):
                continue
            self.by_key[e["key"]] = e
            code = norm_code(e.get("code") or "")
            if code:
                self.by_code.setdefault(code, e)
        # ўхшаш ном фақат ишончли манбали ёзувлардан («fuzzy»: RES далили бор ёки админ ўргатган)
        self._choices = [k for k, e in self.by_key.items() if e.get("fuzzy", True)]

    def __len__(self):
        return len(self.by_key)

    @staticmethod
    def _hit(e: dict, how: str, score: Optional[float] = None) -> Hit:
        reason = e.get("reason") or "базада"
        if how == "fuzzy":
            reason = f"базадаги «{e.get('name') or e['key']}» ({round(score)}%) — {reason}"
        return Hit(KINDS[e["category"]], reason, bool(e.get("strong")) and how != "fuzzy", how)

    def lookup(self, name, code=None) -> Optional[Hit]:
        c = norm_code(str(code or ""))
        if c and c in self.by_code:
            return self._hit(self.by_code[c], "code")
        key = lm_key(name)
        if not key:
            return None
        if key in self.by_key:
            return self._hit(self.by_key[key], "name")
        if len(key) < 6 or not self._choices:
            return None
        found = process.extractOne(key, self._choices, scorer=fuzz.token_sort_ratio, score_cutoff=FUZZY_MIN)
        if found:
            return self._hit(self.by_key[found[0]], "fuzzy", found[1])
        return None
