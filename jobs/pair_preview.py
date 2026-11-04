"""Объект саҳифасидаги жуфтлик картаси учун қисқа намуна: локалда нималар бор (номи, бирлиги, миқдори),
танланган ресурсда (RES листи ёки ресурс базаси) уларнинг қанчаси топилди — мос ва топилмаган қаторлардан бир нечтаси."""
import hashlib
import json
import re

from openpyxl import load_workbook

from core.normalize import norm_code, norm_name, norm_unit

SAMPLE = 6  # картада кўрсатиладиган қаторлар сони
_WORK = re.compile(r"^[ЕEЦ]\d", re.I)  # иш нормаси шифри: «Е0101-195-37», «Ц…»


def _rows(batch, key: str):
    """Листдаги ресурс қаторлари: [{code, name, unit, qty|price, key}] — номи ва бирлиги борлари."""
    info = batch.sheet(key)
    f = batch.files[info["file"]]
    c = info.get("columns") or {}
    name_col, unit_col, start, end = c.get("name"), c.get("unit"), c.get("start"), c.get("end")
    if not (name_col and unit_col and start and end):
        return []
    extra = c.get("price") or c.get("qty_total")
    wb = load_workbook(batch.abs(f["path"]), read_only=True, data_only=True)
    try:
        ws = wb[info["sheet"]]
        out, seen = [], set()
        for r, row in enumerate(ws.iter_rows(min_row=start, max_row=end, values_only=True), start=start):
            def v(col):
                return row[col - 1] if col and col - 1 < len(row) else None
            name, unit = v(name_col), v(unit_col)
            raw = str(v(name_col - 1) or "").strip() if name_col > 1 else ""
            if not norm_name(name) or not norm_unit(unit) or _WORK.match(raw):
                continue  # иш нормаси (Е0101-…, Ц…) — ресурс эмас
            k = norm_name(name)
            if k in seen:  # бир ресурс кўп ишда такрорланади — бир марта
                continue
            seen.add(k)
            out.append({"row": r, "code": norm_code(raw), "name": str(name).strip(), "unit": str(unit).strip(),
                        "value": _num(v(extra)), "key": k})
        return out
    finally:
        wb.close()


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 4)
    try:
        return round(float(str(v).replace(" ", "").replace(",", ".")), 4)
    except ValueError:
        return None


def build(batch, local_key: str, res_key: str = "", base_prices=None) -> dict:
    """Локалнинг биринчи қаторлари ва уларнинг нархи танланган ресурсдан (``res_key`` — RES листи,
    бўлмаса ``base_prices`` — ресурс базаси); топилмаганда нарх бўш. Жами: нечтаси топилди, неча фоиз."""
    local = _rows(batch, local_key)
    if res_key:
        by_name = {r["key"]: r.get("value") for r in _rows(batch, res_key)}
        price = lambda r: (True, by_name[r["key"]]) if r["key"] in by_name else (False, None)
        kind = "res"
    elif base_prices is not None:
        price = lambda r: (True, base_prices[r["code"]]["price"]) if r["code"] in base_prices else (False, None)
        kind = "base"
    else:
        price, kind = (lambda r: (False, None)), ""
    found, rows = 0, []
    for r in local:
        ok, value = price(r)
        found += ok
        if len(rows) < SAMPLE:
            rows.append({"code": r["code"], "name": r["name"], "unit": r["unit"], "qty": r.get("value"),
                         "found": ok, "price": value})
    return {"kind": kind, "total": len(local), "found": found,
            "pct": round(found / len(local), 4) if local and kind else None, "rows": rows}


def cached(batch, local_key: str, res_key: str, base_prices, base_tag: str) -> dict:
    """Натижа объект папкасида сақланади (файллар ўзгармайди) — қайта очилганда тез."""
    folder = batch.dir / "preview"
    name = "pair2-" + hashlib.sha1(f"{local_key}|{res_key or 'base:' + base_tag}".encode("utf-8")).hexdigest()[:20]
    path = folder / f"{name}.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    data = build(batch, local_key, res_key, base_prices)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return data
