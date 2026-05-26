"""
Utility functions for RuleAnalyst — hypothesis collection, Dn4000 diagnostics,
harness report middleware, and formatting helpers.
"""

import asyncio
import json
import logging
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

# Rest wavelengths for quick lookup (canonical name → λ_rest)
_LINE_REST: dict[str, float] = {v[0]: v[1] for v in _LINE_ALIASES.values()}


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


def _rest_wavelength(name: str) -> float | None:
    """Return the rest wavelength for a canonical line name, or None."""
    return _LINE_REST.get(name)


def _compute_lambda_obs(name: str, implied_z: float | None) -> str:
    """Compute observed wavelength from canonical name and implied_z.

    Returns a formatted string like ``"8402.4"`` or ``"—"``.
    """
    if implied_z is None:
        return "—"
    wl_rest = _rest_wavelength(name)
    if wl_rest is None:
        return "—"
    return f"{wl_rest * (1.0 + implied_z):.1f}"


# ── Extraction prompt ──────────────────────────────────────────────

EXTRACTION_PROMPT = """\
Extract structured information from this redshift hypothesis test report.
Return ONLY a valid JSON object (no markdown fences, no explanation).

{
    "verdict": "CONFIRMED" or "NOT CONFIRMED",
    "classification": "e.g. Galaxy (LRG/BGS), QSO, Star, Unknown, Host Galaxy dominated AGN",
    "systemic_redshift": float or null,
    "systemic_source": "the line name used as redshift anchor (short, e.g. '[O II] 3727', 'Ca K_abs')" or null,
    "n_confirmed": int,
    "n_likely": int,
    "n_estimated": int,
    "n_marginal": int,
    "n_not_found": int,
    "n_spurious": int,
    "confirmed_lines": [{"name": "line_name", "implied_z": float, "sn": float or null}],
    "likely_lines": [{"name": "line_name", "implied_z": float, "sn": float or null}],
    "estimated_lines": [{"name": "line_name", "implied_z": float, "sn": float or null}],
    "marginal_lines": [{"name": "line_name", "implied_z": float, "sn": float or null}],
    "not_found_names": ["line names that were explicitly searched but NOT_FOUND — include lines mentioned as absent in the narrative text"],
    "z_scatter": float or null,
    "key_caveat": "1-sentence summary of the most important caveat/uncertainty (or null)"
}

Rules:
- Count ALL lines in each status category, including those mentioned only in narrative text (e.g. "C IV, C III], Mg II all NOT_FOUND" → count them in n_not_found and list in not_found_names).
- If the report says lines are "absent", "not detected", "not found", or "outside the verification window and not supporting", count them as NOT_FOUND.
- z_scatter is the std dev of implied_z for confirmed+likely lines (null if <2 lines).
- If a field cannot be determined: null for scalars, [] for lists, 0 for counts.
- systemic_redshift is null if the report explicitly says it cannot be determined.
- systemic_source should be a short line name only (e.g. "Ca K_abs"), not a verbose sentence.
- Use the exact numbers from the report — do not recompute or guess.
"""


# ── Formatting ─────────────────────────────────────────────────────

def format_structured_summary(
    data: dict,
    hypothesis_idx: int,
    z_tested: float,
    dn4000_lookup: dict = None,
) -> str:
    """Format LLM-extracted structured data into a table-based markdown summary.

    The standard structure has 3 sections:

    1. **Header** — verdict, classification, Dn4000
    2. **Line table** — all detected lines (CONFIRMED → MARGINAL) with
       λ_obs, z_implied, status, S/N
    3. **Footer** — NOT_FOUND/SPURIOUS summary and key caveat
    """
    verdict = data.get('verdict', 'UNKNOWN')
    classification = data.get('classification', 'Unknown')

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
    sys_z = data.get('systemic_redshift')
    sys_src = data.get('systemic_source')
    z_scatter = data.get('z_scatter')
    parts = []
    if sys_z is not None:
        src = f"({_normalise_line_name(sys_src)})" if sys_src else ""
        parts.append(f"Systemic z={sys_z:.4f} {src}".strip())
    if z_scatter is not None:
        parts.append(f"σ_z={z_scatter:.4f}")
    if parts:
        out.append(f"**{' | '.join(parts)}**")

    # ── Line table ──
    rows: list[tuple[str, str, str, str, str]] = []
    for status_key, lines_key in [
        ('CONFIRMED', 'confirmed_lines'),
        ('LIKELY',   'likely_lines'),
        ('ESTIMATED','estimated_lines'),
        ('MARGINAL', 'marginal_lines'),
    ]:
        for line in data.get(lines_key, []):
            name = _normalise_line_name(line.get('name', '?'))
            z_imp = line.get('implied_z')
            sn = line.get('sn')
            z_str = f"{z_imp:.4f}" if z_imp is not None else "—"
            sn_str = f"{sn:.1f}" if sn is not None else "—"
            lam_obs = _compute_lambda_obs(name, z_imp)
            rows.append((name, lam_obs, z_str, status_key, sn_str))

    if rows:
        out.append("")
        out.append("| Line | λ_obs (Å) | z_implied | Status | S/N |")
        out.append("|------|-----------|-----------|--------|-----|")
        for name, lam_obs, z_str, status, sn_str in rows:
            out.append(f"| {name} | {lam_obs} | {z_str} | {status} | {sn_str} |")

    # ── NOT_FOUND / SPURIOUS ──
    n_nf = data.get('n_not_found', 0)
    n_sp = data.get('n_spurious', 0)
    nf_names = data.get('not_found_names', [])
    footer_parts = [f"{n_nf} NOT_FOUND", f"{n_sp} SPURIOUS"]
    if nf_names:
        normalised_nf = [_normalise_line_name(n) for n in nf_names]
        footer_parts.append(f"({', '.join(normalised_nf)})")
    out.append("")
    out.append(" | ".join(footer_parts))

    # ── Key caveat ──
    caveat = data.get('key_caveat')
    if caveat and caveat != 'null':
        out.append(f"> {caveat}")

    out.append("")
    return '\n'.join(out)


def extract_harness_summary(harness_result: dict, dn4000_lookup: dict = None) -> str:
    """Fallback: format a harness result that already has structured data.

    Prefer :func:`a_extract_harness_summaries` for the LLM-driven path.
    """
    hypothesis_idx = harness_result.get('hypothesis_idx', '?')
    z_tested = harness_result.get('redshift', 0)

    error = harness_result.get('error')
    if error and not harness_result.get('report'):
        return (
            f"### H{hypothesis_idx} | z={z_tested:.4f} | ERROR\n\n"
            f"Harness execution failed: {error}\n"
        )

    structured = harness_result.get('_structured')
    if structured:
        return format_structured_summary(
            structured, hypothesis_idx, z_tested, dn4000_lookup,
        )

    return f"### H{hypothesis_idx} | z={z_tested:.4f} | (no structured data)\n"


async def a_extract_harness_summaries(
    harness_results: list,
    dn4000_lookup: dict,
    *,
    model: str,
    api_key: str,
    base_url: str,
    temperature: float = 0.0,
    concurrency: int = 5,
) -> list:
    """Extract structured summaries from harness reports via LLM.

    Each report is sent to the LLM for JSON extraction.  Requests run in
    parallel with bounded concurrency.  Extracted line names are normalised
    to a canonical form so the synthesis agent can cross-compare hypotheses
    without name-variant confusion.
    """
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
    )

    sem = asyncio.Semaphore(max(1, concurrency))

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

        # Use structured_output from harness if available (already JSON)
        structured = r.get('structured_output')
        if structured and isinstance(structured, dict):
            return format_structured_summary(
                structured, hypothesis_idx, z_tested, dn4000_lookup,
            )

        async with sem:
            try:
                resp = await llm.ainvoke([
                    ("system", EXTRACTION_PROMPT),
                    ("user", report),
                ])
                text = resp.content if hasattr(resp, 'content') else str(resp)
                data = _parse_extraction_json(text)
                if data:
                    return format_structured_summary(
                        data, hypothesis_idx, z_tested, dn4000_lookup,
                    )
            except Exception as exc:
                logging.warning(
                    f"LLM extraction failed for H{hypothesis_idx}: {exc}"
                )

        return f"### H{hypothesis_idx} | z={z_tested:.4f} | (extraction failed)\n"

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
