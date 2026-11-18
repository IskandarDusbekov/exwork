"""Мақола (ва «Биз ҳақимизда», «Шартлар») учун HTML: панелдаги Word'га ўхшаш муҳаррир ёзади, бу ерда тозаланади.

Фақат рухсат этилган теглар, атрибутлар ва CSS хоссалари қолади (``sanitize``); қолгани — ташланади, матни сақланади.
``<script>``, ``<style>``, ``<iframe>`` … ичидаги матн ҳам ташланади. Word'дан кўчирилган ``mso-*`` услублар,
шартли изоҳлар ва ``<o:p>`` ҳам кетади. Ташқи кутубхона керак эмас (стандарт ``html.parser``).

Расмлар — ``/img/…`` (панелда юкланган, ``save_image``) ёки ``https://`` манзилдан."""
import re
import secrets
from html import escape
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.utils import timezone

ALLOWED = {
    "p", "br", "h2", "h3", "h4", "strong", "b", "em", "i", "u", "s", "strike", "del", "sub", "sup", "ul", "ol", "li",
    "blockquote", "a", "img", "figure", "figcaption", "hr", "table", "thead", "tbody", "tr", "th", "td", "span",
    "mark", "code", "pre", "div",
}
RENAME = {"h1": "h2", "h5": "h4", "h6": "h4", "font": "span", "strike": "s", "b": "strong", "i": "em"}
VOID = {"br", "img", "hr"}
DROP_WITH_TEXT = {"script", "style", "iframe", "object", "embed", "noscript", "template", "head", "title", "xml",
                  "svg", "math", "textarea", "select", "button", "form", "input"}
STYLED = {"p", "h2", "h3", "h4", "span", "li", "td", "th", "div", "blockquote", "img", "figure", "mark", "a", "strong", "em", "u", "s"}

# Муҳаррирдаги шрифт рўйхати (JS ҳам шунингдек): калит — CSS'даги номи
FONTS = {
    "inter": "Inter, system-ui, sans-serif",
    "arial": "Arial, Helvetica, sans-serif",
    "georgia": "Georgia, serif",
    "times new roman": "'Times New Roman', Times, serif",
    "courier new": "'Courier New', monospace",
}
_KW_SIZE = {"1": "12px", "2": "14px", "3": "16px", "4": "18px", "5": "24px", "6": "32px", "7": "40px",
            "x-small": "12px", "small": "14px", "medium": "16px", "large": "18px", "x-large": "24px", "xx-large": "32px"}
_COLOR = re.compile(r"^(#[0-9a-f]{3,8}|rgba?\(\s*[\d.]+%?\s*,\s*[\d.]+%?\s*,\s*[\d.]+%?\s*(,\s*[\d.]+\s*)?\)|[a-z]{3,20})$", re.I)
_SIZE = re.compile(r"^(\d{1,2}(\.\d+)?)(px|pt)$|^(\d(\.\d+)?)(em|rem)$")
_WIDTH = re.compile(r"^(\d{1,3})(%|px)$")


def _color(v):
    v = v.strip()
    return v if _COLOR.match(v) and not v.lower().startswith(("expression", "url")) else ""


def _size(v):
    v = v.strip().lower()
    if v in _KW_SIZE:
        return _KW_SIZE[v]
    m = _SIZE.match(v)
    if not m:
        return ""
    if m.group(3):
        n = float(m.group(1)) * (4 / 3 if m.group(3) == "pt" else 1)
    else:
        n = float(m.group(4)) * 16
    return f"{max(10, min(56, round(n)))}px"


def _font(v):
    low = v.lower()
    for key, css in FONTS.items():
        if key in low:
            return css
    return ""


def _style(tag, raw):
    out = []
    for part in str(raw or "").split(";"):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        k, v = k.strip().lower(), v.strip().replace("!important", "").strip()
        if not v or "(" in v and not v.lower().startswith("rgb"):
            continue
        val = ""
        if k == "color" or k in ("background-color", "background"):
            val = _color(v)
            k = "color" if k == "color" else "background-color"
            if val.lower() in ("transparent", "initial", "inherit", "windowtext", "white") and k == "background-color":
                val = ""
        elif k == "text-align":
            val = v.lower() if v.lower() in ("left", "right", "center", "justify") else ""
        elif k == "font-size":
            val = _size(v)
        elif k == "font-family":
            val = _font(v)
        elif k == "font-weight":
            val = "bold" if v.lower() in ("bold", "bolder", "600", "700", "800", "900") else ""
        elif k == "font-style":
            val = "italic" if v.lower() == "italic" else ""
        elif k == "text-decoration" or k == "text-decoration-line":
            k = "text-decoration"
            val = " ".join(x for x in v.lower().split() if x in ("underline", "line-through"))
        elif k == "width" and tag in ("img", "figure"):
            m = _WIDTH.match(v.lower())
            val = v.lower() if m and (m.group(2) == "%" and 5 <= int(m.group(1)) <= 100 or m.group(2) == "px") else ""
        elif k in ("float",) and tag in ("img", "figure"):
            val = v.lower() if v.lower() in ("left", "right", "none") else ""
        if val:
            out.append(f"{k}: {val}")
    return "; ".join(dict.fromkeys(out))


def _url(v, img=False):
    v = (v or "").strip()
    low = v.lower()
    if img:
        return v if low.startswith(("/img/", "https://")) else ""
    if low.startswith(("http://", "https://", "mailto:", "tel:", "/", "#")) and not low.startswith("//"):
        return v
    return ""


class _Clean(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in DROP_WITH_TEXT:
            if tag not in VOID:
                self.skip += 1
            return
        if self.skip:
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        style = a.get("style", "")
        if tag == "font":  # execCommand / эски Word: <font face size color>
            extra = []
            if a.get("color"):
                extra.append(f"color:{a['color']}")
            if a.get("size"):
                extra.append(f"font-size:{a['size']}")
            if a.get("face"):
                extra.append(f"font-family:{a['face']}")
            style = ";".join(extra + [style])
        if tag in ("b", "strong") and "font-weight" in style and "normal" in style:  # Google Docs: <b style="font-weight:normal">
            tag = "span"
        tag = RENAME.get(tag, tag)
        if tag not in ALLOWED:
            return
        if tag == "div":
            tag = "p"
        self._implied(tag)
        attr = []
        if tag == "a":
            href = _url(a.get("href"))
            if not href:
                self.stack.append(None)  # ҳаволасиз <a> — фақат матни
                return
            attr.append(f'href="{escape(href)}"')
            if href.startswith(("http://", "https://")):
                attr.append('target="_blank" rel="noopener nofollow"')
        if tag == "img":
            src = _url(a.get("src"), img=True)
            if not src:
                return
            attr.append(f'src="{escape(src)}"')
            attr.append(f'alt="{escape(a.get("alt", "")[:200])}"')
            attr.append('loading="lazy"')
        if tag in ("td", "th"):
            for k in ("colspan", "rowspan"):
                if a.get(k, "").isdigit() and 1 < int(a[k]) <= 20:
                    attr.append(f'{k}="{int(a[k])}"')
        if tag in STYLED:
            s = _style(tag, style)
            if tag == "p" and a.get("align", "").lower() in ("center", "right", "justify") and "text-align" not in s:
                s = "; ".join(filter(None, [s, f"text-align: {a['align'].lower()}"]))
            if s:
                attr.append(f'style="{escape(s)}"')
        if tag == "span" and not attr:
            self.stack.append(None)  # бўш <span> — кераксиз
            return
        self.out.append(f"<{tag}{' ' if attr else ''}{' '.join(attr)}>")
        if tag not in VOID:
            self.stack.append(tag)

    def _implied(self, tag):
        """HTML'да ёпилмай қолган <li>, <p>, <td>, <tr>: янгиси бошланса — олдингиси ёпилади."""
        closes = {"li": ("li",), "tr": ("tr", "td", "th"), "td": ("td", "th"), "th": ("td", "th")}.get(tag)
        if tag in ("p", "h2", "h3", "h4", "ul", "ol", "table", "blockquote", "pre", "figure", "hr"):
            closes = ("p",)
        if not closes:
            return
        real = [t for t in self.stack if t]
        if real and real[-1] in closes:
            self.handle_endtag(real[-1])

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID and not self.skip and self.stack:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in DROP_WITH_TEXT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag in VOID:
            return
        tag = RENAME.get(tag, tag)
        if tag == "div":
            tag = "p"
        if tag in ("span", "a", "strong") and self.stack and self.stack[-1] is None:
            self.stack.pop()
            return
        if tag not in self.stack:
            return
        while self.stack:
            t = self.stack.pop()
            if t:
                self.out.append(f"</{t}>")
            if t == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(escape(data, quote=False))

    def close(self):
        super().close()
        while self.stack:
            t = self.stack.pop()
            if t:
                self.out.append(f"</{t}>")
        return "".join(self.out)


_EMPTY = re.compile(r"<(p|h2|h3|h4|li|blockquote|strong|em|u|s|span|mark)(\s[^>]*)?>(\s|&nbsp;| )*</\1>")


def sanitize(html: str, limit: int = 200000) -> str:
    """Рухсат этилмаган ҳамма нарсани олиб ташлайди; натижа — хавфсиз HTML."""
    p = _Clean()
    p.feed(str(html or "")[:limit])
    out = p.close()
    for _ in range(3):  # ичма-ич бўш теглар
        new = _EMPTY.sub("", out)
        if new == out:
            break
        out = new
    return out.strip()


def plain(html: str) -> str:
    """Тавсиф ва қидирув учун оддий матн."""
    from django.utils.html import strip_tags
    import html as h
    return " ".join(h.unescape(strip_tags(re.sub(r"<(br|/p|/h\d|/li)[^>]*>", " ", html or ""))).split())


# ===================== расмлар =====================
IMG_DIR = "site"
IMG_MAX = 6 * 1024 * 1024
_MAGIC = ((b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpg"), (b"GIF87a", "gif"), (b"GIF89a", "gif"))
IMG_NAME = re.compile(r"^\d{6}/[0-9a-f]{32}\.(png|jpg|gif|webp)$")
IMG_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


def image_kind(head: bytes) -> str:
    for sig, ext in _MAGIC:
        if head.startswith(sig):
            return ext
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return ""


def save_image(f) -> str:
    """Юкланган расм → ``/img/<ойи>/<тасодифий>.<тури>``. Тури файл бошидан аниқланади (номидан эмас); SVG олинмайди.
    Хато бўлса — ``ValueError`` (матни фойдаланувчига)."""
    if f.size > IMG_MAX:
        raise ValueError(f"расм {IMG_MAX // 1024 // 1024} МБ дан катта")
    head = f.read(16)
    f.seek(0)
    ext = image_kind(head)
    if not ext:
        raise ValueError("фақат PNG, JPG, GIF ёки WEBP расм")
    name = f"{timezone.localdate():%Y%m}/{secrets.token_hex(16)}.{ext}"
    path = Path(settings.MEDIA_ROOT) / IMG_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as out:
        for chunk in f.chunks():
            out.write(chunk)
    return "/img/" + name


def image_path(name: str):
    if not IMG_NAME.match(name or ""):
        return None
    p = Path(settings.MEDIA_ROOT) / IMG_DIR / name
    return p if p.is_file() else None
