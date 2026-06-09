"""
AnalysisAuditor — two-stage adversarial spectrum review.

Stage A — FeatureAuditor (pre-synthesis):
    Cross-hypothesis feature verification.  Reads all ``{idx}_lines.csv`` files,
    builds a Feature Contradiction Matrix, then spawns an LLM with
    ``read_spectrum_region`` + ``grep_kb`` to verify whether each claimed
    feature is physically real or a noise artifact.  Writes cleaned
    ``{idx}_lines_cleaned.csv`` files that the synthesis step consumes.

Stage B — SynthesisAudit (post-synthesis):
    Adversarial second review of the synthesis verdict.  Takes the synthesis
    output (winning hypothesis + line catalog) and independently stress-tests
    every key claim by reading the raw spectrum.  Outputs a calibrated
    confidence assessment: CONFIRM / DOWNGRADE / REJECT / UNCERTAIN.
"""

import asyncio
import json
import os
import re
import csv as _csv
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from langchain.agents import create_agent
from langchain_core.tools import tool
from AstroAgent.agents.common.state import SpectroState
from AstroAgent.agents.common.base_agent import BaseAgent
from AstroAgent.agents.common.result_writer import ResultWriter
from AstroAgent.core.runtime.runtime_container import RuntimeContainer
from AstroAgent.core.llm import _detect_vendor, _build_thinking_extra_body, _create_chat_openai
from AstroAgent.agents.multi_agents.harness.tools import grep_kb, _detect_oii_slope_change_core
from AstroAgent.agents.multi_agents.harness.continuation import (
    _find_last_ai_message, _find_last_content_ai_message, _is_truncated,
    _format_tool_call, _format_tool_result,
    run_continuation_streaming, run_continuation_ainvoke,
)


class FeatureAuditorFailed(Exception):
    """Raised when FeatureAuditor fails after all retries are exhausted."""


# ---------------------------------------------------------------------------
# Skill paths
# ---------------------------------------------------------------------------

SKILLS_DIR = Path(__file__).resolve().parent / "harness" / "skills"
FEATURE_AUDIT_SKILL_PATH = SKILLS_DIR / "feature_audit_skill.md"
SYNTHESIS_AUDIT_SKILL_PATH = SKILLS_DIR / "auditor_audit_skill.md"


def _load_feature_audit_skill() -> str:
    return FEATURE_AUDIT_SKILL_PATH.read_text(encoding="utf-8")


def _load_synthesis_audit_skill() -> str:
    return SYNTHESIS_AUDIT_SKILL_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _resolve_max_tokens() -> int | None:
    env_val = os.environ.get("LLM_MAX_TOKENS", "").strip()
    if env_val:
        try:
            return int(env_val)
        except ValueError:
            pass
    base_url = os.environ.get("LLM_BASE_URL", "")
    if "deepseek" in base_url.lower():
        return 65536
    return None


def _build_continuation_prompt(messages: list, json_keys: List[str] = None) -> str:
    """Build a smart continuation prompt that tells the LLM where it stopped.

    Uses *json_keys* to detect which output schema is expected:
    - ``["feature_verdicts"]`` → FeatureAuditor schema (counts wl_obs entries)
    - ``["verdict"]`` → SynthesisAudit schema (tracks completed top-level keys)
    - otherwise → generic prompt

    Uses ``_find_last_content_ai_message`` to skip pure tool-call messages and
    find the last AI message that actually contains text output.
    """
    last_ai = _find_last_content_ai_message(messages)
    if last_ai is None:
        return (
            "Your previous response was truncated. Continue EXACTLY "
            "from where you stopped. Do NOT repeat anything. Output "
            "ONLY the remaining content."
        )

    raw = last_ai.content if hasattr(last_ai, "content") else str(last_ai)
    parts = ["Your previous response was truncated. Continue from where you stopped."]
    json_keys = json_keys or []

    if "feature_verdicts" in json_keys:
        # ── FeatureAuditor schema: count completed wl_obs entries ──
        n_complete = 0
        n_broken = False
        for m in re.finditer(r'\{\s*"wl_obs"', raw):
            start = m.start()
            depth = 0
            for i in range(start, len(raw)):
                if raw[i] == '{':
                    depth += 1
                elif raw[i] == '}':
                    depth -= 1
                    if depth == 0:
                        break
            if depth == 0:
                n_complete += 1
            else:
                n_broken = True

        if n_broken:
            parts.append(
                f"You have {n_complete} COMPLETE feature_verdicts entries "
                f"and 1 BROKEN entry cut off mid-output. "
                f"Finish the broken entry first (continue from the last "
                f"complete character — do NOT start over). "
                f"Then continue with the remaining verdicts."
            )
        elif n_complete > 0:
            parts.append(
                f"You have already output {n_complete} complete "
                f"feature_verdicts entries. Continue with the next verdict. "
                f"Do NOT repeat any of the {n_complete} entries."
            )
        else:
            parts.append(
                "Do NOT repeat anything. Output ONLY the remaining content."
            )

    elif "verdict" in json_keys:
        # ── SynthesisAudit schema: track which top-level keys are done ──
        _AUDIT_KEYS = [
            "verdict", "calibrated_confidence", "spectrum_quality",
            "key_issues", "doublet_issues", "line_inventory_issues",
            "recommendation",
        ]
        found = [k for k in _AUDIT_KEYS
                 if re.search(r'"' + re.escape(k) + r'"\s*:', raw)]

        if found:
            parts.append(
                f"Fields already written: {', '.join(found)}. "
                f"Continue with the remaining fields. Do NOT repeat "
                f"anything already written."
            )
        else:
            parts.append(
                "Do NOT repeat anything. Output ONLY the remaining content."
            )

    else:
        parts.append(
            "Do NOT repeat anything. Output ONLY the remaining content."
        )

    return " ".join(parts)


def _extract_json_block(text: str, keys: List[str] = None) -> Optional[dict]:
    """Extract a JSON block from the LLM response.

    Tries `` ```json ... ``` `` fences first, then a bare ``{...}`` block
    containing any of *keys*.  If *keys* is not provided, matches any ``{...}``
    at the end of *text*.
    """
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    if keys:
        for key in keys:
            m = re.search(r"\{[^{}]*\"" + re.escape(key) + r"\"[^{}]*\}", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
    else:
        m = re.search(r"\{[^{}]+\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return None


# ---------------------------------------------------------------------------
# Formatting helpers (for streaming output)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Shared helpers (not in continuation.py — AnalysisAuditor-specific)
# ---------------------------------------------------------------------------

def _clean_rec(rec: str) -> str:
    """Normalise an LLM-generated recommendation string.

    Strips stray quotes, whitespace, and normalises to uppercase so that
    ``startswith("KEEP" / "FLAG" / "REMOVE")`` works robustly even when a
    model wraps the value in extra quotes or adds leading punctuation.
    """
    rec = (rec or "").strip()
    for q in ('"', "'", "`"):
        if rec.startswith(q) and rec.endswith(q) and len(rec) >= 2:
            rec = rec[1:-1]
            break
    return rec.strip().upper()


# ============================================================================
# Doublet definitions (shared between matrix builder and skills)
# ============================================================================

# ratio_check_fn: (amp_a, amp_b) -> (ratio_ab, is_expected, note)
# ratio_ab = |amp_a| / |amp_b|  (a = shorter λ, b = longer λ)
# Expected values per KB:
#   [O III]: b:a ≈ 3:1 → a/b ≈ 0.33
#   [N II]:  a:b ≈ 1:3 → a/b ≈ 0.33
#   [S II]:  a ≈ b     → a/b ≈ 1.0
#   Ca K/H:  K deeper than H → K/H > 1
DOUBLET_DEFS = [
    {
        "name_a": "Ca K_abs", "rest_a": 3934.8,
        "name_b": "Ca H_abs", "rest_b": 3969.6,
        "sep_rest": 34.8,
        "ratio_desc": "K/H > 1 (K MUST be deeper than H)",
        "check": lambda a, b: (
            abs(a) / max(abs(b), 1e-10),
            abs(a) > abs(b),
            f"K/H={abs(a)/max(abs(b), 1e-10):.2f}",
        ),
    },
    {
        "name_a": "[O III]a", "rest_a": 4960.3,
        "name_b": "[O III]b", "rest_b": 5008.2,
        "sep_rest": 47.9,
        "ratio_desc": "b:a ≈ 3:1 → a/b ≈ 0.33",
        "check": lambda a, b: (
            abs(a) / max(abs(b), 1e-10),
            0.20 <= abs(a) / max(abs(b), 1e-10) <= 0.45,
            f"a/b={abs(a)/max(abs(b), 1e-10):.2f}",
        ),
    },
    {
        "name_a": "[N II]a", "rest_a": 6549.8,
        "name_b": "[N II]b", "rest_b": 6585.3,
        "sep_rest": 35.5,
        "ratio_desc": "a:b ≈ 1:3 → a/b ≈ 0.33",
        "check": lambda a, b: (
            abs(a) / max(abs(b), 1e-10),
            0.20 <= abs(a) / max(abs(b), 1e-10) <= 0.45,
            f"a/b={abs(a)/max(abs(b), 1e-10):.2f}",
        ),
    },
    {
        "name_a": "[S II]a", "rest_a": 6718.3,
        "name_b": "[S II]b", "rest_b": 6732.7,
        "sep_rest": 14.4,
        "ratio_desc": "a ≈ b → a/b ≈ 1.0",
        "check": lambda a, b: (
            abs(a) / max(abs(b), 1e-10),
            0.7 <= abs(a) / max(abs(b), 1e-10) <= 1.4,
            f"a/b={abs(a)/max(abs(b), 1e-10):.2f}",
        ),
    },
]


# ============================================================================
# Contradiction matrix builder (Stage A — FeatureAuditor)
# ============================================================================


def _detect_doublets(
    features_by_hyp: Dict[int, List[dict]],
    redshift_by_hyp: Dict[int, float],
) -> List[dict]:
    """Detect known doublet pairs (and orphans) within each hypothesis.

    Reports:
    - Complete pairs: both components claimed.  Separation and ratio are
      pre-computed; mismatches are strong evidence against the identification.
    - Orphans: only one component claimed.  The missing component's expected
      λ_obs is computed; the LLM should check whether a real feature exists
      there (misidentification) or the claimed component is a false match.
    """
    annotations = []
    for hi, feats in features_by_hyp.items():
        redshift = redshift_by_hyp.get(hi, 0)
        by_name = defaultdict(list)
        for f in feats:
            by_name[f["name"]].append(f)

        for dd in DOUBLET_DEFS:
            candidates_a = by_name.get(dd["name_a"], [])
            candidates_b = by_name.get(dd["name_b"], [])

            # ── Complete pairs ──
            for fa in candidates_a:
                for fb in candidates_b:
                    sep_expected = dd["sep_rest"] * (1.0 + redshift)
                    sep_actual = abs(fb["wl_obs"] - fa["wl_obs"])
                    ratio, _, note = dd["check"](fa["amplitude"], fb["amplitude"])
                    annotations.append({
                        "hypothesis_idx": hi,
                        "complete": True,
                        "name_a": dd["name_a"],
                        "wl_a": fa["wl_obs"],
                        "amp_a": fa["amplitude"],
                        "name_b": dd["name_b"],
                        "wl_b": fb["wl_obs"],
                        "amp_b": fb["amplitude"],
                        "ratio": round(ratio, 3),
                        "note": note,
                        "sep_expected": round(sep_expected, 1),
                        "sep_actual": round(sep_actual, 1),
                    })

            # ── Orphans: only one component claimed ──
            for fa in candidates_a:
                if not candidates_b:
                    wl_missing = dd["rest_b"] * (1.0 + redshift)
                    annotations.append({
                        "hypothesis_idx": hi,
                        "complete": False,
                        "claimed": dd["name_a"],
                        "wl_claimed": fa["wl_obs"],
                        "amp_claimed": fa["amplitude"],
                        "missing": dd["name_b"],
                        "wl_missing": round(wl_missing, 1),
                    })
            for fb in candidates_b:
                if not candidates_a:
                    wl_missing = dd["rest_a"] * (1.0 + redshift)
                    annotations.append({
                        "hypothesis_idx": hi,
                        "complete": False,
                        "claimed": dd["name_b"],
                        "wl_claimed": fb["wl_obs"],
                        "amp_claimed": fb["amplitude"],
                        "missing": dd["name_a"],
                        "wl_missing": round(wl_missing, 1),
                    })
    return annotations


def build_contradiction_matrix(
    harness_results: List[dict],
    harness_dir: str,
    wl_min: float,
    wl_max: float,
) -> Tuple[List[dict], List[dict], dict]:
    """Build the Feature Contradiction Matrix from all hypotheses' lines.csv.

    Returns
    -------
    matrix_rows : list of dict
        Each dict has keys: wl_obs, cells, n_hypotheses, is_edge_blue, is_edge_red.
    doublet_annotations : list of dict
    stats : dict
        n_rows, n_total_features, n_edge_blue, n_edge_red, median_amplitude,
        top_quartile_amplitude, hypothesis_indices.
    """
    all_features = []
    features_by_hyp = defaultdict(list)

    for r in harness_results:
        idx = r.get("hypothesis_idx")
        if idx is None:
            continue
        csv_path = os.path.join(harness_dir, f"{idx}_lines.csv")
        if not os.path.exists(csv_path):
            continue
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                status = (row.get("status") or "").strip()
                if status not in ("LIKELY", "MARGINAL"):
                    continue
                try:
                    wl_obs = float(row.get("fitted_center", 0) or 0)
                except (ValueError, TypeError):
                    continue
                if wl_obs <= 0:
                    continue
                try:
                    amp = float(row.get("amplitude", 0) or 0)
                except (ValueError, TypeError):
                    amp = 0.0

                name = (row.get("name") or "").strip()
                line_type = "absorption" if name.endswith("_abs") else "emission"

                # Width class: broad > 2000 km/s, narrow < 2000 km/s
                fwhm_str = row.get("fwhm_km_s", "—")
                try:
                    fwhm_val = float(fwhm_str)
                    width_class = "broad" if fwhm_val > 2000 else "narrow"
                except (ValueError, TypeError):
                    fwhm_val = None
                    width_class = "—"

                feat = {
                    "hypothesis_idx": idx,
                    "redshift": r.get("redshift", 0),
                    "name": name,
                    "line_type": line_type,
                    "wl_obs": wl_obs,
                    "amplitude": amp,
                    "status": status,
                    "ridge_length": row.get("ridge_length", "—"),
                    "cwt_snr": row.get("cwt_snr", "—"),
                    "fwhm_km_s": fwhm_str,
                    "width_class": width_class,
                }
                all_features.append(feat)
                features_by_hyp[idx].append(feat)

    if not all_features:
        return [], [], {"n_rows": 0, "n_total_features": 0, "n_edge_blue": 0,
                        "n_edge_red": 0, "median_amplitude": 0, "top_quartile_amplitude": 0}

    # Group by (int(wl_obs), amp_sign).  Features at nearly the same wavelength
    # with the same amplitude sign are the same CWT-detected peak/trough.
    groups = defaultdict(list)
    for f in all_features:
        key = (int(f["wl_obs"]), 1 if f["amplitude"] > 0 else -1)
        groups[key].append(f)

    hyp_indices = sorted(set(f["hypothesis_idx"] for f in all_features))
    hyp_info = {}
    for r in harness_results:
        idx = r.get("hypothesis_idx")
        if idx is not None:
            hyp_info[idx] = {"redshift": r.get("redshift", 0)}

    matrix_rows = []
    for (int_wl, amp_sign), group in groups.items():
        first = group[0]
        rep_wl = round(first["wl_obs"], 1)
        group_key = (int_wl, amp_sign)

        cells = {}
        for f in group:
            cells[f["hypothesis_idx"]] = f

        matrix_rows.append({
            "wl_obs": rep_wl,
            "group_key": group_key,
            "cells": cells,
            "n_hypotheses": len(cells),
            "is_edge_blue": rep_wl < 4000.0,
            "is_edge_red": rep_wl > 7800.0,
            "row_type": first["line_type"],
            "row_amp": first["amplitude"],
            "row_width": first["width_class"],
        })

    matrix_rows.sort(key=lambda r: r["wl_obs"])

    redshift_by_hyp = {hi: hyp_info.get(hi, {}).get("redshift", 0) for hi in hyp_indices}
    doublet_annotations = _detect_doublets(
        {hi: feats for hi, feats in features_by_hyp.items() if hi in hyp_indices},
        redshift_by_hyp,
    )

    amplitudes = [abs(f["amplitude"]) for f in all_features if abs(f["amplitude"]) > 0]
    median_amp = float(np.median(amplitudes)) if amplitudes else 0.0
    top_q = float(sorted(amplitudes, reverse=True)[max(0, len(amplitudes) // 4)]) if amplitudes else 0.0

    stats = {
        "n_rows": len(matrix_rows),
        "n_total_features": len(all_features),
        "n_edge_blue": sum(1 for r in matrix_rows if r["is_edge_blue"]),
        "n_edge_red": sum(1 for r in matrix_rows if r["is_edge_red"]),
        "median_amplitude": round(median_amp, 4),
        "top_quartile_amplitude": round(top_q, 4),
        "hypothesis_indices": hyp_indices,
    }

    return matrix_rows, doublet_annotations, stats


# ============================================================================
# Emission-absorption pair detector (for composite profile check prompting)
# ============================================================================

# Species whose emission and absorption forms may form a composite profile.
# Key: (emission_name_suffix, absorption_name_suffix)
_COMPOSITE_SPECIES = [
    ("Mg II",       "Mg II_abs"),
    ("Hα",          "Hα_abs"),
    ("Hβ",          "Hβ_abs"),
]


def _detect_emission_absorption_pairs(
    matrix_rows: List[dict],
    harness_results: List[dict],
) -> List[dict]:
    """Detect emission-absorption pairs of the same species within each hypothesis.

    Returns a list of dicts, each describing a pair that requires a composite
    profile check: hypothesis_idx, species, wl_em, wl_abs, status_em, status_abs.
    """
    pairs = []
    for hi, feat_list in _group_features_by_hypothesis(matrix_rows).items():
        em_by_species: dict[str, dict] = {}
        ab_by_species: dict[str, dict] = {}
        for f in feat_list:
            name = f.get("name", "")
            for em_name, ab_name in _COMPOSITE_SPECIES:
                if name == em_name:
                    em_by_species.setdefault(em_name, f)
                elif name == ab_name:
                    ab_by_species.setdefault(ab_name, f)
        for em_name, ab_name in _COMPOSITE_SPECIES:
            em_feat = em_by_species.get(em_name)
            ab_feat = ab_by_species.get(ab_name)
            if em_feat is not None and ab_feat is not None:
                pairs.append({
                    "hypothesis_idx": hi,
                    "species": em_name.replace("_abs", ""),
                    "wl_em": em_feat.get("wl_obs", 0),
                    "wl_abs": ab_feat.get("wl_obs", 0),
                    "status_em": em_feat.get("status", "?"),
                    "status_abs": ab_feat.get("status", "?"),
                })
    return pairs


def _group_features_by_hypothesis(matrix_rows: List[dict]) -> dict:
    """Group matrix row cells by hypothesis index, flattening into feature dicts."""
    grouped: dict[int, list] = {}
    for row in matrix_rows:
        cells = row.get("cells", {})
        for hi, cell in cells.items():
            feat = {
                "name": cell.get("name", ""),
                "wl_obs": row.get("wl_obs", 0),
                "status": cell.get("status", "?"),
            }
            grouped.setdefault(hi, []).append(feat)
    return grouped


def _build_emission_absorption_pairs_section(
    matrix_rows: List[dict],
    harness_results: List[dict],
) -> str:
    """Build the 'Emission–Absorption Pairs' section for the FA user prompt."""
    pairs = _detect_emission_absorption_pairs(matrix_rows, harness_results)
    if not pairs:
        return (
            "\n## Emission–Absorption Pairs (Composite Profile Check Required)\n\n"
            "*No emission-absorption pairs of the same species detected "
            "in any hypothesis.*\n"
        )

    lines = [
        "## Emission–Absorption Pairs (Composite Profile Check Required)",
        "",
        "The following hypotheses claim BOTH an emission line AND its absorption "
        "counterpart of the same species. These may form a single composite "
        "profile (broad emission split by a central absorption trough).  "
        "**You MUST perform the composite profile check (Step 3.5) for EVERY "
        "pair listed below.**  Read ±200 Å covering both features together, "
        "apply the morphological \"M\" test, and record your verdict in the "
        "`composite_profile_verdicts` field of your JSON output.",
        "",
        "| H | Species | λ_em (Å) | λ_abs (Å) | Status (em) | Status (abs) |",
        "|---|---------|----------|-----------|-------------|-------------|",
    ]
    for p in pairs:
        hi = p["hypothesis_idx"]
        sp = p["species"]
        wl_em = f'{p["wl_em"]:.1f}' if isinstance(p["wl_em"], (int, float)) else str(p["wl_em"])
        wl_abs = f'{p["wl_abs"]:.1f}' if isinstance(p["wl_abs"], (int, float)) else str(p["wl_abs"])
        lines.append(
            f"| H{hi} | {sp} | {wl_em} | {wl_abs} | "
            f'{p["status_em"]} | {p["status_abs"]} |'
        )
    lines.append("")
    return "\n".join(lines)


# ============================================================================
# User message builders
# ============================================================================

def _build_feature_audit_user_message(
    state: SpectroState,
    harness_dir: str,
    matrix_rows: List[dict],
    doublet_annotations: List[dict],
    stats: dict,
) -> str:
    """Build the user prompt for Stage A — feature verification."""
    harness_results = state.get("harness_results") or []
    wl = state["spectrum"]["wavelength"]
    wl_left = float(wl[0])
    wl_right = float(wl[-1])

    hyp_indices = stats.get("hypothesis_indices", [])
    hyp_info = {}
    for r in harness_results:
        idx = r.get("hypothesis_idx")
        if idx is not None:
            hyp_info[idx] = {"redshift": r.get("redshift", 0)}

    parts = []

    # ── Spectrum metadata ──
    parts.append("## Spectrum")
    parts.append(f"- Wavelength range: {wl_left:.0f} – {wl_right:.0f} Å")
    parts.append(f"- **Blue edge**: {wl_left:.0f} – 4000 Å (throughput drop, non-Gaussian noise)")
    parts.append(f"- **Red edge (OH zone)**: 7800 – {wl_right:.0f} Å (OH + OI skyline residuals)")
    parts.append("")

    # ── Statistics ──
    parts.append("## Feature Statistics")
    parts.append(f"- Total features (LIKELY + MARGINAL): {stats['n_total_features']}")
    parts.append(f"- Unique wavelength rows in matrix: {stats['n_rows']}")
    parts.append(f"- Edge zone features: {stats['n_edge_blue']} blue + {stats['n_edge_red']} red")
    parts.append(f"- Median |amplitude|: {stats['median_amplitude']:.4f} (features near/below this are at noise floor)")
    parts.append(f"- Top quartile |amplitude|: {stats['top_quartile_amplitude']:.4f}")
    parts.append("")

    # ── Hypothesis summary ──
    parts.append("## Hypotheses")
    for hi in hyp_indices:
        info = hyp_info.get(hi, {})
        parts.append(f"- **H{hi}**: z = {info.get('redshift', '?')}")
    parts.append("")

    # ── Contradiction matrix ──
    parts.append("## Feature Contradiction Matrix")
    parts.append("")
    parts.append(
        "Each row is a unique observed wavelength where ≥1 hypothesis claims a "
        "feature (LIKELY or MARGINAL). Cells show the line identification + status. "
        "`—` means no claim at this wavelength. "
        "`🔵` = blue edge, `🔴` = red edge. "
        "Type, Amp, and Width are properties of the CWT-detected feature itself "
        "(identical across hypotheses — same feature, different name assignments). "
        "Width: broad > 2000 km/s, narrow < 2000 km/s. "
        "Doublet pairs (below the matrix) show pre-computed separations and "
        "amplitude ratios for the LLM to evaluate."
    )
    parts.append("")

    col_header = "| λ_obs (Å) | Type | Amp | Width |"
    col_sep = "|-----------|------|------|--------|"
    for hi in hyp_indices:
        z = hyp_info.get(hi, {}).get("redshift", "?")
        col_header += f" H{hi} (z={z}) |"
        col_sep += "-------------|"
    parts.append(col_header)
    parts.append(col_sep)

    for row in matrix_rows:
        wl_obs = row["wl_obs"]
        edge_prefix = ""
        if row["is_edge_blue"]:
            edge_prefix = "🔵 "
        elif row["is_edge_red"]:
            edge_prefix = "🔴 "

        vals = [
            f"{edge_prefix}{wl_obs:.1f}",
            row["row_type"],
            f"{row['row_amp']:+.3f}" if row["row_amp"] is not None else "—",
            row.get("row_width", "—"),
        ]
        for hi in hyp_indices:
            cell = row["cells"].get(hi)
            if cell is None:
                vals.append("—")
            else:
                status_mark = " (MARG)" if cell["status"] == "MARGINAL" else ""
                vals.append(f"{cell['name']}{status_mark}")
        parts.append("| " + " | ".join(vals) + " |")
    parts.append("")

    # ── Doublet annotations ──
    if doublet_annotations:
        parts.append("## Doublet Pairs & Orphans")
        parts.append("")
        parts.append(
            "Known doublets claimed by each hypothesis.  **Complete pairs** show "
            "pre-computed separation and amplitude ratio — mismatches are strong "
            "evidence against the identification.  **Orphans** show cases where "
            "only one component is claimed; the missing line's expected λ_obs is "
            "given.  The LLM must check whether a real feature exists at the "
            "missing position (misidentification) or the claimed component is a "
            "false match.  Use `read_spectrum_region` around the expected region."
        )
        parts.append("")

        # Complete pairs first
        complete = [da for da in doublet_annotations if da.get("complete")]
        if complete:
            parts.append("### Complete Pairs")
            parts.append("")
            for da in complete:
                parts.append(
                    f"- **H{da['hypothesis_idx']}**: {da['name_a']}@{da['wl_a']:.1f} + "
                    f"{da['name_b']}@{da['wl_b']:.1f} → "
                    f"ratio {da['note']} (expected sep {da['sep_expected']:.1f} Å, "
                    f"actual {da['sep_actual']:.1f} Å)"
                )
            parts.append("")

        # Orphans
        orphans = [da for da in doublet_annotations if not da.get("complete")]
        if orphans:
            parts.append("### Orphans (only one component claimed)")
            parts.append("")
            for da in orphans:
                parts.append(
                    f"- **H{da['hypothesis_idx']}**: {da['claimed']}@"
                    f"{da['wl_claimed']:.1f} (amp={da['amp_claimed']:+.3f}) → "
                    f"**missing {da['missing']}** at λ ≈ {da['wl_missing']:.1f} Å"
                )
            parts.append("")

    # ── Emission-Absorption Pairs ──
    parts.append(_build_emission_absorption_pairs_section(matrix_rows, harness_results))

    # ── Task ──
    parts.append("## Task")
    parts.append("")
    parts.append(
        "Follow the Step 1 → Step 2 → Step 3 → Step 3.5 → Step 4 → Step 5 "
        "→ Step 6 → Step 7 → Step 8 methodology from your system prompt. "
        "Your job is to independently verify whether each feature is physically "
        "real or a noise artifact. You MUST:\n\n"
        "1. Batch-read the spectrum at EVERY unique λ_obs in the matrix "
        "(±80 Å per read, merge adjacent rows when closer than 80 Å)\n"
        "2. Read BOTH edge zones in full\n"
        "3. Apply the Three-Question Test to each feature\n"
        "4. Check the Emission–Absorption Pairs section above — if any pairs "
        "are listed, you MUST perform the composite profile check (Step 3.5) "
        "for EVERY pair and populate the `composite_profile_verdicts` field\n"
        "5. Check doublet annotations for ratio violations\n"
        "6. Output a verdict for EVERY row in the matrix\n\n"
        "The downstream synthesis agent depends on you to filter noise from "
        "its input. A noise feature left in the catalogs will propagate into "
        "wrong cross-comparisons. A real feature wrongly removed will weaken "
        "the correct hypothesis. Err on the side of FLAG (keep with warning) "
        "rather than REMOVE when uncertain."
    )

    return "\n".join(parts)


def _build_synthesis_audit_user_message(state: SpectroState, harness_dir: str) -> str:
    """Build the user prompt for Stage B — synthesis verdict audit.

    Includes the synthesis verdict, winning hypothesis details, and the
    2nd-best hypothesis for quick alternative checking.
    """
    rule_analysis = state.get("rule_analysis") or {}
    feature_audit_verdict = state.get("feature_audit_verdict") or {}

    # ── Spectrum metadata ──
    wl = state["spectrum"]["wavelength"]
    wl_left = float(wl[0])
    wl_right = float(wl[-1])

    snr = state["spectrum"].get("snr")
    snr_median = float(np.median(snr)) if snr is not None else None

    parts = [
        "## Spectrum",
        f"- Wavelength range: {wl_left:.0f} – {wl_right:.0f} Å",
        f"- Median SNR: {f'{snr_median:.1f}' if snr_median else 'N/A'}",
        f"- **Blue edge**: {wl_left:.0f} – 4000 Å (throughput drop, non-Gaussian noise)",
        f"- **Red edge (OH zone)**: 7800 – {wl_right:.0f} Å (OH + OI skyline residuals)",
        "",
    ]

    # ── Synthesis verdict ──
    parts.append("## Synthesis Verdict")
    parts.append("")
    parts.append("```json")
    parts.append(json.dumps(rule_analysis, indent=2, ensure_ascii=False, default=str))
    parts.append("```")
    parts.append("")

    # ── Line Inventory from synthesis.csv ──
    synthesis_csv_path = os.path.join(harness_dir, "synthesis.csv")
    if os.path.exists(synthesis_csv_path):
        parts.append("## Line Inventory from Synthesis")
        parts.append("")
        parts.append(
            "This is the definitive line catalog produced by the synthesis agent. "
            "Every row is a line it believes supports the best redshift hypothesis. "
            "**Your job is to find lines that don't belong here.**"
        )
        parts.append("")
        parts.append(_format_synthesis_csv(synthesis_csv_path))
    else:
        parts.append("## Line Inventory from Synthesis")
        parts.append("")
        parts.append("*(synthesis.csv not found — nothing to audit.)*")
        parts.append("")

    # ── FA global issues (for reference, not binding) ──
    fa_quality = feature_audit_verdict.get("spectrum_quality", "unknown")
    fa_justification = feature_audit_verdict.get("spectrum_quality_justification", "")
    fa_global = feature_audit_verdict.get("global_issues", [])
    if fa_quality or fa_global:
        parts.append("## FeatureAuditor Notes (FYI — not binding)")
        parts.append("")
        parts.append(f"FA assessed spectrum quality as **{fa_quality}**.")
        if fa_justification:
            parts.append(f"> {fa_justification}")
        if fa_global:
            parts.append("")
            for gi in fa_global:
                parts.append(f"- {gi}")
        parts.append("")
        parts.append(
            "These are the upstream FeatureAuditor's observations.  They are "
            "informational — you are the independent auditor.  Trust your own "
            "judgment over FA's if they disagree."
        )
        parts.append("")

    # ── Task ──
    parts.append("## Task")
    parts.append("")
    parts.append(
        "You are an independent defensive auditor.  Follow the **Layer 1 → Layer 2** "
        "methodology from your system prompt:\n\n"
        "**Layer 1** — Physical sanity screening (no spectrum reads).  Scan the line "
        "inventory above.  Use `grep_kb` to check classification physics.  Identify "
        "every line that is physically inconsistent with the claimed object type, "
        "that has suspicious amplitude/FWHM relative to the catalog, or whose "
        "implied_z deviates significantly from the best redshift.\n\n"
        "**Layer 2** — Targeted verification (spectrum reads ONLY for Layer 1 "
        "suspects).  Call `read_spectrum_region` ±100 Å for each suspicious line. "
        "Judge visually: is this a real feature, or an artifact/noise that FA let "
        "through?\n\n"
        "**After both layers**: Assess spectrum-level issues (OH zone, blue edge, "
        "line inventory sufficiency) and decide whether to recommend re-observation.\n\n"
        "Output your reasoning in free text, then the JSON verdict block."
    )

    return "\n".join(parts)


def _format_synthesis_csv(csv_path: str) -> str:
    """Read synthesis.csv and format as a compact markdown table."""
    if not os.path.exists(csv_path):
        return "*(synthesis.csv not found)*\n"

    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = _csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        return "*(synthesis.csv is empty)*\n"

    display_cols = [
        ("name", "Line"),
        ("status", "Status"),
        ("fitted_center", "λ_fit (Å)"),
        ("amplitude", "Amp"),
        ("fwhm_km_s", "FWHM (km/s)"),
        ("implied_z", "z_implied"),
        ("is_anchor", "Anchor"),
    ]

    header = "| " + " | ".join(label for _, label in display_cols) + " |"
    sep = "|" + "|".join(["------"] * len(display_cols)) + "|"

    body_lines = []
    for row in rows:
        vals = []
        for key, _ in display_cols:
            v = (row.get(key) or "").strip()
            if key == "is_anchor":
                v = "✓" if v.lower() == "true" else ""
            vals.append(v if v else "—")
        body_lines.append("| " + " | ".join(vals) + " |")

    return header + "\n" + sep + "\n" + "\n".join(body_lines) + "\n"


# ============================================================================
# Cleaned CSV writer (Stage A output)
# ============================================================================

def _write_cleaned_csvs(
    harness_dir: str,
    harness_results: List[dict],
    feature_verdicts: List[dict],
) -> None:
    """Write ``{idx}_lines_cleaned.csv`` files with noise features annotated.

    Matches CSV rows to verdicts by ``(int(fitted_center), amp_sign)``
    where amp_sign is derived from the verdict's ``feature_type`` field.
    """
    # Build verdict lookup keyed by (int_wl, amp_sign)
    verdict_lookup = {}
    for v in feature_verdicts:
        wl = v.get("wl_obs")
        if wl is None:
            continue
        ft = (v.get("feature_type") or "").lower()
        amp_sign = -1 if ft.startswith("abs") else 1  # emission by default
        verdict_lookup[(int(float(wl)), amp_sign)] = v

    for r in harness_results:
        idx = r.get("hypothesis_idx")
        if idx is None:
            continue
        csv_path = os.path.join(harness_dir, f"{idx}_lines.csv")
        if not os.path.exists(csv_path):
            continue

        rows = []
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = _csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            for row in reader:
                rows.append(row)

        if not rows:
            continue

        out_fieldnames = list(fieldnames) + ["feature_audit", "feature_audit_flag"]

        cleaned_path = os.path.join(harness_dir, f"{idx}_lines_cleaned.csv")
        with open(cleaned_path, "w", newline="", encoding="utf-8") as f:
            writer = _csv.DictWriter(f, fieldnames=out_fieldnames)
            writer.writeheader()

            for row in rows:
                status = (row.get("status") or "").strip()
                try:
                    wl_obs = float(row.get("fitted_center", 0) or 0)
                except (ValueError, TypeError):
                    wl_obs = 0.0
                try:
                    amp = float(row.get("amplitude", 0) or 0)
                except (ValueError, TypeError):
                    amp = 0.0

                out_row = dict(row)
                out_row["feature_audit"] = ""
                out_row["feature_audit_flag"] = ""

                if status in ("LIKELY", "MARGINAL") and wl_obs > 0:
                    row_key = (int(wl_obs), 1 if amp > 0 else -1)
                    verdict = verdict_lookup.get(row_key)
                    if verdict:
                        rec = _clean_rec(verdict.get("recommendation"))
                        if rec.startswith("REMOVE"):
                            out_row["feature_audit"] = "REMOVED"
                            out_row["feature_audit_flag"] = "; ".join(
                                verdict.get("issues", [])
                            )
                        elif rec.startswith("FLAG"):
                            out_row["feature_audit"] = "FLAGGED"
                            out_row["feature_audit_flag"] = "; ".join(
                                verdict.get("issues", [])
                            )
                        else:
                            out_row["feature_audit"] = "KEEP"

                writer.writerow(out_row)


# ============================================================================
# Shared LLM agent runner (streaming + truncation + fallback)
# ============================================================================

async def _run_llm_agent(
    system_prompt: str,
    user_prompt: str,
    tools: list,
    harness_dir: str,
    stream_filename: str,
    stream_title: str,
    json_keys: List[str],
    log_prefix: str = "",
) -> Optional[dict]:
    """Run an LLM agent with streaming, truncation detection, and fallback.

    Returns the parsed JSON dict, or None on failure.
    """
    model = os.environ.get("LLM_MODEL", "deepseek-v4-pro")
    api_key = os.environ.get("LLM_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")

    vendor = _detect_vendor(base_url)
    extra_body = (
        _build_thinking_extra_body("disabled", vendor)
        if vendor != "unknown"
        else None
    )

    llm = _create_chat_openai(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0.1,
        max_tokens=_resolve_max_tokens(),
        extra_body=extra_body,
    )

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
    )

    config = {"recursion_limit": 100}
    stream_md_path = os.path.join(harness_dir, stream_filename)
    os.makedirs(harness_dir, exist_ok=True)

    # ── Streaming path ──────────────────────────────────────────────
    try:
        md = open(stream_md_path, "w", encoding="utf-8")
        md.write(f"# {stream_title}\n\n---\n\n")
        md.write("<details>\n<summary>System Prompt</summary>\n\n")
        md.write(system_prompt)
        md.write("\n</details>\n\n---\n\n")
        md.write(f"### User\n\n{user_prompt}\n\n")
        md.flush()

        accumulated_messages = []
        turn = 0

        try:
            async for event in agent.astream(
                {"messages": [("user", user_prompt)]},
                config=config,
                stream_mode="updates",
            ):
                for _node_name, update in event.items():
                    msgs = update.get("messages", [])
                    for msg in msgs:
                        accumulated_messages.append(msg)
                        msg_type = getattr(msg, "type", None)

                        if msg_type == "ai":
                            turn += 1
                            content = msg.content if hasattr(msg, "content") else ""
                            tool_calls = getattr(msg, "tool_calls", None)
                            lines = []
                            if content:
                                lines.append(content.strip())
                            if tool_calls:
                                for tc in tool_calls:
                                    lines.append(_format_tool_call(tc))
                            if lines:
                                md.write(f"### Assistant (turn {turn})\n\n")
                                md.write("\n\n".join(lines))
                                md.write("\n\n")
                                md.flush()

                        elif msg_type == "tool":
                            md.write("### Tool Result\n\n")
                            md.write(_format_tool_result(msg))
                            md.write("\n\n")
                            md.flush()
        except Exception as exc:
            md.write(f"\n\n> ❌ {stream_title} streaming failed: {exc}\n\n")
            md.flush()
            raise
        finally:
            md.close()

        # ── Truncation detection (shared continuation loop) ─
        await run_continuation_streaming(
            agent,
            accumulated_messages,
            config=config,
            stream_md_path=stream_md_path,
            continuation_prompt=_build_continuation_prompt(accumulated_messages, json_keys),
            log_prefix=log_prefix,
        )

        # Concatenate ALL AI messages (original + continuation if truncated)
        raw_content = "\n".join(
            msg.content for msg in accumulated_messages
            if getattr(msg, "type", None) == "ai" and hasattr(msg, "content")
        )

        parsed = _extract_json_block(raw_content, keys=json_keys)
        return parsed

    except Exception as exc:
        logging.warning(
            f"{log_prefix} Streaming path failed: {exc}. "
            "Falling back to non-streaming ainvoke."
        )
        try:
            md.close()
        except Exception:
            pass

    # ── Non-streaming fallback ──────────────────────────────────────
    try:
        result = await agent.ainvoke(
            {"messages": [("user", user_prompt)]},
            config=config,
        )
        messages = result.get("messages", [])

        # ── Truncation detection (shared continuation loop) ─
        await run_continuation_ainvoke(
            agent,
            messages,
            config=config,
            continuation_prompt=_build_continuation_prompt(messages, json_keys),
            log_prefix=log_prefix,
        )

        if not messages:
            return None

        # Concatenate ALL AI messages (original + continuation if truncated)
        raw_content = "\n".join(
            msg.content for msg in messages
            if getattr(msg, "type", None) == "ai" and hasattr(msg, "content")
        )
        return _extract_json_block(raw_content, keys=json_keys)

    except Exception as exc:
        logging.warning(f"{log_prefix} LLM call failed: {exc}")
        return None


# ============================================================================
# FeatureAuditor — Stage A (pre-synthesis)
# ============================================================================

class FeatureAuditor(BaseAgent):
    """Cross-hypothesis feature verification before synthesis.

    Reads all per-hypothesis line catalogs, builds a contradiction matrix,
    then spawns an LLM with spectrum-reading tools to independently verify
    whether each claimed feature is real or noise.

    Writes ``feature_audit_verdict.json`` and ``{idx}_lines_cleaned.csv``.
    """

    agent_name = "FeatureAuditor"

    def __init__(self, runtime: RuntimeContainer):
        super().__init__(runtime)

    async def run(self, state: SpectroState) -> SpectroState:
        """Run the feature audit stage."""
        harness_results = state.get("harness_results") or []

        if not harness_results:
            print("[FeatureAuditor] No harness results — skipping.")
            state["feature_audit_verdict"] = {
                "skipped": True, "reason": "No harness results to audit.",
            }
            return state

        # ── Resolve harness directory ──
        harness_dir = state.get("harness_dir")
        if not harness_dir:
            output_dir = state.get("output_dir") or ""
            file_name = state.get("file_name") or ""
            if output_dir and file_name:
                harness_dir = os.path.join(output_dir, f"{file_name}_harness")
            else:
                harness_dir = "."
        state["harness_dir"] = harness_dir

        # ── Build matrix ──
        spec = state["spectrum"]
        wl_min = float(spec["wavelength"][0])
        wl_max = float(spec["wavelength"][-1])

        matrix_rows, doublet_annotations, stats = build_contradiction_matrix(
            harness_results, harness_dir, wl_min, wl_max,
        )

        if not matrix_rows:
            print("[FeatureAuditor] No LIKELY/MARGINAL features found — skipping.")
            state["feature_audit_verdict"] = {
                "skipped": True,
                "reason": "No LIKELY or MARGINAL features to verify.",
                "stats": stats,
            }
            return state

        print(
            f"[FeatureAuditor] Matrix: {stats['n_rows']} rows, "
            f"{stats['n_total_features']} features across "
            f"{len(stats['hypothesis_indices'])} hypotheses. "
            f"Edge: {stats['n_edge_blue']}B + {stats['n_edge_red']}R. "
            f"Median |amp|: {stats['median_amplitude']:.4f}"
        )

        # ── Build prompts ──
        system_prompt = _load_feature_audit_skill()
        user_prompt = _build_feature_audit_user_message(
            state, harness_dir, matrix_rows, doublet_annotations, stats,
        )

        # ── Closure over spectrum arrays ──
        _wl = np.asarray(spec["wavelength"])
        _fl = np.asarray(spec["flux"])

        @tool
        def read_spectrum_region(wl_min: float, wl_max: float, stride: int = 1) -> dict:
            """Read a raw slice of the spectrum for manual inspection.

            Use this to independently verify whether claimed features are real.
            Read ±80 Å around each unique λ_obs in the matrix. Read BOTH edge
            zones in full (blue: λ_min→4000, red: 7800→λ_max).
            """
            mask = (_wl >= wl_min) & (_wl <= wl_max)
            wl_slice = _wl[mask][::stride]
            fl_slice = _fl[mask][::stride]
            return {
                "wl_range": [wl_min, wl_max],
                "n": len(wl_slice),
                "data": [
                    [round(float(w), 3), round(float(f), 4)]
                    for w, f in zip(wl_slice, fl_slice)
                ],
            }

        @tool
        def detect_oii_slope_change(target_wl: float, search_window: float = 25.0) -> dict:
            """Detect the [O II] unresolved doublet slope-change signature.

            [O II] 3727 is a close doublet (3726.0/3729.0 Å, rest sep 2.8 Å)
            unresolved at DESI resolution. A true single line ([O III]b, Hβ)
            cannot produce a derivative dip on the rising edge.

            Use this MANDATORY check when any hypothesis claims [O II] at an
            observed wavelength. Pass the claimed λ_obs as target_wl.
            """
            return _detect_oii_slope_change_core(_wl, _fl, target_wl, search_window)

        # ── Run LLM with retry on JSON parse failure ──
        MAX_RETRIES = 3
        parsed = None
        for attempt in range(MAX_RETRIES):
            parsed = await _run_llm_agent(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tools=[read_spectrum_region, grep_kb, detect_oii_slope_change],
                harness_dir=harness_dir,
                stream_filename="feature_audit_stream.md",
                stream_title="Feature Audit — Cross-Hypothesis Verification",
                json_keys=["feature_verdicts"],
                log_prefix="FeatureAuditor",
            )
            if parsed is not None and parsed.get("feature_verdicts"):
                break
            if attempt < MAX_RETRIES - 1:
                logging.warning(
                    f"[FeatureAuditor] LLM call {attempt+1}/{MAX_RETRIES} "
                    f"produced no valid JSON, retrying..."
                )
                await asyncio.sleep(1)

        if parsed is None or not parsed.get("feature_verdicts"):
            print("[FeatureAuditor] All retries exhausted — could not extract valid JSON.")
            parsed = {
                "skipped": True,
                "reason": (
                    f"Failed to parse valid JSON from LLM response "
                    f"after {MAX_RETRIES} attempts."
                ),
                "spectrum_quality": "unknown",
                "spectrum_quality_justification": (
                    f"Failed to parse valid JSON from LLM response "
                    f"after {MAX_RETRIES} attempts."
                ),
                "feature_verdicts": [],
                "global_issues": [],
                "doublet_verdicts": [],
            }

        state["feature_audit_verdict"] = parsed

        # ── Write cleaned CSVs ──
        feature_verdicts = parsed.get("feature_verdicts", [])
        if feature_verdicts:
            try:
                _write_cleaned_csvs(harness_dir, harness_results, feature_verdicts)
                print(
                    f"[FeatureAuditor] Wrote cleaned CSVs for "
                    f"{len(harness_results)} hypotheses."
                )
            except Exception as exc:
                logging.warning(f"[FeatureAuditor] Failed to write cleaned CSVs: {exc}")

        # ── Summary ──
        n_keep = sum(1 for v in feature_verdicts
                     if _clean_rec(v.get("recommendation")).startswith("KEEP"))
        n_remove = sum(1 for v in feature_verdicts
                       if _clean_rec(v.get("recommendation")).startswith("REMOVE"))
        n_flag = sum(1 for v in feature_verdicts
                     if _clean_rec(v.get("recommendation")).startswith("FLAG"))
        print(
            f"[FeatureAuditor] Verdicts: {n_keep} KEEP, {n_flag} FLAG, "
            f"{n_remove} REMOVE. Quality: {parsed.get('spectrum_quality', '?')}. "
            f"Doublets verified: {len(parsed.get('doublet_verdicts', []))}."
        )

        # Save verdict JSON
        verdict_path = os.path.join(harness_dir, "feature_audit_verdict.json")
        try:
            with open(verdict_path, "w", encoding="utf-8") as f:
                json.dump(parsed, f, indent=2, ensure_ascii=False)
        except Exception as exc:
            logging.warning(f"[FeatureAuditor] Failed to write verdict JSON: {exc}")

        return state


# ============================================================================
# AnalysisAuditor — Stage B (post-synthesis)
# ============================================================================

class AnalysisAuditor(BaseAgent):
    """Adversarial second reviewer for the synthesis verdict.

    Takes the synthesis output (rule_analysis + harness_results) and
    independently stress-tests the winning hypothesis by reading the raw
    spectrum.  Outputs a calibrated verdict: CONFIRM / DOWNGRADE / REJECT
    / UNCERTAIN.
    """

    agent_name = "AnalysisAuditor"

    def __init__(self, runtime: RuntimeContainer):
        super().__init__(runtime)
        self._writer = ResultWriter()

    # ========================================================================
    # Public entry point
    # ========================================================================

    async def run(self, state: SpectroState) -> SpectroState:
        """Run the synthesis audit.

        Reads ``state['rule_analysis']`` (the synthesis verdict) and
        synthesis.csv (the best-answer line catalog).  Spawns an LLM agent
        with ``read_spectrum_region`` + ``grep_kb`` + ``detect_oii_slope_change``
        tools for independent defensive review.
        """
        rule_analysis = state.get("rule_analysis") or {}

        # ── Guard: no synthesis results ──
        if not rule_analysis or rule_analysis.get("redshift") is None:
            print("[AnalysisAuditor] No valid synthesis verdict — skipping audit.")
            state["auditor_verdict"] = "SKIPPED: no synthesis verdict"
            state["auditor_verdict_json"] = {
                "verdict": "UNCERTAIN",
                "calibrated_confidence": "LOW",
                "spectrum_quality": "unknown",
                "has_real_peak": False,
                "confirmed_lines": [],
                "line_revisions": [],
                "spectrum_issues": ["No synthesis verdict to audit."],
                "reobserve": False,
                "reobserve_reason": None,
            }
            return state

        print(
            "[AnalysisAuditor] Auditing synthesis verdict: z={}, classification={}, "
            "confidence={}".format(
                rule_analysis.get("redshift"),
                rule_analysis.get("classification", "?"),
                rule_analysis.get("confidence", "?"),
            )
        )

        # ── Resolve harness directory ──
        harness_dir = state.get("harness_dir")
        if not harness_dir:
            output_dir = state.get("output_dir") or ""
            file_name = state.get("file_name") or ""
            if output_dir and file_name:
                harness_dir = os.path.join(output_dir, f"{file_name}_harness")
            else:
                harness_dir = "."

        # ── Build prompts ──
        system_prompt = _load_synthesis_audit_skill()
        user_prompt = _build_synthesis_audit_user_message(state, harness_dir)

        # ── Closure over spectrum arrays ──
        spec = state["spectrum"]
        _wl = np.asarray(spec["wavelength"])
        _fl = np.asarray(spec["flux"])

        @tool
        def read_spectrum_region(wl_min: float, wl_max: float, stride: int = 1) -> dict:
            """Read a raw slice of the spectrum for manual inspection.

            Use this to independently verify the winning hypothesis's key
            claims.  Read ±80 Å around each key line, and read BOTH edge
            zones in full (blue: λ_min→4000, red: 7800→λ_max).
            """
            mask = (_wl >= wl_min) & (_wl <= wl_max)
            wl_slice = _wl[mask][::stride]
            fl_slice = _fl[mask][::stride]
            return {
                "wl_range": [wl_min, wl_max],
                "n": len(wl_slice),
                "data": [
                    [round(float(w), 3), round(float(f), 4)]
                    for w, f in zip(wl_slice, fl_slice)
                ],
            }

        @tool
        def detect_oii_slope_change(target_wl: float, search_window: float = 25.0) -> dict:
            """Detect the [O II] unresolved doublet slope-change signature.

            [O II] 3727 is a close doublet (3726.0/3729.0 Å, rest sep 2.8 Å)
            unresolved at DESI resolution. A true single line ([O III]b, Hβ)
            cannot produce a derivative dip on the rising edge.

            Use this when the synthesis verdict hinges on a [O II] identification
            and you need independent morphological confirmation.
            """
            return _detect_oii_slope_change_core(_wl, _fl, target_wl, search_window)

        # ── Run LLM ──
        parsed = await _run_llm_agent(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=[read_spectrum_region, grep_kb, detect_oii_slope_change],
            harness_dir=harness_dir,
            stream_filename="auditor_stream.md",
            stream_title="Auditor — Synthesis Audit",
            json_keys=["verdict", "line_revisions", "spectrum_issues"],
            log_prefix="AnalysisAuditor",
        )

        if parsed is None:
            print("[AnalysisAuditor] Could not extract JSON from audit response.")
            state["auditor_verdict"] = "ERROR: could not parse JSON"
            state["auditor_verdict_json"] = {
                "verdict": "UNCERTAIN",
                "calibrated_confidence": "LOW",
                "spectrum_quality": "unknown",
                "has_real_peak": False,
                "confirmed_lines": [],
                "line_revisions": [],
                "spectrum_issues": ["Failed to parse JSON from auditor response."],
                "reobserve": False,
                "reobserve_reason": None,
            }
            return state

        state["auditor_verdict_json"] = parsed
        state["auditor_verdict"] = parsed.get("verdict", "?")

        n_revisions = len(parsed.get("line_revisions", []))
        n_issues = len(parsed.get("spectrum_issues", []))
        print(
            "[AnalysisAuditor] Verdict: {} | Confidence: {} | Quality: {} | "
            "Revisions: {} | Issues: {} | Reobserve: {}".format(
                parsed.get("verdict", "?"),
                parsed.get("calibrated_confidence", "?"),
                parsed.get("spectrum_quality", "?"),
                n_revisions,
                n_issues,
                parsed.get("reobserve", False),
            )
        )
        if n_revisions:
            for rev in parsed["line_revisions"]:
                print(f"  📝 {rev['line']} → {rev['action']}: {rev['reason']}")
        if n_issues:
            for issue in parsed["spectrum_issues"]:
                print(f"  ⚠ {issue}")

        return state
