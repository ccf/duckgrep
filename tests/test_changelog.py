"""CHANGELOG.md has a dated entry for every released version, newest first, including the one being packaged."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = re.compile(r"(?m)^## \[(\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?)\] - (\d{4}-\d{2}-\d{2})$")


def entries():
    return ENTRY.findall((ROOT / "CHANGELOG.md").read_text())


def test_the_package_version_has_a_dated_entry():
    (version,) = re.findall(r'(?m)^version = "([^"]+)"$', (ROOT / "pyproject.toml").read_text())
    assert version in {v for v, _ in entries()}, f"CHANGELOG.md needs a '## [{version}] - YYYY-MM-DD' entry"


def test_entries_are_newest_first_and_unreleased_leads():
    text = (ROOT / "CHANGELOG.md").read_text()
    assert re.search(r"(?m)^## \[Unreleased\]$", text), "keep an Unreleased section on top"
    versions = [tuple(int(x) for x in re.findall(r"\d+", v)[:3]) for v, _ in entries()]
    dates = [d for _, d in entries()]
    assert versions == sorted(versions, reverse=True) and dates == sorted(dates, reverse=True)
    assert text.index("## [Unreleased]") < text.index(f"## [{entries()[0][0]}]")
