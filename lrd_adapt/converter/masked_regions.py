"""
Derive masked (grism-contamination-flagged) wavelength intervals directly
from a FORMA-format FITS file's own {ARM}_MASK/{ARM}_WAVELENGTH HDUs.

FORMA's loader (utils/VI.py:_load_spectrum_from_fits) silently drops any
pixel with a nonzero mask value before the LLM-facing pipeline ever sees
the spectrum -- the wavelength/flux arrays it returns simply have those
points removed, with no record of where. This reconstructs the dropped
wavelength ranges purely by re-reading the FITS file, so they can be
surfaced through the same "masked regions" channel multi-arm overlap
already uses (VisualInterpreter.py's state['spectrum']['overlap_regions'],
consumed by HypothesisAnalyst.py and AnalysisAuditor.py).
"""
from __future__ import annotations

import numpy as np
from astropy.io import fits


def masked_wavelength_intervals(fits_path, arm_name):
    """Return a list of [lo, hi] Angstrom intervals where {arm_name}_MASK is
    nonzero, merging adjacent masked pixels into contiguous runs.

    Returns [] if the file doesn't have the expected HDUs, or if nothing is
    masked.
    """
    with fits.open(fits_path) as hdul:
        names = [hdu.name.upper() for hdu in hdul]
        mask_key = f"{arm_name}_MASK"
        wave_key = f"{arm_name}_WAVELENGTH"
        if mask_key not in names or wave_key not in names:
            return []
        mask = np.ravel(hdul[mask_key].data)
        wavelength = np.ravel(hdul[wave_key].data)

    bad = mask != 0
    if not np.any(bad):
        return []

    order = np.argsort(wavelength)
    wavelength = wavelength[order]
    bad = bad[order]

    intervals = []
    in_run = False
    run_start = None
    prev_wl = wavelength[0]
    for wl, is_bad in zip(wavelength, bad):
        if is_bad and not in_run:
            in_run = True
            run_start = wl
        elif not is_bad and in_run:
            intervals.append([float(run_start), float(prev_wl)])
            in_run = False
        prev_wl = wl
    if in_run:
        intervals.append([float(run_start), float(prev_wl)])

    return intervals
