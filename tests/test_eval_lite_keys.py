"""Opt-in, needs the network: DUCKGREP_EVAL_NETWORK=1 uv run pytest tests/test_eval_lite_keys.py

Downloads SWE-bench Lite (czlll's copy) and each instance's edited file at its base commit, then checks the key
rules against czlll's edit_functions on every instance."""

import os

import pytest

from bench.eval import datasets, gold

pytestmark = pytest.mark.skipif(not os.environ.get("DUCKGREP_EVAL_NETWORK"), reason="set DUCKGREP_EVAL_NETWORK=1")


def test_derived_python_keys_match_czlll_on_every_lite_instance():
    disagree = []
    for row in datasets.rows("lite"):

        def read(path, row=row):
            return datasets.fetch_file(row["repo"], row["base_commit"], path)

        derived = gold.derive(row["patch"], read, "python").entries
        if not gold.same_key(derived, gold.normalize_listed(list(row["edit_functions"]), read)):
            disagree.append(row["instance_id"])
    assert disagree == []
