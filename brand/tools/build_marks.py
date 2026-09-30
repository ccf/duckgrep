"""Build the duckgrep marks and wordmarks as pure-path SVGs."""

import os

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from palette import colors

HERE = os.path.dirname(os.path.abspath(__file__))
BRAND = os.path.dirname(HERE)
FNT = os.environ.get("DUCKGREP_FONTS", os.path.join(HERE, "fonts"))  # see tools/README.md
MONO = TTFont(f"{FNT}/fontsource-jetbrains-mono-5.3.0/package/files/jetbrains-mono-latin-800-normal.woff2")
SANS = TTFont(f"{FNT}/fontsource-hanken-grotesk-5.3.0/package/files/hanken-grotesk-latin-700-normal.woff2")
OUT = os.path.join(BRAND, "logos")
os.makedirs(OUT, exist_ok=True)

LIGHT, DARK = colors("light"), colors("dark")  # tokens.json
YELLOW = LIGHT["duck-yellow"]
INK = LIGHT["ink"]
PAPER = DARK["ink"]

# ---- the duck-d, in font units (y up), sized to JetBrains Mono 800 ----
R = 275  # bowl radius
CY = 265  # bowl centre height
S = 146  # stem width
TOP = 740  # ascender (730) + overshoot
BOT = CY - R  # -15, baseline overshoot
CX = R  # bowl starts at x=0
STEM_X = CX + R - S
EYE_R = 54
EYE = (CX - 34, CY + 70)
D_WIDTH = 2 * R


def d_shapes(ox, oy, k, fill, eye_fill=None, mask_id=None):
    """SVG for the duck-d. (ox, oy) = baseline-left in SVG px, k = px per unit."""

    def X(x):
        return ox + x * k

    def Y(y):
        return oy - y * k

    cx, cy, r = X(CX), Y(CY), R * k
    sx, sw = X(STEM_X), S * k
    sy, sh = Y(TOP), (TOP - BOT) * k
    ex, ey, er = X(EYE[0]), Y(EYE[1]), EYE_R * k
    body = (
        f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}"/>'
        f'<rect x="{sx:.2f}" y="{sy:.2f}" width="{sw:.2f}" height="{sh:.2f}" rx="{sw / 2:.2f}"/>'
    )
    if eye_fill:  # painted eye
        return f'<g fill="{fill}">{body}</g><circle fill="{eye_fill}" cx="{ex:.2f}" cy="{ey:.2f}" r="{er:.2f}"/>'
    # knocked-out eye (transparent)
    return (
        f'<mask id="{mask_id}" maskUnits="userSpaceOnUse"><rect x="-9999" y="-9999" width="99999" height="99999" fill="#fff"/>'
        f'<circle cx="{ex:.2f}" cy="{ey:.2f}" r="{er:.2f}" fill="#000"/></mask>'
        f'<g fill="{fill}" mask="url(#{mask_id})">{body}</g>'
    )


def text_path(font, s, ox, oy, k, tracking=0):
    gs = font.getGlyphSet()
    cmap = font.getBestCmap()
    pen = SVGPathPen(gs)
    x = 0
    for ch in s:
        g = cmap[ord(ch)]
        tp = TransformPen(pen, (k, 0, 0, -k, ox + x * k, oy))
        gs[g].draw(tp)
        x += gs[g].width + tracking
    return pen.getCommands(), x * k


def svg(w, h, body, title):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" width="{w:.0f}" height="{h:.0f}" role="img" aria-label="{title}">'
        f"<title>{title}</title>{body}</svg>\n"
    )


def write(name, content):
    with open(os.path.join(OUT, name), "w") as f:
        f.write(content)


# ---- mark (app tile) 256x256 ----
T = 256
k = 0.235
gw, gh = D_WIDTH * k, (TOP - BOT) * k
ox = (T - gw) / 2
oy = (T - gh) / 2 + TOP * k


def tile(fill):
    return f'<rect width="{T}" height="{T}" rx="56" fill="{fill}"/>'


write("duckgrep-mark.svg", svg(T, T, tile(INK) + d_shapes(ox, oy, k, YELLOW, eye_fill=INK), "duckgrep"))
write("duckgrep-mark-yellow.svg", svg(T, T, tile(YELLOW) + d_shapes(ox, oy, k, INK, eye_fill=YELLOW), "duckgrep"))

# ---- bare glyph (transparent), 256 tall ----
k2 = 240 / (TOP - BOT)
pad = 8
W2 = D_WIDTH * k2 + 2 * pad
write("duckgrep-glyph-ink.svg", svg(W2, 256, d_shapes(pad, pad + TOP * k2, k2, INK, mask_id="e"), "duckgrep"))
write("duckgrep-glyph-yellow.svg", svg(W2, 256, d_shapes(pad, pad + TOP * k2, k2, YELLOW, mask_id="e"), "duckgrep"))

# ---- dgrep wordmark: duck-d + "grep" in JetBrains Mono ExtraBold ----
k3 = 0.2
padx, asc, desc = 12, TOP, 190
H3 = (asc + desc) * k3 + 2 * padx
base = padx + asc * k3
gap = 70
g_origin = D_WIDTH + gap - 60  # 'g' left bearing is 60
for variant, dfill, _eye, tfill in (("light", INK, YELLOW, INK), ("dark", YELLOW, INK, PAPER)):
    d = d_shapes(padx, base, k3, dfill, eye_fill=None, mask_id="e")
    tp, tw = text_path(MONO, "grep", padx + g_origin * k3, base, k3)
    W3 = padx + g_origin * k3 + tw - 60 * k3 + padx
    body = d + f'<path fill="{tfill}" d="{tp}"/>'
    write(f"dgrep-wordmark-{variant}.svg", svg(W3, H3, body, "dgrep"))

# ---- lockup: tile mark + "duckgrep" in Hanken Grotesk Bold ----
TL = 160
k4 = 0.15
kk = 0.15
gw, gh = D_WIDTH * kk, (TOP - BOT) * kk
mo_x, mo_y = (TL - gw) / 2, (TL - gh) / 2 + TOP * kk
for variant, tfill in (("light", INK), ("dark", PAPER)):
    mark = f'<rect width="{TL}" height="{TL}" rx="35" fill="{INK if variant == "light" else YELLOW}"/>'
    mark += d_shapes(
        mo_x, mo_y, kk, YELLOW if variant == "light" else INK, eye_fill=INK if variant == "light" else YELLOW
    )
    # baseline so the x-height band is centred on the tile
    xh = 493 * k4
    base4 = TL / 2 + xh / 2
    tp, tw = text_path(SANS, "duckgrep", TL + 28 - 38 * k4, base4, k4, tracking=-12)
    W4 = TL + 28 + tw - 4
    write(f"duckgrep-lockup-{variant}.svg", svg(W4, TL, mark + f'<path fill="{tfill}" d="{tp}"/>', "duckgrep"))

print(sorted(os.listdir(OUT)))
