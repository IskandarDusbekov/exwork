"""Тендер таҳлили учун: смета­чи юклаган (тендердаги) сметанинг ўз нархлари — нархлашдан ОЛДИН ўқилади.

Нархлаш G/H устунларини ўзи ёзади (эски нарх ва сумма устидан), шунинг учун тендер нархлари асл файлдан,
устунлар сарлавҳаси бўйича олинади: «Цена …» — нарх, «Сумма / Стоимость всего» — сумма. Сарлавҳа топилмаса,
лекин сметанинг ўзи стандарт кўринишда бўлса (G — нарх, H — сумма) — шу устунлар."""
from typing import Dict, Optional, Tuple

from .columns import LocalColumns, header_role, num
from .detect import cell_value, header_texts

PRICE_COL, AMOUNT_COL = 7, 8  # стандарт кўриниш: G — нарх, H — сумма
MIN_FILL = 0.3  # сарлавҳасиз G устунини нарх деб олиш учун ресурс қаторларининг шунча улуши сон бўлсин


def money_columns(ws, cols: LocalColumns) -> Tuple[Optional[int], Optional[int]]:
    """(нарх устуни, сумма устуни) — топилмаганлари ``None``."""
    price = amount = None
    if cols.header_row and cols.money:
        bottom = (cols.numbering_row - 1) if cols.numbering_row else cols.header_row
        texts = header_texts(ws, cols.header_row, bottom)
        for c in cols.money:
            role = header_role(texts.get(c, ""))
            if role == "price" and price is None:
                price = c
            elif role == "amount" and amount is None:
                amount = c
    if price is None and amount is None:
        rows = [r for r in range(cols.data_start, cols.data_stop)
                if cell_value(ws, r, cols.name) and cell_value(ws, r, cols.unit)]
        filled = sum(1 for r in rows if num(cell_value(ws, r, PRICE_COL)))
        if rows and filled >= MIN_FILL * len(rows) and PRICE_COL not in (cols.qty_unit, cols.qty_total):
            price = PRICE_COL
            if AMOUNT_COL not in (cols.qty_unit, cols.qty_total):
                amount = AMOUNT_COL
    return price, amount


def tender_prices(ws, cols: Optional[LocalColumns]) -> Dict[int, Tuple[Optional[float], Optional[float]]]:
    """``{қатор: (нарх, сумма)}`` — асл (тендердаги) қийматлар; иккови ҳам йўқ қаторлар киритилмайди.
    ``ws`` — қийматлар варағи (``data_only=True``), ``prepare_local`` дан ОЛДИН."""
    if cols is None:
        return {}
    price_col, amount_col = money_columns(ws, cols)
    if price_col is None and amount_col is None:
        return {}
    out = {}
    for r in range(cols.data_start, cols.data_stop):
        p = num(cell_value(ws, r, price_col)) if price_col else None
        a = num(cell_value(ws, r, amount_col)) if amount_col else None
        if p or a:
            out[r] = (p, a)
    return out
