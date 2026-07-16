"""
Unit tests against synthetic profiles for the LSF/BIC broad-line tool
(CLAUDE.md build order A3). No pytest dependency — run directly:

    python lrd_adapt/tools/test_broadline_lsf_bic.py

Two ground-truth cases:
  A. Narrow-only line, spatially extended source, no real broadening.
     Expect: best_model == narrow_extended_lsf, broad_line_real == False.
  B. Real narrow+broad structure (broad FWHM in Kapoor+26's observed LRD
     range, 1400-2100 km/s), compact/point source.
     Expect: best_model in {narrow_broad_point_lsf, mixed},
     broad_line_real == True, recovered FWHM within ~30% of injected.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lrd_adapt.tools.broadline_lsf_bic import (  # noqa: E402
    fit_broadline_lsf_bic,
    lsf_sigma_ang,
    extended_lsf_sigma_ang,
    velocity_sigma_to_ang,
    _gaussian,
    R_POINT_SOURCE,
    R_EXTENDED_DEFAULT,
)

LINE_REST_ANG = 10833.0  # He I, per CLAUDE.md
Z_TRUE = 2.328


def _make_spectrum(center_obs, components, snr=25.0, seed=0):
    """components: list of (amp_frac, sigma_ang) Gaussians on a flat continuum."""
    rng = np.random.default_rng(seed)
    x = np.linspace(center_obs - 200, center_obs + 200, 400)
    continuum = 1.0
    y = np.full_like(x, continuum)
    peak_amp = 0.0
    for amp_frac, sigma_ang, comp_center in components:
        y = y + _gaussian(x, amp_frac, comp_center, sigma_ang)
        peak_amp = max(peak_amp, amp_frac)
    noise_sigma = peak_amp / snr
    y = y + rng.normal(0, noise_sigma, size=x.shape)
    return x, y


def test_case_a_no_real_broadening():
    center_obs = LINE_REST_ANG * (1 + Z_TRUE)
    sigma_ext = extended_lsf_sigma_ang(center_obs, R_extended=R_EXTENDED_DEFAULT)
    v_sigma_narrow_true = 100.0  # km/s, narrow NLR-like width
    sigma_total = float(np.hypot(velocity_sigma_to_ang(v_sigma_narrow_true, center_obs), sigma_ext))

    x, y = _make_spectrum(center_obs, [(1.0, sigma_total, center_obs)], snr=30.0, seed=1)

    result = fit_broadline_lsf_bic(x, y, LINE_REST_ANG, Z_TRUE)

    print("Case A (no real broadening):")
    print(f"  best_model={result['best_model']}  delta_bic_vs_null={result['delta_bic_vs_null']:.2f}"
          f"  broad_line_real={result['broad_line_real']}")

    assert result["best_model"] == "narrow_extended_lsf", (
        f"expected the null model to win, got {result['best_model']}"
    )
    assert result["broad_line_real"] is False


def test_case_b_real_broad_line():
    center_obs = LINE_REST_ANG * (1 + Z_TRUE)
    sigma_lsf_point = lsf_sigma_ang(center_obs, R_POINT_SOURCE)

    v_sigma_narrow_true = 100.0
    fwhm_broad_true_kms = 1800.0  # within Kapoor+26's 1400-2100 km/s LRD range
    v_sigma_broad_true = fwhm_broad_true_kms / 2.3548200450309493

    sigma_narrow = float(np.hypot(velocity_sigma_to_ang(v_sigma_narrow_true, center_obs), sigma_lsf_point))
    sigma_broad = float(np.hypot(velocity_sigma_to_ang(v_sigma_broad_true, center_obs), sigma_lsf_point))

    x, y = _make_spectrum(
        center_obs,
        [(0.7, sigma_narrow, center_obs), (0.4, sigma_broad, center_obs)],
        snr=30.0, seed=2,
    )

    result = fit_broadline_lsf_bic(x, y, LINE_REST_ANG, Z_TRUE)

    print("Case B (real broad + narrow):")
    print(f"  best_model={result['best_model']}  delta_bic_vs_null={result['delta_bic_vs_null']:.2f}"
          f"  broad_line_real={result['broad_line_real']}  broad_fwhm_kms={result['broad_fwhm_kms']:.1f}"
          f" (true={fwhm_broad_true_kms:.1f})")

    assert result["best_model"] in ("narrow_broad_point_lsf", "mixed"), (
        f"expected a real-broadening model to win, got {result['best_model']}"
    )
    assert result["broad_line_real"] is True
    assert result["delta_bic_vs_null"] > 10.0

    recovered = result["broad_fwhm_kms"]
    assert abs(recovered - fwhm_broad_true_kms) / fwhm_broad_true_kms < 0.3, (
        f"recovered FWHM {recovered:.1f} km/s too far from injected {fwhm_broad_true_kms:.1f} km/s"
    )


if __name__ == "__main__":
    test_case_a_no_real_broadening()
    test_case_b_real_broad_line()
    print("\nOK -- both synthetic-profile cases passed.")
