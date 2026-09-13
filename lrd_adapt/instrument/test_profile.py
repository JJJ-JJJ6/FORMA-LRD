"""
Instrument-profile tests.

The load-bearing ones are the EQUIVALENCE tests: the F356W profile must
reproduce the pipeline's previously-hardcoded behaviour exactly, so that
introducing the abstraction cannot quietly change any EIGER result.

The rest guard the specific failure this module exists to prevent -- an
instrument constant drifting away from the data it claims to describe, which
is how f356w.env's CWT_MAX_SCALE comment ended up wrong by a factor of 2.

Run directly:
    python lrd_adapt/instrument/test_profile.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lrd_adapt.instrument.profile import (  # noqa: E402
    measure_extracted_dispersion,
    measure_valid_coverage,
    classify_line_availability,
    slitless_lsf_model,
    unsupported_lsf_model,
    InstrumentProfile,
    MASK_BAD_FLAT,
    MASK_BAD_ERR,
    MASK_CONTAMINATED,
)
from lrd_adapt.instrument.profiles import (  # noqa: E402
    F356W_EIGER,
    get_profile,
    register,
    available_profiles,
)
from lrd_adapt.hypothesis.lrd_hypothesis_provider import (  # noqa: E402
    REST_WAVELENGTHS_ANG,
)
from lrd_adapt.tools.broadline_lsf_bic import (  # noqa: E402
    extended_lsf_sigma_ang,
)

# The six windows as they were hardcoded before this module existed, copied
# verbatim from Kapoor+26 Section 3.2 via the old REDSHIFT_WINDOWS literal.
PUBLISHED_F356W_WINDOWS = {
    "Paalpha": (0.68, 1.10),
    "Pabeta": (1.45, 2.08),
    "HeI_Pagamma": (1.91, 2.64),
    "SIII": (2.30, 3.14),
    "OI_8446": (2.73, 3.68),
    "Halpha": (3.80, 5.02),
}


# ------------------------------------------------------- equivalence to legacy


def test_derived_windows_reproduce_the_published_f356w_values():
    """Deriving must not move any window more than published rounding."""
    derived = F356W_EIGER.redshift_windows(REST_WAVELENGTHS_ANG)
    assert set(derived) == set(PUBLISHED_F356W_WINDOWS)
    for line, (lo, hi) in derived.items():
        plo, phi = PUBLISHED_F356W_WINDOWS[line]
        assert abs(lo - plo) < 0.01, (
            "%s: derived z_min %.4f vs published %.2f" % (line, lo, plo)
        )
        assert abs(hi - phi) < 0.01, (
            "%s: derived z_max %.4f vs published %.2f" % (line, hi, phi)
        )


def test_profile_lsf_matches_the_legacy_hardcoded_model():
    """The F356W profile's LSF must equal the pre-refactor function exactly."""
    for lam in (31500.0, 36030.0, 39500.0):
        for r_circ in (None, 50.0, 100.0, 200.0, 400.0):
            legacy = extended_lsf_sigma_ang(lam, r_circ)
            via_profile = extended_lsf_sigma_ang(lam, r_circ, profile=F356W_EIGER)
            assert np.isclose(legacy, via_profile, rtol=1e-12), (
                "lambda=%.0f r_circ=%s: legacy %.9f vs profile %.9f"
                % (lam, r_circ, legacy, via_profile)
            )


def test_provider_still_uses_f356w_windows_by_default():
    """Existing single-arm setups must be unaffected by the abstraction."""
    from lrd_adapt.hypothesis.lrd_hypothesis_provider import REDSHIFT_WINDOWS

    derived = F356W_EIGER.redshift_windows(REST_WAVELENGTHS_ANG)
    assert REDSHIFT_WINDOWS == derived, (
        "module-level REDSHIFT_WINDOWS drifted from the F356W profile"
    )


# ------------------------------------------------- the drift this module stops


def test_measured_dispersion_beats_the_nominal_value():
    """A real grid's spacing is measurable; profiles must not override it."""
    wl = np.arange(31500.0, 39500.0, 19.778)
    measured = measure_extracted_dispersion(wl)
    assert abs(measured - 19.778) < 0.01, measured

    # The profile's stored value is only a fallback and should agree.
    assert abs(F356W_EIGER.extracted_dispersion_ang_per_px - measured) < 0.1


def test_cwt_scale_round_trips_through_velocity():
    """cwt_max_scale and velocity_fwhm_for_cwt_scale must be inverses."""
    for v in (1400.0, 1800.0, 2100.0, 5000.0):
        s = F356W_EIGER.cwt_max_scale(v, at_wavelength_ang=36030.0)
        back = F356W_EIGER.velocity_fwhm_for_cwt_scale(s, at_wavelength_ang=36030.0)
        assert np.isclose(v, back, rtol=1e-9), "%.1f -> %.4f -> %.1f" % (v, s, back)


def test_the_two_pixel_scales_are_not_interchangeable():
    """Guard the exact conflation that made CWT_MAX_SCALE wrong by 2x.

    Using the DETECTOR dispersion (9.8) where the EXTRACTED grid spacing
    (~19.78) belongs understates the velocity width of a CWT scale by ~2x.
    """
    lam = 36030.0
    correct = F356W_EIGER.velocity_fwhm_for_cwt_scale(14.0, at_wavelength_ang=lam)
    wrong = F356W_EIGER.velocity_fwhm_for_cwt_scale(
        14.0,
        at_wavelength_ang=lam,
        extracted_dispersion_ang_per_px=F356W_EIGER.detector_dispersion_ang_per_px,
    )
    ratio = correct / wrong
    assert 1.9 < ratio < 2.1, (
        "expected the detector/extracted mix-up to be a ~2x error, got %.2fx "
        "(correct %.0f km/s vs mistaken %.0f km/s)" % (ratio, correct, wrong)
    )


def test_documented_cwt_max_scale_is_wider_than_its_stated_target():
    """f356w.env claims CWT_MAX_SCALE=14.0 targets 1400-2100 km/s; it doesn't.

    Recorded as a fact, not a failure: 14.0 is an upper bound, so being
    generous is defensible. What is NOT defensible is the stale comment, and
    this test pins the real number so the next person sees it.
    """
    v = F356W_EIGER.velocity_fwhm_for_cwt_scale(14.0, at_wavelength_ang=36030.0)
    assert v > 2100.0, (
        "CWT_MAX_SCALE=14.0 should correspond to a width well above the "
        "1400-2100 km/s LRD range; got %.0f km/s" % v
    )
    assert 5000.0 < v < 6000.0, "expected ~5400 km/s, got %.0f" % v


# --------------------------------------------------------- extension surface


def test_observable_lines_grows_with_coverage():
    """Wider coverage puts more lines in band -- the degeneracy escape hatch."""
    narrow = F356W_EIGER.observable_lines(REST_WAVELENGTHS_ANG, z=2.328)
    wide_profile = InstrumentProfile(
        name="WIDE_TEST",
        description="synthetic wide-coverage instrument",
        bandpass_ang=(6000.0, 53000.0),
        detector_dispersion_ang_per_px=10.0,
        spatial_pixel_arcsec=0.1,
        extracted_dispersion_ang_per_px=20.0,
        R_point_source=1000.0,
        lsf_sigma_ang=unsupported_lsf_model("WIDE_TEST"),
    )
    wide = wide_profile.observable_lines(REST_WAVELENGTHS_ANG, z=2.328)
    # At SRC04's claimed z=2.328 the NOMINAL F356W band covers two of our
    # lines: He I 10833 -> 36052 A (the validated detection at 36030) and
    # [S III] 9533 -> 31724 A near the blue edge.
    #
    # IMPORTANT: nominal band coverage is NOT observability. Checked against
    # the real j1030_01539.1D.fits on 2026-09-13: that source's valid
    # calibration (flat > 0) spans 33721-40308 A, so [S III] at 31724 A has
    # no data at all -- flat=0, err=0, flux identically 0. It is not
    # contamination-masked and cannot be recovered by relaxing contam_frac.
    # Per-source coverage must be read from the file; see
    # measure_valid_coverage().
    assert set(narrow) == {"HeI_Pagamma", "SIII"}, narrow
    assert len(wide) > len(narrow), (
        "wider coverage must expose more corroborating lines: %s vs %s" % (wide, narrow)
    )


def test_unsupported_lsf_refuses_instead_of_inventing_a_width():
    """A missing LSF model must fail loudly, never fall back to slitless."""
    lsf = unsupported_lsf_model("NIRSPEC_PRISM")
    try:
        lsf(30000.0, 100.0)
    except NotImplementedError as exc:
        assert "NIRSPEC_PRISM" in str(exc)
    else:
        raise AssertionError("unsupported LSF silently returned a width")


def test_bracketing_filters_generalize_the_balmer_break_proxy():
    """phot_evidence's F200W/F115W pair should fall out of the filter set."""
    # Rest-frame 3645 A Balmer break at z=3.0 lands at 14580 A.
    blue, red = F356W_EIGER.bracketing_filters(3645.0, z=3.0)
    assert blue is not None and red is not None, (blue, red)
    lo_b, hi_b = F356W_EIGER.photometry_filters[blue]
    lo_r, hi_r = F356W_EIGER.photometry_filters[red]
    assert 0.5 * (lo_b + hi_b) < 14580.0 <= 0.5 * (lo_r + hi_r) + 1e-6


def test_registry_lookup_and_duplicate_guard():
    assert "F356W" in available_profiles()
    assert get_profile("f356w") is F356W_EIGER
    try:
        get_profile("NOPE")
    except KeyError as exc:
        assert "NOPE" in str(exc) and "F356W" in str(exc)
    else:
        raise AssertionError("unknown profile did not raise")

    try:
        register(F356W_EIGER)
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate registration was allowed")


def test_measure_dispersion_rejects_degenerate_input():
    for bad in ([], [31500.0]):
        try:
            measure_extracted_dispersion(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("accepted a grid too short to measure: %r" % (bad,))


# ------------------------------------------- per-source coverage vs nominal


def _fake_spectrum():
    """A grid shaped like the real j1030_01539: no calibration blueward.

    Real numbers, measured 2026-09-13: valid calibration (flat > 0) spans
    33721-40308 A, while the nominal F356W band is 31500-39500 A.
    """
    wl = np.arange(30510.0, 40990.0, 19.778)
    mask = np.zeros(wl.size, dtype=int)
    mask[wl < 33721.0] |= MASK_BAD_FLAT | MASK_BAD_ERR
    mask[wl > 40308.0] |= MASK_BAD_FLAT | MASK_BAD_ERR
    # He I region is contaminated but calibrated (contam/flux = 0.66 > 0.5)
    mask[(wl > 35970.0) & (wl < 36340.0)] |= MASK_CONTAMINATED
    return wl, mask


def test_valid_coverage_is_narrower_than_the_nominal_band():
    wl, mask = _fake_spectrum()
    lo, hi = measure_valid_coverage(wl, mask)
    nlo, nhi = F356W_EIGER.bandpass_ang
    assert lo > nlo, "valid coverage should start redward of the nominal band"
    assert abs(lo - 33721.0) < 25.0, lo
    assert hi > nhi, "real data extends past the nominal red edge"


def test_availability_distinguishes_no_coverage_from_contamination():
    """The distinction SRC04's report collapsed into 'not predicted'."""
    wl, mask = _fake_spectrum()

    # [S III]b at H1's redshift: inside the nominal band, but no calibration.
    assert classify_line_availability(31705.8, wl, mask, F356W_EIGER) == "NO_COVERAGE"
    # Pa-delta: same.
    assert classify_line_availability(33431.9, wl, mask, F356W_EIGER) == "NO_COVERAGE"
    # He I: calibrated, but contamination-dominated -- a different conclusion.
    assert classify_line_availability(36029.5, wl, mask, F356W_EIGER) == "CONTAMINATED"
    # Something outside the instrument entirely.
    assert classify_line_availability(12000.0, wl, mask, F356W_EIGER) == "OUT_OF_BAND"
    # A clean, calibrated position.
    assert classify_line_availability(34500.0, wl, mask, F356W_EIGER) == "AVAILABLE"


def test_no_coverage_is_not_recoverable_by_relaxing_contamination():
    """Guard the wrong inference: NO_COVERAGE has no contamination bit set."""
    wl, mask = _fake_spectrum()
    i = int(np.argmin(np.abs(wl - 31705.8)))
    assert mask[i] & (MASK_BAD_FLAT | MASK_BAD_ERR)
    assert not (mask[i] & MASK_CONTAMINATED), (
        "if this were contamination it could be revisited by changing "
        "contam_frac; it is not -- there is simply no data there"
    )


def test_measure_valid_coverage_rejects_a_fully_masked_spectrum():
    wl = np.arange(31500.0, 39500.0, 20.0)
    mask = np.full(wl.size, MASK_BAD_FLAT, dtype=int)
    try:
        measure_valid_coverage(wl, mask)
    except ValueError:
        pass
    else:
        raise AssertionError("accepted a spectrum with no valid pixels")


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
