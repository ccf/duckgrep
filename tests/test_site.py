"""The duckgrep.dev landing page: it builds, its links resolve, and its numbers match their sources."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "site/index.html"
RESULTS = ROOT / "bench/eval/runs/full/results.jsonl"
SNAPSHOT = ROOT / "site/tools/replayed.json"


class Collect(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[str] = []
        self.scripts: list[str] = []
        self.steps: list[dict] = []
        self.lanes: list[str] = []
        self._script = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for key in ("href", "src", "srcset"):
            if a.get(key):
                self.refs.append(a[key])
        if tag == "script" and "src" not in a:
            self._script = True
            self.scripts.append("")
        if tag == "div" and a.get("data-task"):
            self.lanes.append(a["data-task"])
        if tag == "li" and "data-ms" in a:
            self.steps.append({**a, "task": self.lanes[-1]})

    def handle_endtag(self, tag):
        if tag == "script":
            self._script = False

    def handle_data(self, data):
        if self._script:
            self.scripts[-1] += data


def parse() -> Collect:
    c = Collect()
    c.feed(PAGE.read_text())
    return c


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("site") / "_site"
    subprocess.run(["sh", str(ROOT / "site/build.sh"), str(out)], check=True, capture_output=True)
    return out


def test_local_links_resolve(built):
    for ref in parse().refs + re.findall(r'"src": "([^"]+)"', (built / "site.webmanifest").read_text()):
        if re.match(r"^(https?:|#|mailto:)", ref):
            continue
        target = built / ref.split("#")[0].lstrip("/")
        assert target.is_file() or (target / "index.html").is_file(), f"{ref} is missing from the build"


def test_anchors_exist():
    page = PAGE.read_text()
    ids = set(re.findall(r'\bid="([^"]+)"', page))
    for anchor in re.findall(r'href="#([^"]+)"', page):
        assert anchor in ids, f"#{anchor} has no target"


def test_csp_allows_the_inline_script():
    (script,) = parse().scripts
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    assert f"'sha256-{digest}'" in (ROOT / "site/_headers").read_text()


def cells(row: str) -> list[str]:
    """A table row's values with formatting stripped: '**2.3 (−18%)**' and '2.3 −18%' both become '2.3−18%'."""
    return [re.sub(r"[\s*()]", "", c) for c in row]


def test_results_table_matches_readme():
    readme = (ROOT / "README.md").read_text()
    section = readme[readme.index("## What it saves") : readme.index("## Why it matters")]
    want = [cells(line.strip("|").split("|")) for line in section.splitlines() if re.match(r"^\| [A-Z][a-z]", line)]
    page = PAGE.read_text()
    table = page[page.index('<table class="results__table">') : page.index("</table>")]
    got = []
    for row in re.findall(r"<tr>(.*?)</tr>", table, re.S):
        values = [re.sub(r"<[^>]+>", "", c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row)]
        got.append(cells(values))
    assert got == want


def test_replay_matches_snapshot():
    """The terminals' final totals are the replayed runs' recorded ones (site/tools/replayed.json)."""
    runs = json.loads(SNAPSHOT.read_text())["runs"]
    answers = [s for s in parse().steps if "step--answer" in s["class"]]
    assert len(answers) == 4
    setups = ["baseline", "duckgrep-hint"] * 2
    for step, setup in zip(answers, setups, strict=True):
        r = runs[step["task"]][setup]
        assert int(step["data-calls"]) == r["tool_calls"]
        assert int(step["data-tokens"]) == r["tokens_total"]
        assert float(step["data-cost"]) == round(r["cost_usd"], 4)
        assert int(step["data-ms"]) == r["duration_ms"]
        assert r["success"]


@pytest.mark.skipif(not RESULTS.exists(), reason="the A/B run's records are not in this checkout")
def test_snapshot_matches_records():
    """The committed snapshot is what the extractor would write from the run records today."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("extract_replay", ROOT / "site/tools/extract_replay.py")
    extract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(extract)
    records = {}
    for line in RESULTS.open():
        r = json.loads(line)
        records[(r["task"], r["setup"], r["rep"])] = r
    assert extract.snapshot(records) == json.loads(SNAPSHOT.read_text())


def test_no_absolute_paths_leak():
    page = PAGE.read_text()
    assert "/Volumes/" not in page and "/Users/" not in page and "code-tasks" not in page


def test_hint_is_the_measured_one():
    from bench.eval.setups import DUCKGREP_HINT

    hint = re.search(r'<code class="cmd__text" id="hint">(.*?)</code>', PAGE.read_text(), re.S).group(1)
    assert hint == DUCKGREP_HINT


def test_prose_numbers_match_readme():
    """Figures the page repeats from the README's results and FAQ."""
    readme, page = (ROOT / "README.md").read_text(), PAGE.read_text()
    for figure in (
        "75%",
        "12–19%",
        "14–18%",
        "3,620 runs",
        "160 comparisons",
        "230 SWE-bench",
        "11–13%",
        "23–56%",
        "1,411",
        "73.4%",
        "99.8%",
        "1,646",
        "71.9%",
        "85.5%",
        "30–90 ms",
    ):
        assert figure in readme and figure in page, figure


def test_race_claims_match_snapshot():
    """The race's result lines, meter deltas and median claim, against the replayed runs' records."""
    snap = json.loads(SNAPSHOT.read_text())
    page = PAGE.read_text()
    assert f"the {snap['both_correct']} structural questions both setups answered correctly" in page
    blocks = re.findall(r'data-task="([^"]+)">(.*?)<p class="race__result" data-result>(.*?)</p>', page, re.S)
    assert len(blocks) == 2
    for task, lanes, result in blocks:
        base, dg = snap["runs"][task]["baseline"], snap["runs"][task]["duckgrep-hint"]
        calls = base["tool_calls"] - dg["tool_calls"]
        tokens = round((1 - dg["tokens_total"] / base["tokens_total"]) * 100)
        cost = round((1 - dg["cost_usd"] / base["cost_usd"]) * 100)
        assert (
            f"{calls} fewer tool call" in result
            and f"{tokens}% fewer tokens" in result
            and f"{cost}% less cost" in result
        )
        assert re.findall(r'class="meter__d">([^<]+)<', lanes) == [f"−{calls}", f"−{tokens}%", f"−{cost}%"]
        ratio = dg["cost_usd"] / base["cost_usd"]
        assert abs(ratio - snap["median_cost_ratio"]) < 0.03, "the replayed run is no longer typical"


def test_wrangler_serves_the_build_output():
    """Cloudflare deploys what wrangler.jsonc points at; it must be what site/build.sh writes."""
    text = "\n".join(
        ln for ln in (ROOT / "wrangler.jsonc").read_text().splitlines() if not ln.lstrip().startswith("//")
    )
    config = json.loads(text)
    assert config["assets"]["directory"] == "./_site"
    assert 'OUT="${1:-_site}"' in (ROOT / "site/build.sh").read_text()
    assert "main" not in config  # static assets only: no Worker script
