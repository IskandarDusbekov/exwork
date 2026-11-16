"""Дизайн ранглари (3-босқич): 13 та ранг оиласи ўрнига 5 та маъноли оила.

    ink   — матн, чегара, фон (кулранг)          ← slate, gray, zinc, stone
    brand — асосий амал, ҳавола, «ҳозир» қадам   ← blue, sky, indigo, violet, purple, fuchsia
    ok    — тайёр, нархланди, локал               ← emerald, green, teal, lime, cyan
    warn  — эътибор керак, текширинг              ← amber, yellow, orange
    bad   — хато, ўчириш                         ← rose, red, pink

Ишлатиш:
    python tokens.py tokens     — tokens.css ни palette'дан қайта ёзади (ранг ўзгартирилса — шу ерда)
    python tokens.py convert F… — файллардаги Tailwind hex'ларини var(--…) га алмаштиради (бир марта)

Ранг танлаш — фақат шу файлда (PALETTE). Tailwind класслари (bg-sky-100 ва ҳ.к.) tailwind.config.js орқали
шу оилаларга боғланган, шаблонларни ўзгартириш шарт эмас."""
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
FAMILIES = {
    "ink": ["slate", "gray", "zinc", "stone"],
    "brand": ["blue", "sky", "indigo", "violet", "purple", "fuchsia"],
    "ok": ["emerald", "green", "teal", "lime", "cyan"],
    "warn": ["amber", "yellow", "orange"],
    "bad": ["rose", "red", "pink"],
}
# ҳар оиланинг асоси — биринчиси (slate, blue, emerald, amber, rose)
SHADES = ["50", "100", "200", "300", "400", "500", "600", "700", "800", "900", "950"]


def palette() -> dict:
    path = Path(os.environ.get("TEMP", "/tmp")) / "tw_colors.json"
    return json.loads(path.read_text())


def rgb(hex6: str) -> str:
    h = hex6.lstrip("#")
    return " ".join(str(int(h[i:i + 2], 16)) for i in (0, 2, 4))


def write_tokens():
    pal = palette()
    lines = ["/* 3-босқич: асосий ранглар. Қўлда ўзгартирманг — frontend/tokens.py tokens */", ":root {"]
    for fam, names in FAMILIES.items():
        base = pal[names[0]]
        lines.append("  " + " ".join(f"--{fam}-{s}: {rgb(base[s])};" for s in SHADES))
    lines.append("}")
    (HERE / "tokens.css").write_text("\n".join(lines) + "\n", encoding="utf-8")


def hex_map() -> dict:
    pal = palette()
    out = {}
    for fam, names in FAMILIES.items():
        for name in names:
            for s in SHADES:
                out.setdefault(pal[name][s].lower(), f"rgb(var(--{fam}-{s}))")
    return out


HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
KEEP = re.compile(r"const COLOR\b|\bc: \"#|data-keep-color")  # тоифа ранглари (устун вазифалари) — алоҳида


NEAR = 14  # Tailwind'да йўқ, лекин деярли бир хил ранг (#e3e8f0 ≈ ink-200) — энг яқин токенга


def near_map(m: dict) -> callable:
    toks = [(tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)), v) for h, v in m.items()
            if v.split("--")[1].split("-")[0] in FAMILIES and h == f"#{rgb_hex(v, m)}"]

    def find(h: str):
        c = tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))
        if c[0] == c[1] == c[2]:
            return None  # соф кулранг — Excel кўриниши (катак чегараси, сарлавҳа), токен эмас
        best = min(toks, key=lambda t: sum((a - b) ** 2 for a, b in zip(t[0], c)))
        return best[1] if sum((a - b) ** 2 for a, b in zip(best[0], c)) ** .5 <= NEAR else None
    return find


def rgb_hex(v: str, m: dict) -> str:
    """Токен номидан унинг асосий hex'и (оиланинг биринчи палитраси)."""
    fam, shade = v.split("--")[1].rstrip("))").rsplit("-", 1)
    return BASE_HEX[(fam, shade)]


BASE_HEX: dict = {}


def convert(files):
    m = hex_map()
    pal = palette()
    for fam, names in FAMILIES.items():
        for s in SHADES:
            BASE_HEX[(fam, s)] = pal[names[0]][s].lower().lstrip("#")
    near = near_map(m)
    for f in files:
        p = Path(f)
        text = p.read_text(encoding="utf-8")
        out, n = [], 0
        for line in text.splitlines(keepends=True):
            if KEEP.search(line):
                out.append(line)
                continue
            def sub(mt):
                nonlocal n
                v = m.get(mt.group(0).lower()) or near(mt.group(0).lower())
                if v:
                    n += 1
                    return v
                return mt.group(0)
            out.append(HEX.sub(sub, line))
        if n:
            p.write_text("".join(out), encoding="utf-8", newline="")
        print(f"{n:4} {f}")


if __name__ == "__main__":
    if sys.argv[1] == "tokens":
        write_tokens()
    elif sys.argv[1] == "convert":
        convert(sys.argv[2:])
