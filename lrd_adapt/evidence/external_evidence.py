"""
External-evidence channel (CLAUDE.md change budget, new code #4).

Stage A (line-identity/redshift verification) deliberately keeps the
LRD-vs-classical-AGN question out of scope (see kb/classification.md's
scope note). Stage B needs the evidence that call actually depends on:
photometry, Balmer-break estimate, compactness (r_circ), X-ray coverage,
morphology notes — none of which come from the spectrum itself. This
module loads that evidence, per anonymized SRC code, and renders it as the
"External Evidence" block CLAUDE.md specifies for single_hypothesis.py's
user prompt.

Every item is provenance-tagged ("paper" — from Kapoor+26 directly — or
"ours" — independently measured/derived by this pipeline) per CLAUDE.md's
Anonymization section. Agent-visible text here must stay in terms of the
anonymized SRC code only — never real J-names or coordinates.
"""
from __future__ import annotations

import json
import os


def _default_table_path():
    return os.path.join(os.path.dirname(__file__), "..", "configs", "external_evidence.json")


def load_external_evidence(source_code, table_path=None):
    """
    Parameters
    ----------
    source_code : str
        Anonymized code (e.g. "SRC01").
    table_path : str, optional
        Defaults to lrd_adapt/configs/external_evidence.json, or the
        LRD_EXTERNAL_EVIDENCE_TABLE environment variable if set.

    Returns
    -------
    dict or None
        The raw evidence entry for this source, or None if not registered
        (external evidence is optional per source — a Stage A run should
        still work without it; the Stage B verdict just can't be made yet).
    """
    path = table_path or os.getenv("LRD_EXTERNAL_EVIDENCE_TABLE") or _default_table_path()
    if not os.path.exists(path):
        return None
    with open(path) as f:
        table = json.load(f)
    return table.get(source_code)


def load_external_evidence_for_state(state):
    """Convenience wrapper keyed off state['file_name'] (the anonymized code)."""
    source_code = state.get("file_name") if isinstance(state, dict) else None
    if not source_code:
        return None
    return load_external_evidence(source_code)


_FIELD_LABELS = {
    "photometry": "Photometry / continuum shape",
    "balmer_break": "Balmer break (f_nu,4050/f_nu,3650)",
    "r_circ_mas": "Compactness (circularized half-light radius, mas)",
    "morphology_notes": "Morphology notes",
}


def format_external_evidence_markdown(evidence):
    """Render an evidence dict as a Markdown block for the LLM user prompt.

    Returns "" if evidence is falsy, so callers can unconditionally splice
    the result into a prompt without an extra branch.
    """
    if not evidence:
        return ""

    lines = [
        "## External Evidence",
        "",
        "Independent of the spectrum itself — used for the Stage B LRD-vs-"
        "classical-AGN call (see kb/lrd_classification.md), never for line "
        "identity or redshift. Each item is tagged `[paper]` (Kapoor+26) or "
        "`[ours]` (independently derived).",
        "",
        "| Item | Value | Provenance |",
        "|------|-------|------------|",
    ]

    for key, label in _FIELD_LABELS.items():
        item = evidence.get(key)
        if not item:
            continue
        value = item.get("value")
        if key == "balmer_break" and "unit" in item:
            value = f"{value} ({item['unit']})"
        provenance = item.get("provenance", "unknown")
        lines.append(f"| {label} | {value} | [{provenance}] |")

    xray = evidence.get("xray")
    if xray:
        provenance = xray.get("provenance", "unknown")
        if not xray.get("coverage", True):
            xray_desc = "no X-ray coverage in this field (absence of X-ray != absence of emission)"
        elif xray.get("detected"):
            xray_desc = "X-ray detected"
        else:
            xray_desc = "covered, not detected"
        lines.append(f"| X-ray | {xray_desc} | [{provenance}] |")

    return "\n".join(lines) + "\n"
