"""The brand's colours for the asset builders, read from tokens.json, the source of truth for colour."""

import json
import os

BRAND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def colors(theme: str) -> dict[str, str]:
    """{token name: hex} for the "light" or "dark" theme."""
    with open(os.path.join(BRAND, "tokens.json")) as f:
        tokens = json.load(f)["color"]["tokens"]
    return {c["name"]: c["value"][theme] for c in tokens}
