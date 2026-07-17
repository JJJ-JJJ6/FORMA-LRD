"""
Blueshifted He I absorption tool (Kapoor+26 SS4.2; CLAUDE.md decision
rules). Fills a gap flagged (not closed) in the Stage B commit: the
original A3 broadline_lsf_bic.py only models positive-amplitude emission
components, and AnalysisAuditor.py's composite-pair auto-detection is
hardcoded to Mg II/Halpha/Hbeta (there's no static He I_abs line by design
-- A2 treated this as a kinematic component of He I itself, not a separate
rest line). This module is the actual fitting tool that decision rule needs.

Per the paper: "we model the possible blueshifted He I lambda10830
absorption as an additional Gaussian with negative flux... the model
including the absorption is preferred for only two objects (dBIC>10)."
Preferred over the emission-only model, i.e. this is a BIC comparison
between [narrow+broad He I emission] and [narrow+broad He I emission +
blueshifted negative Gaussian], not a from-scratch joint He I+Pagamma fit
(that full joint model is a larger undertaking; see the module's __main__
test for what ground truth it does and doesn't recover).
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import curve_fit

from lrd_adapt.tools.broadline_lsf_bic import (
    C_KMS,
    SIGMA_TO_FWHM,
    R_POINT_SOURCE,
    _estimate_local_rms,
    _gaussian,
    _power_law_continuum,
    lsf_sigma_ang,
    velocity_sigma_to_ang,
)


def fit_blueshifted_absorption_bic(
    wavelength_ang,
    flux,
    line_rest_ang,
    z_guess,
    window_half_ang=150.0,
    narrow_v_sigma_bounds=(20.0, 220.0),
    broad_v_sigma_bounds=(220.0, 1300.0),
    broad_offset_bounds_kms=(-300.0, 300.0),
    absorption_blueshift_bounds_kms=(50.0, 1500.0),
    absorption_v_sigma_bounds=(50.0, 600.0),
):
    """
    Compare [narrow+broad emission] vs [narrow+broad emission + blueshifted
    negative-Gaussian absorption] for one line, via BIC.

    Parameters
    ----------
    wavelength_ang, flux : array-like
        Full spectrum arrays (Angstrom).
    line_rest_ang : float
        Rest-frame wavelength of the line (e.g. 10833.0 for He I).
    z_guess : float
        Redshift hypothesis.
    absorption_blueshift_bounds_kms : (float, float)
        Magnitude bounds (both positive) on the blueshift velocity -- the
        absorption center is constrained to be BLUEWARD of the emission
        center by this much, never redward (a physical prior: this is an
        outflow signature, not a coincidental second component).
    absorption_v_sigma_bounds : (float, float)
        Velocity-sigma bounds on the absorption component's width.

    Returns
    -------
    dict
        n_points, local_rms, base_bic, with_absorption_bic,
        delta_bic (base - with_absorption; positive favors absorption),
        absorption_real (bool, delta_bic > 10),
        absorption_velocity_kms, absorption_fwhm_kms (None if not real).
    """
    wl_full = np.asarray(wavelength_ang, dtype=float)
    flux_full = np.asarray(flux, dtype=float)

    center_guess = line_rest_ang * (1.0 + z_guess)
    mask = (wl_full >= center_guess - window_half_ang) & (wl_full <= center_guess + window_half_ang)
    x = wl_full[mask]
    y = flux_full[mask]
    n = len(x)
    if n < 15:
        raise ValueError(f"Too few points ({n}) for a blueshifted-absorption BIC comparison.")

    local_rms = _estimate_local_rms(x, y)
    if not local_rms:
        raise ValueError(f"Could not estimate a noise level for the line at {center_guess:.1f} A.")

    pivot = center_guess
    sigma_lsf_point = lsf_sigma_ang(center_guess, R_POINT_SOURCE)

    def continuum(x_, cont_amp, cont_index):
        return _power_law_continuum(x_, cont_amp, cont_index, pivot)

    amp0 = max(np.max(y) - np.median(y), 1e-6)
    cont0 = max(np.median(y), 1e-6)

    def base_model(x_, amp_n, amp_b, center, v_off_b, v_sigma_n, v_sigma_b, cont_amp, cont_index):
        sigma_n_ang = np.hypot(velocity_sigma_to_ang(v_sigma_n, center_guess), sigma_lsf_point)
        sigma_b_ang = np.hypot(velocity_sigma_to_ang(v_sigma_b, center_guess), sigma_lsf_point)
        center_b = center + (v_off_b / C_KMS) * center_guess
        return (_gaussian(x_, amp_n, center, sigma_n_ang)
                + _gaussian(x_, amp_b, center_b, sigma_b_ang)
                + continuum(x_, cont_amp, cont_index))

    p0_base = [amp0 * 0.7, amp0 * 0.3, center_guess, 0.0, 80.0, 500.0, cont0, 0.0]
    bounds_base = (
        [0, 0, center_guess - 20, broad_offset_bounds_kms[0],
         narrow_v_sigma_bounds[0], broad_v_sigma_bounds[0], -np.inf, -5],
        [np.inf, np.inf, center_guess + 20, broad_offset_bounds_kms[1],
         narrow_v_sigma_bounds[1], broad_v_sigma_bounds[1], np.inf, 5],
    )

    def model_with_absorption(x_, amp_n, amp_b, center, v_off_b, v_sigma_n, v_sigma_b,
                               cont_amp, cont_index, amp_abs, v_blueshift, v_sigma_abs):
        emission = base_model(x_, amp_n, amp_b, center, v_off_b, v_sigma_n, v_sigma_b, cont_amp, cont_index)
        center_abs = center - (v_blueshift / C_KMS) * center_guess  # blueward only
        sigma_abs_ang = velocity_sigma_to_ang(v_sigma_abs, center_guess)
        absorption = _gaussian(x_, -abs(amp_abs), center_abs, sigma_abs_ang)
        return emission + absorption

    amp_abs0 = amp0 * 0.2
    p0_with_abs = p0_base + [amp_abs0, float(np.mean(absorption_blueshift_bounds_kms)), 150.0]
    bounds_with_abs = (
        list(bounds_base[0]) + [0, absorption_blueshift_bounds_kms[0], absorption_v_sigma_bounds[0]],
        list(bounds_base[1]) + [np.inf, absorption_blueshift_bounds_kms[1], absorption_v_sigma_bounds[1]],
    )

    popt_base, _ = curve_fit(base_model, x, y, p0=p0_base, bounds=bounds_base, maxfev=20000)
    chi2_base = float(np.sum(((y - base_model(x, *popt_base)) / local_rms) ** 2))
    bic_base = chi2_base + len(popt_base) * np.log(n)

    popt_abs, _ = curve_fit(
        model_with_absorption, x, y, p0=p0_with_abs, bounds=bounds_with_abs, maxfev=20000,
    )
    chi2_abs = float(np.sum(((y - model_with_absorption(x, *popt_abs)) / local_rms) ** 2))
    bic_abs = chi2_abs + len(popt_abs) * np.log(n)

    delta_bic = bic_base - bic_abs
    absorption_real = bool(delta_bic > 10.0)

    return {
        "n_points": n,
        "local_rms": local_rms,
        "base_bic": bic_base,
        "with_absorption_bic": bic_abs,
        "delta_bic": float(delta_bic),
        "absorption_real": absorption_real,
        "absorption_velocity_kms": float(popt_abs[-2]) if absorption_real else None,
        "absorption_fwhm_kms": float(popt_abs[-1] * SIGMA_TO_FWHM) if absorption_real else None,
    }
