"""Pinned inputs: dataset files checked by SHA-256, and single source files fetched at a commit."""

from __future__ import annotations

import hashlib
import shutil
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pyarrow.parquet as pq

from . import config

USER_AGENT = "duckgrep-eval"
RAW_BASE = "https://raw.githubusercontent.com"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, expected_sha256: str | None = None) -> Path:
    """Fetch `url` to `dest` once; verify the checksum on every call so a corrupt cache is never used."""
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f"{dest.name}.{threading.get_ident()}.part")  # concurrent fetches never share one
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        tmp.rename(dest)
    if expected_sha256 and sha256(dest) != expected_sha256:
        raise ValueError(f"{dest} does not match its pinned SHA-256; delete it and retry")
    return dest


def rows(name: str, cache: Path | None = None) -> list[dict]:
    """Every row of a pinned dataset (config.DATASETS) as a dict."""
    ds = config.DATASETS[name]
    cache = cache or config.cache_dir()
    local = download(ds.url, cache / "datasets" / ds.repo.replace("/", "__") / ds.revision / ds.path, ds.sha256)
    return pq.read_table(local).to_pylist()


def fetch_file(repo: str, commit: str, path: str, cache: Path | None = None) -> str | None:
    """`path` at `commit` from GitHub, cached on disk; None if the file doesn't exist there."""
    cache = cache or config.cache_dir()
    local = cache / "raw" / repo.lower().replace("/", "__") / commit / path
    missing = local.with_name(local.name + ".missing")
    if missing.exists():
        return None
    if not local.exists():
        url = f"{RAW_BASE}/{repo}/{commit}/{path}"
        try:
            download(url, local)
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            missing.parent.mkdir(parents=True, exist_ok=True)
            missing.touch()
            return None
    return local.read_text(encoding="utf-8", errors="replace")
