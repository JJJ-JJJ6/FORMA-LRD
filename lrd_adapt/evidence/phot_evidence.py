"""
Photometric external evidence from a grizli field catalog
({root}_phot.fits): observed colors and a Balmer-break proxy for a given
source, in the provenance-tagged shape external_evidence.py consumes --
provenance "ours", never "paper".

This is the measurement side of the blind-search classification gap: the
existing external_evidence.json only holds paper-transcribed values for
Kapoor+26's 19 sources, so any NEW blind-search candidate classifies as
"Unknown". This module computes the same evidence categories from the
field's own photometric catalog instead.

SCHEMA CAVEAT (same standing rule as zfit_reader.py): grizli's
multiband_catalog() column names vary by version and setup --
select_negative_controls.py hit exactly this. This reader searches a
list of known column-name patterns and FAILS LOUDLY listing the columns
it actually found, never guessing silently. Validated against synthetic
catalogs only until a real {root}_phot.fits is checked (the real one
exists on eor1: j1030/Prep/j1030_phot.fits).

Balmer-break physics encoded here (Kapoor+26 Section 4.1/4.3):
  - the break is at rest 3645 A; the paper's estimator is
    f_nu(4050)/f_nu(3650), range ~1-4 across their LRD sample;
  - with EIGER's three NIRCam bands, F115W (pivot 1.154 um) sits below
    the break and F200W (pivot 1.989 um) above it only in a limited
    redshift window -- outside it the proxy is flagged unreliable, not
    silently reported.
"""
from __future__ import annotations

import numpy as np
from astropy.table import Table

# NIRCam pivot wavelengths (Angstrom, observed)
PIVOT_ANG = {"f115w": 11540.0, "f200w": 19890.0, "f356w": 35680.0}

BALMER_BREAK_REST_ANG = 3645.0

# The F115W-below / F200W-above geometry only holds when F115W's pivot
# lands blueward of the break with margin and F200W's redward of it:
#   z_min: 11540 / 3500 - 1  (F115W pivot rest < ~3500 A, below break)
#   z_max: F115W leaves the usable range / F200W rest drifts too red to
#          track the near-break continuum (rest > ~7500 A)
BREAK_PROXY_Z_RANGE = (2.30, 4.20)

# Column-name patterns to try per band, in order (grizli versions vary).
# {b} is the lowercase band name. Values are assumed uJy-like (linear
# flux) -- only ratios are used, so the absolute unit cancels.
_FLUX_COL_PATTERNS = [
    "{b}_flux_aper_1",
    "{b}_flux_aper_0",
    "{b}_flux",
    "{b}_tot_1",
    "flux_{b}",
]


def _find_flux_col(colnames, band):
    lower = {c.lower(): c for c in colnames}
    for pat in _FLUX_COL_PATTERNS:
        cand = pat.format(b=band)
        if cand in lower:
            return lower[cand]
    return None


def read_phot_catalog(path):
    """Read a {root}_phot.fits-style catalog, resolve the id + band flux
    columns defensively, and return (table, colmap). Raises ValueError
    listing the actual columns when a required one cannot be found."""
    cat = Table.read(path)
    colnames = list(cat.colnames)

    lower = {c.lower(): c for c in colnames}
    id_col = lower.get("id") or lower.get("number")
    if id_col is None:
        raise ValueError(
            f"{path}: no 'id'/'NUMBER' column. Columns present: {colnames}"
        )

    colmap = {"id": id_col}
    missing = []
    for band in PIVOT_ANG:
        col = _find_flux_col(colnames, band)
        if col is None:
            missing.append(band)
        else:
            colmap[band] = col
    if missing:
        raise ValueError(
            f"{path}: no recognized flux column for band(s) {missing} "
            f"(tried patterns {_FLUX_COL_PATTERNS}). Columns present: "
            f"{colnames}. If this is a real grizli catalog, extend "
            "_FLUX_COL_PATTERNS against THIS file rather than guessing."
        )
    return cat, colmap


def measure_phot_evidence(catalog_path, source_id, z=None):
    """
    Photometric evidence for one source: band fluxes, observed colors,
    and (when z is given and in the valid window) a Balmer-break proxy.

    Returns a dict shaped for external_evidence entries, every item
    provenance-tagged "ours". Flux ratios only -- absolute calibration
    cancels.
    """
    cat, colmap = read_phot_catalog(catalog_path)

    match = cat[np.asarray(cat[colmap["id"]], dtype=int) == int(source_id)]
    if len(match) == 0:
        raise ValueError(f"source id {source_id} not in {catalog_path}")
    row = match[0]

    flux = {}
    for band in PIVOT_ANG:
        val = float(row[colmap[band]])
        flux[band] = val if np.isfinite(val) and val > 0 else None

    out = {
        "provenance": "ours",
        "source_catalog": str(catalog_path),
        "flux_columns": {b: colmap[b] for b in PIVOT_ANG},
        "fluxes": flux,
    }

    # observed colors (magnitude differences; positive = redder)
    def _color(b1, b2):
        if flux[b1] and flux[b2]:
            return round(-2.5 * float(np.log10(flux[b1] / flux[b2])), 3)
        return None

    out["color_f115w_f200w_mag"] = _color("f115w", "f200w")
    out["color_f200w_f356w_mag"] = _color("f200w", "f356w")

    # Balmer-break proxy: f_nu(F200W)/f_nu(F115W), interpretable as a
    # break-strength stand-in only inside the valid z window.
    proxy = None
    reliable = False
    note = "no redshift given; break proxy not computed"
    if z is not None:
        if flux["f115w"] and flux["f200w"]:
            proxy = round(float(flux["f200w"] / flux["f115w"]), 3)
            zmin, zmax = BREAK_PROXY_Z_RANGE
            reliable = zmin <= z <= zmax
            f115_rest = PIVOT_ANG["f115w"] / (1 + z)
            f200_rest = PIVOT_ANG["f200w"] / (1 + z)
            note = (
                f"f_nu(F200W)/f_nu(F115W) at z={z}: rest pivots "
                f"{f115_rest:.0f}/{f200_rest:.0f} A vs break at "
                f"{BALMER_BREAK_REST_ANG:.0f} A; "
                + (
                    "band geometry brackets the break -- comparable to the "
                    "paper's f4050/f3650 estimator (range ~1-4 for LRDs)"
                    if reliable
                    else f"OUTSIDE valid z window {BREAK_PROXY_Z_RANGE} -- "
                    "bands do not cleanly bracket the break; do NOT use "
                    "as a break measurement"
                )
            )
        else:
            note = "missing F115W and/or F200W flux; break proxy not computed"
    out["balmer_break_proxy"] = proxy
    out["balmer_break_proxy_reliable"] = reliable
    out["balmer_break_note"] = note

    return out
