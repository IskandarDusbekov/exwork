"""Resurs indeksi va moslashtirish: EXACT / FUZZY / AMBIGUOUS / NOT_FOUND (reja §2.3–2.4).

Moslik rejimida faqat ``difflib.SequenceMatcher`` ishlatiladi.
"""
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from openpyxl.utils import column_index_from_string

from .normalize import norm_name, norm_unit

EXACT = "EXACT"
FUZZY = "FUZZY"
AMBIGUOUS = "AMBIGUOUS"
NOT_FOUND = "NOT_FOUND"


@dataclass
class Match:
    status: str
    unit: str = ""
    name: str = ""
    price: Any = None
    score: Optional[float] = None
    second_name: Optional[str] = None
    second_score: Optional[float] = None


def best_two_matches(key: str, candidates: List[str]):
    best = None
    second = None
    best_score = 0.0
    second_score = 0.0
    for c in candidates:
        score = SequenceMatcher(None, key, c).ratio()
        if score > best_score:
            second, second_score = best, best_score
            best, best_score = c, score
        elif score > second_score:
            second, second_score = c, score
    return best, best_score, second, second_score


class ResourceIndex:
    """``by_unit[birlik][nom] = narx``; takrorlangan nomdan birinchisi olinadi."""

    def __init__(self, by_unit: Dict[str, Dict[str, Any]]):
        self.by_unit = by_unit
        self.units = list(by_unit.keys())
        self._names = {u: list(d.keys()) for u, d in by_unit.items()}

    @classmethod
    def from_sheet(cls, ws, name_col: str = "D", price_col: str = "G") -> "ResourceIndex":
        rn = column_index_from_string(name_col)
        ru = rn + 1
        rp = column_index_from_string(price_col)
        by_unit: Dict[str, Dict[str, Any]] = {}
        for r in range(1, ws.max_row + 1):
            unit = norm_unit(ws.cell(row=r, column=ru).value)
            name = norm_name(ws.cell(row=r, column=rn).value)
            if not unit or not name:
                continue
            d = by_unit.setdefault(unit, {})
            if name not in d:
                d[name] = ws.cell(row=r, column=rp).value
        return cls(by_unit)

    def match(self, local_name: str, local_unit: str, threshold: float, ambiguity_delta: float = 0.01) -> Match:
        """``local_name``/``local_unit`` allaqachon normallashtirilgan bo'lishi kerak."""
        best_fuzzy = None
        best_ambig = None

        search_units = []
        if local_unit and local_unit in self.by_unit:
            search_units.append(local_unit)
        search_units.extend(u for u in self.units if u != local_unit)

        for u in search_units:
            udict = self.by_unit[u]
            if not udict:
                continue
            if local_name in udict:
                return Match(EXACT, u, local_name, udict[local_name], 1.0)

            b, bs, s, ss = best_two_matches(local_name, self._names[u])
            if not b:
                continue
            if bs >= threshold:
                if s and (bs - ss) < ambiguity_delta:
                    if best_ambig is None or bs > best_ambig.score:
                        best_ambig = Match(AMBIGUOUS, u, b, None, bs, s, ss)
                elif best_fuzzy is None or bs > best_fuzzy.score:
                    best_fuzzy = Match(FUZZY, u, b, udict[b], bs, s, ss)

        if best_fuzzy is not None:
            return best_fuzzy
        if best_ambig is not None:
            return best_ambig
        return Match(NOT_FOUND)
