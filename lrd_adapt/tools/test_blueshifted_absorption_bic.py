"""
Unit tests against synthetic profiles for the blueshifted-He-I-absorption
tool. Same pattern as test_broadline_lsf_bic.py (A3). Run directly:

    python lrd_adapt/tools/test_blueshifted_absorption_bic.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lrd_adapt.tools.blueshifted_absorption_bic import fit_blueshifted_absorption_bic  # noqa: E402
from lrd_adapt.tools.broadline_lsf_bic import (  # noqa: E402
    C_KMS,
    SIGMA_TO_FWHM,
    R_POINT_SOURCE,
    _gaussian,
    lsf_sigma_ang,
    velocity_sigma_to_ang,
)

LINE_REST_ANG = 10833.0  # He I
Z_TRUE = 2.328


def _make_spectrum(center_obs, components, snr=40.0, seed=0):
    rng = np.random.default_rng(seed)
    x = np.linspace(center_obs - 200, center_obs + 200, 500)
    y = np.full_like(x, 1.0)
    peak_amp = 0.0
    for amp, sigma_ang, comp_center in components:
        y = y + _gaussian(x, amp, comp_center, sigma_ang)
        peak_amp = max(peak_amp, abs(amp))
    noise_sigma = peak_amp / snr
    y = y + rng.normal(0, noise_sigma, size=x.shape)
    return x, y


def test_no_absorption_case():
    center_obs = LINE_REST_ANG * (1 + Z_TRUE)
    sigma_lsf = lsf_sigma_ang(center_obs, R_POINT_SOURCE)
    sigma_n = float(np.hypot(velocity_sigma_to_ang(100.0, center_obs), sigma_lsf))
    sigma_b = float(np.hypot(velocity_sigma_to_ang(1800.0 / SIGMA_TO_FWHM, center_obs), sigma_lsf))

    x, y = _make_spectrum(center_obs, [(0.7, sigma_n, center_obs), (0.4, sigma_b, center_obs)], seed=1)
    result = fit_blueshifted_absorption_bic(x, y, LINE_REST_ANG, Z_TRUE)

    print(f"Case A (no absorption): delta_bic={result['delta_bic']:.2f} absorption_real={result['absorption_real']}")
    assert result["absorption_real"] is False


def test_real_blueshifted_absorption_case():
    center_obs = LINE_REST_ANG * (1 + Z_TRUE)
    sigma_lsf = lsf_sigma_ang(center_obs, R_POINT_SOURCE)
    sigma_n = float(np.hypot(velocity_sigma_to_ang(100.0, center_obs), sigma_lsf))
    sigma_b = float(np.hypot(velocity_sigma_to_ang(1800.0 / SIGMA_TO_FWHM, center_obs), sigma_lsf))

    true_blueshift_kms = 600.0
    true_abs_fwhm_kms = 400.0
    abs_center = center_obs - (true_blueshift_kms / C_KMS) * center_obs
    abs_sigma_ang = velocity_sigma_to_ang(true_abs_fwhm_kms / SIGMA_TO_FWHM, center_obs)

    x, y = _make_spectrum(
        center_obs,
        [(0.7, sigma_n, center_obs), (0.4, sigma_b, center_obs), (-0.25, abs_sigma_ang, abs_center)],
        seed=2,
    )
    result = fit_blueshifted_absorption_bic(x, y, LINE_REST_ANG, Z_TRUE)

    print(
        f"Case B (real blueshifted absorption): delta_bic={result['delta_bic']:.2f} "
        f"absorption_real={result['absorption_real']} "
        f"recovered_v={result['absorption_velocity_kms']:.1f} (true={true_blueshift_kms}) "
        f"recovered_fwhm={result['absorption_fwhm_kms']:.1f} (true={true_abs_fwhm_kms})"
    )
    assert result["absorption_real"] is True
    assert result["delta_bic"] > 10.0
    assert 200.0 < result["absorption_velocity_kms"] < 1200.0  # order-of-magnitude sanity, not exact recovery


if __name__ == "__main__":
    test_no_absorption_case()
    test_real_blueshifted_absorption_case()
    print("\nOK -- both blueshifted-absorption synthetic cases passed.")
