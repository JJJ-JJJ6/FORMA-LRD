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
from langchain_core.messages import HumanMessage

from AstroAgent.agents.common.state import SpectroState
from AstroAgent.agents.common.base_agent import BaseAgent
from AstroAgent.agents.common.result_writer import ResultWriter
from AstroAgent.core.runtime.runtime_container import RuntimeContainer
from AstroAgent.core.llm import _detect_vendor, _build_thinking_extra_body, _create_chat_openai
from AstroAgent.agents.multi_agents.harness.tools import grep_kb


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
        return 16384
    return None


def _find_last_ai_message(messages: list):
    """Return the last AI message, skipping tool/other message types."""
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "ai":
            return msg
    return None


def _is_truncated(messages: list) -> bool:
    """Check whether the last AI message was truncated by max_tokens."""
    last_ai = _find_last_ai_message(messages)
    if last_ai is None:
        return False
    return last_ai.response_metadata.get("finish_reason") == "length"


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

def _format_tool_call(tc) -> str:
    """Format a single tool call as readable markdown."""
    name = tc.get("name", "unknown")
    args = tc.get("args", {})
    args_json = json.dumps(args, indent=2, ensure_ascii=False)
    return f"**`{name}`**\n```json\n{args_json}\n```"


def _format_tool_result(msg) -> str:
    """Format a tool result message as readable markdown."""
    content = msg.content if hasattr(msg, "content") else str(msg)
    try:
        parsed = json.loads(content) if isinstance(content, str) else content
        return f"```json\n{json.dumps(parsed, indent=2, ensure_ascii=False)}\n```"
    except (json.JSONDecodeError, TypeError):
        return str(content)


# ============================================================================
# Doublet definitions (shared between matrix builder and skills)
# ============================================================================

# ratio_check_fn: (amp_a, amp_b) -> (ratio, is_expected, note)
DOUBLET_DEFS = [
    {
        "name_a": "Ca K_abs", "rest_a": 3934.8,
        "name_b": "Ca H_abs", "rest_b": 3969.6,
        "sep_rest": 34.8,
        "ratio_desc": "K MUST be deeper than H (|amp_K| > |amp_H|)",
        "check": lambda a, b: (
            abs(b) / max(abs(a), 1e-10),
            abs(a) > abs(b),
            "K deeper" if abs(a) > abs(b) else "REVERSED: H deeper than K",
        ),
    },
    {
        "name_a": "[O III]a", "rest_a": 4960.3,
        "name_b": "[O III]b", "rest_b": 5008.2,
        "sep_rest": 47.9,
        "ratio_desc": "b:a ≈ 3:1 (ratio range 0.25–0.40 acceptable)",
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
        "ratio_desc": "a:b ≈ 1:3 (ratio range 0.25–0.40 acceptable)",
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
        "ratio_desc": "a ≈ b (ratio range 0.7–1.4 acceptable)",
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

def _group_features_by_wavelength(
    all_features: List[dict],
    tolerance: float = 10.0,
) -> List[List[dict]]:
    """Group features by observed wavelength proximity.

    Features within ``tolerance`` Å of each other are merged into the same
    group.  Each group becomes one row in the contradiction matrix.
    """
    if not all_features:
        return []
    sorted_feats = sorted(all_features, key=lambda f: f["wl_obs"])
    groups = []
    used = set()
    for i, f in enumerate(sorted_feats):
        if i in used:
            continue
        group = [f]
        used.add(i)
        for j in range(i + 1, len(sorted_feats)):
            if j in used:
                continue
            if abs(sorted_feats[j]["wl_obs"] - f["wl_obs"]) <= tolerance:
                group.append(sorted_feats[j])
                used.add(j)
        groups.append(group)
    return groups


def _detect_doublets(
    features_by_hyp: Dict[int, List[dict]],
    redshift_by_hyp: Dict[int, float],
) -> List[dict]:
    """Detect known doublet pairs within each hypothesis.

    Returns a list of doublet annotations.
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
            for fa in candidates_a:
                for fb in candidates_b:
                    sep_expected = dd["sep_rest"] * (1.0 + redshift)
                    sep_actual = abs(fb["wl_obs"] - fa["wl_obs"])
                    if abs(sep_actual - sep_expected) <= max(10.0, sep_expected * 0.1):
                        ratio, is_expected, note = dd["check"](fa["amplitude"], fb["amplitude"])
                        annotations.append({
                            "hypothesis_idx": hi,
                            "name_a": dd["name_a"],
                            "wl_a": fa["wl_obs"],
                            "amp_a": fa["amplitude"],
                            "name_b": dd["name_b"],
                            "wl_b": fb["wl_obs"],
                            "amp_b": fb["amplitude"],
                            "ratio": round(ratio, 3),
                            "is_expected": is_expected,
                            "note": note,
                            "sep_expected": round(sep_expected, 1),
                            "sep_actual": round(sep_actual, 1),
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
                line_type = "abs" if name.endswith("_abs") else "em"

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
                    "fwhm_km_s": row.get("fwhm_km_s", "—"),
                }
                all_features.append(feat)
                features_by_hyp[idx].append(feat)

    if not all_features:
        return [], [], {"n_rows": 0, "n_total_features": 0, "n_edge_blue": 0,
                        "n_edge_red": 0, "median_amplitude": 0, "top_quartile_amplitude": 0}

    groups = _group_features_by_wavelength(all_features)

    hyp_indices = sorted(set(f["hypothesis_idx"] for f in all_features))
    hyp_info = {}
    for r in harness_results:
        idx = r.get("hypothesis_idx")
        if idx is not None:
            hyp_info[idx] = {"redshift": r.get("redshift", 0)}

    matrix_rows = []
    for group in groups:
        wls = sorted(f["wl_obs"] for f in group)
        rep_wl = wls[len(wls) // 2]
        cells = {}
        for f in group:
            cells[f["hypothesis_idx"]] = f

        # Type and amplitude from the first feature in the group
        # (all features in a group are the same CWT detection — type & amp are identical)
        first = group[0]

        matrix_rows.append({
            "wl_obs": round(rep_wl, 1),
            "cells": cells,
            "n_hypotheses": len(cells),
            "is_edge_blue": rep_wl < 4000.0,
            "is_edge_red": rep_wl > 9000.0,
            "row_type": first["line_type"],
            "row_amp": first["amplitude"],
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
    parts.append(f"- **Red edge**: 9000 – {wl_right:.0f} Å (OH skyline residuals)")
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
        "Type and Amp are properties of the CWT-detected feature itself (identical "
        "across hypotheses — same feature, different name assignments). "
        "Doublet annotations (➔) show pre-computed amplitude ratios with ✓ (pass) "
        "or ✗ (fail)."
    )
    parts.append("")

    col_header = "| λ_obs (Å) | Type | Amp |"
    col_sep = "|-----------|------|------|"
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
        parts.append("## Doublet Ratio Checks")
        parts.append("")
        for da in doublet_annotations:
            symbol = "✓" if da["is_expected"] else "✗"
            parts.append(
                f"- **H{da['hypothesis_idx']}**: {da['name_a']}@{da['wl_a']:.1f} + "
                f"{da['name_b']}@{da['wl_b']:.1f} → "
                f"ratio {da['note']} (expected {da['sep_expected']:.1f} Å sep, "
                f"actual {da['sep_actual']:.1f} Å) {symbol}"
            )
        parts.append("")

    # ── Task ──
    parts.append("## Task")
    parts.append("")
    parts.append(
        "Follow the Step 1 → Step 6 methodology from your system prompt. "
        "Your job is to independently verify whether each feature is physically "
        "real or a noise artifact. You MUST:\n\n"
        "1. Batch-read the spectrum at EVERY unique λ_obs in the matrix "
        "(±80 Å per read, merge adjacent rows when closer than 80 Å)\n"
        "2. Read BOTH edge zones in full\n"
        "3. Apply the Three-Question Test to each feature\n"
        "4. Check doublet annotations for ratio violations\n"
        "5. Output a verdict for EVERY row in the matrix\n\n"
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
    harness_results = state.get("harness_results") or []

    # ── Spectrum metadata ──
    wl = state["spectrum"]["wavelength"]
    wl_left = float(wl[0])
    wl_right = float(wl[-1])

    spec_lines = [
        f"## Spectrum",
        f"- Wavelength range: {wl_left:.0f} – {wl_right:.0f} Å",
    ]

    if harness_results:
        first_meta = harness_results[0].get("hypothesis_meta") or {}
        snr = first_meta.get("snr_median")
        if snr is not None:
            spec_lines.append(f"- Median SNR: {float(snr):.1f}")

    spec_lines.append(f"- **Blue edge**: {wl_left:.0f} – 4000 Å (throughput drop, non-Gaussian noise)")
    spec_lines.append(f"- **Red edge**: 9000 – {wl_right:.0f} Å (OH skyline residuals)")

    parts = ["\n".join(spec_lines), ""]

    # ── Synthesis verdict ──
    parts.append("## Synthesis Verdict (from synthesize.py)")
    parts.append("")
    parts.append("```json")
    parts.append(json.dumps(rule_analysis, indent=2, ensure_ascii=False, default=str))
    parts.append("```")
    parts.append("")

    # ── Winning hypothesis line data ──
    best_idx = rule_analysis.get("best_hypothesis_idx")
    if best_idx is not None and harness_results:
        winner = None
        for r in harness_results:
            if r.get("hypothesis_idx") == best_idx:
                winner = r
                break

        if winner:
            parts.append("## Winning Hypothesis (H{}) — z={}".format(
                best_idx,
                winner.get("redshift", "?"),
            ))
            parts.append("")

            csv_path = os.path.join(harness_dir, f"{best_idx}_lines.csv")
            if os.path.exists(csv_path):
                rows = []
                with open(csv_path, newline="", encoding="utf-8") as f:
                    for row in _csv.DictReader(f):
                        rows.append(row)

                if rows:
                    likely = [r for r in rows if r.get("status", "").strip() == "LIKELY"]
                    marginal = [r for r in rows if r.get("status", "").strip() == "MARGINAL"]
                    other = [r for r in rows if r.get("status", "").strip() not in ("LIKELY", "MARGINAL")]

                    cols = list(rows[0].keys())
                    header = "| " + " | ".join(cols) + " |\n|" + "|".join(["------"] * len(cols)) + "|"

                    def _fmt_rows(rlist):
                        lines = []
                        for row in rlist:
                            vals = [(row.get(c) or "").strip() or "—" for c in cols]
                            lines.append("| " + " | ".join(vals) + " |")
                        return "\n".join(lines)

                    parts.append(f"### LIKELY ({len(likely)} lines)")
                    parts.append("")
                    parts.append(header)
                    parts.append(_fmt_rows(likely))
                    parts.append("")

                    if marginal:
                        parts.append(f"### MARGINAL ({len(marginal)} lines)")
                        parts.append("")
                        parts.append(header)
                        parts.append(_fmt_rows(marginal))
                        parts.append("")

                    if other:
                        parts.append(f"### Other ({len(other)} lines)")
                        parts.append("")
                        parts.append(header)
                        parts.append(_fmt_rows(other))
                        parts.append("")
                else:
                    parts.append("*(no line data found)*")
                    parts.append("")
            else:
                report = winner.get("report", "")
                if report:
                    parts.append("<details>")
                    parts.append("<summary>Full harness report</summary>")
                    parts.append("")
                    parts.append(report)
                    parts.append("</details>")
                    parts.append("")

    # ── 2nd-best hypothesis ──
    excluded = rule_analysis.get("excluded_hypotheses") or []
    if excluded:
        parts.append("## Rejected Alternative (2nd-best)")
        parts.append("")
        first_excluded = excluded[0] if isinstance(excluded, list) else excluded
        if isinstance(first_excluded, dict):
            idx2 = first_excluded.get("idx")
            z2 = first_excluded.get("z")
            reason2 = first_excluded.get("reason", "no reason given")
            parts.append(f"- H{idx2} at z={z2}: {reason2}")
            parts.append("")

            if idx2 is not None:
                csv_path2 = os.path.join(harness_dir, f"{idx2}_lines.csv")
                if os.path.exists(csv_path2):
                    likely2 = []
                    with open(csv_path2, newline="", encoding="utf-8") as f:
                        for row in _csv.DictReader(f):
                            if row.get("status", "").strip() == "LIKELY":
                                likely2.append(row)
                    if likely2:
                        parts.append(f"Its LIKELY lines ({len(likely2)}):")
                        for r in likely2:
                            name = r.get("name", "?")
                            wl_pred = r.get("predicted_obs", "?")
                            parts.append(f"  - {name} at λ_pred={wl_pred} Å")
                        parts.append("")

    # ── Task ──
    parts.append("## Task")
    parts.append("")
    parts.append(
        "Follow the Step 1 → Step 6 methodology from your system prompt. "
        "Your value is independent spectrum verification — you MUST call "
        "`read_spectrum_region` for every key claim. Read BOTH edge zones "
        "in full. Calibrate the confidence level. Output your reasoning in "
        "free text, then end with the JSON verdict block."
    )

    return "\n".join(parts)


# ============================================================================
# Cleaned CSV writer (Stage A output)
# ============================================================================

def _write_cleaned_csvs(
    harness_dir: str,
    harness_results: List[dict],
    feature_verdicts: List[dict],
) -> None:
    """Write ``{idx}_lines_cleaned.csv`` files with noise features annotated.

    Builds a lookup from (wl_obs) → verdict, then for each hypothesis's CSV:
    - Features whose wl_obs matches a REMOVE verdict are marked REMOVED
    - Features whose wl_obs matches a FLAG verdict are marked FLAGGED
    - KEEP features are written unchanged
    - All features get ``feature_audit`` and ``feature_audit_flag`` columns.
    """
    verdict_lookup = {}
    for v in feature_verdicts:
        wl = v.get("wl_obs")
        if wl is not None:
            verdict_lookup[round(float(wl), 1)] = v

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

                out_row = dict(row)
                out_row["feature_audit"] = ""
                out_row["feature_audit_flag"] = ""

                if status in ("LIKELY", "MARGINAL") and wl_obs > 0:
                    verdict = verdict_lookup.get(round(wl_obs, 1))
                    if verdict:
                        rec = verdict.get("recommendation", "KEEP")
                        if rec == "REMOVE":
                            out_row["feature_audit"] = "REMOVED"
                            out_row["feature_audit_flag"] = "; ".join(
                                verdict.get("issues", [])
                            )
                        elif rec == "FLAG":
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

        # ── Truncation detection ─
        if _is_truncated(accumulated_messages):
            logging.warning(
                f"{log_prefix} output truncated (finish_reason=length). "
                "Requesting continuation..."
            )
            try:
                _ctn_md = open(stream_md_path, "a", encoding="utf-8")
                continuation_msgs = list(accumulated_messages)
                continuation_msgs.append(
                    HumanMessage(
                        content=(
                            "Your previous response was truncated. Continue EXACTLY "
                            "from where you stopped. Do NOT repeat anything. Output "
                            "ONLY the remaining content."
                        )
                    )
                )
                async for event in agent.astream(
                    {"messages": continuation_msgs},
                    config=config,
                    stream_mode="updates",
                ):
                    for _node_name, update in event.items():
                        for msg in update.get("messages", []):
                            msg_type = getattr(msg, "type", None)
                            if msg_type == "ai":
                                content = (
                                    msg.content
                                    if hasattr(msg, "content")
                                    else ""
                                )
                                _ctn_md.write(
                                    "### Assistant (continuation)\n\n"
                                    f"{content.strip()}\n\n"
                                )
                                _ctn_md.flush()
                            accumulated_messages.append(msg)
                _ctn_md.close()
                logging.info(f"{log_prefix} continuation completed successfully.")
            except Exception as exc:
                logging.warning(
                    f"{log_prefix} continuation retry failed: {exc}. "
                    "Returning truncated result."
                )
                try:
                    _ctn_md.close()
                except Exception:
                    pass

        last_msg = _find_last_ai_message(accumulated_messages)
        raw_content = (
            last_msg.content if last_msg and hasattr(last_msg, "content") else ""
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

        if _is_truncated(messages):
            logging.warning(
                f"{log_prefix} output truncated. Requesting continuation..."
            )
            try:
                continuation_msgs = list(messages)
                continuation_msgs.append(
                    HumanMessage(
                        content=(
                            "Your previous response was truncated. Continue EXACTLY "
                            "from where you stopped. Do NOT repeat anything. Output "
                            "ONLY the remaining content."
                        )
                    )
                )
                continuation_result = await agent.ainvoke(
                    {"messages": continuation_msgs}, config=config
                )
                messages.extend(continuation_result.get("messages", []))
            except Exception as exc:
                logging.warning(f"{log_prefix} continuation failed: {exc}")

        if not messages:
            return None

        last_msg = _find_last_ai_message(messages)
        raw_content = (
            last_msg.content if last_msg and hasattr(last_msg, "content") else ""
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
            zones in full (blue: λ_min→4000, red: 9000→λ_max).
            """
            mask = (_wl >= wl_min) & (_wl <= wl_max)
            wl_slice = _wl[mask][::stride]
            fl_slice = _fl[mask][::stride]
            return {
                "wl_range": [wl_min, wl_max],
                "n": len(wl_slice),
                "wl": [round(float(w), 3) for w in wl_slice],
                "fl": [round(float(f), 4) for f in fl_slice],
            }

        # ── Run LLM ──
        parsed = await _run_llm_agent(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=[read_spectrum_region, grep_kb],
            harness_dir=harness_dir,
            stream_filename="feature_audit_stream.md",
            stream_title="Feature Audit — Cross-Hypothesis Verification",
            json_keys=["feature_verdicts"],
            log_prefix="FeatureAuditor",
        )

        if parsed is None:
            print("[FeatureAuditor] Could not extract JSON from audit response.")
            parsed = {
                "spectrum_quality": "unknown",
                "spectrum_quality_justification": "Failed to parse JSON from LLM response.",
                "feature_verdicts": [],
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
        n_keep = sum(1 for v in feature_verdicts if v.get("recommendation") == "KEEP")
        n_remove = sum(1 for v in feature_verdicts if v.get("recommendation") == "REMOVE")
        n_flag = sum(1 for v in feature_verdicts if v.get("recommendation") == "FLAG")
        print(
            f"[FeatureAuditor] Verdicts: {n_keep} KEEP, {n_flag} FLAG, "
            f"{n_remove} REMOVE. Quality: {parsed.get('spectrum_quality', '?')}"
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
        ``state['harness_results']`` (per-hypothesis reports), then spawns
        an LLM agent with ``read_spectrum_region`` + ``grep_kb`` tools to
        independently verify the winning hypothesis.
        """
        rule_analysis = state.get("rule_analysis") or {}
        harness_results = state.get("harness_results") or []

        # ── Guard: no synthesis results ──
        if not rule_analysis or rule_analysis.get("redshift") is None:
            print("[AnalysisAuditor] No valid synthesis verdict — skipping audit.")
            state["auditor_verdict"] = "SKIPPED: no synthesis verdict"
            state["auditor_verdict_json"] = {
                "verdict": "UNCERTAIN",
                "calibrated_confidence": "LOW",
                "spectrum_quality": "unknown",
                "key_issues": ["No synthesis verdict to audit."],
                "recommendation": "Synthesis pipeline did not produce a valid result.",
            }
            return state

        if not harness_results:
            print("[AnalysisAuditor] No harness results — skipping audit.")
            state["auditor_verdict"] = "SKIPPED: no harness results"
            state["auditor_verdict_json"] = {
                "verdict": "UNCERTAIN",
                "calibrated_confidence": "LOW",
                "spectrum_quality": "unknown",
                "key_issues": ["No harness results available for audit."],
                "recommendation": "Harness pipeline did not produce results.",
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
            zones in full (blue: λ_min→4000, red: 9000→λ_max).
            """
            mask = (_wl >= wl_min) & (_wl <= wl_max)
            wl_slice = _wl[mask][::stride]
            fl_slice = _fl[mask][::stride]
            return {
                "wl_range": [wl_min, wl_max],
                "n": len(wl_slice),
                "wl": [round(float(w), 3) for w in wl_slice],
                "fl": [round(float(f), 4) for f in fl_slice],
            }

        # ── Run LLM ──
        parsed = await _run_llm_agent(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=[read_spectrum_region, grep_kb],
            harness_dir=harness_dir,
            stream_filename="auditor_stream.md",
            stream_title="Auditor — Synthesis Audit",
            json_keys=["verdict"],
            log_prefix="AnalysisAuditor",
        )

        if parsed is None:
            print("[AnalysisAuditor] Could not extract JSON from audit response.")
            state["auditor_verdict"] = "ERROR: could not parse JSON"
            state["auditor_verdict_json"] = {
                "verdict": "UNCERTAIN",
                "calibrated_confidence": "LOW",
                "spectrum_quality": "unknown",
                "key_issues": ["Failed to parse JSON from auditor response."],
                "recommendation": "Auditor LLM produced unparseable output.",
            }
            return state

        state["auditor_verdict_json"] = parsed

        print(
            "[AnalysisAuditor] Verdict: {} | Confidence: {} | Quality: {}".format(
                parsed.get("verdict", "?"),
                parsed.get("calibrated_confidence", "?"),
                parsed.get("spectrum_quality", "?"),
            )
        )
        if parsed.get("key_issues"):
            for issue in parsed["key_issues"]:
                print(f"  ⚠ {issue}")

        return state
