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


def prepare_diagnostic_slices(wl, fl, snr, harness_results: list) -> str:
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
        "| Hypothesis z | Dn4000 | N_blue | N_red | Interpretation |\n"
        "|-------------|--------|--------|-------|----------------|"
    )

    for z, info in sorted(lookup.items()):
        dn4000 = info['dn4000']
        interp = info['interpretation']
        if dn4000 is None:
            sections.append(
                f"| z={z:.4f} | — (out of range) | — | — | — |"
            )
        else:
            # Recompute to get pixel counts
            result = compute_dn4000(wl, fl, z)
            if result is None:
                n_blue = n_red = "—"
            else:
                n_blue, n_red = result[1], result[2]
            sections.append(
                f"| z={z:.4f} | **{dn4000:.3f}** | {n_blue} | {n_red} | {interp} |"
            )
    sections.append("")

    return "\n".join(sections)


# =========================================================================
# Harness report middleware — LLM-driven structured extraction
# =========================================================================

EXTRACTION_PROMPT = """\
Extract structured information from this redshift hypothesis test report.
Return ONLY a valid JSON object (no markdown fences, no explanation).

{
    "verdict": "CONFIRMED" or "NOT CONFIRMED",
    "classification": "e.g. Galaxy (LRG/BGS), QSO, Star, Unknown, Host Galaxy dominated AGN",
    "systemic_redshift": float or null,
    "systemic_source": "the line/method used as redshift anchor" or null,
    "n_confirmed": int,
    "n_likely": int,
    "n_estimated": int,
    "n_marginal": int,
    "n_not_found": int,
    "n_spurious": int,
    "confirmed_lines": [{"name": "line_name", "implied_z": float}],
    "likely_lines": [{"name": "line_name", "implied_z": float}],
    "z_scatter": float or null
}

Rules:
- z_scatter is the std dev of implied_z for confirmed+likely lines (null if <2 lines).
- If a field cannot be determined: null for scalars, [] for lists, 0 for counts.
- systemic_redshift is null if the report explicitly says it cannot be determined.
- Use the exact numbers from the report — do not recompute or guess.
"""


def format_structured_summary(
    data: dict,
    hypothesis_idx: int,
    z_tested: float,
    dn4000_lookup: dict = None,
) -> str:
    """Format LLM-extracted structured data into a concise markdown summary.

    Parameters
    ----------
    data : dict
        Parsed JSON from the LLM extraction prompt. Keys: verdict,
        classification, systemic_redshift, systemic_source, n_confirmed,
        n_likely, n_estimated, n_marginal, n_not_found, n_spurious,
        confirmed_lines, likely_lines, z_scatter.
    hypothesis_idx : int
    z_tested : float
    dn4000_lookup : dict, optional

    Returns
    -------
    str
    """
    verdict = data.get('verdict', 'UNKNOWN')
    classification = data.get('classification', 'Unknown')

    # ── Dn4000 ──
    dn4000_str = ""
    if dn4000_lookup:
        key = round(z_tested, 4)
        info = dn4000_lookup.get(key)
        if info and info['dn4000'] is not None:
            dn4000_str = f", Dn4000={info['dn4000']:.3f} ({info['interpretation']})"
        elif info:
            dn4000_str = f", Dn4000={info['interpretation']}"

    out = []
    out.append(
        f"### H{hypothesis_idx} (z={z_tested:.4f}) — {verdict} — {classification}{dn4000_str}"
    )

    # Systemic redshift
    sys_z = data.get('systemic_redshift')
    sys_src = data.get('systemic_source')
    if sys_z is not None:
        out.append(f"- Systemic z: {sys_z:.4f} (from {sys_src})" if sys_src else
                   f"- Systemic z: {sys_z:.4f}")

    # Line counts
    status_order = [
        'n_confirmed', 'n_likely', 'n_estimated',
        'n_marginal', 'n_not_found', 'n_spurious',
    ]
    labels = ['CONFIRMED', 'LIKELY', 'ESTIMATED', 'MARGINAL', 'NOT_FOUND', 'SPURIOUS']
    counts = {label: data.get(key, 0) for key, label in zip(status_order, labels)}
    count_str = ", ".join(f"{counts[l]} {l}" for l in labels)
    out.append(f"- Lines: {count_str}")

    # CONFIRMED lines
    conf = data.get('confirmed_lines', [])
    if conf:
        items = [
            f"{l['name']} (z={l['implied_z']:.4f})"
            for l in conf if l.get('implied_z') is not None
        ]
        if items:
            out.append(f"- CONFIRMED: {', '.join(items)}")

    # LIKELY lines
    likely = data.get('likely_lines', [])
    if likely:
        items = [
            f"{l['name']} (z={l['implied_z']:.4f})"
            for l in likely if l.get('implied_z') is not None
        ]
        if items:
            out.append(f"- LIKELY: {', '.join(items)}")

    # z_scatter
    z_scatter = data.get('z_scatter')
    if z_scatter is not None:
        out.append(f"- z_scatter (CONFIRMED+LIKELY): σ={z_scatter:.4f}")

    out.append("")
    return '\n'.join(out)


def extract_harness_summary(harness_result: dict, dn4000_lookup: dict = None) -> str:
    """Fallback: format a harness result that already has structured data.

    Prefer :func:`a_extract_harness_summaries` for the LLM-driven path.
    This function handles error results and cases where structured_data
    was already extracted.
    """
    hypothesis_idx = harness_result.get('hypothesis_idx', '?')
    z_tested = harness_result.get('redshift', 0)

    error = harness_result.get('error')
    if error and not harness_result.get('report'):
        return (
            f"### H{hypothesis_idx} (z={z_tested:.4f}) — ERROR\n\n"
            f"Harness execution failed: {error}\n"
        )

    # If structured data was pre-extracted, use it
    structured = harness_result.get('_structured')
    if structured:
        return format_structured_summary(
            structured, hypothesis_idx, z_tested, dn4000_lookup,
        )

    # Bare-minimum fallback: just show what we know
    return f"### H{hypothesis_idx} (z={z_tested:.4f}) — (no structured data)\n"


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

    Each report is sent to a small, fast LLM for JSON extraction.
    Requests run in parallel with bounded concurrency.

    Parameters
    ----------
    harness_results : list
        Per-hypothesis harness results. Each dict needs hypothesis_idx,
        redshift, report.
    dn4000_lookup : dict
        From :func:`build_dn4000_lookup`.
    model, api_key, base_url : str
        LLM configuration.
    temperature : float
        LLM temperature (default 0 for deterministic extraction).
    concurrency : int
        Max concurrent LLM requests.

    Returns
    -------
    list[str]
        Formatted markdown summary for each hypothesis, in the same order
        as *harness_results*.
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

        # Error case
        if not report:
            error = r.get('error', 'Unknown error')
            return (
                f"### H{hypothesis_idx} (z={z_tested:.4f}) — ERROR\n\n"
                f"Harness execution failed: {error}\n"
            )

        # Try structured_output from harness first (already JSON)
        structured = r.get('structured_output')
        if structured and isinstance(structured, dict):
            return format_structured_summary(
                structured, hypothesis_idx, z_tested, dn4000_lookup,
            )

        # LLM extraction
        async with sem:
            try:
                resp = await llm.ainvoke([
                    ("system", EXTRACTION_PROMPT),
                    ("user", report),
                ])
                text = resp.content if hasattr(resp, 'content') else str(resp)

                # Parse JSON from response
                data = _parse_extraction_json(text)
                if data:
                    return format_structured_summary(
                        data, hypothesis_idx, z_tested, dn4000_lookup,
                    )
            except Exception as exc:
                logging.warning(
                    f"LLM extraction failed for H{hypothesis_idx}: {exc}"
                )

        # Fallback
        return f"### H{hypothesis_idx} (z={z_tested:.4f}) — (extraction failed)\n"

    tasks = [_extract_one(i, r) for i, r in enumerate(harness_results)]
    return await asyncio.gather(*tasks)


def _parse_extraction_json(text: str) -> dict | None:
    """Parse JSON from an LLM extraction response. Tolerant of markdown fences."""
    # Strip ```json fences if present
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    # Find first { ... } block
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
