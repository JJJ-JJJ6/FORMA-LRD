"""
Tests for the testable part of lrd_adapt/eval/loo_harness.py -- config
building and input-file detection. The actual pipeline invocation
(dry_run=False) needs Docker/langchain and is not exercised here; see the
module docstring.

No pytest dependency:
    python lrd_adapt/eval/test_loo_harness.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lrd_adapt.eval.loo_harness import (  # noqa: E402
    build_run_config,
    registered_loo_sources,
    run_loo_batch,
    run_source,
)


def test_registered_loo_sources_includes_demo():
    sources = registered_loo_sources()
    assert "SRC01" in sources


def test_build_run_config_isolates_output_per_source():
    cfg_a = build_run_config("SRC01", input_dir="/data/in", output_dir="/data/out")
    cfg_b = build_run_config("SRC02", input_dir="/data/in", output_dir="/data/out")
    assert cfg_a["env"]["FILE_NAME"] == "SRC01"
    assert cfg_b["env"]["FILE_NAME"] == "SRC02"
    assert cfg_a["fits_path"] != cfg_b["fits_path"]
    assert cfg_a["env"]["HYPOTHESIS_PROVIDER"] == "lrd"
    assert cfg_a["env"]["REDROCK"] == "false"


def test_run_source_reports_input_missing_without_a_real_fits():
    with tempfile.TemporaryDirectory() as d:
        result = run_source("SRC01", input_dir=d, output_dir=os.path.join(d, "out"), dry_run=True)
        assert result["status"] == "input_missing"


def test_run_source_dry_run_ok_when_fits_present():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "SRC01.fits"), "wb").close()  # empty stand-in, only existence checked
        result = run_source("SRC01", input_dir=d, output_dir=os.path.join(d, "out"), dry_run=True)
        assert result["status"] == "dry_run_ok"


def test_run_loo_batch_covers_all_registered_sources():
    with tempfile.TemporaryDirectory() as d:
        results = run_loo_batch(input_dir=d, output_dir=os.path.join(d, "out"), dry_run=True)
        assert set(results.keys()) == set(registered_loo_sources())
        assert all(r["status"] == "input_missing" for r in results.values())  # no FITS in this empty temp dir


if __name__ == "__main__":
    test_registered_loo_sources_includes_demo()
    test_build_run_config_isolates_output_per_source()
    test_run_source_reports_input_missing_without_a_real_fits()
    test_run_source_dry_run_ok_when_fits_present()
    test_run_loo_batch_covers_all_registered_sources()
    print("OK -- LOO harness config/dry-run tests passed.")
