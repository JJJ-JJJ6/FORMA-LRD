"""
Scale-invariance regression tests for the whole fitting chain.

Every fitting test in this repo used to build spectra on ``continuum = 1.0``
(DESI-style, order-unity flux).  Real flux-calibrated grizli data is ~1e-19
erg/s/cm2/A, and scipy's ``curve_fit`` uses an ABSOLUTE finite-difference
step (~1.5e-8): at that scale the Jacobian columns for amplitude-like
parameters are pure numerical noise, the optimizer terminates on iteration 0,
and the initial guess comes back as though it were a converged fit.  It does
not raise -- it returns a confident wrong number.

Measured before the fix (2026-09-13):
  * ``_do_fit_peak`` -- amplitude pinned at exactly 1e-10 and local_snr
    identical at 426.16 for true amplitudes of 8e-18, 8e-20 and 8e-22, with
    center and sigma frozen at their initial guesses.
  * ``_do_fit_doublet`` -- BOTH components returned exactly 1e-10, so the
    He I/Pagamma amplitude ratio became a constant 1.000 regardless of the
    data.  kb/lrd_classification.md makes ``He I/Pagamma > 2.3`` the primary
    LRD-vs-ClassicalAGN discriminator, so every real source would have been
    classified LRD independently of its spectrum.
  * ``fit_broadline_lsf_bic`` -- recovered 2970 km/s for an injected
    1800 km/s broad line, outside Kapoor+26's 1400-2100 km/s window.
  * ``fit_blueshifted_absorption_bic`` -- verdict inverted to False on a
    genuine injected 600 km/s blueshifted absorption.

These tests assert that results are SCALE-INVARIANT: the same spectrum
multiplied by 1e-19 must produce the same physics.  Run directly:
    python lrd_adapt/tools/test_real_flux_scale.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from FORMA.agents.multi_agents.harness.tools import (  # noqa: E402
    _do_fit_peak,
    _do_fit_doublet,
    _try_fit_single,
)
from lrd_adapt.tools.broadline_lsf_bic import (  # noqa: E402
    fit_broadline_lsf_bic,
    lsf_sigma_ang,
    velocity_sigma_to_ang,
    _gaussian,
    R_POINT_SOURCE,
)
from lrd_adapt.tools.blueshifted_absorption_bic import (  # noqa: E402
    fit_blueshifted_absorption_bic,
)

# Real flux-calibrated grizli scale, plus two decades either side.
SCALES = (1.0, 1e-17, 1e-19, 1e-21)
HE_I, PA_GAMMA, Z = 10833.0, 10941.0, 2.328
C_KMS = 2.99792458e5


def _gauss(x, amp, mu, sigma):
    return amp * np.exp(-0.5 * ((x - mu) / sigma) ** 2)


def test_fit_peak_amplitude_tracks_flux_scale():
    """Recovered amplitude must scale with the data, and S/N must not."""
    center = HE_I * (1 + Z)
    wl = np.linspace(center - 400, center + 400, 800)
    base = 1.0 + _gauss(wl, 0.8, center, 25.0)
    rng = np.random.default_rng(0)

    snrs = []
    for scale in SCALES:
        flux = base * scale + rng.normal(0, 0.03 * scale, wl.size)
        r = _do_fit_peak(wl, flux, center, 75.0)
        amp = r["amplitude"]
        assert amp is not None, "fit failed outright at scale %g" % scale
        rel = abs(amp - 0.8 * scale) / (0.8 * scale)
        assert rel < 0.05, (
            "scale %g: amplitude %.4e is %.1f%% off the true %.4e -- "
            "curve_fit is not converging at this scale"
            % (scale, amp, rel * 100, 0.8 * scale)
        )
        assert abs(r["center"] - center) < 5.0, (
            "scale %g: center %s frozen/adrift (true %.1f)"
            % (scale, r["center"], center)
        )
        assert abs(r["sigma"] - 25.0) < 3.0, (
            "scale %g: sigma %s frozen at its initial guess" % (scale, r["sigma"])
        )
        snrs.append(r["local_snr"])

    assert max(snrs) - min(snrs) < 0.25 * float(np.mean(snrs)), (
        "local_snr should be scale-invariant, got %s" % (snrs,)
    )
    # The pre-fix failure signature was a constant, fabricated S/N.
    assert len(set(snrs)) > 1, "identical fabricated S/N returned at every scale"


def test_fit_doublet_preserves_he_i_pagamma_ratio():
    """The ratio driving the LRD-vs-AGN call must survive real flux scale."""
    c1, c2 = HE_I * (1 + Z), PA_GAMMA * (1 + Z)
    wl = np.linspace(c1 - 400, c2 + 400, 1200)
    base = 1.0 + _gauss(wl, 1.0, c1, 30.0) + _gauss(wl, 0.4, c2, 30.0)
    true_ratio = 1.0 / 0.4  # He I / Pagamma = 2.5, above the 2.3 threshold
    rng = np.random.default_rng(1)

    for scale in SCALES:
        flux = base * scale + rng.normal(0, 0.03 * scale, wl.size)
        r = _do_fit_doublet(wl, flux, c1, c2, width_3sigma=90.0)
        a1 = (r["component_1"] or {}).get("amplitude")
        a2 = (r["component_2"] or {}).get("amplitude")
        assert a1 and a2, "scale %g: doublet fit returned no amplitudes" % scale
        ratio = abs(a1) / abs(a2)
        assert abs(ratio - true_ratio) / true_ratio < 0.10, (
            "scale %g: He I/Pagamma ratio %.3f vs true %.3f. A ratio pinned "
            "near 1.000 is the pre-fix signature and would classify every "
            "source as LRD." % (scale, ratio, true_ratio)
        )
        assert ratio > 2.3, (
            "scale %g: ratio %.3f fell below the 2.3 ClassicalAGN threshold "
            "on data built to sit above it" % (scale, ratio)
        )


def test_try_fit_single_fallback_survives_real_scale():
    """The doublet fallback fitter shares the same failure mode."""
    center = HE_I * (1 + Z)
    wl = np.linspace(center - 300, center + 300, 600)
    base = 1.0 + _gauss(wl, 0.6, center, 20.0)
    rng = np.random.default_rng(2)

    for scale in SCALES:
        flux = base * scale + rng.normal(0, 0.02 * scale, wl.size)
        r = _try_fit_single(wl, flux, center, 60.0, "emission", 200.0)
        assert r is not None, "scale %g: fallback fit returned None" % scale
        amp = r["amplitude"]
        assert amp != 0.0, (
            "scale %g: amplitude rounded to exactly 0.0 (round(x, 6) "
            "collapses real ~1e-19 flux)" % scale
        )
        rel = abs(amp - 0.6 * scale) / (0.6 * scale)
        assert rel < 0.10, (
            "scale %g: amplitude %.4e is %.1f%% off" % (scale, amp, rel * 100)
        )


def test_broadline_bic_verdict_is_scale_invariant():
    """An injected 1800 km/s broad line must read the same at every scale."""
    center = HE_I * (1 + Z)
    s_pt = lsf_sigma_ang(center, R_POINT_SOURCE)
    s_broad = float(np.hypot(velocity_sigma_to_ang(1800 / 2.35482, center), s_pt))
    s_narrow = float(np.hypot(velocity_sigma_to_ang(90.0, center), s_pt))
    x = np.linspace(center - 200, center + 200, 400)
    rng = np.random.default_rng(3)
    base = (1.0 + _gaussian(x, 0.4, center, s_narrow)
            + _gaussian(x, 0.8, center, s_broad)
            + rng.normal(0, 0.032, x.size))

    fwhms, verdicts = [], []
    for scale in SCALES:
        r = fit_broadline_lsf_bic(x, base * scale, HE_I, Z)
        verdicts.append(r["broad_line_real"])
        assert r["broad_line_real"], (
            "scale %g: a genuine 1800 km/s broad line was not detected" % scale
        )
        fwhm = r["broad_fwhm_kms"]
        fwhms.append(fwhm)
        assert 1400.0 <= fwhm <= 2100.0, (
            "scale %g: recovered FWHM %.0f km/s falls outside Kapoor+26's "
            "1400-2100 km/s LRD range (injected 1800)" % (scale, fwhm)
        )
    assert len(set(verdicts)) == 1, "verdict flipped across scales: %s" % (verdicts,)
    assert max(fwhms) - min(fwhms) < 50.0, "FWHM drifted across scales: %s" % (fwhms,)


def test_blueshifted_absorption_verdict_is_scale_invariant():
    """CLAUDE.md section 4.2's rare blueshifted He I absorption, at real scale."""
    center = HE_I * (1 + Z)
    s_pt = lsf_sigma_ang(center, R_POINT_SOURCE)
    s_n = float(np.hypot(velocity_sigma_to_ang(100.0, center), s_pt))
    s_b = float(np.hypot(velocity_sigma_to_ang(1800 / 2.35482, center), s_pt))
    s_a = float(np.hypot(velocity_sigma_to_ang(200.0, center), s_pt))
    c_abs = center * (1 - 600.0 / C_KMS)
    x = np.linspace(center - 200, center + 200, 500)
    rng = np.random.default_rng(7)
    base = (1.0 + _gaussian(x, 0.7, center, s_n)
            + _gaussian(x, 0.4, center, s_b)
            - _gaussian(x, 0.30, c_abs, s_a)
            + rng.normal(0, 0.7 / 40.0, x.size))

    verdicts = []
    for scale in SCALES:
        r = fit_blueshifted_absorption_bic(x, base * scale, HE_I, Z)
        verdicts.append(r["absorption_real"])
        assert r["absorption_real"], (
            "scale %g: a genuine 600 km/s blueshifted absorption was missed "
            "-- the verdict inverted at real flux scale before the fix" % scale
        )
    assert len(set(verdicts)) == 1, "verdict flipped across scales: %s" % (verdicts,)


def test_local_rms_is_reported_in_physical_units():
    """local_rms must come back in the data's own units, not normalized ones."""
    center = HE_I * (1 + Z)
    wl = np.linspace(center - 400, center + 400, 800)
    base = 1.0 + _gauss(wl, 0.8, center, 25.0)
    rng = np.random.default_rng(11)
    for scale in SCALES:
        flux = base * scale + rng.normal(0, 0.03 * scale, wl.size)
        rms = _do_fit_peak(wl, flux, center, 75.0)["local_rms"]
        assert 0.3 * 0.03 * scale < rms < 3.0 * 0.03 * scale, (
            "scale %g: local_rms %.3e is not in physical units (expected ~%.3e)"
            % (scale, rms, 0.03 * scale)
        )


if __name__ == "__main__":
    failures = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print("PASS  %s" % _name)
            except AssertionError as exc:
                failures += 1
                print("FAIL  %s\n      %s" % (_name, exc))
    print("\n%d failure(s)" % failures)
    sys.exit(1 if failures else 0)
