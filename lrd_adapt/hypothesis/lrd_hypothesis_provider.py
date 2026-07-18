"""
LRD hypothesis provider — replaces Redrock at the VisualInterpreter call site
(CLAUDE.md finding #2). For each source, emits the primary hypothesis (the
paper's line ID + z_spec, passed in as a hypothesis to test — never trusted)
plus every rival line identity consistent with the observed wavelength of the
strongest detected line, using the F356W grism redshift windows from
Kapoor+26 Section 3.2.

Output matches the schema of utils.VI._redrock_to_hypotheses /
brute_force_line_matching: {z, zmedian, hypotheses: [...]}. Downstream
harness code does not care where hypotheses come from (finding #1).
"""
from __future__ import annotations

import json
import os
import statistics

# Rest-frame line wavelengths, vacuum Angstrom — working values, CLAUDE.md
# flags these for verification against NIST/SDSS before the KB freeze.
REST_WAVELENGTHS_ANG = {
    "Halpha": 6564.6,
    "OI_8446": 8448.7,       # air label "O I 8446"; vacuum ~8448.7
    "SIII": 9532.5,          # stronger member of the [S III] 9071/9533 doublet
    "HeI_Pagamma": 10833.0,  # blended broad component, anchored on He I 10833
    "Pabeta": 12822.0,
    "Paalpha": 18756.0,
}

# F356W grism redshift windows, Kapoor+26 Section 3.2 — defines the rival
# hypothesis space for any single detected line (CLAUDE.md science constants).
REDSHIFT_WINDOWS = {
    "Paalpha": (0.68, 1.10),
    "Pabeta": (1.45, 2.08),
    "HeI_Pagamma": (1.91, 2.64),
    "SIII": (2.30, 3.14),
    "OI_8446": (2.73, 3.68),
    "Halpha": (3.80, 5.02),
}


def _score_for_window(z, z_min, z_max):
    """Triangular score peaking at the window center, capped below the
    primary hypothesis's score of 100 so the paper's claim never gets
    silently outranked by an untested rival."""
    center = (z_min + z_max) / 2.0
    half_width = (z_max - z_min) / 2.0
    if half_width <= 0:
        return 0.0
    frac = max(0.0, 1.0 - abs(z - center) / half_width)
    return 70.0 * frac


def generate_lrd_hypotheses(observed_wavelength_ang, primary_line, z_spec, source_code=None):
    """
    Parameters
    ----------
    observed_wavelength_ang : float
        Observed wavelength of the strongest detected broad line (Angstrom).
    primary_line : str
        Key into REST_WAVELENGTHS_ANG — the paper's claimed line identity
        for this source (the hypothesis to be audited, not trusted).
    z_spec : float
        The paper's claimed redshift for that identity.
    source_code : str, optional
        Anonymized code, carried into provenance only (see CLAUDE.md
        Anonymization section — never a real J-name/coordinate).

    Returns
    -------
    dict
        {z, zmedian, hypotheses} — same schema as
        utils.VI._redrock_to_hypotheses / brute_force_line_matching.
    """
    if primary_line not in REST_WAVELENGTHS_ANG:
        raise ValueError(
            f"Unknown primary_line {primary_line!r}; known: {sorted(REST_WAVELENGTHS_ANG)}"
        )

    hypotheses = []
    for line, rest_ang in REST_WAVELENGTHS_ANG.items():
        z = observed_wavelength_ang / rest_ang - 1.0
        z_min, z_max = REDSHIFT_WINDOWS[line]
        is_primary = line == primary_line

        if not is_primary and not (z_min <= z <= z_max):
            continue  # not a live rival for this observed wavelength

        score = 100.0 if is_primary else _score_for_window(z, z_min, z_max)
        z_used = z_spec if is_primary else z

        hypotheses.append({
            "Hypothesis": (
                f"{z_used:.4f}-{line} "
                f"({'paper' if is_primary else 'rival'}, "
                f"obs={observed_wavelength_ang:.1f}A -> rest={rest_ang:.1f}A)"
            ),
            "z_center": z_used,
            "z_list": [z_used],
            "z_max": z_used,
            "z_min": z_used,
            "z_spread": 0.0,
            "Emission matches": [line],
            "Absorption matches": [],
            "N_emission": 1,
            "N_absorption": 0,
            "matched_lines": {line: observed_wavelength_ang},
            "z_representative": z_used,
            "score": score,
            "source": "lrd_adapt:paper" if is_primary else "lrd_adapt:rival",
            "_lrd_provenance": {
                "source_code": source_code,
                "line": line,
                "is_primary": is_primary,
                "window": [z_min, z_max],
            },
        })

    hypotheses.sort(key=lambda h: -h["score"])
    all_z = sorted({h["z_representative"] for h in hypotheses})
    zmedian = round(statistics.median(all_z), 4) if all_z else None

    return {"z": all_z, "zmedian": zmedian, "hypotheses": hypotheses}


def _strongest_peak_wavelength(state):
    """Pick the observed wavelength of the highest-amplitude detected peak.

    Returns None if no peaks were detected -- this is a normal, expected
    outcome (a real spectrum can legitimately show no significant feature,
    e.g. a negative control, low SNR, or a masked/contaminated region), not
    an error condition. Mirrors upstream's own brute_force_line_matching
    (utils/VI.py), which naturally falls through to an empty-but-valid
    {"z": [], "zmedian": None, "hypotheses": []} result on zero peaks
    rather than raising -- callers of generate_lrd_hypotheses_for_state
    rely on that same convention (CLAUDE.md finding #1: "downstream does
    not care where hypotheses come from").
    """
    peaks = state.get("peaks") or state.get("emission_records") or []
    if not peaks:
        return None

    def amp_key(p):
        return p.get("amplitude", p.get("amplitude_rank", 0))

    best = max(peaks, key=amp_key)
    return float(best["wavelength"])


def generate_lrd_hypotheses_for_state(state, params):
    """
    Wired in at the VisualInterpreter Redrock call site
    (HYPOTHESIS_PROVIDER=lrd). Looks up the per-source primary hypothesis
    (paper line + z_spec) from the anonymized-code-keyed table in
    lrd_adapt/configs/primary_hypotheses.json — never from real source
    identity (that mapping lives separately, outside agent reach, per
    CLAUDE.md Anonymization).
    """
    table_path = os.getenv("LRD_PRIMARY_HYPOTHESIS_TABLE") or os.path.join(
        os.path.dirname(__file__), "..", "configs", "primary_hypotheses.json"
    )
    with open(table_path) as f:
        table = json.load(f)

    source_code = state["file_name"]
    if source_code not in table:
        raise ValueError(
            f"No primary hypothesis registered for {source_code!r} in {table_path}"
        )

    entry = table[source_code]
    observed_wavelength_ang = _strongest_peak_wavelength(state)
    if observed_wavelength_ang is None:
        return {"z": [], "zmedian": None, "hypotheses": []}

    return generate_lrd_hypotheses(
        observed_wavelength_ang=observed_wavelength_ang,
        primary_line=entry["line"],
        z_spec=entry["z_spec"],
        source_code=source_code,
    )
