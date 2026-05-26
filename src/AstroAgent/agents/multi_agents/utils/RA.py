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
        Interpretation is one of: "old stellar population (LRG/BGS)",
        "intermediate", "young / star-forming (ELG, QSO)", "out of range".
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
            lookup[z] = {'dn4000': None, 'interpretation': 'out of range'}
        else:
            dn4000 = result[0]
            if dn4000 > 1.6:
                interp = "old stellar population (LRG/BGS)"
            elif dn4000 > 1.3:
                interp = "intermediate"
            else:
                interp = "young / star-forming (ELG, QSO)"
            lookup[z] = {'dn4000': dn4000, 'interpretation': interp}

    return lookup


def prepare_diagnostic_slices(wl, fl, harness_results: list) -> str:
    """Build a Dn4000 comparison table for all hypotheses.

    Dn4000 (Balogh+ 1999) is the single most powerful continuum-based
    tiebreaker — it is completely independent of the CWT→harness pipeline.

    Interpretation:
      Dn4000 > 1.6  → old stellar population (LRG/BGS)
      Dn4000 1.3–1.6 → intermediate
      Dn4000 < 1.3  → young / star-forming (ELG, QSO)
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
            sections.append(f"| z={z:.4f} | — (out of range) | — |")
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
    "verdict": "CONFIRMED" or "NOT CONFIRMED",
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
        Detected lines (CONFIRMED → MARGINAL), each with name, lam_obs,
        z_implied, status, sn.
    not_found_names : list[str]
        Canonical names of NOT_FOUND lines.
    spurious_names : list[str]
        Canonical names of SPURIOUS lines.
    z_scatter : float or None
        Std dev of z_implied for CONFIRMED + LIKELY lines (None if < 2).
    """
    status_order = {'CONFIRMED': 0, 'LIKELY': 1, 'ESTIMATED': 2, 'MARGINAL': 3,
                    'NOT_FOUND': 4, 'SPURIOUS': 5}

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
    table_rows = [r for r in all_rows if r['_status_rank'] <= 3]
    not_found = [r for r in all_rows if r['_status_rank'] == 4]
    spurious = [r for r in all_rows if r['_status_rank'] == 5]

    # z_scatter from CONFIRMED + LIKELY
    z_vals = [r['_z_val'] for r in all_rows
              if r['_status_rank'] <= 1 and r['_z_val'] is not None]
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
    3. **Line table** — CONFIRMED → MARGINAL with λ_obs, z_implied, S/N
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
