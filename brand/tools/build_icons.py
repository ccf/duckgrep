"""Favicons, app icons and social cards for duckgrep."""

import asyncio
import os
import shutil

from PIL import Image
from playwright.async_api import async_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
BRAND = os.path.dirname(HERE)
OUT = os.path.join(BRAND, "_build")
os.makedirs(OUT, exist_ok=True)
FNT = os.environ.get("DUCKGREP_FONTS", os.path.join(HERE, "fonts"))  # see tools/README.md
Y, INK, PAPER = "#fff100", "#0d0d0d", "#f2f2f2"


# Small-size duck-d on a 32 grid: heavier stem, bigger eye so it survives 16px.
def glyph32(fill, eye, dx=0, dy=0, s=1.0):
    return (
        f'<g transform="translate({dx} {dy}) scale({s})">'
        f'<g fill="{fill}"><circle cx="16" cy="19.5" r="9"/>'
        f'<rect x="19.5" y="4" width="5.5" height="24.5" rx="2.75"/></g>'
        f'<circle fill="{eye}" cx="14.3" cy="17.2" r="2.3"/></g>'
    )


def svg(w, h, body):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">{body}</svg>\n'


favicon = svg(32, 32, f'<rect width="32" height="32" rx="7" fill="{INK}"/>' + glyph32(Y, INK))
open(f"{OUT}/favicon.svg", "w").write(favicon)
# apple-touch: iOS rounds corners itself -> full-bleed square
apple = svg(32, 32, f'<rect width="32" height="32" fill="{INK}"/>' + glyph32(Y, INK))
# maskable: glyph inside the 80% safe circle
maskable = svg(32, 32, f'<rect width="32" height="32" fill="{INK}"/>' + glyph32(Y, INK, dx=3.2, dy=3.2, s=0.8))
yellow_tile = svg(32, 32, f'<rect width="32" height="32" rx="7" fill="{Y}"/>' + glyph32(INK, Y))

FONTS = f"""
@font-face{{font-family:'Hanken Grotesk';font-weight:400;src:url(file://{FNT}/fontsource-hanken-grotesk-5.3.0/package/files/hanken-grotesk-latin-400-normal.woff2)}}
@font-face{{font-family:'Hanken Grotesk';font-weight:700;src:url(file://{FNT}/fontsource-hanken-grotesk-5.3.0/package/files/hanken-grotesk-latin-700-normal.woff2)}}
@font-face{{font-family:'JetBrains Mono';font-weight:400;src:url(file://{FNT}/fontsource-jetbrains-mono-5.3.0/package/files/jetbrains-mono-latin-400-normal.woff2)}}
@font-face{{font-family:'JetBrains Mono';font-weight:700;src:url(file://{FNT}/fontsource-jetbrains-mono-5.3.0/package/files/jetbrains-mono-latin-700-normal.woff2)}}
"""

THEMES = {
    "dark": dict(
        bg="#0d0d0d",
        ink="#f2f2f2",
        muted="#b2b2b2",
        code="#141414",
        border="#333333",
        path="#5fafff",
        ln="#00af00",
        str="#ffd700",
        prompt="#888888",
        mbg="#454100",
        mink="#fff866",
        slab="#1a1a1a",
        lockup="duckgrep-lockup-dark.svg",
    ),
    "light": dict(
        bg="#fcfcfc",
        ink="#0d0d0d",
        muted="#666666",
        code="#f7f7f7",
        border="#e6e6e6",
        path="#005fff",
        ln="#007a00",
        str="#875f00",
        prompt="#626262",
        mbg="#fff100",
        mink="#0d0d0d",
        slab="#0d0d0d",
        lockup="duckgrep-lockup-light.svg",
    ),
}


def card(w, h, t, pad):
    """Social card: lockup + lede + terminal on the left, the cover motif on the right."""
    lock = open(os.path.join(BRAND, "logos", t["lockup"])).read()
    slab_x = int(w * 0.66)
    dots = "".join(
        f'<circle cx="{slab_x + 40 + c * 24}" cy="{pad + 8 + r * 24}" r="4.5"/>' for r in range(4) for c in range(7)
    )
    return f"""<html><head><style>{FONTS}
    body{{margin:0}} .c{{position:relative;width:{w}px;height:{h}px;background:{t["bg"]};overflow:hidden;font-family:'Hanken Grotesk'}}
    .art{{position:absolute;inset:0}}
    .left{{position:absolute;left:{pad}px;top:{pad}px;width:{slab_x - pad - 56}px}}
    .lock svg{{height:84px;width:auto;display:block}}
    .sub{{margin-top:16px;font-weight:400;font-size:21px;line-height:30px;color:{t["muted"]}}}
    .lede{{margin-top:32px;font-weight:700;font-size:48px;line-height:56px;letter-spacing:-0.02em;color:{t["ink"]}}}
    .term{{position:absolute;left:{pad}px;bottom:{pad}px;width:{slab_x - pad - 56}px;box-sizing:border-box;background:{t["code"]};border:1px solid {t["border"]};border-radius:8px;padding:20px 24px;
      font:400 18px/30px 'JetBrains Mono';color:{t["ink"]};white-space:pre;font-variant-ligatures:none}}
    .p{{color:{t["prompt"]}}} .path{{color:{t["path"]}}} .ln{{color:{t["ln"]}}} .ctx{{color:{t["muted"]}}}
    .kw{{color:{t["path"]}}} .fn{{color:{t["ln"]}}} .str{{color:{t["str"]}}}
    .hl{{background:{t["mbg"]};color:{t["mink"]};border-radius:4px;padding:0 6px;margin-left:-2px}}
    .m{{background:{t["mbg"]};color:{t["mink"]};border-radius:4px;padding:0 3px}}
    </style></head><body><div class="c">
    <svg class="art" viewBox="0 0 {w} {h}" width="{w}" height="{h}">
      <rect x="{slab_x}" y="0" width="{w - slab_x}" height="{h}" fill="{t["slab"]}"/>
      <g fill="{Y}">{dots}
        <circle cx="{slab_x + 150}" cy="{h - 120}" r="150"/>
        <rect x="{w - 116}" y="{pad}" width="64" height="{h}" rx="32"/></g>
      <circle cx="{slab_x - 60}" cy="{pad + 40}" r="28" fill="#ff6900"/>
    </svg>
    <div class="left"><div class="lock">{lock}</div>
      <div class="lede">Grep finds strings.<br>duckgrep <span class="hl">answers questions.</span></div>
      <div class="sub">Give your coding agent a live, queryable model of your codebase (definitions, callers, imports, history), so questions that take dozens of greps take one query.</div></div>
    <div class="term"><span class="p">-- who calls get_or_create, and from which function?</span>
<span class="kw">SELECT</span> * <span class="kw">FROM</span> <span class="fn">callers</span>(<span class="str">'</span><span class="m">get_or_create</span><span class="str">'</span>);
<span class="p">-- django: 1 query, 9 KB (grep: 22 calls, 58 KB)</span></div>
    </div></body></html>"""


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()

        async def shot(html, w, h, name):
            pg = await b.new_page(viewport={"width": w, "height": h})
            path = os.path.join(OUT, "_tmp.html")
            open(path, "w").write(html)
            await pg.goto("file://" + path)
            await pg.wait_for_timeout(250)
            await pg.screenshot(path=os.path.join(OUT, name), omit_background=True)
            await pg.close()

        def page(s, px):
            sized = s.replace('width="32" height="32"', f'width="{px}" height="{px}"', 1)
            return "<html><body style='margin:0;background:transparent'>" + sized + "</body></html>"

        await shot(page(favicon, 512), 512, 512, "_fav512.png")
        await shot(page(apple, 180), 180, 180, "apple-touch-icon.png")
        for px in (192, 512):
            await shot(page(favicon, px), px, px, f"icon-{px}.png")
        await shot(page(maskable, 512), 512, 512, "icon-maskable-512.png")
        await shot(page(yellow_tile, 512), 512, 512, "icon-yellow-512.png")
        for theme, t in THEMES.items():
            await shot(card(1200, 630, t, 64), 1200, 630, f"og-image-{theme}.png")
            await shot(card(1280, 640, t, 72), 1280, 640, f"github-social-{theme}.png")
        await b.close()


asyncio.run(main())
big = Image.open(f"{OUT}/_fav512.png").convert("RGBA")
for px in (16, 32, 48):
    big.resize((px, px), Image.LANCZOS).save(f"{OUT}/favicon-{px}.png")
os.remove(f"{OUT}/_fav512.png")
ims = [Image.open(f"{OUT}/favicon-{px}.png") for px in (16, 32, 48)]
ims[2].save(f"{OUT}/favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)], append_images=ims[:2])
# flatten social cards to opaque RGB JPEG-safe PNGs
for f in os.listdir(OUT):
    if f.startswith(("og-", "github-")):
        Image.open(f"{OUT}/{f}").convert("RGB").save(f"{OUT}/{f}", optimize=True)


# file the results into the brand folder
for f in sorted(os.listdir(OUT)):
    src = os.path.join(OUT, f)
    if f.startswith(("og-image", "github-social")):
        shutil.move(src, os.path.join(BRAND, "social", f))
    elif f.startswith(("favicon", "apple-touch", "icon-")):
        shutil.move(src, os.path.join(BRAND, "favicons", f))
shutil.rmtree(OUT)
