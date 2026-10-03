"""CHANGELOG.md has a dated entry for every released version, newest first, including the one being packaged."""

import re
from pathlib import Path

from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
ENTRY = re.compile(r"(?m)^## \[(\d+\.\d+\.\d+(?:(?:a|b|rc)\d+|\.dev\d+)?)\] - (\d{4}-\d{2}-\d{2})$")


def changelog():
    return (ROOT / "CHANGELOG.md").read_text()


def entries(text=None):
    return ENTRY.findall(changelog() if text is None else text)


def newest_first(text):
    found = entries(text)
    versions = [Version(v) for v, _ in found]
    dates = [d for _, d in found]
    return versions == sorted(versions, reverse=True) and dates == sorted(dates, reverse=True)


def test_the_package_version_has_a_dated_entry():
    (version,) = re.findall(r'(?m)^version = "([^"]+)"$', (ROOT / "pyproject.toml").read_text())
    assert version in {v for v, _ in entries()}, f"CHANGELOG.md needs a '## [{version}] - YYYY-MM-DD' entry"


def test_entries_are_newest_first_and_unreleased_leads():
    text = changelog()
    assert re.search(r"(?m)^## \[Unreleased\]$", text), "keep an Unreleased section on top"
    assert newest_first(text)
    assert text.index("## [Unreleased]") < text.index(f"## [{entries()[0][0]}]")


def test_dev_releases_are_entries():
    assert entries("## [0.3.0.dev1] - 2026-10-04\n") == [("0.3.0.dev1", "2026-10-04")]


def test_prereleases_out_of_order_are_caught():
    text = "## [Unreleased]\n\n## [1.0.0rc1] - 2026-10-04\n\n## [1.0.0rc2] - 2026-10-04\n"
    assert not newest_first(text)
    assert newest_first(text.replace("rc1", "rcX").replace("rc2", "rc1").replace("rcX", "rc2"))
