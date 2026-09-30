"""Generate brand/tokens.css from brand/tokens.json.

Light is the default; dark applies under prefers-color-scheme unless the page
sets data-theme="light", and always under data-theme="dark".
Run: python brand/tools/tokens_to_css.py
"""

import json
import os

BRAND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    t = json.load(open(os.path.join(BRAND, "tokens.json")))
    colors = t["color"]["tokens"]

    def block(theme: str, indent: str = "  ") -> str:
        return "\n".join(f"{indent}--{c['name']}: {c['value'][theme]};" for c in colors)

    out = [
        "/* Generated from tokens.json by tools/tokens_to_css.py. Do not edit by hand. */",
        '@import url("https://fonts.googleapis.com/css2?family=Hanken+Grotesk:wght@400;600;700&family=JetBrains+Mono:wght@400;700;800&display=swap");',
        "",
        ":root {",
        block("light"),
    ]
    for fam in ("spacing", "radius"):
        out += [f"  --{x['name']}: {x['value']};" for x in t[fam]["tokens"]]
    out += [f"  --font-{k}: {v};" for k, v in t["type"]["families"].items()]
    out += [
        "  color-scheme: light dark;",
        "}",
        "",
        "@media (prefers-color-scheme: dark) {",
        '  :root:not([data-theme="light"]) {',
        block("dark", "    "),
        "  }",
        "}",
        "",
        ':root[data-theme="dark"] {',
        block("dark"),
        "}",
        "",
    ]
    for g in t["type"]["groups"]:
        fam = g["family"]
        for s in g["styles"]:
            props = [
                f"font-family: var(--font-{s.get('family', fam)})",
                f"font-size: {s['fontSize']}",
                f"line-height: {s['lineHeight']}",
                f"font-weight: {s['fontWeight']}",
            ]
            if "letterSpacing" in s:
                props.append(f"letter-spacing: {s['letterSpacing']}")
            if s["name"] == "label":
                props.append("text-transform: uppercase")
            out.append(f".type-{s['name']} {{ " + "; ".join(props) + "; }")
    open(os.path.join(BRAND, "tokens.css"), "w").write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
