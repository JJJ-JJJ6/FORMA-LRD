"""
Utility functions for RuleAnalyst — hypothesis collection, Dn4000 diagnostics,
harness report middleware, and formatting helpers.
"""

import asyncio
import csv
import json
import logging
import os
import re
import numpy as np


# =========================================================================
# Hypothesis collection
# =========================================================================

def collect_hypotheses(scoring: dict) -> list:
    """Merge low_z / high_z from scoring into a flat list sorted by score."""
    hypotheses = []
    for cat_key in ('low_z', 'high_z'):
        cat_label = cat_key.rstrip('_z')
        for h in scoring.get(cat_key, []):
            hypotheses.append({**h, 'category': cat_label})
    hypotheses.sort(key=lambda h: -h['score'])
    return hypotheses


# =========================================================================
# Dn4000 diagnostic
# =========================================================================

def compute_dn4000(wl, fl, z):
    """Compute Dn4000 for a given redshift hypothesis.

    Dn4000 = ⟨F(4000–4100 Å rest)⟩ / ⟨F(3850–3950 Å rest)⟩  (Balogh+ 1999)

    Returns a tuple (dn4000, n_blue, n_red, blue_mean, red_mean,
    blue_lo, blue_hi, red_lo, red_hi, blue_fluxes, red_fluxes) or None if
    the required rest-frame windows fall outside the observed range.
    """
    blue_lo, blue_hi = 3850.0 * (1.0 + z), 3950.0 * (1.0 + z)
    red_lo,  red_hi  = 4000.0 * (1.0 + z), 4100.0 * (1.0 + z)

    mask_blue = (wl >= blue_lo) & (wl < blue_hi)
    mask_red  = (wl >= red_lo)  & (wl < red_hi)

    n_blue = int(np.sum(mask_blue))
    n_red  = int(np.sum(mask_red))

    if n_blue < 3 or n_red < 3:
        return None

    blue_mean = float(np.mean(fl[mask_blue]))
    red_mean  = float(np.mean(fl[mask_red]))

    if blue_mean <= 0:
        return None

    dn4000 = red_mean / blue_mean

    blue_fluxes = fl[mask_blue]
    red_fluxes  = fl[mask_red]

    return (dn4000, n_blue, n_red, blue_mean, red_mean,
            blue_lo, blue_hi, red_lo, red_hi,
            blue_fluxes, red_fluxes)


def build_dn4000_lookup(wl, fl, harness_results: list) -> dict:
    """Build a lookup dict for Dn4000 values keyed by rounded redshift.

    Returns
    -------
    dict[float, dict]
        {z: {'dn4000': float|None, 'interpretation': str}}.
        Interpretation is one of: "strong 4000 Å break",
        "moderate 4000 Å break", "weak or absent 4000 Å break",
        "insufficient coverage".
    """
    lookup = {}
    zs = set()
    for r in harness_results:
        if r.get('error'):
            continue
        z = r['z'] if 'z' in r else r.get('redshift', 0)
        zs.add(round(z, 4))

    for z in sorted(zs):
        result = compute_dn4000(wl, fl, z)
        if result is None:
            lookup[z] = {'dn4000': None, 'interpretation': 'insufficient coverage'}
        else:
            dn4000 = result[0]
            if dn4000 > 1.6:
                interp = "strong 4000 Å break"
            elif dn4000 > 1.3:
                interp = "moderate 4000 Å break"
            else:
                interp = "weak or absent 4000 Å break"
            lookup[z] = {'dn4000': dn4000, 'interpretation': interp}

    return lookup


def prepare_diagnostic_slices(wl, fl, harness_results: list) -> str:
    """Build a Dn4000 comparison table for all hypotheses.

    Dn4000 (Balogh+ 1999) is the single most powerful continuum-based
    tiebreaker — it is completely independent of the CWT→harness pipeline.

    Interpretation:
      Dn4000 > 1.6  → strong 4000 Å break
      Dn4000 1.3–1.6 → moderate 4000 Å break
      Dn4000 < 1.3  → weak or absent 4000 Å break
    """
    lookup = build_dn4000_lookup(wl, fl, harness_results)

    if not lookup:
        return "(Dn4000 could not be computed for any hypothesis — windows outside spectral range.)"

    sections = ["## Dn4000 Comparison\n"]
    sections.append(
        "| Hypothesis z | Dn4000 | Interpretation |\n"
        "|-------------|--------|----------------|"
    )

    for z, info in sorted(lookup.items()):
        dn4000 = info['dn4000']
        interp = info['interpretation']
        if dn4000 is None:
            sections.append(f"| z={z:.4f} | — (insufficient coverage) | — |")
        else:
            sections.append(
                f"| z={z:.4f} | **{dn4000:.3f}** | {interp} |"
            )
    sections.append("")

    return "\n".join(sections)


# =========================================================================
# Harness report middleware — LLM-driven structured extraction
# =========================================================================

# ── Line name normalisation ──────────────────────────────────────────
# Maps variant names from harness reports to canonical names and rest
# wavelengths.  Format: "Ion λ_rest" for emission, "Ion λ_rest_abs" for
# absorption.

_LINE_ALIASES: dict[str, tuple[str, float]] = {
    # ── Emission lines ─────────────────────────────────────────
    "O [II]":            ("[O II] 3727",    3727.0),
    "[O II]":             ("[O II] 3727",    3727.0),
    "[O II] 3727":        ("[O II] 3727",    3727.0),
    "O[II]":              ("[O II] 3727",    3727.0),
    "Ne [V]":             ("Ne V 3426",      3426.0),
    "Ne V":               ("Ne V 3426",      3426.0),
    "Ne[V]":              ("Ne V 3426",      3426.0),
    "O [III]a":           ("[O III]a 4960.3", 4960.3),
    "[O III]a":           ("[O III]a 4960.3", 4960.3),
    "O [III]b":           ("[O III]b 5008.2", 5008.2),
    "[O III]b":           ("[O III]b 5008.2", 5008.2),
    "N[II]a":             ("[N II]a 6549.9", 6549.9),
    "N [II]a":            ("[N II]a 6549.9", 6549.9),
    "[N II]a":            ("[N II]a 6549.9", 6549.9),
    "N[II]b":             ("[N II]b 6585.3", 6585.3),
    "N [II]b":            ("[N II]b 6585.3", 6585.3),
    "[N II]b":            ("[N II]b 6585.3", 6585.3),
    "S[II]a":             ("[S II]a 6718.3", 6718.3),
    "S [II]a":            ("[S II]a 6718.3", 6718.3),
    "[S II]a":            ("[S II]a 6718.3", 6718.3),
    "S[II]b":             ("[S II]b 6732.7", 6732.7),
    "S [II]b":            ("[S II]b 6732.7", 6732.7),
    "[S II]b":            ("[S II]b 6732.7", 6732.7),
    "Mg II":              ("Mg II 2800",     2800.0),
    "Mg II 2800":         ("Mg II 2800",     2800.0),
    "C IV":               ("C IV 1549",      1549.0),
    "C III]":             ("C III] 1909",    1909.0),
    "He II":              ("He II 1640.4",   1640.4),
    "Lyα":                ("Lyα 1216",       1216.0),
    "Hα":                 ("Hα 6564.6",      6564.6),
    "Hβ":                 ("Hβ 4862.7",      4862.7),
    "Hγ":                 ("Hγ 4341.7",      4341.7),
    "Hγ 4341.7":          ("Hγ 4341.7",      4341.7),
    "Hδ":                 ("Hδ 4102.9",      4102.9),
    "Hε":                 ("Hε 3970.1",      3970.1),
    "Hε 3970":            ("Hε 3970.1",      3970.1),
    # ── Absorption lines ───────────────────────────────────────
    "Ca K":               ("Ca K 3934.8_abs",  3934.8),
    "Ca K_abs":           ("Ca K 3934.8_abs",  3934.8),
    "Ca H":               ("Ca H 3969.6_abs",  3969.6),
    "Ca H_abs":           ("Ca H 3969.6_abs",  3969.6),
    "Na D_abs":           ("Na D 5895_abs",    5895.0),
    "Na D":               ("Na D 5895_abs",    5895.0),
    "G-band_abs":         ("G-band 4305.6_abs", 4305.6),
    "G-band":             ("G-band 4305.6_abs", 4305.6),
    "Mg_abs":             ("Mg I 5176.7_abs",  5176.7),
    "Mg I_abs":           ("Mg I 5176.7_abs",  5176.7),
    "Hδ_abs":             ("Hδ 4102.9_abs",    4102.9),
    "Hγ_abs":             ("Hγ 4341.7_abs",    4341.7),
    "Hβ_abs":             ("Hβ 4862.7_abs",    4862.7),
    "Hα_abs":             ("Hα 6564.6_abs",    6564.6),
    "Hε_abs":             ("Hε 3970.1_abs",    3970.1),
    "Mg II_abs":          ("Mg II 2800_abs",   2800.0),
    "CaT2_abs":           ("CaT2 8544.4_abs",  8544.4),
    "CaT3_abs":           ("CaT3 8662.0_abs",  8662.0),
}


def _normalise_line_name(raw: str) -> str:
    """Normalise a line name from a harness report to canonical form.

    Returns the canonical name (e.g. ``[O II] 3727``) or the original
    string if no mapping is found.
    """
    # Exact match
    if raw in _LINE_ALIASES:
        return _LINE_ALIASES[raw][0]
    # Try stripping trailing "(Balmer ...)" annotations
    clean = re.sub(r'\s*\(.*\)', '', raw).strip()
    if clean in _LINE_ALIASES:
        return _LINE_ALIASES[clean][0]
    # Try replacing [N] with N (common bracket variant)
    if clean != raw and clean in _LINE_ALIASES:
        return _LINE_ALIASES[clean][0]
    # Return as-is if we can't map it
    return raw


# ── Extraction prompt (LLM only extracts natural-language fields) ──

EXTRACTION_PROMPT = """\
Extract a few key fields from this redshift hypothesis test report.
Return ONLY a valid JSON object (no markdown fences, no explanation).

{
    "verdict": "SUPPORTED" or "NOT SUPPORTED",
    "classification": "e.g. Galaxy (LRG/BGS), QSO, Star, Unknown, Host Galaxy dominated AGN",
    "systemic_redshift": float or null,
    "systemic_source": "short line name used as redshift anchor, or null",
    "key_caveat": "1-sentence summary of the most important caveat/uncertainty, or null"
}

Rules:
- systemic_redshift is null if the report explicitly says it cannot be determined.
- systemic_source should be a short line name (e.g. "Ca K_abs", "[O II] 3727"), not a verbose sentence.
- key_caveat should be null if there are no significant caveats.
- Use the exact numbers from the report — do not recompute or guess.
"""


# ── CSV line reader ─────────────────────────────────────────────────

def _read_csv_lines(csv_path: str) -> list[dict]:
    """Read per-line results from a harness CSV file.

    Returns a list of dicts with keys: name, rest_wavelength, fitted_center,
    snr, status, fwhm_km_s, delta_chi2_per_n.
    """
    if not os.path.exists(csv_path):
        return []
    rows = []
    with open(csv_path, newline='') as f:
        for row in csv.DictReader(f):
            # Skip empty rows
            if not row.get('name', '').strip():
                continue
            rows.append(row)
    return rows


def _parse_csv_float(val: str) -> float | None:
    """Parse a CSV string to float, returning None on empty/invalid."""
    if val is None:
        return None
    val = val.strip()
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _build_line_table(csv_rows: list[dict]) -> tuple[list[dict], list[str], list[str], float | None]:
    """Build structured line data from CSV rows.

    Returns
    -------
    table_rows : list[dict]
        Detected lines (LIKELY → MARGINAL), each with name, lam_obs,
        z_implied, status, sn.
    not_found_names : list[str]
        Canonical names of NOT_FOUND lines.
    spurious_names : list[str]
        Canonical names of SPURIOUS lines.
    z_scatter : float or None
        Std dev of z_implied for LIKELY lines (None if < 2).
    """
    status_order = {'LIKELY': 0, 'MARGINAL': 1, 'ESTIMATED': 2,
                    'NOT_FOUND': 3, 'SPURIOUS': 4}

    all_rows = []
    for row in csv_rows:
        raw_name = row.get('name', '').strip()
        status = row.get('status', '').strip()
        if not raw_name or status not in status_order:
            continue
        norm_name = _normalise_line_name(raw_name)
        rest_wl = _parse_csv_float(row.get('rest_wavelength'))
        fitted_center = _parse_csv_float(row.get('fitted_center'))
        sn = _parse_csv_float(row.get('snr'))

        # Compute λ_obs and z_implied
        lam_obs: str = "—"
        z_imp_str: str = "—"
        z_imp: float | None = None
        if fitted_center is not None and rest_wl is not None and rest_wl > 0:
            lam_obs = f"{fitted_center:.1f}"
            z_imp = fitted_center / rest_wl - 1.0
            z_imp_str = f"{z_imp:.4f}"
        elif rest_wl is not None:
            # CWT-adopted (ESTIMATED): no fitted center; use predicted
            pred = _parse_csv_float(row.get('predicted_obs'))
            if pred is not None:
                lam_obs = f"{pred:.1f}"
                z_imp = pred / rest_wl - 1.0
                z_imp_str = f"{z_imp:.4f}"

        sn_str = f"{sn:.1f}" if sn is not None else "—"

        all_rows.append({
            'name': norm_name, 'lam_obs': lam_obs, 'z_implied': z_imp_str,
            'status': status, 'sn': sn_str, '_z_val': z_imp,
            '_status_rank': status_order.get(status, 99),
        })

    # Sort by status rank, then by name
    all_rows.sort(key=lambda r: (r['_status_rank'], r['name']))

    # Split into detected (table) vs not-found/spurious
    table_rows = [r for r in all_rows if r['_status_rank'] <= 2]
    not_found = [r for r in all_rows if r['_status_rank'] == 3]
    spurious = [r for r in all_rows if r['_status_rank'] == 4]

    # z_scatter from LIKELY lines only
    z_vals = [r['_z_val'] for r in all_rows
              if r['_status_rank'] == 0 and r['_z_val'] is not None]
    z_scatter = float(np.std(z_vals)) if len(z_vals) >= 2 else None

    return table_rows, [r['name'] for r in not_found], [r['name'] for r in spurious], z_scatter


# ── Formatting ─────────────────────────────────────────────────────

def format_structured_summary(
    *,
    hypothesis_idx: int,
    z_tested: float,
    text_fields: dict,
    table_rows: list[dict],
    not_found_names: list[str],
    spurious_names: list[str],
    z_scatter: float | None,
    dn4000_lookup: dict | None = None,
) -> str:
    """Format CSV-derived line data and LLM-extracted text into markdown.

    Standard structure:

    1. **Header** — verdict, classification, Dn4000
    2. **Systemic** — redshift anchor and z_scatter
    3. **Line table** — LIKELY → MARGINAL with λ_obs, z_implied, S/N
    4. **Footer** — NOT_FOUND/SPURIOUS counts + names, key caveat
    """
    verdict = text_fields.get('verdict', 'UNKNOWN')
    classification = text_fields.get('classification', 'Unknown')

    # ── Dn4000 ──
    dn4000_str = ""
    if dn4000_lookup:
        key = round(z_tested, 4)
        info = dn4000_lookup.get(key)
        if info and info['dn4000'] is not None:
            dn4000_str = f" | Dn4000={info['dn4000']:.3f} ({info['interpretation']})"
        elif info:
            dn4000_str = f" | Dn4000={info['interpretation']}"

    out = []
    out.append(
        f"### H{hypothesis_idx} | z={z_tested:.4f} | {verdict} | {classification}{dn4000_str}"
    )

    # ── Systemic redshift ──
    sys_z = text_fields.get('systemic_redshift')
    sys_src = text_fields.get('systemic_source')
    parts = []
    if sys_z is not None:
        src = f"({_normalise_line_name(sys_src)})" if sys_src else ""
        parts.append(f"Systemic z={sys_z:.4f} {src}".strip())
    if z_scatter is not None:
        parts.append(f"σ_z={z_scatter:.4f}")
    if parts:
        out.append(f"**{' | '.join(parts)}**")

    # ── Line table ──
    if table_rows:
        out.append("")
        out.append("| Line | λ_obs (Å) | z_implied | Status | S/N |")
        out.append("|------|-----------|-----------|--------|-----|")
        for r in table_rows:
            out.append(
                f"| {r['name']} | {r['lam_obs']} | {r['z_implied']} | {r['status']} | {r['sn']} |"
            )

    # ── NOT_FOUND / SPURIOUS ──
    nf_parts = [f"{len(not_found_names)} NOT_FOUND", f"{len(spurious_names)} SPURIOUS"]
    all_missing = not_found_names + spurious_names
    if all_missing:
        nf_parts.append(f"({', '.join(all_missing)})")
    out.append("")
    out.append(" | ".join(nf_parts))

    # ── Key caveat ──
    caveat = text_fields.get('key_caveat')
    if caveat and str(caveat) != 'null' and str(caveat) != 'None':
        out.append(f"> {caveat}")

    out.append("")
    return '\n'.join(out)


def extract_harness_summary(
    harness_result: dict,
    dn4000_lookup: dict = None,
    harness_dir: str = None,
) -> str:
    """Format a single harness result.  CSV-driven when *harness_dir* is given."""
    hypothesis_idx = harness_result.get('hypothesis_idx', '?')
    z_tested = harness_result.get('redshift', 0)

    error = harness_result.get('error')
    if error and not harness_result.get('report'):
        return (
            f"### H{hypothesis_idx} | z={z_tested:.4f} | ERROR\n\n"
            f"Harness execution failed: {error}\n"
        )

    if harness_dir:
        csv_path = os.path.join(harness_dir, f"{hypothesis_idx}_lines.csv")
        csv_rows = _read_csv_lines(csv_path)
        table_rows, nf_names, sp_names, z_scat = _build_line_table(csv_rows)
        structured = harness_result.get('structured_output') or harness_result.get('_structured') or {}
        return format_structured_summary(
            hypothesis_idx=hypothesis_idx,
            z_tested=z_tested,
            text_fields=structured,
            table_rows=table_rows,
            not_found_names=nf_names,
            spurious_names=sp_names,
            z_scatter=z_scat,
            dn4000_lookup=dn4000_lookup,
        )

    structured = harness_result.get('_structured')
    if structured:
        return format_structured_summary(
            hypothesis_idx=hypothesis_idx,
            z_tested=z_tested,
            text_fields=structured,
            table_rows=[],
            not_found_names=[],
            spurious_names=[],
            z_scatter=None,
            dn4000_lookup=dn4000_lookup,
        )

    return f"### H{hypothesis_idx} | z={z_tested:.4f} | (no structured data)\n"


async def a_extract_harness_summaries(
    harness_results: list,
    dn4000_lookup: dict,
    *,
    harness_dir: str | None = None,
    model: str,
    api_key: str,
    base_url: str,
    temperature: float = 0.0,
    concurrency: int = 5,
) -> list:
    """Build structured summaries from CSV + LLM extraction.

    Line data comes from per-hypothesis ``{idx}_lines.csv`` files (deterministic,
    100% accurate).  The LLM only extracts a few natural-language fields from
    the report text (verdict, classification, systemic redshift, caveats).

    When *harness_dir* is None (e.g. testing without CSV files), falls back to
    LLM-only extraction.
    """
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
    )

    sem = asyncio.Semaphore(max(1, concurrency))

    async def _llm_extract_text(report: str) -> dict:
        """Extract natural-language fields from the report via LLM."""
        async with sem:
            try:
                resp = await llm.ainvoke([
                    ("system", EXTRACTION_PROMPT),
                    ("user", report),
                ])
                content = resp.content if hasattr(resp, 'content') else str(resp)
                return _parse_extraction_json(content) or {}
            except Exception as exc:
                logging.warning(f"LLM text extraction failed: {exc}")
                return {}

    async def _extract_one(idx: int, r: dict) -> str:
        hypothesis_idx = r.get('hypothesis_idx', idx + 1)
        z_tested = r.get('redshift', 0)
        report = r.get('report', '')

        if not report:
            error = r.get('error', 'Unknown error')
            return (
                f"### H{hypothesis_idx} | z={z_tested:.4f} | ERROR\n\n"
                f"Harness execution failed: {error}\n"
            )

        # ── CSV line data ──
        csv_rows: list[dict] = []
        if harness_dir:
            csv_path = os.path.join(harness_dir, f"{hypothesis_idx}_lines.csv")
            csv_rows = _read_csv_lines(csv_path)
        table_rows, nf_names, sp_names, z_scat = _build_line_table(csv_rows)

        # ── Text fields: prefer harness structured_output, fall back to LLM ──
        text_fields = r.get('structured_output')
        if not isinstance(text_fields, dict):
            text_fields = {}
        # Only use structured_output if it has the expected fields
        if not text_fields.get('verdict'):
            text_fields = await _llm_extract_text(report)

        return format_structured_summary(
            hypothesis_idx=hypothesis_idx,
            z_tested=z_tested,
            text_fields=text_fields,
            table_rows=table_rows,
            not_found_names=nf_names,
            spurious_names=sp_names,
            z_scatter=z_scat,
            dn4000_lookup=dn4000_lookup,
        )

    tasks = [_extract_one(i, r) for i, r in enumerate(harness_results)]
    return await asyncio.gather(*tasks)


def _parse_extraction_json(text: str) -> dict | None:
    """Parse JSON from an LLM extraction response. Tolerant of markdown fences."""
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


# =========================================================================
# Formatting helpers
# =========================================================================

def format_summaries(summaries: list) -> str:
    lines = []
    for s in summaries:
        lines.append(
            f"- **H{s['idx']}** (z={s['z_tested']:.4f}): "
            f"verdict={s['verdict']}, class={s['classification']}, "
            f"n_features={s['n_features']}"
            + (f", ERROR={s['error']}" if s.get('error') else "")
        )
    return "\n".join(lines) if lines else "(none)"


def format_ranked(ranked: list) -> str:
    if not ranked:
        return "(ranking unavailable)"
    lines = []
    for i, r in enumerate(ranked):
        lines.append(
            f"{i+1}. H{r['hypothesis_idx']}: "
            f"Δχ²={r['delta_chi2']}, "
            f"n_lines={r['n_lines_used']}"
        )
    return "\n".join(lines)


# =========================================================================
# Synthesis verdict extraction (for ground-truth comparison)
# =========================================================================

def extract_verdict_summary(rule_analysis: dict) -> dict:
    """Extract a normalised summary from synthesis output for comparison.

    Returns a flat dict with redshift, classification, and confidence,
    handling the various shapes rule_analysis can take (parsed JSON,
    fallback dict, error dict).
    """
    return {
        "redshift": rule_analysis.get("redshift"),
        "redshift_err": rule_analysis.get("redshift_err"),
        "classification": rule_analysis.get("classification", "Unknown"),
        "confidence": rule_analysis.get("confidence", "LOW"),
        "best_hypothesis_idx": rule_analysis.get("best_hypothesis_idx"),
    }


# =========================================================================
# Failure analysis — root-cause identification via LLM
# =========================================================================

# ── Round 1: blind review (no ground truth) ──────────────────────

BLIND_REVIEW_PROMPT = """\
You are reviewing a redshift synthesis pipeline's output. Critically examine the
synthesis reasoning and identify any potential issues — you do NOT know the correct
answer yet.

## Pipeline Architecture

1. **Harness**: Per-hypothesis verification with Gaussian fitting → CSV line catalogs
2. **Middleware**: Structured markdown summaries from CSV + LLM text extraction
3. **Synthesis**: Cross-compares all hypotheses (Phase 1 blind review → Phase 2 targeted spectrum reads → Phase 3 JSON verdict)

The synthesis agent uses a skill prompt (methodology) and a knowledge base (kb/*.md: line tables, doublet rules, ionization priorities, classification diagnostics).

## Harness Summaries

{harness_summaries}

## Synthesis Reasoning

{synthesis_reasoning}

## Task

Critically review the synthesis above. Focus on:

1. **Contradictions**: Are there inconsistencies in the contradiction matrix or between hypotheses?
2. **Evidence quality**: Does the conclusion follow from the line data, or are there weak / missing lines being glossed over?
3. **Methodology gaps**: What should the synthesis have checked but didn't?
4. **Alternative hypotheses**: Is there a stronger candidate being overlooked?

Return ONLY a valid JSON object (no markdown fences):

{{
    "overall_assessment": "<1 paragraph: is the synthesis conclusion well-supported or questionable?>",
    "issues": [
        {{
            "severity": "critical | major | minor",
            "category": "contradiction | evidence_quality | methodology_gap | overlooked_hypothesis | classification_error | other",
            "description": "<specific observation, cite wavelengths and line names>",
            "affected_hypothesis": "<which hypothesis index, or 'overall'>"
        }}
    ],
    "strongest_alternative": "<which hypothesis (if any) looks stronger than the chosen one, and why; or null>",
    "should_have_abstained": true or false
}}
"""

# ── Round 2: root-cause analysis (ground truth revealed) ─────────

ROOT_CAUSE_PROMPT = """\
You previously reviewed a redshift synthesis and identified potential issues
(BLIND — without knowing the correct answer). Now the ground truth is revealed.

## Blind Review Findings

{blind_review}

## The Error

**Ground truth**: z={ground_truth_z}, type={ground_truth_type}
**True z in scoring candidates**: {in_scoring}
**Synthesis result**: z={synthesis_z}, type={synthesis_type}, confidence={synthesis_confidence}
**Mismatch**: {mismatch_desc}

## Harness Summaries (same as Round 1)

{harness_summaries}

## Task

Given the blind review observations AND the revealed error, identify the ROOT CAUSE.
Choose ONE of:

- **skill_gap**: The skill prompt is missing a necessary methodological instruction.
- **kb_gap**: The knowledge base is missing physics knowledge needed for this case.
- **kb_error**: The knowledge base contains incorrect or misleading physics rules.
- **harness_error**: The harness produced misleading line data that synthesis reasonably trusted.
- **llm_error**: Knowledge and methodology were sufficient, but synthesis made a reasoning mistake.
- **ambiguous**: Multiple factors contributed; cannot single out one root cause.

**If the blind review already caught the error**: this suggests the synthesis LLM ignored or
misweighted available signals → likely `llm_error` or `skill_gap` (insufficient weighting guidance).

**If the blind review missed the error**: the issue is deeper — the skill prompt or KB may lack
the diagnostic that would have surfaced it → likely `skill_gap` or `kb_gap`.

**If in_scoring is False** (true z not in candidates): focus on why synthesis failed to reject
all hypotheses. The blind review's `should_have_abstained` field is key here.

Return ONLY a valid JSON object (no markdown fences):

{{
    "root_cause": "skill_gap | kb_gap | kb_error | harness_error | llm_error | ambiguous",
    "blind_review_alignment": "<did the blind review catch the error? 1-2 sentences>",
    "explanation": "<1-paragraph analysis of what went wrong and why>",
    "suggested_fix": {{
        "target_file": "kb/classification.md or targeted_search_skill.md or synthesize_skill.md or null",
        "target_section": "<section heading in the target file, or null>",
        "proposed_change": "<specific text to add or modify, or null if no clear fix>",
        "rationale": "<why this change would prevent this class of error>"
    }}
}}
"""


async def analyze_failure(
    synthesis_result: dict,
    harness_results: list,
    harness_dir: str,
    ground_truth: dict,
    mismatch_info: dict,
    *,
    model: str,
    api_key: str,
    base_url: str,
    temperature: float = 0.0,
    stream_md_path: str | None = None,
) -> dict:
    """Call an LLM to identify the root cause of a synthesis failure.

    Parameters
    ----------
    synthesis_result : dict
        The full rule_analysis dict from synthesis.
    harness_results : list
        Raw harness results (used to build summaries the synthesis saw).
    harness_dir : str
        Directory with harness reports and CSVs.
    ground_truth : dict
        ``{"z": float, "type": str}``.
    mismatch_info : dict
        ``{"z_mismatch": bool, "type_mismatch": bool}``.
    model, api_key, base_url : str
        LLM configuration.

    Returns
    -------
    dict
        Parsed failure analysis with keys: root_cause, explanation, suggested_fix.
    """
    from langchain_openai import ChatOpenAI
    from pathlib import Path

    # ── Build harness summaries (same as what synthesis saw) ──
    wl = np.linspace(3600, 9824, 7000)  # placeholder — not needed for summaries
    fl = np.ones(7000) * 5.0
    dn4000_lookup = build_dn4000_lookup(wl, fl, harness_results)
    summaries = []
    for i, r in enumerate(harness_results):
        idx = r.get("hypothesis_idx", i + 1)
        csv_path = os.path.join(harness_dir, f"{idx}_lines.csv")
        csv_rows = _read_csv_lines(csv_path) if os.path.exists(csv_path) else []
        table_rows, nf_names, sp_names, z_scat = _build_line_table(csv_rows)
        structured = r.get("structured_output") or {}
        summaries.append(format_structured_summary(
            hypothesis_idx=idx,
            z_tested=r.get("redshift", 0),
            text_fields=structured,
            table_rows=table_rows,
            not_found_names=nf_names,
            spurious_names=sp_names,
            z_scatter=z_scat,
            dn4000_lookup=dn4000_lookup,
        ))

    # ── Extract key synthesis reasoning ──
    stream_path = os.path.join(harness_dir, "synthesis_stream.md")
    synthesis_reasoning = ""
    if os.path.exists(stream_path):
        text = Path(stream_path).read_text(encoding="utf-8")
        # Extract the assistant turns (skip raw data dumps)
        parts = re.split(r"### Assistant \(turn \d+\)", text)
        # Keep the Phase 1 analysis (turn 1) and the last turn (verdict)
        if len(parts) >= 2:
            # First assistant turn has Phase 1 analysis
            synthesis_reasoning += "### Phase 1 Analysis\n\n"
            turn1 = parts[1]
            # Truncate before tool results
            m = re.search(r"```json\s*\{.*?\}\s*```", turn1, re.DOTALL)
            if m:
                synthesis_reasoning += turn1[:m.start()].strip()[-3000:]
            else:
                synthesis_reasoning += turn1.strip()[-3000:]
        if len(parts) >= 3:
            synthesis_reasoning += "\n\n### Final Assessment\n\n"
            last = parts[-1].strip()[-3000:]
            synthesis_reasoning += last

    # ── Build mismatch description ──
    desc_parts = []
    if mismatch_info.get("z_mismatch"):
        desc_parts.append(
            f"redshift: expected {ground_truth['z']}, got {synthesis_result.get('redshift')}"
        )
    if mismatch_info.get("type_mismatch"):
        desc_parts.append(
            f"type: expected {ground_truth['type']}, got {synthesis_result.get('classification')}"
        )
    mismatch_desc = "; ".join(desc_parts)

    harness_text = "\n\n".join(summaries)

    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
    )

    async def _stream_one(md, label: str, prompt: str) -> str:
        """Stream a single LLM call into *md*, return accumulated text."""
        md.write(f"### {label}\n\n")
        md.write("<details>\n<summary>Prompt</summary>\n\n")
        md.write(prompt)
        md.write("\n</details>\n\n---\n\n")
        md.flush()

        accumulated = ""
        try:
            async for chunk in llm.astream([("user", prompt)]):
                token = chunk.content if hasattr(chunk, "content") else str(chunk)
                if token:
                    accumulated += token
                    md.write(token)
                    md.flush()
        except Exception as exc:
            md.write(f"\n\n> ❌ {label} streaming failed: {exc}\n\n")
            md.flush()
            raise
        md.write("\n\n---\n\n")
        md.flush()
        return accumulated

    async def _invoke_one(prompt: str) -> str:
        resp = await llm.ainvoke([("user", prompt)])
        return resp.content if hasattr(resp, "content") else str(resp)

    # ── Round 1: blind review ────────────────────────────────────
    blind_prompt = BLIND_REVIEW_PROMPT.format(
        harness_summaries=harness_text,
        synthesis_reasoning=synthesis_reasoning[:6000],
    )

    # ── Round 2: root cause with ground truth ────────────────────
    root_cause_prompt_tpl = ROOT_CAUSE_PROMPT  # format after Round 1

    if stream_md_path:
        os.makedirs(os.path.dirname(stream_md_path) or ".", exist_ok=True)

        with open(stream_md_path, "w", encoding="utf-8") as md:
            md.write("# Failure Analysis (two-round)\n\n")
            md.write(f"**Ground truth**: z={ground_truth['z']}, type={ground_truth['type']}\n\n")
            md.write(f"**Synthesis**: z={synthesis_result.get('redshift')}, "
                     f"type={synthesis_result.get('classification')}, "
                     f"confidence={synthesis_result.get('confidence')}\n\n")
            md.write("---\n\n")

            # Round 1
            try:
                blind_text = await _stream_one(md, "Round 1 — Blind Review", blind_prompt)
            except Exception as exc:
                logging.warning(f"Failure analysis Round 1 stream failed: {exc}")
                return _fallback_analysis()

            blind_parsed = _parse_extraction_json(blind_text)
            blind_summary = (
                json.dumps(blind_parsed, indent=2, ensure_ascii=False)
                if blind_parsed
                else blind_text[:2000]
            )

            # Round 2
            root_prompt = root_cause_prompt_tpl.format(
                blind_review=blind_summary,
                ground_truth_z=ground_truth["z"],
                ground_truth_type=ground_truth["type"],
                in_scoring=mismatch_info.get("in_scoring", True),
                synthesis_z=synthesis_result.get("redshift"),
                synthesis_type=synthesis_result.get("classification"),
                synthesis_confidence=synthesis_result.get("confidence"),
                mismatch_desc=mismatch_desc,
                harness_summaries=harness_text,
            )

            try:
                root_text = await _stream_one(md, "Round 2 — Root Cause Analysis", root_prompt)
            except Exception as exc:
                logging.warning(f"Failure analysis Round 2 stream failed: {exc}")
                return _fallback_analysis()

        parsed = _parse_extraction_json(root_text)
        if parsed:
            # Preserve blind review alongside root cause
            parsed["_blind_review"] = blind_parsed
            return parsed
        return _fallback_analysis()

    # ── Non-streaming path ────────────────────────────────────────
    try:
        blind_text = await _invoke_one(blind_prompt)
    except Exception as exc:
        logging.warning(f"Failure analysis Round 1 failed: {exc}")
        return _fallback_analysis()

    blind_parsed = _parse_extraction_json(blind_text)
    blind_summary = (
        json.dumps(blind_parsed, indent=2)
        if blind_parsed
        else blind_text[:2000]
    )

    root_prompt = root_cause_prompt_tpl.format(
        blind_review=blind_summary,
        ground_truth_z=ground_truth["z"],
        ground_truth_type=ground_truth["type"],
        in_scoring=mismatch_info.get("in_scoring", True),
        synthesis_z=synthesis_result.get("redshift"),
        synthesis_type=synthesis_result.get("classification"),
        synthesis_confidence=synthesis_result.get("confidence"),
        mismatch_desc=mismatch_desc,
        harness_summaries=harness_text,
    )

    try:
        root_text = await _invoke_one(root_prompt)
    except Exception as exc:
        logging.warning(f"Failure analysis Round 2 failed: {exc}")
        return _fallback_analysis()

    parsed = _parse_extraction_json(root_text)
    if parsed:
        parsed["_blind_review"] = blind_parsed
        return parsed
    return _fallback_analysis()


def _fallback_analysis() -> dict:
    return {
        "root_cause": "ambiguous",
        "explanation": "LLM-based analysis failed to produce a parseable result.",
        "suggested_fix": {
            "target_file": None,
            "target_section": None,
            "proposed_change": None,
            "rationale": "Automatic analysis failed — requires manual review.",
        },
    }


# =========================================================================
# Batch failure accumulation & analysis
# =========================================================================

def _append_pending_failure(output_dir: str, record: dict) -> str:
    """Append a failure record to the pending batch queue.

    Returns the path to pending.jsonl.
    """
    failure_dir = os.path.join(output_dir, "failure")
    os.makedirs(failure_dir, exist_ok=True)
    pending_path = os.path.join(failure_dir, "pending.jsonl")
    with open(pending_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return pending_path


def _read_pending_failures(output_dir: str) -> list[dict]:
    """Read all pending failure records."""
    pending_path = os.path.join(output_dir, "failure", "pending.jsonl")
    if not os.path.exists(pending_path):
        return []
    records = []
    with open(pending_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def _clear_pending_failures(output_dir: str) -> None:
    """Remove the pending queue after batch analysis."""
    pending_path = os.path.join(output_dir, "failure", "pending.jsonl")
    if os.path.exists(pending_path):
        os.remove(pending_path)


BATCH_ANALYSIS_PROMPT = """\
You are reviewing a batch of {n_failures} redshift pipeline failures to identify
systematic patterns and suggest concrete improvements.

## Failure Summaries

{failure_summaries}

## Task

1. **Group** these failures by their likely root cause category:
   - `skill_gap`: methodology / prompt instructions are missing
   - `kb_gap`: knowledge base is missing physics knowledge
   - `kb_error`: knowledge base has incorrect physics rules
   - `harness_error`: per-hypothesis line fitting produced misleading data
   - `llm_error`: the synthesis LLM made a reasoning mistake despite adequate knowledge
   - `ambiguous`: multiple factors, or not enough information

2. **For each group**, identify the common pattern and write a concise diagnosis.

3. **For skill_gap / kb_gap / kb_error groups**, propose ONE concrete fix per group:
   - `target_file`: which file to modify
   - `target_section`: which section
   - `proposed_change`: what text to add or change
   - `rationale`: why this addresses the pattern

4. **For harness_error / llm_error / ambiguous groups**, note that manual review is needed.

5. **Prioritise**: which fix would prevent the most failures?

Return ONLY a valid JSON object (no markdown fences):

{{
    "batch_diagnosis": "<2-3 sentence summary of the dominant failure mode across this batch>",
    "groups": [
        {{
            "root_cause": "skill_gap|kb_gap|kb_error|harness_error|llm_error|ambiguous",
            "count": <number of failures in this group>,
            "spectrum_ids": ["id1", "id2", ...],
            "pattern": "<1-sentence description of the common pattern>",
            "diagnosis": "<1-paragraph analysis>",
            "suggested_fix": {{
                "target_file": "<path or null>",
                "target_section": "<section or null>",
                "proposed_change": "<specific text or null>",
                "rationale": "<why this fix addresses the pattern or null>"
            }}
        }}
    ],
    "priority_fix": {{
        "group_index": <index into groups array, 0-based>,
        "reasoning": "<why this fix should be applied first>"
    }}
}}
"""


async def analyze_failure_batch(
    output_dir: str,
    batch_num: int,
    *,
    model: str,
    api_key: str,
    base_url: str,
    temperature: float = 0.0,
) -> dict | None:
    """Run a collective LLM analysis on a batch of pending failures.

    Reads failures from ``output_dir/failure/pending.jsonl``, streams the
    LLM response to ``output_dir/failure/batch_{batch_num}_stream.md``,
    and clears the pending queue on success.

    Returns the parsed batch analysis dict, or None if no pending failures
    or the LLM call fails.
    """
    from langchain_openai import ChatOpenAI

    pending = _read_pending_failures(output_dir)
    if not pending:
        return None

    # Build compact failure summaries
    summary_lines = []
    for i, rec in enumerate(pending):
        gt = rec.get("ground_truth", {})
        sr = rec.get("synthesis_result", {})
        mm = rec.get("mismatch", {})
        summary_lines.append(
            f"**{i + 1}. {rec.get('spectrum_id', '?')}** — "
            f"expected z={gt.get('z')} type={gt.get('type')}, "
            f"got z={sr.get('redshift')} type={sr.get('classification')} "
            f"(confidence={sr.get('confidence')}); "
            f"in_scoring={mm.get('in_scoring')}, min_dz={mm.get('min_dz')}"
        )

    prompt = BATCH_ANALYSIS_PROMPT.format(
        n_failures=len(pending),
        failure_summaries="\n".join(summary_lines),
    )

    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
    )

    failure_dir = os.path.join(output_dir, "failure")
    os.makedirs(failure_dir, exist_ok=True)
    stream_path = os.path.join(failure_dir, f"batch_{batch_num}_stream.md")

    with open(stream_path, "w", encoding="utf-8") as md:
        md.write(f"# Batch Failure Analysis #{batch_num}\n\n")
        md.write(f"**Failures**: {len(pending)}\n\n")
        md.write("---\n\n")
        md.write("<details>\n<summary>Prompt</summary>\n\n")
        md.write(prompt)
        md.write("\n</details>\n\n---\n\n")
        md.write("## Response\n\n")
        md.flush()

        accumulated = ""
        try:
            async for chunk in llm.astream([("user", prompt)]):
                token = chunk.content if hasattr(chunk, "content") else str(chunk)
                if token:
                    accumulated += token
                    md.write(token)
                    md.flush()
        except Exception as exc:
            md.write(f"\n\n> ❌ Batch analysis streaming failed: {exc}\n\n")
            md.flush()
            logging.warning(f"Batch analysis LLM stream failed: {exc}")
            return None

    # Also write a clean markdown version (no prompt dump)
    report_path = os.path.join(failure_dir, f"batch_{batch_num}.md")
    parsed = _parse_extraction_json(accumulated)
    if parsed:
        _write_batch_report(report_path, batch_num, pending, parsed)
        _clear_pending_failures(output_dir)
        return parsed

    # Parse failed — still write the raw response
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# Batch Failure Analysis #{batch_num}\n\n")
        f.write(f"**Failures**: {len(pending)}\n\n")
        f.write("## Raw Response (parse failed)\n\n")
        f.write(accumulated)
    _clear_pending_failures(output_dir)
    return None


def _write_batch_report(
    report_path: str,
    batch_num: int,
    pending: list[dict],
    analysis: dict,
) -> None:
    """Write a formatted batch analysis report in markdown."""
    lines = [
        f"# Batch Failure Analysis #{batch_num}",
        "",
        f"**Failures reviewed**: {len(pending)}",
        "",
        f"## Diagnosis",
        "",
        analysis.get("batch_diagnosis", "(none)"),
        "",
        "---",
        "",
        "## Failure Groups",
        "",
    ]

    for gi, group in enumerate(analysis.get("groups", [])):
        rc = group.get("root_cause", "?")
        count = group.get("count", 0)
        ids = ", ".join(group.get("spectrum_ids", []))
        pattern = group.get("pattern", "")
        diagnosis = group.get("diagnosis", "")
        fix = group.get("suggested_fix") or {}

        lines.append(f"### {gi + 1}. {rc} ({count} failures)")
        lines.append(f"**Spectra**: {ids}")
        lines.append(f"**Pattern**: {pattern}")
        lines.append("")
        lines.append(diagnosis)
        lines.append("")

        target = fix.get("target_file") or "—"
        section = fix.get("target_section") or "—"
        change = fix.get("proposed_change") or "—"
        rationale = fix.get("rationale") or "—"
        lines.append(f"| Field | Value |")
        lines.append(f"|-------|-------|")
        lines.append(f"| Target file | {target} |")
        lines.append(f"| Target section | {section} |")
        lines.append(f"| Proposed change | {change} |")
        lines.append(f"| Rationale | {rationale} |")
        lines.append("")

    # Priority fix
    pf = analysis.get("priority_fix") or {}
    if pf:
        lines.append("---")
        lines.append("")
        lines.append("## Priority Fix")
        lines.append("")
        lines.append(f"**Group {pf.get('group_index', -1) + 1}** — {pf.get('reasoning', '')}")
        lines.append("")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _record_failure(
    synthesis_result: dict,
    harness_dir: str,
    ground_truth: dict,
    mismatch_info: dict,
    analysis: dict,
    output_dir: str = "",
) -> str:
    """Write a structured failure record.

    Two outputs:
    1. Per-sample JSONL appended to ``output_dir/failure/pending.jsonl``
       (for batch accumulation).
    2. Per-sample JSONL appended to ``data/failures/failure_log.jsonl``
       (global archive, relative to project root).

    Returns the pending path written to.
    """
    import datetime

    if not output_dir:
        output_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(harness_dir))),
        )

    record = {
        "timestamp": datetime.datetime.now().isoformat(),
        "spectrum_id": os.path.basename(os.path.dirname(harness_dir)),
        "ground_truth": ground_truth,
        "synthesis_result": extract_verdict_summary(synthesis_result),
        "mismatch": mismatch_info,
        "root_cause": analysis.get("root_cause"),
        "explanation": analysis.get("explanation"),
        "suggested_fix": analysis.get("suggested_fix"),
        "harness_dir": harness_dir,
        "synthesis_stream": os.path.join(harness_dir, "synthesis_stream.md"),
        "failure_analysis_stream": os.path.join(harness_dir, "failure_analysis_stream.md"),
    }

    # Per-sample stream to harness dir (individual review)
    harness_failure_path = os.path.join(harness_dir, "failure_analysis.json")
    with open(harness_failure_path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    # Append to batch pending queue in output_dir/failure/
    pending_path = _append_pending_failure(output_dir, record)

    # Also write to global archive
    global_failure_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(harness_dir))),
        "failures",
    )
    os.makedirs(global_failure_dir, exist_ok=True)
    global_log_path = os.path.join(global_failure_dir, "failure_log.jsonl")
    with open(global_log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    logging.info(f"Failure record written: harness={harness_failure_path}, pending={pending_path}")
    return pending_path
