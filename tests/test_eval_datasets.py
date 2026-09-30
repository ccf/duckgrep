import hashlib
import urllib.error

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from bench.eval import config, datasets


def test_download_verifies_the_checksum(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"payload")
    good = hashlib.sha256(b"payload").hexdigest()
    dest = datasets.download(src.as_uri(), tmp_path / "cache" / "f.bin", good)
    assert dest.read_bytes() == b"payload"
    dest.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="SHA-256"):
        datasets.download(src.as_uri(), dest, good)


def test_rows_reads_the_pinned_parquet(tmp_path, monkeypatch):
    src = tmp_path / "d.parquet"
    pq.write_table(pa.table({"instance_id": ["a", "b"], "n": [1, 2]}), src)
    ds = config.Dataset("o/d", "rev1", "data/d.parquet", datasets.sha256(src))
    monkeypatch.setitem(config.DATASETS, "tiny", ds)
    monkeypatch.setattr(config.Dataset, "url", property(lambda self: src.as_uri()))
    assert datasets.rows("tiny", tmp_path / "cache") == [{"instance_id": "a", "n": 1}, {"instance_id": "b", "n": 2}]


def test_fetch_file_caches_and_remembers_missing_files(tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    (origin / "o/r/c1/pkg").mkdir(parents=True)
    (origin / "o/r/c1/pkg/a.py").write_text("x = 1\n")
    monkeypatch.setattr(datasets, "RAW_BASE", origin.as_uri())
    cache = tmp_path / "cache"
    assert datasets.fetch_file("o/r", "c1", "pkg/a.py", cache) == "x = 1\n"
    (origin / "o/r/c1/pkg/a.py").unlink()
    assert datasets.fetch_file("o/r", "c1", "pkg/a.py", cache) == "x = 1\n"  # served from the cache

    def not_found(url, dest, sha=None):
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    monkeypatch.setattr(datasets, "download", not_found)
    assert datasets.fetch_file("o/r", "c1", "pkg/gone.py", cache) is None
    monkeypatch.setattr(datasets, "download", lambda *a: pytest.fail("a known-missing file is not refetched"))
    assert datasets.fetch_file("o/r", "c1", "pkg/gone.py", cache) is None
