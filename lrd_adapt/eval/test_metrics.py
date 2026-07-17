"""
Unit tests for lrd_adapt/eval/metrics.py. No pytest dependency:
    python lrd_adapt/eval/test_metrics.py

Includes a regression test for a real bug caught while building this module:
primary_hypotheses.json uses blended pseudo-identities ("HeI_Pagamma") while
the actual pipeline output uses individual line names ("He I"/"Paγ") --
naively comparing them silently scored every correct confirmation as wrong.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lrd_adapt.eval.metrics import aggregate, score_source  # noqa: E402


def test_correct_result_scores_correct():
    result = {
        "type": "LineIDConfirmed", "classification": "LRD",
        "redshift": 2.3281, "lines": ["He I"], "confidence": "HIGH",
    }
    scored = score_source("SRC01", result)
    assert scored["line_id_correct"] is True
    assert scored["redshift_correct"] is True
    assert scored["classification_correct"] is True


def test_vocabulary_mismatch_regression():
    """He I+Pagamma is stored as one blended identity in primary_hypotheses.json
    but reported as individual line names by the pipeline -- either
    component confirming the identity must count."""
    result_he1 = {"type": "LineIDConfirmed", "lines": ["He I"]}
    result_pagamma = {"type": "LineIDConfirmed", "lines": ["Paγ"]}
    result_wrong = {"type": "LineIDConfirmed", "lines": ["Hα"]}

    assert score_source("SRC01", result_he1)["line_id_correct"] is True
    assert score_source("SRC01", result_pagamma)["line_id_correct"] is True
    assert score_source("SRC01", result_wrong)["line_id_correct"] is False


def test_wrong_result_scores_wrong():
    result = {
        "type": "LineIDConfirmed", "classification": "ClassicalAGN",
        "redshift": 3.5, "lines": ["Hα"], "confidence": "HIGH",
    }
    scored = score_source("SRC01", result)
    assert scored["line_id_correct"] is False
    assert scored["redshift_correct"] is False
    assert scored["classification_correct"] is False


def test_unscoreable_source_is_none_not_false():
    """A source with no ground truth (e.g. SRC03's classification is
    genuinely unknown pending real Table 1 data) must score as None, not
    silently count as correct or incorrect."""
    result = {"type": "Unknown", "classification": "Unknown", "redshift": None, "lines": []}
    scored = score_source("SRC03", result)
    assert scored["classification_correct"] is None  # SRC03 has no known paper_classification
    assert scored["line_id_correct"] is None  # SRC03 has no primary_hypotheses.json entry either


def test_unregistered_source_scores_all_none():
    scored = score_source("SRC_NOT_REGISTERED", {"type": "Unknown"})
    assert scored["line_id_correct"] is None
    assert scored["redshift_correct"] is None
    assert scored["classification_correct"] is None


def test_aggregate_denominators_exclude_unscored():
    results = [
        score_source("SRC01", {"type": "LineIDConfirmed", "classification": "LRD", "redshift": 2.328, "lines": ["He I"]}),
        score_source("SRC03", {"type": "Unknown", "classification": "Unknown", "redshift": None, "lines": []}),
    ]
    agg = aggregate(results)
    assert agg["n_sources"] == 2
    assert agg["n_line_id_scored"] == 1  # SRC03 has no primary hypothesis to score against
    assert agg["line_id_accuracy"] == 1.0
    assert agg["n_classification_scored"] == 1  # SRC03 excluded, not scored as wrong


if __name__ == "__main__":
    test_correct_result_scores_correct()
    test_vocabulary_mismatch_regression()
    test_wrong_result_scores_wrong()
    test_unscoreable_source_is_none_not_false()
    test_unregistered_source_scores_all_none()
    test_aggregate_denominators_exclude_unscored()
    print("OK -- metrics tests passed.")
