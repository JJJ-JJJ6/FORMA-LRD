"""
Synthetic injection utility (CLAUDE.md build order C: "synthetic
injections"). Generates grizli-shaped 1D spectra with a known, injected
line signal at a known redshift and (optionally) known real/fake
broadening — for building test cases with a ground-truth answer, distinct
from the real 19-source LOO evaluation and the (not-yet-available)
negative controls.

Reuses the same generation approach as lrd_adapt/eval/demo_one_source.py
(A1) and lrd_adapt/tools/test_broadline_lsf_bic.py (A3), consolidated here
so eval code has one source of truth instead of three copies of ad hoc
spectrum-synthesis logic.
"""
from __future__ import annotations

import numpy as np
from astropy.io import fits

from lrd_adapt.hypothesis.lrd_hypothesis_provider import REST_WAVELENGTHS_ANG
from lrd_adapt.tools.broadline_lsf_bic import (
    R_POINT_SOURCE,
    R_EXTENDED_DEFAULT,
    extended_lsf_sigma_ang,
    lsf_sigma_ang,
    velocity_sigma_to_ang,
)

SIGMA_TO_FWHM = 2.3548200450309493


def _gaussian(x, amp, center, sigma):
    return amp * np.exp(-0.5 * ((x - center) / sigma) ** 2)


def make_synthetic_case(
    line,
    z,
    *,
    broad_fwhm_kms=None,
    narrow_fwhm_kms=200.0,
    r_circ_mas=None,
    R_extended=R_EXTENDED_DEFAULT,
    snr=25.0,
    contam_regions=None,
    seed=0,
    wave_min_um=3.15,
    wave_max_um=3.95,
    n_pixels=800,
):
    """
    Build one synthetic case with a known ground truth.

    Parameters
    ----------
    line : str
        Key into REST_WAVELENGTHS_ANG (e.g. "HeI_Pagamma").
    z : float
        Ground-truth redshift.
    broad_fwhm_kms : float, optional
        If set, injects a REAL broad component (point-source LSF) at this
        FWHM on top of the narrow one -- the ground truth for
        `_fit_broadline_lsf_bic` should be broad_line_real=True. If None,
        only the narrow component is injected, broadened solely by the
        extended-source LSF -- ground truth should be broad_line_real=False.
    narrow_fwhm_kms : float
        Intrinsic narrow-component velocity FWHM.
    r_circ_mas : float, optional
        If set, the "no real broadening" case uses this to derive the
        extended-source LSF (see lrd_adapt.tools.broadline_lsf_bic); else
        falls back to R_extended.
    contam_regions : list of (lo, hi), optional
        Angstrom ranges (observed) to mark contaminated in the mask (bit 4)
        -- for building contamination-stress-case-like synthetic sources.
    snr : float
        Approximate peak amplitude / noise ratio.
    seed : int
        RNG seed, for reproducibility.

    Returns
    -------
    dict
        wave_um, flux, err, flat, contam (grizli-shaped arrays, ready for
        lrd_adapt.converter.grizli_to_forma.convert_grizli_1d_to_forma),
        plus a "ground_truth" dict recording what was injected.
    """
    if line not in REST_WAVELENGTHS_ANG:
        raise ValueError(f"Unknown line {line!r}; known: {sorted(REST_WAVELENGTHS_ANG)}")

    rest_ang = REST_WAVELENGTHS_ANG[line]
    center_obs_ang = rest_ang * (1.0 + z)

    rng = np.random.default_rng(seed)
    wave_um = np.linspace(wave_min_um, wave_max_um, n_pixels)
    wave_ang = wave_um * 1e4

    continuum = 0.5 + 0.02 * (wave_um - wave_um.mean())

    narrow_v_sigma = narrow_fwhm_kms / SIGMA_TO_FWHM
    ground_truth = {
        "line": line, "z": z, "center_obs_ang": float(center_obs_ang),
        "narrow_fwhm_kms": narrow_fwhm_kms, "broad_fwhm_kms": broad_fwhm_kms,
        "broad_line_real": broad_fwhm_kms is not None,
    }

    if broad_fwhm_kms is not None:
        sigma_lsf_point = lsf_sigma_ang(center_obs_ang, R_POINT_SOURCE)
        narrow_sigma_ang = float(np.hypot(velocity_sigma_to_ang(narrow_v_sigma, center_obs_ang), sigma_lsf_point))
        broad_v_sigma = broad_fwhm_kms / SIGMA_TO_FWHM
        broad_sigma_ang = float(np.hypot(velocity_sigma_to_ang(broad_v_sigma, center_obs_ang), sigma_lsf_point))
        flux = continuum + _gaussian(wave_ang, 2.0, center_obs_ang, narrow_sigma_ang) \
            + _gaussian(wave_ang, 1.2, center_obs_ang, broad_sigma_ang)
    else:
        sigma_ext = extended_lsf_sigma_ang(center_obs_ang, r_circ_mas=r_circ_mas, R_extended=R_extended)
        narrow_sigma_ang = float(np.hypot(velocity_sigma_to_ang(narrow_v_sigma, center_obs_ang), sigma_ext))
        flux = continuum + _gaussian(wave_ang, 2.5, center_obs_ang, narrow_sigma_ang)

    peak_amp = float(np.max(flux) - np.median(continuum))
    noise_sigma = peak_amp / snr
    flux = flux + rng.normal(0, noise_sigma, size=wave_um.shape)

    err = np.full_like(flux, noise_sigma)
    flat = np.ones_like(flux)
    contam = np.zeros_like(flux)
    if contam_regions:
        for lo, hi in contam_regions:
            in_region = (wave_ang >= lo) & (wave_ang <= hi)
            contam[in_region] = 0.6 * np.abs(flux[in_region])  # >0.5 * flux -> converter masks it

    return {
        "wave_um": wave_um, "flux": flux, "err": err, "flat": flat, "contam": contam,
        "ground_truth": ground_truth,
    }


def write_synthetic_1d_fits(case, path, arm_extname="F356W"):
    """Write a synthetic case (from make_synthetic_case) as a grizli-shaped
    *.1D.fits file, ready for lrd_adapt.converter.grizli_to_forma."""
    col = fits.ColDefs([
        fits.Column(name="wave", format="D", array=case["wave_um"]),
        fits.Column(name="flux", format="D", array=case["flux"]),
        fits.Column(name="err", format="D", array=case["err"]),
        fits.Column(name="flat", format="D", array=case["flat"]),
        fits.Column(name="contam", format="D", array=case["contam"]),
    ])
    hdu = fits.BinTableHDU.from_columns(col, name=arm_extname)
    fits.HDUList([fits.PrimaryHDU(), hdu]).writeto(path, overwrite=True)
