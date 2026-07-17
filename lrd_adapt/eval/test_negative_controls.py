"""
Minimal test for the negative-controls scaffolding -- there's nothing to
test yet beyond "loading an empty/missing manifest doesn't crash", since no
real catalog data exists (see negative_controls.py's module docstring).

    python lrd_adapt/eval/test_negative_controls.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lrd_adapt.eval.negative_controls import load_negative_controls  # noqa: E402


def test_default_manifest_loads_without_error():
    manifest = load_negative_controls()
    assert isinstance(manifest, dict)


def test_missing_manifest_returns_empty_dict():
    assert load_negative_controls(path="/nonexistent/path.json") == {}


if __name__ == "__main__":
    test_default_manifest_loads_without_error()
    test_missing_manifest_returns_empty_dict()
    print("OK -- negative controls scaffolding loads cleanly (no real entries yet).")
