"""
Utility functions for RuleAnalyst — hypothesis collection, Dn4000 diagnostics,
and formatting helpers.
"""

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


def prepare_diagnostic_slices(wl, fl, snr, harness_results: list) -> str:
    """Compute Dn4000 for each hypothesis and provide a comparison table.

    Dn4000 (Balogh+ 1999) is the single most powerful continuum-based
    tiebreaker — it is completely independent of the CWT→harness pipeline.

    Interpretation:
      Dn4000 > 1.6  → old stellar population (LRG/BGS)
      Dn4000 1.3–1.6 → intermediate
      Dn4000 < 1.3  → young / star-forming (ELG, QSO)
    """
    rows = []
    zs_to_check = set()
    for r in harness_results:
        if r.get('error'):
            continue
        z = r['z'] if 'z' in r else r.get('redshift', 0)
        zs_to_check.add(round(z, 4))

    for z in sorted(zs_to_check):
        result = compute_dn4000(wl, fl, z)
        if result is None:
            rows.append((z, None, 0, 0, None, None, None, None, None, None, None, None, None))
            continue
        (dn4000, n_blue, n_red,
         blue_mean, red_mean,
         blue_lo, blue_hi, red_lo, red_hi,
         blue_fluxes, red_fluxes) = result

        if dn4000 > 1.6:
            interp = "old stellar population (LRG/BGS)"
        elif dn4000 > 1.3:
            interp = "intermediate"
        else:
            interp = "young / star-forming (ELG, QSO)"

        rows.append((
            z, dn4000, n_blue, n_red, interp,
            blue_lo, blue_hi, blue_mean, red_lo, red_hi, red_mean,
            blue_fluxes, red_fluxes,
        ))

    if not rows:
        return "(Dn4000 could not be computed for any hypothesis — windows outside spectral range.)"

    sections = ["## Dn4000 Comparison\n"]
    sections.append(
        "| Hypothesis z | Dn4000 | N_blue | N_red | Interpretation |\n"
        "|-------------|--------|--------|-------|----------------|"
    )
    for z, dn4000, n_blue, n_red, interp, *_ in rows:
        if dn4000 is None:
            sections.append(
                f"| z={z:.4f} | — (out of range) | — | — | — |"
            )
        else:
            sections.append(
                f"| z={z:.4f} | **{dn4000:.3f}** | {n_blue} | {n_red} | {interp} |"
            )
    sections.append("")

    sections.append("## Dn4000 Window Details\n")
    sections.append(
        "For each hypothesis, the blue sideband (3850–3950 Å rest) and "
        "red sideband (4000–4100 Å rest) fluxes. Data shown as "
        "(wavelength Å, flux) for every ~3rd point.\n"
    )

    for z, dn4000, n_blue, n_red, interp, blue_lo, blue_hi, blue_mean, red_lo, red_hi, red_mean, bf, rf in rows:
        if dn4000 is None:
            continue
        sections.append(
            f"### z={z:.4f} — Dn4000 = {dn4000:.3f} ({interp})\n"
        )
        sections.append(
            f"Blue sideband ({blue_lo:.0f}–{blue_hi:.0f} Å obs, "
            f"{n_blue} pts, mean flux = {blue_mean:.4f}):"
        )
        bf_sub = bf[::3]
        wl_blue = np.linspace(blue_lo, blue_hi, len(bf))[::3]
        sections.append(
            "  " + "  ".join(
                f"{w:.1f}:{v:.3f}" for w, v in zip(wl_blue, bf_sub)
            )
        )
        sections.append(
            f"Red sideband ({red_lo:.0f}–{red_hi:.0f} Å obs, "
            f"{n_red} pts, mean flux = {red_mean:.4f}):"
        )
        rf_sub = rf[::3]
        wl_red = np.linspace(red_lo, red_hi, len(rf))[::3]
        sections.append(
            "  " + "  ".join(
                f"{w:.1f}:{v:.3f}" for w, v in zip(wl_red, rf_sub)
            )
        )
        sections.append("")

    return "\n".join(sections)


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
