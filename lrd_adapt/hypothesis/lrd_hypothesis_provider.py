"""
LRD hypothesis provider — replaces Redrock at the VisualInterpreter call site
(CLAUDE.md finding #2). For each source, generates every line identity
consistent with the observed wavelength of the strongest detected line,
using the F356W grism redshift windows from Kapoor+26 Section 3.2.

A registered claim (`primary_line`/`z_spec`, e.g. from a paper table) is
OPTIONAL and, when given, is carried through only as a provenance tag
(`_lrd_provenance.matches_registered_claim`) for later scoring/comparison —
it never changes a candidate's score, computed redshift, or whether it's
generated at all. Every candidate is derived from the observed wavelength
the same way, so this works identically for a source with a known claim to
audit (leave-one-out evaluation against the 19 registered sources) and a
source with no claim at all (blind search over an unscreened sample).

Output matches the schema of utils.VI._redrock_to_hypotheses /
brute_force_line_matching: {z, zmedian, hypotheses: [...]}. Downstream
harness code does not care where hypotheses come from (finding #1).
"""
from __future__ import annotations

import json
import math
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
    """Triangular score peaking at the window center — the same formula for
    every candidate, including one that happens to match a registered claim.
    Purely a data-plausibility measure, not a trust signal."""
    center = (z_min + z_max) / 2.0
    half_width = (z_max - z_min) / 2.0
    if half_width <= 0:
        return 0.0
    frac = max(0.0, 1.0 - abs(z - center) / half_width)
    return 100.0 * frac


def _prior_bonus(z, prior, prior_sigma):
    """Gaussian bonus in [0, 100] for how close a candidate's data-implied
    redshift is to an independent external prior, in units of the prior's
    own uncertainty. 0 if no prior was given. A tight prior (small sigma —
    e.g. a confident multi-line automated fit) sharply favors the one
    matching candidate and does almost nothing for the rest; a loose prior
    (large sigma — e.g. a coarse photometric redshift) gives everyone a
    gentle, roughly even nudge. This is additive on top of _score_for_window,
    not a replacement -- a candidate outside its own physically plausible
    window is still never generated at all, no matter how well it matches
    the prior."""
    if prior is None or prior_sigma is None or prior_sigma <= 0:
        return 0.0
    n_sigma = abs(z - prior) / prior_sigma
    return 100.0 * math.exp(-0.5 * n_sigma ** 2)


def generate_lrd_hypotheses(
    observed_wavelength_ang,
    primary_line=None,
    z_spec=None,
    source_code=None,
    external_z_prior=None,
    external_z_prior_sigma=None,
    external_z_prior_source=None,
):
    """
    Parameters
    ----------
    observed_wavelength_ang : float
        Observed wavelength of the strongest detected broad line (Angstrom).
    primary_line : str, optional
        Key into REST_WAVELENGTHS_ANG — a registered claim for this source
        (e.g. a paper's line identity), if one exists. Used only to tag
        whichever generated candidate happens to match it
        (`_lrd_provenance.matches_registered_claim`) for later scoring — it
        does not affect that candidate's score or computed redshift, and a
        claimed line whose data-implied redshift falls outside its own
        plausible window is not specially retained. If None (no registered
        claim — the normal case for a blind search), every hypothesis is
        generated identically with no provenance match possible.
    z_spec : float, optional
        The registered claim's redshift, carried through only as
        `_lrd_provenance.registered_z_spec` for human/eval comparison —
        never substituted for the data-derived redshift of any candidate.
    source_code : str, optional
        Anonymized code, carried into provenance only (see CLAUDE.md
        Anonymization section — never a real J-name/coordinate).
    external_z_prior : float, optional
        An independently-derived redshift estimate for this source (e.g.
        grizli's own template-fit best-z from its full.fits product, or a
        photometric redshift) -- NOT derived from assuming any particular
        identity for the line under test here, or scoring becomes
        circular (a prior is "independent" if you could have computed it
        without already knowing which line this is). When given, every
        candidate's score gets a bonus for how close its data-implied
        redshift sits to this prior -- see _prior_bonus. This is the hook
        for consuming e.g. grizli's automated redshift fit once a real
        product exists to parse (not implemented yet -- no real grizli
        full.fits has been available to check the schema against; see
        lrd_adapt/hypothesis/test_lrd_hypothesis_provider.py for how this
        parameter behaves once something does supply it).
    external_z_prior_sigma : float, optional
        1-sigma uncertainty on external_z_prior. Required for the prior to
        have any effect -- an unquantified prior can't be weighted
        meaningfully. Smaller = sharper discrimination (e.g. a confident
        multi-line automated fit); larger = a gentler, more permissive
        nudge (e.g. a coarse photo-z).
    external_z_prior_source : str, optional
        Free-text provenance for the prior (e.g. "grizli_zfit", "photo_z"),
        carried into _lrd_provenance only -- audit trail, never affects
        the computed score.

    Returns
    -------
    dict
        {z, zmedian, hypotheses} — same schema as
        utils.VI._redrock_to_hypotheses / brute_force_line_matching.
    """
    if primary_line is not None and primary_line not in REST_WAVELENGTHS_ANG:
        raise ValueError(
            f"Unknown primary_line {primary_line!r}; known: {sorted(REST_WAVELENGTHS_ANG)}"
        )

    hypotheses = []
    for line, rest_ang in REST_WAVELENGTHS_ANG.items():
        z = observed_wavelength_ang / rest_ang - 1.0
        z_min, z_max = REDSHIFT_WINDOWS[line]

        if not (z_min <= z <= z_max):
            continue  # not physically plausible at this observed wavelength

        window_score = _score_for_window(z, z_min, z_max)
        prior_bonus = _prior_bonus(z, external_z_prior, external_z_prior_sigma)
        score = window_score + prior_bonus
        matches_registered_claim = line == primary_line

        hypotheses.append({
            "Hypothesis": (
                f"{z:.4f}-{line} (obs={observed_wavelength_ang:.1f}A -> rest={rest_ang:.1f}A)"
            ),
            "z_center": z,
            "z_list": [z],
            "z_max": z,
            "z_min": z,
            "z_spread": 0.0,
            "Emission matches": [line],
            "Absorption matches": [],
            "N_emission": 1,
            "N_absorption": 0,
            "matched_lines": {line: observed_wavelength_ang},
            "z_representative": z,
            "score": score,
            "source": "lrd_adapt:candidate",
            "_lrd_provenance": {
                "source_code": source_code,
                "line": line,
                "matches_registered_claim": matches_registered_claim,
                "registered_z_spec": z_spec if matches_registered_claim else None,
                "window": [z_min, z_max],
                "window_score": window_score,
                "external_z_prior": external_z_prior,
                "external_z_prior_sigma": external_z_prior_sigma,
                "external_z_prior_source": external_z_prior_source,
                "prior_bonus": prior_bonus,
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
    (HYPOTHESIS_PROVIDER=lrd). If this source has a registered claim
    (anonymized-code-keyed entry in lrd_adapt/configs/primary_hypotheses.json
    — never from real source identity, that mapping lives separately outside
    agent reach per CLAUDE.md Anonymization), it's passed through for
    provenance tagging only. A source with NO registered entry is not an
    error -- that's the normal case for a blind search over sources with no
    prior claim -- hypotheses are still generated purely from the observed
    wavelength.

    Also reads an optional external redshift prior straight from state
    (state['external_z_prior']/['external_z_prior_sigma']/
    ['external_z_prior_source']) if some upstream step has set one -- e.g.
    a future converter that parses grizli's own automated redshift-fit
    product (full.fits), once a real one exists to check the schema
    against. Nothing currently sets these state fields; they default to
    None, which generate_lrd_hypotheses treats as "no prior" (unchanged
    behavior). This is the wiring point for that future integration, not
    the integration itself.
    """
    table_path = os.getenv("LRD_PRIMARY_HYPOTHESIS_TABLE") or os.path.join(
        os.path.dirname(__file__), "..", "configs", "primary_hypotheses.json"
    )
    table = {}
    if os.path.exists(table_path):
        with open(table_path) as f:
            table = json.load(f)

    source_code = state["file_name"]
    entry = table.get(source_code)

    observed_wavelength_ang = _strongest_peak_wavelength(state)
    if observed_wavelength_ang is None:
        return {"z": [], "zmedian": None, "hypotheses": []}

    return generate_lrd_hypotheses(
        observed_wavelength_ang=observed_wavelength_ang,
        primary_line=entry["line"] if entry else None,
        z_spec=entry["z_spec"] if entry else None,
        source_code=source_code,
        external_z_prior=state.get("external_z_prior"),
        external_z_prior_sigma=state.get("external_z_prior_sigma"),
        external_z_prior_source=state.get("external_z_prior_source"),
    )
