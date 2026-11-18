"""mhisobot бренд файллари: белги (K — n + тескари h, тўқдан яшилга ўтиш, учи қия кесик), ёзувли логотип, муҳр.

Ишлатиш:  python brand/build.py   →  brand/final/*.svg, сўнг PNG лар (Chrome headless орқали).
Белги ўлчамлари Onest 800 шрифтидан олинган (100px да: оёқ 18, x-баландлик 53, h-баландлик 71) — ёзувдаги
«isobot» билан бир хил қалинликда. Сайтдаги нусхалар: jobs/templates/jobs/_logo.html, _brand.html, web/static/favicon.svg.
"""
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "final"
STATIC = HERE.parent / "web" / "static"

INK, ACC_D = "#0d1522", "#0c7a4c"
WHITE, ACC_L = "#ffffff", "#34d399"
FONTS = ("@import url('https://fonts.googleapis.com/css2?family=Onest:wght@700;800"
         "&amp;family=JetBrains+Mono:wght@600;700&amp;display=swap');")

# белги: baseline y=0, эни 88, баландлиги 71 (юқори — манфий)
MARK_W, MARK_H = 88, 71
STROKES = ("M9 0V-53M9-22.26C9-51.25 44-51.25 44-22.26M44 0V-22.26"
           "M44-22.26C44-51.25 79-51.25 79-22.26")
TIP = "M70 0V-57.5L88-71V0Z"


def grad(gid, ink, acc):
    return (f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="10.8" y1="0" x2="84.4" y2="-71">'
            f'<stop offset="0" stop-color="{ink}"/><stop offset=".45" stop-color="{ink}"/>'
            f'<stop offset="1" stop-color="{acc}"/></linearGradient>')


def mark(paint, cid="c"):
    """paint — 'url(#g)' ёки битта ранг (муҳр, бир рангли). Арка ўнг оёқ ичида кесилади — четда чок қолмасин."""
    return (f'<clipPath id="{cid}"><rect x="-1" y="-80" width="88.4" height="81"/></clipPath>'
            f'<path d="{STROKES}" fill="none" stroke="{paint}" stroke-width="18" clip-path="url(#{cid})"/>'
            f'<path d="{TIP}" fill="{paint}"/>')


def icon(bg, ink, acc, shape="round", size=100, s=0.5909):
    # s=0.5909: белги иконканинг 52% ини эгаллайди; favicon да каттароқ (16px да ўқилсин)
    tx, ty = (100 - MARK_W * s) / 2, (100 + MARK_H * s) / 2
    back = {"round": f'<rect width="100" height="100" rx="22" fill="{bg}"/>',
            "square": f'<rect width="100" height="100" fill="{bg}"/>',
            "circle": f'<circle cx="50" cy="50" r="50" fill="{bg}"/>',
            "none": ""}[shape]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 100 100">'
            f'<defs>{grad("g", ink, acc)}</defs>{back}'
            f'<g transform="translate({tx:.2f} {ty:.2f}) scale({s})">{mark("url(#g)")}</g></svg>')


def wordmark(ink, acc, sub_col, sub=True, bg=None):
    # 72px ёзув: белги ×0.72, оралиқ 5.4, «isobot» эни ≈ 189.7
    W, H = 300, 132 if sub else 112
    back = f'<rect width="{W}" height="{H}" fill="{bg}"/>' if bg else ""
    s = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W * 2}" height="{H * 2}" viewBox="0 0 {W} {H}">'
         f'<style>{FONTS}</style><defs>{grad("g", ink, acc)}</defs>{back}'
         f'<g transform="translate(14 92)"><g transform="scale(.72)">{mark("url(#g)")}</g>'
         f'<text x="68.76" font-family="Onest" font-weight="800" font-size="72" letter-spacing="-2" fill="{ink}">isobot</text>')
    if sub:
        s += (f'<text x="70.76" y="26" font-family="JetBrains Mono" font-weight="600" font-size="12.5" '
              f'letter-spacing="2.2" fill="{sub_col}">МУҲАНДИС ҲИСОБОТ</text>')
    return s + "</g></svg>"


def seal(c, size=600):
    """Бренд муҳри (бир рангли). Юридик муҳр эмас — расмий муҳрга СТИР/номи қўшилади."""
    ms = 0.62
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 200 200">'
            f'<style>{FONTS}</style><defs>'
            '<path id="top" d="M28 100A72 72 0 0 1 172 100"/>'
            '<path id="bot" d="M19 100A81 81 0 0 0 181 100"/></defs>'
            f'<g fill="none" stroke="{c}"><circle cx="100" cy="100" r="96" stroke-width="3.5"/>'
            '<circle cx="100" cy="100" r="90" stroke-width="1"/><circle cx="100" cy="100" r="63" stroke-width="1.6"/></g>'
            f'<g fill="{c}">'
            '<text font-family="Onest" font-weight="700" font-size="13.5" letter-spacing="2.6">'
            '<textPath href="#top" startOffset="50%" text-anchor="middle">МУҲАНДИС ҲИСОБОТ</textPath></text>'
            '<text font-family="JetBrains Mono" font-weight="700" font-size="11.5" letter-spacing="3">'
            '<textPath href="#bot" startOffset="50%" text-anchor="middle">MHISOBOT.UZ</textPath></text>'
            '<path d="M23 96l1.6 3.3 3.6.5-2.6 2.5.6 3.6-3.2-1.7-3.2 1.7.6-3.6-2.6-2.5 3.6-.5z"/>'
            '<path d="M177 96l1.6 3.3 3.6.5-2.6 2.5.6 3.6-3.2-1.7-3.2 1.7.6-3.6-2.6-2.5 3.6-.5z"/>'
            f'<g transform="translate({100 - MARK_W * ms / 2:.2f} 112) scale({ms})">{mark(c)}</g>'
            f'<rect x="70" y="119" width="60" height="1.4"/>'
            '<text x="100" y="134" text-anchor="middle" font-family="JetBrains Mono" font-weight="700" '
            'font-size="7.6" letter-spacing="1.6">СМЕТА · ТЕХНАЗОР</text></g></svg>')


FILES = {
    "mark-dark.svg": icon(INK, WHITE, ACC_L),
    "mark-green.svg": icon(ACC_L, INK, WHITE),
    "mark-light.svg": icon(WHITE, INK, ACC_D, "square"),
    "mark-circle.svg": icon(INK, WHITE, ACC_L, "circle"),
    "mark-transparent.svg": icon("", INK, ACC_D, "none"),
    "wordmark-light.svg": wordmark(INK, ACC_D, "#6b7686", bg=WHITE),
    "wordmark-dark.svg": wordmark(WHITE, ACC_L, "#8b97a8", bg=INK),
    "wordmark-transparent.svg": wordmark(INK, ACC_D, "#6b7686"),
    "seal-blue.svg": seal("#1f3fae"),
    "seal-green.svg": seal(ACC_D),
    "seal-black.svg": seal(INK),
}

# PNG: (манба svg, натижа, ўлчам px (эни, бўйи))
PNGS = [
    ("mark-dark.svg", "mark-dark-512.png", (512, 512)),
    ("mark-dark.svg", "mark-dark-1024.png", (1024, 1024)),
    ("mark-green.svg", "mark-green-1024.png", (1024, 1024)),
    ("mark-circle.svg", "telegram-avatar-640.png", (640, 640)),
    ("mark-dark.svg", "apple-touch-icon.png", (180, 180)),
    ("wordmark-light.svg", "wordmark-light.png", (1200, 528)),
    ("wordmark-dark.svg", "wordmark-dark.png", (1200, 528)),
    ("seal-blue.svg", "seal-blue.png", (1200, 1200)),
    ("seal-green.svg", "seal-green.png", (1200, 1200)),
    ("seal-black.svg", "seal-black.png", (1200, 1200)),
]

CHROME = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
          r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]


def render(svg_name, png_name, wh):
    w, h = wh
    page = OUT / "_render.html"
    svg = (OUT / svg_name).read_text(encoding="utf-8")  # ичига қўйилади: <img> ичидаги SVG шрифт юклай олмайди
    page.write_text('<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="https://fonts.googleapis.com/'
                    'css2?family=Onest:wght@700;800&family=JetBrains+Mono:wght@600;700&display=block">'
                    '<style>html,body{margin:0;background:transparent;overflow:hidden}'
                    f'svg{{display:block;width:{w}px;height:{h}px}}</style>{svg}', encoding="utf-8")
    exe = next(p for p in CHROME if Path(p).exists())
    subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--default-background-color=00000000",
                    f"--window-size={w},{h}", "--virtual-time-budget=6000", f"--screenshot={OUT / png_name}",
                    page.as_uri()], check=True, capture_output=True)
    page.unlink()


def main():
    OUT.mkdir(exist_ok=True)
    for name, svg in FILES.items():
        (OUT / name).write_text(svg, encoding="utf-8")
    for src, dst, wh in PNGS:
        render(src, dst, wh)
    # сайт: favicon (тўқ иконка), apple-touch-icon, logo-512 (хат ва SEO учун)
    (STATIC / "favicon.svg").write_text(icon(INK, WHITE, ACC_L, size=32, s=0.75).replace(' width="32" height="32"', ""),
                                        encoding="utf-8")
    shutil.copy(OUT / "apple-touch-icon.png", STATIC / "apple-touch-icon.png")
    shutil.copy(OUT / "mark-dark-512.png", STATIC / "logo-512.png")
    print("ok:", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
