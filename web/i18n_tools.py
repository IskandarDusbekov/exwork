# i18n: ok-file — ички маълумот / тил номлари (ҳар бири ўз ёзувида)
"""Таржима воситалари (GNU gettext ва ташқи кутубхонасиз): матнларни йиғиш, .po ўқиш/ёзиш, .mo ясаш,
«қолиб кетган» кирилл матнни топиш. Буйруқ: ``manage.py i18n`` (jobs/management/commands/i18n.py).

Манба тил — кирилл ўзбекча: msgid ўзи кирилл матн. Таржималар:
  locale/ru/LC_MESSAGES/django.po, djangojs.po      — рус (қўлда)
  locale/uz_Latn/LC_MESSAGES/django.po, djangojs.po — лотин (транслитерация, ``# қўлда`` изоҳлилари сақланади)
"""
from __future__ import annotations

import ast
import io
import re
import struct
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

ROOT = Path(settings.BASE_DIR)
# админ панел (panel/) таржима қилинмайди — доим кирилл
APPS = ["accounts", "jobs", "texnazor", "support", "news", "web", "core", "billing"]
SKIP_DIRS = {"migrations", "tests", "__pycache__", "management", "node_modules"}
JS_DIR = ROOT / "web" / "static" / "js"
JS_SKIP = {"tizim.js"}  # панел «Тизим» саҳифаси
LOCALES = ["ru", "uz_Latn"]

PY_FUNCS = {
    "_": "s", "_l": "s", "gettext": "s", "gettext_lazy": "s", "gettext_noop": "s", "_noop": "s",
    "pgettext": "cs", "pgettext_lazy": "cs",
    "ngettext": "sp", "ngettext_lazy": "sp",
    "npgettext": "csp", "npgettext_lazy": "csp",
}

PLURAL = {
    "ru": ("3", "(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2)"),
    "uz_Latn": ("2", "(n != 1)"),
}


# ----------------------------------------------------------------------------- .po
@dataclass
class Entry:
    msgid: str
    msgctxt: str | None = None
    msgid_plural: str | None = None
    msgstr: str = ""
    msgstr_plural: dict = field(default_factory=dict)
    refs: list = field(default_factory=list)
    comments: list = field(default_factory=list)   # «# …» таржимон изоҳи
    extracted: list = field(default_factory=list)  # «#. …» коддаги изоҳ
    flags: set = field(default_factory=set)
    obsolete: bool = False

    @property
    def key(self):
        return (self.msgctxt, self.msgid)

    @property
    def translated(self) -> bool:
        if self.msgid_plural:
            return bool(self.msgstr_plural) and all(self.msgstr_plural.values())
        return bool(self.msgstr)


def _unq(s: str) -> str:
    s = s.strip()
    assert s.startswith('"') and s.endswith('"'), s
    body = s[1:-1]
    return re.sub(r'\\(.)', lambda m: {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"'}.get(m.group(1), m.group(1)), body)


def _q(s: str) -> str:
    esc = s.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t").replace("\r", "\\r")
    if "\n" in s.rstrip("\n") or len(esc) > 76:
        parts = esc.split("\n")
        lines = [p + "\\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
        return '""\n' + "\n".join(f'"{ln}"' for ln in lines)
    return '"' + esc.replace("\n", "\\n") + '"'


def read_po(path: Path) -> tuple[str, list[Entry]]:
    """(header, entries). header — msgid "" нинг msgstr'и. Эскирган («#~») ёзувлар ташлаб юборилади."""
    if not path.exists():
        return "", []
    entries, header = [], ""
    cur = None          # ёзилаётган ёзув
    seen_id = False     # жорий ёзувда msgid бўлдими (кейинги изоҳ/msgctxt/msgid — янги ёзув)
    last = None         # давом этаётган майдон

    def flush():
        nonlocal cur, header, seen_id
        if cur is not None and seen_id:
            if cur.msgid == "" and cur.msgctxt is None:
                header = cur.msgstr
            elif not cur.obsolete:
                entries.append(cur)
        if cur is None or seen_id:
            cur = Entry(msgid="")
        seen_id = False

    cur = Entry(msgid="")
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#~"):
            if seen_id:
                flush()
            cur.obsolete = True
            continue
        if not line.strip():
            continue
        if line.startswith("#"):
            if seen_id:
                flush()
            tag, rest = line[:2], line[2:].strip()
            if tag == "#:":
                cur.refs += rest.split()
            elif tag == "#,":
                cur.flags |= {f.strip() for f in rest.split(",") if f.strip()}
            elif tag == "#.":
                cur.extracted.append(rest)
            elif tag != "#|":
                cur.comments.append(line[1:].strip())
            continue
        m = re.match(r'(msgctxt|msgid_plural|msgid|msgstr(?:\[(\d+)\])?)\s+(".*")$', line)
        if m:
            kw, idx, val = m.group(1), m.group(2), _unq(m.group(3))
            if kw in ("msgctxt", "msgid") and seen_id and last not in ("msgctxt",) and not (kw == "msgid" and last == "msgctxt"):
                flush()
            if kw == "msgctxt":
                cur.msgctxt = val
            elif kw == "msgid":
                cur.msgid, seen_id = val, True
            elif kw == "msgid_plural":
                cur.msgid_plural = val
            elif idx is not None:
                cur.msgstr_plural[int(idx)] = val
                kw = f"msgstr[{idx}]"
            else:
                cur.msgstr = val
            last = kw
        elif line.startswith('"'):
            val = _unq(line)
            if last == "msgctxt":
                cur.msgctxt += val
            elif last == "msgid":
                cur.msgid += val
            elif last == "msgid_plural":
                cur.msgid_plural += val
            elif last == "msgstr":
                cur.msgstr += val
            elif last and last.startswith("msgstr["):
                cur.msgstr_plural[int(last[7:-1])] += val
    flush()
    return header, entries


def write_po(path: Path, header: str, entries: list[Entry]):
    path.parent.mkdir(parents=True, exist_ok=True)
    out = io.StringIO()
    out.write('msgid ""\nmsgstr ' + _q(header) + "\n\n")
    for e in entries:
        for c in e.comments:
            out.write(f"# {c}\n")
        for c in e.extracted:
            out.write(f"#. {c}\n")
        if e.refs:
            line = "#:"
            for r in e.refs:
                if len(line) + len(r) > 78:
                    out.write(line + "\n"); line = "#:"
                line += " " + r
            out.write(line + "\n")
        if e.flags:
            out.write("#, " + ", ".join(sorted(e.flags)) + "\n")
        if e.msgctxt is not None:
            out.write("msgctxt " + _q(e.msgctxt) + "\n")
        out.write("msgid " + _q(e.msgid) + "\n")
        if e.msgid_plural is not None:
            out.write("msgid_plural " + _q(e.msgid_plural) + "\n")
            for i in sorted(e.msgstr_plural):
                out.write(f"msgstr[{i}] " + _q(e.msgstr_plural[i]) + "\n")
        else:
            out.write("msgstr " + _q(e.msgstr) + "\n")
        out.write("\n")
    path.write_text(out.getvalue(), encoding="utf-8", newline="\n")


def header_for(locale: str) -> str:
    n, expr = PLURAL[locale]
    lang = {"ru": "ru", "uz_Latn": "uz_Latn"}[locale]
    return ("Project-Id-Version: mhisobot\n"
            f"Language: {lang}\n"
            "MIME-Version: 1.0\n"
            "Content-Type: text/plain; charset=UTF-8\n"
            "Content-Transfer-Encoding: 8bit\n"
            f"Plural-Forms: nplurals={n}; plural={expr};\n")


def write_mo(path: Path, header: str, entries: list[Entry]):
    """GNU .mo (little-endian, хеш жадвалсиз). Таржимаси йўқ ва «fuzzy»лари ёзилмайди."""
    pairs = {b"": header.encode("utf-8")}
    for e in entries:
        if not e.translated or "fuzzy" in e.flags:
            continue
        k = e.msgid if e.msgctxt is None else f"{e.msgctxt}\x04{e.msgid}"
        if e.msgid_plural is not None:
            k += "\x00" + e.msgid_plural
            v = "\x00".join(e.msgstr_plural[i] for i in sorted(e.msgstr_plural))
        else:
            v = e.msgstr
        pairs[k.encode("utf-8")] = v.encode("utf-8")
    keys = sorted(pairs)
    n = len(keys)
    ko, vo = 7 * 4, 7 * 4 + n * 8
    data_start = 7 * 4 + n * 16
    kdata, vdata, ktab, vtab = b"", b"", [], []
    for k in keys:
        ktab.append((len(k), data_start + len(kdata))); kdata += k + b"\x00"
    vstart = data_start + len(kdata)
    for k in keys:
        v = pairs[k]
        vtab.append((len(v), vstart + len(vdata))); vdata += v + b"\x00"
    out = struct.pack("<7I", 0x950412de, 0, n, ko, vo, 0, 0)
    out += b"".join(struct.pack("<2I", *t) for t in ktab) + b"".join(struct.pack("<2I", *t) for t in vtab)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out + kdata + vdata)


# ----------------------------------------------------------------------------- йиғиш
@dataclass
class Msg:
    msgid: str
    msgctxt: str | None = None
    msgid_plural: str | None = None
    ref: str = ""
    note: str = ""


def _strings_from_tokens(src: str, ref_path: str, funcs: dict) -> list[Msg]:
    """tokenize орқали: f(«…», …) чақириқларидаги матн литераллари (Python ва templatize натижаси учун)."""
    out = []
    try:
        toks = [t for t in tokenize.generate_tokens(io.StringIO(src).readline)
                if t.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT)]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return out
    i = 0
    while i < len(toks) - 2:
        t = toks[i]
        if t.type == tokenize.NAME and t.string in funcs and toks[i + 1].string == "(" and \
                (i == 0 or toks[i - 1].string not in (".", "def")):
            kinds = funcs[t.string]
            args, j, cur, ok = [], i + 2, None, True
            while j < len(toks) and len(args) < len(kinds):
                tk = toks[j]
                if tk.type == tokenize.STRING:
                    if tk.string.lstrip("rRbBuU")[:1] not in "'\"" or re.match(r"^[fF]|^[rR][fF]", tk.string):
                        ok = False; break
                    try:
                        val = ast.literal_eval(tk.string)
                    except Exception:
                        ok = False; break
                    cur = (cur or "") + val
                elif tk.string == "," and cur is not None:
                    args.append(cur); cur = None
                elif tk.string == ")" and cur is not None:
                    args.append(cur); cur = None; break
                else:
                    ok = False; break
                j += 1
            if ok and cur is not None:
                args.append(cur)
            if ok and len(args) >= len(kinds):
                m = Msg(msgid="", ref=f"{ref_path}:{t.start[0]}")
                for kind, val in zip(kinds, args):
                    if kind == "c":
                        m.msgctxt = val
                    elif kind == "s":
                        m.msgid = val
                    else:
                        m.msgid_plural = val
                if m.msgid:
                    out.append(m)
            i = j
        i += 1
    return out


def extract_python(path: Path) -> list[Msg]:
    return _strings_from_tokens(path.read_text(encoding="utf-8"), _rel(path), PY_FUNCS)


def extract_template(path: Path) -> list[Msg]:
    """Ўзи ўраладиган матн (web/tr.py) + қўлда ёзилган {% translate %} / {% blocktranslate %} / _("…")."""
    from django.utils.translation.template import templatize

    from web.tr import collect
    if path.name.endswith("_css.html"):  # фақат CSS бўлаги — web/tr.py Loader ҳам ўрамайди
        return []
    src = path.read_text(encoding="utf-8")
    rel = _rel(path)
    out = [Msg(msgid=m, ref=f"{rel}:{ln}", note=mode) for m, ln, mode in collect(src)]
    if re.search(r"{%\s*(translate|trans|blocktranslate|blocktrans)\b|_\(", src):
        py = templatize(src, origin=str(path))
        funcs = {"gettext": "s", "pgettext": "cs", "ngettext": "sp", "npgettext": "csp"}
        out += _strings_from_tokens(py, rel, funcs)
    return out


_JS_STR = r"""("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')"""
_JS_CALL = re.compile(r"\b(gettext|ngettext|pgettext)\(\s*" + _JS_STR + r"(?:\s*,\s*" + _JS_STR + r")?")


def _js_unq(s: str) -> str:
    body = s[1:-1]
    return re.sub(r"\\(u[0-9a-fA-F]{4}|.)", lambda m: chr(int(m.group(1)[1:], 16)) if m.group(1)[0] == "u" and len(m.group(1)) == 5
                  else {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), body)


def extract_js(path: Path) -> list[Msg]:
    src = path.read_text(encoding="utf-8")
    out = []
    for m in _JS_CALL.finditer(src):
        fn, a, b = m.group(1), m.group(2), m.group(3)
        line = src.count("\n", 0, m.start()) + 1
        msg = Msg(msgid=_js_unq(a), ref=f"{_rel(path)}:{line}")
        if fn == "ngettext" and b:
            msg.msgid_plural = _js_unq(b)
        elif fn == "pgettext" and b:
            msg.msgctxt, msg.msgid = msg.msgid, _js_unq(b)
        out.append(msg)
    return out


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def iter_files(kind: str):
    if kind == "js":
        for p in sorted(JS_DIR.glob("*.js")):
            if p.name not in JS_SKIP and not p.name.endswith(".min.js"):
                yield p
        return
    for app in APPS:
        base = ROOT / app
        for p in sorted(base.rglob("*.py" if kind == "py" else "*.html")):
            if SKIP_DIRS & set(p.relative_to(base).parts[:-1]) or p.name.startswith("test"):
                continue
            yield p
        if kind == "html":
            for p in sorted(base.rglob("*.txt")):
                if "templates" in p.parts:
                    yield p


def collect(domain: str) -> list[Msg]:
    msgs = []
    if domain == "djangojs":
        for p in iter_files("js"):
            msgs += extract_js(p)
    else:
        for p in iter_files("py"):
            msgs += extract_python(p)
        for p in iter_files("html"):
            msgs += extract_template(p)
    return msgs


def merge(domain: str, locale: str, fill=None) -> tuple[int, int]:
    """Коддаги матнларни .po га қўшади (эскилари сақланади, ишлатилмаётганлари ўчади).
    ``fill(entry)`` — бўш таржимани тўлдириш (лотин учун). (жами, таржимасиз) қайтаради."""
    path = ROOT / "locale" / locale / "LC_MESSAGES" / f"{domain}.po"
    _, old = read_po(path)
    old_by = {e.key: e for e in old}
    nplurals = int(PLURAL[locale][0])
    merged: dict = {}
    for m in collect(domain):
        k = (m.msgctxt, m.msgid)
        e = merged.get(k)
        if e is None:
            prev = old_by.get(k)
            e = Entry(msgid=m.msgid, msgctxt=m.msgctxt, msgid_plural=m.msgid_plural)
            if prev:
                e.msgstr, e.comments, e.flags = prev.msgstr, prev.comments, prev.flags - {"python-format"}
                e.msgstr_plural = dict(prev.msgstr_plural)
            if e.msgid_plural is not None:
                for i in range(nplurals):
                    e.msgstr_plural.setdefault(i, "")
                e.msgstr_plural = {i: e.msgstr_plural[i] for i in range(nplurals)}
            if re.search(r"%\(\w+\)[sdif]|%[sd]", m.msgid):
                e.flags.add("python-format")
            merged[k] = e
        if m.ref not in e.refs:
            e.refs.append(m.ref)
    entries = list(merged.values())
    if fill:
        for e in entries:
            fill(e)
    write_po(path, header_for(locale), entries)
    write_mo(path.with_suffix(".mo"), header_for(locale), entries)
    return len(entries), sum(1 for e in entries if not e.translated)


def compile_all():
    for locale in LOCALES:
        for domain in ("django", "djangojs"):
            path = ROOT / "locale" / locale / "LC_MESSAGES" / f"{domain}.po"
            if path.exists():
                header, entries = read_po(path)
                write_mo(path.with_suffix(".mo"), header or header_for(locale), entries)


# ----------------------------------------------------------------------------- «қолиб кетган» матн
_CYR = re.compile(r"[Ѐ-ӿ]")
_TRANS_ARG = re.compile(r"""_\(\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')\s*\)""")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
OK_MARK = "i18n: ok"  # шу белгили қатор текширилмайди (масалан, рус смета сарлавҳасини таниш учун)


def leftovers_template(path: Path) -> list[tuple[int, str]]:
    """Таржимага ўралмаган кирилл матн: шаблондаги матн, {{ … }} ва {% … %} ичидаги литераллар."""
    from django.template.base import Lexer, TokenType

    from web.tr import Transformer
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    t = Transformer(src)
    t.run()
    # 1) матн қисмида ўралмай қолгани (тег ичида, CSS, JS коди — изоҳлар ҳисобга кирмайди)
    out = [(ln, f"[{kind}] {txt}") for ln, (kind, txt) in sorted(t.notes.items())
           if OK_MARK not in (lines[ln - 1] if 0 < ln <= len(lines) else "")]
    # 2) {{ … }} ва {% … %} ичидаги кирилл литераллар
    in_bt = in_comment = False
    for tok in Lexer(src).tokenize():
        c = tok.contents
        if tok.token_type == TokenType.COMMENT:
            continue
        if tok.token_type == TokenType.BLOCK:
            bits = c.split(None, 1)
            name = bits[0] if bits else ""
            if name == "comment":
                in_comment = True
                continue
            if name == "endcomment":
                in_comment = False
                continue
            if in_comment or name in ("endblocktranslate", "endblocktrans", "plural"):
                if name.startswith("endblock"):
                    in_bt = False
                continue
            if name in ("blocktranslate", "blocktrans"):
                in_bt = True
            if name in ("tr", "tra", "trj", "trc"):
                continue
            if name in ("translate", "trans") and len(bits) > 1:
                c = re.sub(r"""^("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')""", "", bits[1].strip())
            c = _TRANS_ARG.sub("", c)
        elif tok.token_type == TokenType.VAR:
            if in_comment:
                continue
            c = _TRANS_ARG.sub("", c)
        else:  # матн — 1-бандда (Transformer) текширилди
            continue
        if _CYR.search(c):
            base = tok.lineno or 0
            for off, ln in enumerate(c.split("\n")):
                if _CYR.search(ln):
                    n = base + off
                    src_line = lines[n - 1] if 0 < n <= len(lines) else ""
                    if OK_MARK not in src_line:
                        out.append((n, ln.strip()[:120]))
    return out


def leftovers_python(path: Path) -> list[tuple[int, str]]:
    """Кирилл матнли литераллар (docstring ва gettext чақириқлари ичидагилардан ташқари).
    Файл бошида ``# i18n: ok-file`` — бутун файл ўтказиб юборилади (фақат ички маълумот)."""
    src = path.read_text(encoding="utf-8")
    if "i18n: ok-file" in src[:3000]:
        return []
    lines = src.splitlines()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                skip.add(id(first.value))
        if isinstance(node, ast.Call):
            f = node.func
            fn = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if fn in PY_FUNCS:
                for a in ast.walk(node):
                    skip.add(id(a))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip \
                and _CYR.search(node.value):
            ln = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
            if OK_MARK in ln:
                continue
            out.append((node.lineno, node.value.strip().replace("\n", " ")[:120]))
    return sorted(out)


_JS_CYR_STR = re.compile(r"""("(?:[^"\\\n]|\\.)*[Ѐ-ӿ](?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*[Ѐ-ӿ](?:[^'\\\n]|\\.)*'|`[^`]*[Ѐ-ӿ][^`]*`)""")


def leftovers_js(path: Path) -> list[tuple[int, str]]:
    out = []
    in_block = False  # /* … */ изоҳ ичидами
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if in_block:
            if "*/" in line:
                in_block = False
            continue
        if stripped.startswith("/*") and "*/" not in stripped:
            in_block = True
            continue
        if stripped.startswith(("//", "*", "/*")) or OK_MARK in line:
            continue
        code = re.sub(r"\s//\s.*$", "", line)
        for m in _JS_CYR_STR.finditer(code):
            before = code[:m.start()]
            if re.search(r"\b(gettext|ngettext|pgettext)\(\s*$", before) or \
                    re.search(r"\b(ngettext|pgettext)\(\s*(\"[^\"]*\"|'[^']*')\s*,\s*$", before):
                continue
            out.append((i, m.group(0)[:120]))
    return out


def leftovers() -> dict:
    res = {}
    for kind, fn in (("html", leftovers_template), ("py", leftovers_python), ("js", leftovers_js)):
        for p in iter_files(kind):
            if r := fn(p):
                res[_rel(p)] = r
    return res
