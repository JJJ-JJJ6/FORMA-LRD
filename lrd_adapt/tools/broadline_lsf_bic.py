"""
Extended-source-LSF broad-line tool (CLAUDE.md change budget, new code #3).

The core "is the broad line real velocity broadening" check, mirroring
Kapoor+26 Sections 3.1/4.2: models the observed line profile as intrinsic
velocity structure convolved with the instrumental line-spread function
(LSF), and compares three physical scenarios via BIC:

  M1 narrow_extended_lsf   — one narrow-velocity Gaussian convolved with the
                             EXTENDED-source LSF. The null/skeptical model:
                             all apparent width is spatial-extent smearing,
                             not real velocity broadening.
  M2 narrow_broad_point_lsf — narrow + broad Gaussians, both convolved with
                             the POINT-source LSF. Real velocity structure,
                             compact/unresolved source.
  M3 mixed                 — narrow Gaussian (x) EXTENDED LSF + broad
                             Gaussian (x) POINT LSF. Physical size
                             contributes to the narrow core AND there is
                             genuine broad emission on top.

A Gaussian convolved with a Gaussian is a Gaussian with sigmas added in
quadrature, so no numerical convolution is needed. Model selection follows
CLAUDE.md's decision rule: broad-line reality requires the best model to
beat the null (M1) by ΔBIC > 10.

Fitting follows this repo's existing convention (see
harness/tools.py:_do_fit_peak) — curve_fit is run unweighted, and a robust
noise estimate (median-absolute-deviation of a reference single-Gaussian
fit's residuals) is applied afterward to compute a consistent chi2/BIC
across all three models. The noise estimator is reimplemented locally
(rather than imported from harness/tools.py) so this module only needs
numpy/scipy — harness/tools.py pulls in the full LangGraph/LangChain agent
stack, which isn't needed for this deterministic fitting tool.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import curve_fit

C_KMS = 299792.458
SIGMA_TO_FWHM = 2.3548200450309493  # 2 * sqrt(2 * ln 2)

# CLAUDE.md Science constants / finding #10.
R_POINT_SOURCE = 1600.0
R_EXTENDED_DEFAULT = 500.0  # midpoint of the quoted 400-600 effective range
NIRCAM_LW_PIXEL_ARCSEC = 0.063
NIRCAM_LW_PIXEL_ANG = 9.8  # 1 LW pixel at F356W, per CLAUDE.md


def lsf_sigma_ang(wavelength_ang, R):
    """Gaussian LSF sigma (Angstrom) at resolving power R (FWHM = lambda/R)."""
    return wavelength_ang / (R * SIGMA_TO_FWHM)


def extended_lsf_sigma_ang(wavelength_ang, r_circ_mas=None, R_extended=R_EXTENDED_DEFAULT):
    """
    Effective LSF sigma (Angstrom) for a spatially-extended source along the
    dispersion direction.

    If `r_circ_mas` (measured circularized half-light radius, Kapoor+26
    Fig. 5) is given, the extra smearing is derived directly from the
    source's physical size via the pixel scale (1 LW pixel = 0.063" =
    9.8 A) and added in quadrature to the point-source LSF. Otherwise falls
    back to a fixed effective resolving power (CLAUDE.md: R ~= 400-600).
    """
    point_sigma = lsf_sigma_ang(wavelength_ang, R_POINT_SOURCE)
    if r_circ_mas is not None:
        extra_fwhm_ang = (r_circ_mas / (NIRCAM_LW_PIXEL_ARCSEC * 1000.0)) * NIRCAM_LW_PIXEL_ANG
        extra_sigma_ang = extra_fwhm_ang / SIGMA_TO_FWHM
        return float(np.hypot(point_sigma, extra_sigma_ang))
    return lsf_sigma_ang(wavelength_ang, R_extended)


def velocity_sigma_to_ang(v_sigma_kms, wavelength_ang):
    return (v_sigma_kms / C_KMS) * wavelength_ang


def _gaussian(x, amp, center, sigma):
    return amp * np.exp(-0.5 * ((x - center) / sigma) ** 2)


def _power_law_continuum(x, amp, index, pivot):
    return amp * (x / pivot) ** index


def _estimate_local_rms(x, y):
    """Robust noise estimate via a quick single-Gaussian+linear reference
    fit, MAD of its residuals — same formula as harness/tools.py's
    _do_fit_peak local_rms, reimplemented standalone (see module docstring)."""
    slope0, intercept0 = np.polyfit(x, y, 1)
    amp0 = float(np.max(y) - np.median(y)) or 1e-6
    center0 = float(x[np.argmax(y)])
    sigma0 = (x.max() - x.min()) / 8.0

    def _gauss_lin(x_, amp, center, sigma, slope, intercept):
        return _gaussian(x_, amp, center, sigma) + slope * x_ + intercept

    try:
        popt, _ = curve_fit(
            _gauss_lin, x, y,
            p0=[amp0, center0, sigma0, slope0, intercept0],
            maxfev=5000,
        )
        resid = y - _gauss_lin(x, *popt)
    except RuntimeError:
        resid = y - (slope0 * x + intercept0)

    local_rms = 1.4826 * np.median(np.abs(resid - np.median(resid)))
    if local_rms < 1e-10:
        local_rms = np.std(resid) or 1e-10
    return float(local_rms)


def fit_broadline_lsf_bic(
    wavelength_ang,
    flux,
    line_rest_ang,
    z_guess,
    r_circ_mas=None,
    R_extended=R_EXTENDED_DEFAULT,
    window_half_ang=150.0,
    narrow_v_sigma_bounds=(20.0, 220.0),    # FWHM ~ 47-518 km/s
    broad_v_sigma_bounds=(220.0, 1300.0),   # FWHM ~ 518-3060 km/s (covers Kapoor+26's 1400-2100 km/s)
    broad_offset_bounds_kms=(-300.0, 300.0),  # CLAUDE.md SS4.2: velocity offsets +/-300 km/s
):
    """
    Compare the three profile models above for a single line and return the
    BIC-based broad-line-reality verdict.

    Parameters
    ----------
    wavelength_ang, flux : array-like
        Full spectrum arrays (Angstrom, same units as fit_peak/fit_doublet).
    line_rest_ang : float
        Rest-frame wavelength of the line under test (Angstrom).
    z_guess : float
        Redshift hypothesis being audited — sets the observed-frame center.
    r_circ_mas : float, optional
        Measured circularized half-light radius (mas) for this source, if
        available (Kapoor+26 Fig. 5); refines the extended-source LSF.
    R_extended : float
        Fallback effective extended-source resolving power if r_circ_mas
        is not supplied.
    window_half_ang : float
        Half-width of the fitting window (Angstrom).

    Returns
    -------
    dict
        n_points, models (per-model params/chi2/bic), best_model,
        delta_bic_vs_null, broad_line_real, broad_fwhm_kms (of the winning
        model, None if the null model wins).
    """
    wl_full = np.asarray(wavelength_ang, dtype=float)
    flux_full = np.asarray(flux, dtype=float)

    center_guess = line_rest_ang * (1.0 + z_guess)
    mask = (wl_full >= center_guess - window_half_ang) & (wl_full <= center_guess + window_half_ang)
    x = wl_full[mask]
    y = flux_full[mask]
    n = len(x)
    if n < 15:
        raise ValueError(
            f"Too few points ({n}) in [{center_guess - window_half_ang:.1f}, "
            f"{center_guess + window_half_ang:.1f}] A for a 3-model BIC comparison."
        )

    # Robust noise estimate from a simple reference fit, matching the
    # local_rms convention used elsewhere in this codebase (_do_fit_peak).
    local_rms = _estimate_local_rms(x, y)
    if not local_rms:
        raise ValueError(f"Could not estimate a noise level for the line at {center_guess:.1f} A.")

    pivot = center_guess
    sigma_lsf_point = lsf_sigma_ang(center_guess, R_POINT_SOURCE)
    sigma_lsf_ext = extended_lsf_sigma_ang(center_guess, r_circ_mas, R_extended)

    amp0 = max(np.max(y) - np.median(y), 1e-6)
    cont0 = max(np.median(y), 1e-6)

    def continuum(x_, cont_amp, cont_index):
        return _power_law_continuum(x_, cont_amp, cont_index, pivot)

    # ---- M1: narrow (x) extended LSF ----
    def model_m1(x_, amp, center, v_sigma_n, cont_amp, cont_index):
        sigma_ang = np.hypot(velocity_sigma_to_ang(v_sigma_n, center_guess), sigma_lsf_ext)
        return _gaussian(x_, amp, center, sigma_ang) + continuum(x_, cont_amp, cont_index)

    p0_m1 = [amp0, center_guess, 80.0, cont0, 0.0]
    bounds_m1 = (
        [0, center_guess - 20, narrow_v_sigma_bounds[0], -np.inf, -5],
        [np.inf, center_guess + 20, narrow_v_sigma_bounds[1], np.inf, 5],
    )

    # ---- M2: narrow + broad, both (x) point LSF ----
    def model_m2(x_, amp_n, amp_b, center, v_off_b, v_sigma_n, v_sigma_b, cont_amp, cont_index):
        sigma_n_ang = np.hypot(velocity_sigma_to_ang(v_sigma_n, center_guess), sigma_lsf_point)
        sigma_b_ang = np.hypot(velocity_sigma_to_ang(v_sigma_b, center_guess), sigma_lsf_point)
        center_b = center + (v_off_b / C_KMS) * center_guess
        return (_gaussian(x_, amp_n, center, sigma_n_ang)
                + _gaussian(x_, amp_b, center_b, sigma_b_ang)
                + continuum(x_, cont_amp, cont_index))

    p0_m2 = [amp0 * 0.7, amp0 * 0.3, center_guess, 0.0, 80.0, 500.0, cont0, 0.0]
    bounds_23 = (
        [0, 0, center_guess - 20, broad_offset_bounds_kms[0],
         narrow_v_sigma_bounds[0], broad_v_sigma_bounds[0], -np.inf, -5],
        [np.inf, np.inf, center_guess + 20, broad_offset_bounds_kms[1],
         narrow_v_sigma_bounds[1], broad_v_sigma_bounds[1], np.inf, 5],
    )

    # ---- M3: narrow (x) extended LSF + broad (x) point LSF ----
    def model_m3(x_, amp_n, amp_b, center, v_off_b, v_sigma_n, v_sigma_b, cont_amp, cont_index):
        sigma_n_ang = np.hypot(velocity_sigma_to_ang(v_sigma_n, center_guess), sigma_lsf_ext)
        sigma_b_ang = np.hypot(velocity_sigma_to_ang(v_sigma_b, center_guess), sigma_lsf_point)
        center_b = center + (v_off_b / C_KMS) * center_guess
        return (_gaussian(x_, amp_n, center, sigma_n_ang)
                + _gaussian(x_, amp_b, center_b, sigma_b_ang)
                + continuum(x_, cont_amp, cont_index))

    models = {
        "narrow_extended_lsf": (model_m1, p0_m1, bounds_m1),
        "narrow_broad_point_lsf": (model_m2, p0_m2, bounds_23),
        "mixed": (model_m3, p0_m2, bounds_23),
    }

    results = {}
    for name, (model_fn, p0, bounds) in models.items():
        try:
            popt, _ = curve_fit(model_fn, x, y, p0=p0, bounds=bounds, maxfev=20000)
        except RuntimeError as exc:
            results[name] = {"fit_ok": False, "error": str(exc)}
            continue

        resid = y - model_fn(x, *popt)
        chi2 = float(np.sum((resid / local_rms) ** 2))
        k = len(popt)
        bic = chi2 + k * np.log(n)
        entry = {
            "fit_ok": True,
            "params": [float(p) for p in popt],
            "chi2": chi2,
            "n_params": k,
            "bic": bic,
        }
        if name != "narrow_extended_lsf":
            entry["broad_fwhm_kms"] = float(popt[5] * SIGMA_TO_FWHM)
        results[name] = entry

    fit_ok = {k: v for k, v in results.items() if v.get("fit_ok")}
    if not fit_ok:
        raise RuntimeError("All three profile models failed to converge.")

    best_name = min(fit_ok, key=lambda k: fit_ok[k]["bic"])
    null_bic = results.get("narrow_extended_lsf", {}).get("bic")
    if null_bic is not None and best_name != "narrow_extended_lsf":
        delta_bic_vs_null = null_bic - fit_ok[best_name]["bic"]
    else:
        delta_bic_vs_null = 0.0

    broad_line_real = bool(best_name != "narrow_extended_lsf" and delta_bic_vs_null > 10.0)

    return {
        "n_points": n,
        "local_rms": local_rms,
        "models": results,
        "best_model": best_name,
        "delta_bic_vs_null": float(delta_bic_vs_null),
        "broad_line_real": broad_line_real,
        "broad_fwhm_kms": fit_ok[best_name].get("broad_fwhm_kms") if broad_line_real else None,
    }
