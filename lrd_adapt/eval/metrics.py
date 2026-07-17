"""
Scoring for the evaluation harness (CLAUDE.md build order C: "metrics").

Compares a pipeline run's final report_writer-style JSON block against the
registered ground truth for that source, and aggregates across many
sources. This module is orchestration/scoring-level code -- it reads
ground_truth.json, so (like anonymizer.py) it must never be imported by
any agent-facing module; see test_anonymizer_isolation.py.

Also tracks pre-freeze vs post-freeze provenance (CLAUDE.md: "Before ANY
evaluation run whose numbers might be reported: git tag kb-freeze-<date>.
Results from pre-freeze prompt/KB iterations must be reported separately
from post-freeze results -- n=19 makes this critical").
"""
from __future__ import annotations

import json
import os
import subprocess

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# lrd_hypothesis_provider.py's REST_WAVELENGTHS_ANG uses blended pseudo-
# identities ("HeI_Pagamma", "OI_8446", ...) as the primary_hypotheses.json
# "line" value, but the actual Stage A pipeline (line_tables.py, skills,
# CSV/report output) reports individual physical line names ("He I", "Paγ",
# "O I 8446", ...). Scoring "is the paper's claimed identity confirmed?"
# needs to translate between these two vocabularies -- comparing them
# directly (as a naive string match would) silently scores every correct
# He I+Pagamma/[S III] confirmation as wrong. A match on EITHER associated
# name counts as confirming the claimed identity (permissive by design --
# tighten to "all names present" later if a stricter pair-confirmation
# criterion is wanted).
_PRIMARY_LINE_TO_TABLE_NAMES = {
    "Paalpha": {"Paα"},
    "Pabeta": {"Paβ"},
    "HeI_Pagamma": {"He I", "Paγ"},
    "SIII": {"[S III]a", "[S III]b"},
    "OI_8446": {"O I 8446"},
    "Halpha": {"Hα"},
}


def _load_json(relpath):
    path = os.path.join(REPO_ROOT, relpath)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def current_git_ref():
    """Short commit hash and, if HEAD is exactly on a kb-freeze-* tag, that
    tag -- so every scored run can be tied to the KB/prompt state it ran
    against (pre-freeze vs post-freeze, CLAUDE.md's overfitting control)."""
    try:
        commit = subprocess.run(
            ["git", "-C", REPO_ROOT, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {"commit": None, "freeze_tag": None, "is_frozen": False}

    freeze_tag = None
    try:
        exact = subprocess.run(
            ["git", "-C", REPO_ROOT, "describe", "--tags", "--exact-match", "--match=kb-freeze-*"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        freeze_tag = exact or None
    except subprocess.CalledProcessError:
        pass  # HEAD isn't exactly on a freeze tag -- that's fine, just means pre-freeze/mid-iteration

    return {"commit": commit, "freeze_tag": freeze_tag, "is_frozen": freeze_tag is not None}


def score_source(src_code, pipeline_result, z_tolerance=0.005):
    """
    Parameters
    ----------
    src_code : str
    pipeline_result : dict
        The report_writer JSON block: {"type", "classification", "redshift",
        "lines", "confidence", "classification_confidence", "human_review"}.
    z_tolerance : float
        Absolute redshift tolerance for a "correct" match.

    Returns
    -------
    dict
        Per-source scoring breakdown. `line_id_correct` / `classification_correct`
        are None (not False) when there's no ground truth to compare against
        -- an unscored source is not the same as a wrong one.
    """
    primary_table = _load_json("lrd_adapt/configs/primary_hypotheses.json")
    ground_truth_table = _load_json("lrd_adapt/eval/ground_truth.json")

    primary = primary_table.get(src_code)
    ground_truth = ground_truth_table.get(src_code)

    result = {
        "src_code": src_code,
        "pipeline_type": pipeline_result.get("type"),
        "pipeline_classification": pipeline_result.get("classification"),
        "pipeline_redshift": pipeline_result.get("redshift"),
        "line_id_correct": None,
        "redshift_correct": None,
        "classification_correct": None,
    }

    if primary:
        claimed_lines = set(pipeline_result.get("lines") or [])
        expected_names = _PRIMARY_LINE_TO_TABLE_NAMES.get(primary["line"], {primary["line"]})
        result["line_id_correct"] = (
            pipeline_result.get("type") == "LineIDConfirmed"
            and bool(claimed_lines & expected_names)
        )
        z = pipeline_result.get("redshift")
        result["redshift_correct"] = (
            z is not None and abs(z - primary["z_spec"]) <= z_tolerance
        )

    if ground_truth and ground_truth.get("paper_classification") not in (None, "unknown_not_in_CLAUDE.md"):
        result["classification_correct"] = (
            pipeline_result.get("classification") == ground_truth["paper_classification"]
        )

    return result


def aggregate(per_source_results):
    """Roll up score_source() outputs into summary accuracy numbers.

    Denominators only count sources where that metric was actually
    scoreable (ground truth known) -- an unscored source doesn't silently
    count as wrong, and doesn't inflate an "N/A" metric to look like 0%.
    """
    n = len(per_source_results)
    line_scored = [r for r in per_source_results if r["line_id_correct"] is not None]
    z_scored = [r for r in per_source_results if r["redshift_correct"] is not None]
    cls_scored = [r for r in per_source_results if r["classification_correct"] is not None]

    def _rate(scored, key):
        if not scored:
            return None
        return sum(1 for r in scored if r[key]) / len(scored)

    return {
        "n_sources": n,
        "line_id_accuracy": _rate(line_scored, "line_id_correct"),
        "n_line_id_scored": len(line_scored),
        "redshift_accuracy": _rate(z_scored, "redshift_correct"),
        "n_redshift_scored": len(z_scored),
        "classification_accuracy": _rate(cls_scored, "classification_correct"),
        "n_classification_scored": len(cls_scored),
    }
