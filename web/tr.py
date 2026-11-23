# i18n: ok-file — ички маълумот / тил номлари (ҳар бири ўз ёзувида)
"""Шаблонлардаги кирилл матнни ўзи таржимага ўраш (дискдаги шаблон ўзгармайди).

Юкловчи (``Loader``) шаблон манбасини компиляциядан олдин ўқиб, кирилл матн бўлакларини уч хил тегга ўрайди:

* ``{% tr "…" "…" %}``  — HTML матн (``<b>Тайёр</b>`` ичидаги «Тайёр»);
* ``{% tra "…" "…" %}`` — атрибут қиймати (title, placeholder, aria-label, data-*…);
* ``{% trj "…" "…" %}`` — ``<script>`` ичидаги JS сатри («…», '…' ёки `…`);
* ``{% trc "…" "…" %}`` — ``<style>`` ичидаги CSS сатри (``content: "…"``).

Биринчи литерал — msgid (таржима калити), иккинчиси — асл матн (асосий тилда айнан шу ёзилади).
Бўлак ичидаги ``{{ … }}`` ўзгарувчилар msgid'да ``{0}``, ``{1}`` бўлиб туради: «{0} та смета» → «смет: {0}».
Ўраб бўлмайдиганлари (``{% … "кирилл" %}`` ва ``{{ x|default:"…" }}`` литераллари) ``manage.py i18n check``да
чиқади — уларни қўлда ``_("…")`` қилинг. ``{% blocktranslate %}``, ``{% comment %}``, ``{% verbatim %}``,
``<style>``, HTML ва JS изоҳлари тегилмайди.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from django.template import Library, Node, TemplateSyntaxError
from django.template.base import render_value_in_context, tag_re
from django.template.loaders.app_directories import Loader as AppLoader
from django.utils.html import escapejs
from django.utils.safestring import mark_safe
from django.utils.translation import get_language, gettext

CYR = re.compile(r"[\u0400-\u04ff]")
JS_BLOCKS = {"guide"}  # {% block guide %} — base.html'да <script> ичида (JS объект)
SOURCE_LANG = "uz-cyrl"
NUL = chr(0)  # асл матн бўлакларини ажратувчи
BUF_MODE = {"data": "t", "attr": "a", "jsstr": "j"}


@dataclass
class Run:
    """Битта таржима бўлаги."""
    mode: str     # "t" — матн, "a" — атрибут, "j" — JS сатри
    parts: list   # [("t", "матн") | ("v", "{{ x }}")]
    line: int


_JS_ESC = re.compile(r"\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", re.S)


def js_unescape(s: str) -> str:
    def rep(m):
        e = m.group(1)
        if e[0] in "ux" and len(e) in (3, 5):
            return chr(int(e[1:], 16))
        return {"n": "\n", "t": "\t", "r": "\r", "\n": ""}.get(e, e)
    return _JS_ESC.sub(rep, s)


def _edges(run: Run) -> tuple[str, str]:
    """Бўлак бошидаги ва охиридаги бўшлиқ (тегдан ташқарида қолади)."""
    lead = trail = ""
    if run.parts and run.parts[0][0] == "t":
        lead = re.match(r"\s*", run.parts[0][1]).group(0)
    if run.parts and run.parts[-1][0] == "t":
        last = run.parts[-1][1]
        if len(run.parts) == 1:
            last = last[len(lead):]
        trail = re.search(r"\s*$", last).group(0)
    return lead, trail


def _raw_texts(run: Run, lead: str, trail: str) -> list[str]:
    """Ўзгарувчилар орасидаги асл матн бўлаклари (бош/охирги бўшлиқсиз)."""
    texts, cur, last = [], "", len(run.parts) - 1
    for idx, (k, v) in enumerate(run.parts):
        if k == "v":
            texts.append(cur)
            cur = ""
            continue
        if idx == 0 and lead:
            v = v[len(lead):]
        if idx == last and trail:
            v = v[: len(v) - len(trail)]
        cur += v
    texts.append(cur)
    return texts


def msgid_of(run: Run):
    """(msgid, ўзгарувчилар, бош бўшлиқ, охирги бўшлиқ) ёки None (кирилл йўқ)."""
    if not any(CYR.search(v) for k, v in run.parts if k == "t"):
        return None
    lead, trail = _edges(run)
    texts = _raw_texts(run, lead, trail)
    args = [v[2:-2].strip() for k, v in run.parts if k == "v"]
    if run.mode == "j":
        texts = [js_unescape(t) for t in texts]
    msgid = texts[0] + "".join("{%d}%s" % (i, t) for i, t in enumerate(texts[1:]))
    if run.mode != "j":
        msgid = re.sub(r"\s+", " ", msgid)
    return msgid, args, lead, trail


def css_escape(s: str) -> str:
    """CSS сатри учун: \\ → \\\\, қўштирноқлар ва янги сатр (\\A)."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'").replace("\n", "\\A ")


def _q(s: str) -> str:
    """Тег ичидаги литерал: {% … %} бир сатрда бўлиши шарт (\\n, \\r, \\0 билан)."""
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace(NUL, "\\0")
    return '"' + s + '"'


def _unq(s: str) -> str:
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        raise TemplateSyntaxError("tr: қўштирноқли матн керак")
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "r": "\r", "0": NUL}.get(m.group(1), m.group(1)), s[1:-1])


class Transformer:
    """Шаблон манбасини бўлакларга ажратиб, кирилл бўлакларни тегларга ўрайди (ва йиғади)."""

    def __init__(self, src: str):
        self.src = src
        self.out: list[str] = []
        self.runs: list[Run] = []
        # data | tag | attr | script | jsstr | jsline | jsblock | style | hcomment | skip
        self.mode = "data"
        self.quote = ""
        self.tagname = ""
        self.tag_closing = False
        self.skip_end = ""
        self.skip_restore = "data"
        self.blocks: list = []
        self.buf: list = []
        self.buf_mode = ""
        self.buf_line = 0
        self.line = 1
        self.js_last = "("  # скриптдаги охирги муҳим белги (regex литерални бўлувдан ажратиш учун)
        self.notes: dict = {}  # ўралмай қолган кирилл: {сатр: (жой, матн)} — ``i18n check`` учун
        self._piece = ""

    def _note(self, i: int, kind: str, text: str):
        ln = self.line + self._piece.count("\n", 0, i)
        self.notes.setdefault(ln, (kind, text.strip()[:120]))

    # ------------------------------------------------------------------ бўлак
    def _start(self):
        self.buf, self.buf_mode, self.buf_line = [], BUF_MODE[self.mode], self.line

    def _add_text(self, s: str):
        if not s:
            return
        if not self.buf_mode:
            self._start()
        if self.buf and self.buf[-1][0] == "t":
            self.buf[-1] = ("t", self.buf[-1][1] + s)
        else:
            self.buf.append(("t", s))

    def _add_var(self, piece: str):
        if not self.buf_mode:
            self._start()
        self.buf.append(("v", piece))

    def _flush(self):
        if not self.buf_mode:
            return
        run = Run(self.buf_mode, self.buf, self.buf_line)
        self.buf, self.buf_mode = [], ""
        texts = "".join(v for k, v in run.parts if k == "t")
        if not CYR.search(texts):
            self.out.append("".join(v for _, v in run.parts))
        elif re.search(r"\{\d+\}", texts) or (("{" in texts or "}" in texts) and run.mode != "t"):
            # матнда ўзи «{0}» ёки фигурали қавс бор — ўзгарувчиларни ўз жойида қолдириб, бўлакларни алоҳида ўраймиз
            for k, v in run.parts:
                if k == "v":
                    self.out.append(v)
                else:
                    self._emit(Run(run.mode, [("t", v)], run.line))
        else:
            self._emit(run)

    def _emit(self, run: Run):
        res = msgid_of(run)
        if res is None:
            self.out.append("".join(v for _, v in run.parts))
            return
        msgid, args, lead, trail = res
        self.runs.append(run)
        tag = {"t": "tr", "a": "tra", "j": "trj", "c": "trc"}[run.mode]
        raw = NUL.join(_raw_texts(run, lead, trail))
        self.out.append(lead + "{% " + tag + " " + _q(msgid) + " " + _q(raw)
                        + "".join(" " + a for a in args) + " %}" + trail)

    # ------------------------------------------------------------------ асосий цикл
    def run(self) -> str:
        for piece in tag_re.split(self.src):
            if not piece:
                continue
            if piece.startswith("{%") and piece.endswith("%}"):
                self._block(piece)
            elif piece.startswith("{{") and piece.endswith("}}"):
                if self.mode in BUF_MODE:
                    self._add_var(piece)
                else:
                    self.out.append(piece)
            elif piece.startswith("{#") and piece.endswith("#}"):
                self._flush()
                self.out.append(piece)
            else:
                self._text(piece)
            self.line += piece.count("\n")
        self._flush()
        return "".join(self.out)

    def _block(self, piece: str):
        bits = piece[2:-2].split()
        name = bits[0] if bits else ""
        if self.mode == "skip":
            self.out.append(piece)
            if name == self.skip_end:
                self.mode = self.skip_restore
            return
        self._flush()
        self.out.append(piece)
        if name in ("comment", "verbatim", "blocktranslate", "blocktrans"):
            self.skip_restore, self.mode = self.mode, "skip"
            self.skip_end = {"comment": "endcomment", "verbatim": "endverbatim"}.get(name, "end" + name)
        elif name == "block":
            if len(bits) > 1 and bits[1] in JS_BLOCKS:
                self.blocks.append(self.mode)
                self.mode, self.js_last = "script", "("
            else:
                self.blocks.append(None)
        elif name == "endblock" and self.blocks:
            prev = self.blocks.pop()
            if prev is not None:
                self.mode = prev

    def _text(self, s: str):
        self._piece = s
        i, n, out = 0, len(s), self.out
        while i < n:
            m = self.mode
            if m == "skip":
                out.append(s[i:])
                return
            if m == "data":
                j = i
                while j < n and not (s[j] == "<" and j + 1 < n and (s[j + 1].isalpha() or s[j + 1] in "/!")):
                    j += 1
                self._add_text(s[i:j])
                if j >= n:
                    return
                self._flush()
                if s.startswith("<!--", j):
                    self.mode = "hcomment"
                    out.append("<!--")
                    i = j + 4
                    continue
                self.mode, self.tagname, self.tag_closing = "tag", "", s[j + 1] == "/"
                k = j + 1 + (1 if self.tag_closing else 0)
                mm = re.match(r"[A-Za-z][\w:-]*", s[k:])
                end = k + (mm.end() if mm else 0)
                self.tagname = mm.group(0).lower() if mm else ""
                out.append(s[j:end])
                i = end
                continue
            if m == "hcomment":
                j = s.find("-->", i)
                if j < 0:
                    out.append(s[i:])
                    return
                out.append(s[i:j + 3])
                i, self.mode = j + 3, "data"
                continue
            if m == "tag":
                c = s[i]
                if CYR.match(c):
                    self._note(i, "тег", s[i:s.find(">", i) if ">" in s[i:] else n])
                out.append(c)
                i += 1
                if c in "\"'":
                    self.quote, self.mode = c, "attr"
                elif c == ">":
                    if not self.tag_closing and self.tagname == "script":
                        self.mode, self.js_last = "script", "("
                    elif not self.tag_closing and self.tagname == "style":
                        self.mode = "style"
                    else:
                        self.mode = "data"
                continue
            if m == "attr":
                j = s.find(self.quote, i)
                if j < 0:
                    self._add_text(s[i:])
                    return
                self._add_text(s[i:j])
                self._flush()
                out.append(self.quote)
                i, self.mode = j + 1, "tag"
                continue
            if m == "style":
                j = s.lower().find("</style", i)
                end = n if j < 0 else j
                self._style(s, i, end)
                if j < 0:
                    return
                i, self.mode = j, "data"
                continue
            if m == "script":
                i = self._script(s, i)
                continue
            if m == "jsstr":
                j = i
                while j < n:
                    c = s[j]
                    if c == "\\" and j + 1 < n:
                        j += 2
                        continue
                    if c == self.quote or (c == "\n" and self.quote != "`"):
                        break
                    j += 1
                self._add_text(s[i:j])
                if j >= n:
                    return
                self._flush()
                out.append(s[j])
                i, self.mode, self.js_last = j + 1, "script", "a"
                continue
            if m == "jsline":
                j = s.find("\n", i)
                if j < 0:
                    out.append(s[i:])
                    return
                out.append(s[i:j])
                i, self.mode = j, "script"
                continue
            if m == "jsblock":
                j = s.find("*/", i)
                if j < 0:
                    out.append(s[i:])
                    return
                out.append(s[i:j + 2])
                i, self.mode = j + 2, "script"
                continue
            raise AssertionError(m)

    _CSS_TOK = re.compile(r"""/\*.*?(?:\*/|$)|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'""", re.S)

    def _style(self, s: str, i: int, end: int):
        """CSS: ``content: "…"`` каби кирилл сатрлар ўралади; изоҳлар тегилмайди, қолган кирилл — эслатма."""
        pos = i
        for mm in self._CSS_TOK.finditer(s, i, end):
            gap = s[pos:mm.start()]
            if CYR.search(gap):
                self._note(pos + CYR.search(gap).start(), "CSS", gap)
            self.out.append(gap)
            tok = mm.group(0)
            if tok[0] in "\"'" and CYR.search(tok):
                run = Run("c", [("t", tok[1:-1])], self.line + s.count("\n", 0, mm.start()))
                self.out.append(tok[0])
                self._emit(run)
                self.out.append(tok[-1])
            else:
                self.out.append(tok)
            pos = mm.end()
        gap = s[pos:end]
        if CYR.search(gap):
            self._note(pos + CYR.search(gap).start(), "CSS", gap)
        self.out.append(gap)

    def _script(self, s: str, i: int) -> int:
        n, out = len(s), self.out
        while i < n:
            c = s[i]
            if c == "<" and s[i:i + 8].lower() == "</script":
                self.mode = "data"
                return i
            if c in "\"'`":
                ls, le = s.rfind("\n", 0, i) + 1, s.find("\n", i)
                if c != "`" and "i18n: ok" in s[ls:le if le >= 0 else n]:
                    j = i + 1  # «// i18n: ok» қатори — ички калит сўзлар, ўралмайди
                    while j < n and s[j] not in (c, "\n"):
                        j += 2 if s[j] == "\\" else 1
                    out.append(s[i:j + 1])
                    i, self.js_last = j + 1, "a"
                    continue
                out.append(c)
                self.quote, self.mode = c, "jsstr"
                return i + 1
            if c == "/" and s[i + 1:i + 2] == "/":
                self.mode = "jsline"
                return i
            if c == "/" and s[i + 1:i + 2] == "*":
                out.append("/*")
                self.mode = "jsblock"
                return i + 2
            if c == "/" and self.js_last in "(,=:[!&|?{};+-*%<>~^":
                j, in_cls = i + 1, False  # regex литерал: /…/flags
                while j < n and s[j] != "\n":
                    ch = s[j]
                    if ch == "\\":
                        j += 2
                        continue
                    if ch == "[":
                        in_cls = True
                    elif ch == "]":
                        in_cls = False
                    elif ch == "/" and not in_cls:
                        break
                    j += 1
                if CYR.search(s[i:j + 1]):
                    self._note(i, "JS regex", s[i:j + 1])
                out.append(s[i:j + 1])
                i, self.js_last = j + 1, "a"
                continue
            if CYR.match(c):
                self._note(i, "JS код", s[i:s.find("\n", i) if "\n" in s[i:] else n])
            out.append(c)
            if not c.isspace():
                self.js_last = c if c in "(,=:[!&|?{};+-*%<>~^)]}" else "a"
            i += 1
        return i


def transform(src: str) -> str:
    return Transformer(src).run()


def collect(src: str) -> list[tuple[str, int, str]]:
    """(msgid, сатр, режим) — ``manage.py i18n update`` учун."""
    t = Transformer(src)
    t.run()
    out = []
    for r in t.runs:
        res = msgid_of(r)
        if res:
            out.append((res[0], r.line, r.mode))
    return out


# ---------------------------------------------------------------------- юкловчи
class Loader(AppLoader):
    """app_directories юкловчиси + таржима ўрами (фақат web.i18n_tools.APPS шаблонлари; панел — йўқ)."""

    def get_contents(self, origin):
        src = super().get_contents(origin)
        if os.environ.get("TR_OFF") == "1":  # текшириш учун: ўрамсиз (асл) шаблон
            return src
        from web.i18n_tools import APPS, ROOT
        try:
            rel = Path(origin.name).resolve().relative_to(ROOT)
        except ValueError:
            return src
        if rel.name.endswith("_css.html"):  # фақат CSS бўлаги (изоҳлар) — ўралмайди
            return src
        return transform(src) if rel.parts and rel.parts[0] in APPS else src


# ---------------------------------------------------------------------- теглар
register = Library()


class TrNode(Node):
    def __init__(self, mode, msgid, raw, args):
        self.mode, self.msgid, self.raw, self.args = mode, msgid, raw.split(NUL), args

    def render(self, context):
        vals = [render_value_in_context(a.resolve(context), context) for a in self.args]
        if (get_language() or SOURCE_LANG) == SOURCE_LANG:
            out = [self.raw[0]]
            for k, v in enumerate(vals):
                out += [v, self.raw[k + 1] if k + 1 < len(self.raw) else ""]
            return mark_safe("".join(out))
        tr = gettext(self.msgid)
        texts = re.split(r"\{\d+\}", tr)
        order = [int(x) for x in re.findall(r"\{(\d+)\}", tr)]
        if self.mode == "j":
            texts = [escapejs(t) for t in texts]
        elif self.mode == "a":
            texts = [t.replace('"', "&quot;") for t in texts]
        elif self.mode == "c":
            texts = [css_escape(t) for t in texts]
        out = [texts[0]]
        for k, idx in enumerate(order):
            out += [vals[idx] if idx < len(vals) else "", texts[k + 1]]
        return mark_safe("".join(out))


def _make(mode):
    def tag(parser, token):
        bits = token.split_contents()
        if len(bits) < 3:
            raise TemplateSyntaxError(f"{bits[0]}: матн керак")
        return TrNode(mode, _unq(bits[1]), _unq(bits[2]), [parser.compile_filter(b) for b in bits[3:]])
    return tag


register.tag("tr", _make("t"))
register.tag("tra", _make("a"))
register.tag("trj", _make("j"))
register.tag("trc", _make("c"))


@register.filter(name="msg")
def msg_filter(value):
    """``{{ job.error|msg }}`` — базада асосий тилда сақланган хабарни жорий тилга ўгириш (web.i18n.ui)."""
    from web.i18n import ui
    return ui(value)
