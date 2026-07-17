"""
Plumbing check for the Stage B external-evidence channel (CLAUDE.md new
code #4). No pytest dependency — run directly:

    python lrd_adapt/evidence/test_external_evidence.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lrd_adapt.evidence.external_evidence import (  # noqa: E402
    load_external_evidence,
    load_external_evidence_for_state,
    format_external_evidence_markdown,
)


def test_known_source_loads_and_renders():
    ev = load_external_evidence("SRC01")
    assert ev is not None
    assert ev["balmer_break"]["provenance"] == "paper"

    md = format_external_evidence_markdown(ev)
    assert "## External Evidence" in md
    assert "[paper]" in md
    assert "Balmer break" in md
    assert "X-ray" in md


def test_unknown_source_returns_none():
    assert load_external_evidence("SRC99") is None
    assert format_external_evidence_markdown(None) == ""


def test_state_wrapper():
    state = {"file_name": "SRC01"}
    ev = load_external_evidence_for_state(state)
    assert ev is not None

    assert load_external_evidence_for_state({"file_name": "SRC99"}) is None
    assert load_external_evidence_for_state({}) is None


if __name__ == "__main__":
    test_known_source_loads_and_renders()
    test_unknown_source_returns_none()
    test_state_wrapper()
    print("OK -- external evidence loader/renderer plumbing checks passed.")
