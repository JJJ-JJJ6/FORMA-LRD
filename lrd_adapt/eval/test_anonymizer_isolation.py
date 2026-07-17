"""
Regression check for the anonymization boundary (CLAUDE.md Anonymization
section): mapping.csv (real J-name/coordinate <-> SRC code) must never be
reachable from any agent-facing code path, and the per-source loaders that
DO run inside the agent pipeline must never return more than the single
requested source's data (no accidental full-table leakage).

No pytest dependency -- run directly:
    python lrd_adapt/eval/test_anonymizer_isolation.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lrd_adapt.eval.anonymizer import load_mapping, mapping_path, register_source  # noqa: E402
from lrd_adapt.evidence.external_evidence import load_external_evidence  # noqa: E402
from lrd_adapt.hypothesis.lrd_hypothesis_provider import (  # noqa: E402
    generate_lrd_hypotheses,
)


def test_mapping_csv_not_in_kb_grep_registry():
    """harness/tools.py's _GREP_FILES is what grep_kb searches -- if mapping.csv
    ever ended up there, an LLM tool call could read real source identities."""
    tools_src = (REPO_ROOT / "src/FORMA/agents/multi_agents/harness/tools.py").read_text(encoding="utf-8")
    assert "mapping.csv" not in tools_src
    assert "eval/mapping" not in tools_src
    assert "ground_truth" not in tools_src


def test_mapping_csv_not_referenced_by_agent_facing_modules():
    """None of the converter, hypothesis provider, or evidence loader --
    the modules that DO run inside the agent pipeline -- should mention
    mapping.csv, ground_truth.json, or import the anonymizer/metrics
    modules. ground_truth.json holds the paper's final LRD/AGN label,
    which is exactly the answer Stage B is trying to independently reach --
    exposing it would make that test circular."""
    agent_facing = [
        REPO_ROOT / "lrd_adapt/converter/grizli_to_forma.py",
        REPO_ROOT / "lrd_adapt/converter/masked_regions.py",
        REPO_ROOT / "lrd_adapt/hypothesis/lrd_hypothesis_provider.py",
        REPO_ROOT / "lrd_adapt/evidence/external_evidence.py",
        REPO_ROOT / "lrd_adapt/tools/broadline_lsf_bic.py",
    ]
    for path in agent_facing:
        src = path.read_text(encoding="utf-8")
        assert "mapping.csv" not in src, f"{path} references mapping.csv"
        assert "anonymizer" not in src, f"{path} imports the anonymizer module"
        assert "ground_truth" not in src, f"{path} references ground_truth.json"


def test_external_evidence_loader_returns_single_source_only():
    """load_external_evidence must never hand back the whole table -- only
    the one entry for the requested SRC code."""
    ev = load_external_evidence("SRC01")
    assert ev is not None
    # A single-source entry has evidence-item keys (photometry, balmer_break,
    # ...), not other SRC codes as top-level keys.
    assert "SRC02" not in ev
    assert "SRC01" not in ev  # not nested under its own code either
    assert set(ev.keys()) <= {"photometry", "balmer_break", "r_circ_mas", "xray", "morphology_notes", "_note"}


def test_hypothesis_provider_takes_explicit_args_not_a_table():
    """generate_lrd_hypotheses is a pure function over explicit args -- it
    has no way to see any other source's data even in principle (unlike
    the *_for_state wrapper, it doesn't even read a file)."""
    result = generate_lrd_hypotheses(
        observed_wavelength_ang=36052.2, primary_line="HeI_Pagamma",
        z_spec=2.328, source_code="SRC01",
    )
    assert result["hypotheses"][0]["_lrd_provenance"]["source_code"] == "SRC01"
    # nothing in the return value should reference another registered source
    dumped = json.dumps(result)
    assert "SRC02" not in dumped
    assert "SRC03" not in dumped


def test_anonymizer_registration_roundtrip(tmp_path=None):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "mapping.csv")
        code1 = register_source("TEST_REAL_ID_A", notes="first", path=path)
        code2 = register_source("TEST_REAL_ID_B", notes="second", path=path)
        code1_again = register_source("TEST_REAL_ID_A", notes="re-registered", path=path)

        assert code1 != code2
        assert code1 == code1_again, "re-registering the same real_id must be idempotent"

        mapping = load_mapping(path)
        assert mapping[code1]["real_id"] == "TEST_REAL_ID_A"
        assert mapping[code2]["real_id"] == "TEST_REAL_ID_B"


def test_default_mapping_file_exists_and_is_well_formed():
    path = mapping_path()
    assert os.path.exists(path), f"expected {path}"
    mapping = load_mapping(path)
    assert "SRC01" in mapping
    for code, entry in mapping.items():
        assert entry["real_id"], f"{code} has an empty real_id"


def test_ground_truth_file_exists_and_every_entry_has_a_registered_src_code():
    gt_path = REPO_ROOT / "lrd_adapt/eval/ground_truth.json"
    assert gt_path.exists(), f"expected {gt_path}"
    ground_truth = json.loads(gt_path.read_text(encoding="utf-8"))
    mapping = load_mapping()
    for code in ground_truth:
        if code == "_readme":
            continue
        assert code in mapping, f"ground_truth.json has {code} but mapping.csv doesn't register it"


if __name__ == "__main__":
    test_mapping_csv_not_in_kb_grep_registry()
    test_mapping_csv_not_referenced_by_agent_facing_modules()
    test_external_evidence_loader_returns_single_source_only()
    test_hypothesis_provider_takes_explicit_args_not_a_table()
    test_anonymizer_registration_roundtrip()
    test_default_mapping_file_exists_and_is_well_formed()
    test_ground_truth_file_exists_and_every_entry_has_a_registered_src_code()
    print("OK -- anonymization isolation boundary checks passed.")
