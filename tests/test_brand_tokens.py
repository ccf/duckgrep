"""brand/tools/tokens_to_css.py: the generated CSS, and that the committed tokens.css is current."""

import importlib.util
import json
from pathlib import Path

BRAND = Path(__file__).resolve().parents[1] / "brand"


def render():
    spec = importlib.util.spec_from_file_location("tokens_to_css", BRAND / "tools" / "tokens_to_css.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool.render(json.loads((BRAND / "tokens.json").read_text()))


def test_forced_themes_set_their_color_scheme():
    css = render()
    # with the OS in dark mode, `color-scheme: light dark` alone would give a forced-light page dark controls
    assert ':root[data-theme="light"] {\n  color-scheme: light;\n}' in css
    forced_dark = css.split(':root[data-theme="dark"] {', 1)[1].split("}", 1)[0]
    assert "color-scheme: dark;" in forced_dark


def test_committed_tokens_css_is_current():
    assert render() == (BRAND / "tokens.css").read_text()
