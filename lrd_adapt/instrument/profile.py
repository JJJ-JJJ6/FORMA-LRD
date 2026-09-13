"""
Instrument profiles: everything about the pipeline that is a property of the
*instrument* rather than of the science.

Why this exists
---------------
Before this module, instrument facts were scattered as hardcoded constants and
hand-tuned config values, each with a comment explaining its derivation, and
nothing forced those derivations to be rechecked against real data:

  * ``lrd_hypothesis_provider.REDSHIFT_WINDOWS`` -- six (z_min, z_max) pairs
    written out by hand. They are in fact pure bandpass arithmetic
    (lambda_band / lambda_rest - 1) and are now derived, not asserted.
  * ``f356w.env``'s ``CWT_MAX_SCALE=14.0`` -- justified in a comment that
    reasons from "F356W's ~9.8 A/pixel". The real extracted grid is
    19.78 A/px (measured 2026-09-13 from j1030_01539.1D.fits), so that
    comment's velocity arithmetic is off by 2x. The config even said
    "Re-derive and document precisely once real J1030 extractions are in
    hand" -- they arrived, and nothing forced the recheck.
  * ``broadline_lsf_bic.R_POINT_SOURCE`` / ``R_EXTENDED_DEFAULT`` -- module
    constants, unreachable from config.

TWO DIFFERENT PIXEL SCALES -- do not conflate them
--------------------------------------------------
This is the trap that produced the CWT error above.

  ``detector_dispersion_ang_per_px`` (F356W: 9.8) is the dispersion on the
  DETECTOR. It pairs with ``spatial_pixel_arcsec`` (0.063") and is the right
  number for slitless-LSF physics, where a source's angular size on the
  detector maps into a wavelength smear.

  ``extracted_dispersion_ang_per_px`` (F356W: ~19.78) is the spacing of the
  EXTRACTED 1D wavelength grid -- grizli bins ~2x relative to the detector.
  It is the right number for anything counting pixels in the extracted
  spectrum, which is what CWT scales do.

Prefer ``measure_extracted_dispersion(wavelength)`` over the profile's stored
value whenever you have the actual spectrum: the file is the authority, the
profile is the fallback.

Adding an instrument
--------------------
Supply a profile. Note ``lsf_sigma_ang`` is a FUNCTION, not a number: in
slitless grism the spectral resolution depends on the source's spatial size
(which is the entire premise of the broad-line BIC test), whereas a slit or
microshutter instrument does not work that way. A wrong LSF model returns
confident nonsense rather than an error, so it needs someone who knows the
instrument's optics -- it is not a config value to guess at.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Sequence, Tuple

import numpy as np

C_KMS = 299792.458
SIGMA_TO_FWHM = 2.3548200450309493  # 2 * sqrt(2 * ln 2)


def lsf_sigma_from_R(wavelength_ang: float, R: float) -> float:
    """Gaussian LSF sigma (Angstrom) at resolving power R (FWHM = lambda/R)."""
    return wavelength_ang / (R * SIGMA_TO_FWHM)


@dataclass(frozen=True)
class InstrumentProfile:
    """Instrument-dependent facts the pipeline needs.

    Attributes
    ----------
    name, description
        Identification; ``name`` is the registry key and the ``ARM_NAME``.
    bandpass_ang
        (lambda_min, lambda_max) observed-frame coverage in Angstrom. Drives
        the redshift windows and the plausibility filter.
    detector_dispersion_ang_per_px, spatial_pixel_arcsec
        DETECTOR-frame scales, for LSF physics. See the module docstring.
    extracted_dispersion_ang_per_px
        Nominal spacing of the extracted 1D grid; a fallback for when the
        real spectrum is not in hand. Measure it instead when you can.
    R_point_source
        Resolving power for an unresolved source.
    lsf_sigma_ang
        ``f(wavelength_ang, r_circ_mas=None) -> sigma_ang``. The effective
        LSF sigma including any source-size dependence. MUST be supplied per
        instrument -- see the module docstring on why this is not a scalar.
    photometry_filters
        {filter_name: (lambda_min_ang, lambda_max_ang)} for evidence tools
        that need to know which bands bracket a rest-frame feature.
    data_quality_model
        Short tag naming this instrument's dominant artifact class (e.g.
        "wfss_contamination"). Used to gate domain-specific KB/prompt text.
    """

    name: str
    description: str
    bandpass_ang: Tuple[float, float]
    detector_dispersion_ang_per_px: float
    spatial_pixel_arcsec: float
    extracted_dispersion_ang_per_px: float
    R_point_source: float
    lsf_sigma_ang: Callable[..., float]
    photometry_filters: Mapping[str, Tuple[float, float]] = field(default_factory=dict)
    data_quality_model: str = "unknown"

    # ---------------------------------------------------------------- derived

    def redshift_windows(
        self, rest_wavelengths: Mapping[str, float]
    ) -> dict[str, Tuple[float, float]]:
        """Redshift range over which each rest wavelength falls in the band.

        Pure arithmetic: a line at ``lambda_rest`` is observable exactly when
        ``lambda_min <= lambda_rest * (1 + z) <= lambda_max``. Verified to
        reproduce the six hand-written F356W windows to rounding.
        """
        lo, hi = self.bandpass_ang
        return {
            name: (lo / rest - 1.0, hi / rest - 1.0)
            for name, rest in rest_wavelengths.items()
        }

    def observable_lines(
        self, rest_wavelengths: Mapping[str, float], z: float
    ) -> list[str]:
        """Which of these lines actually land in the band at redshift z.

        The reason a wide-coverage instrument breaks the single-line
        degeneracy that caps every narrow-band verdict at LineIDAmbiguous:
        more lines in band at once means more corroboration available.
        """
        lo, hi = self.bandpass_ang
        return [
            name
            for name, rest in rest_wavelengths.items()
            if lo <= rest * (1.0 + z) <= hi
        ]

    def cwt_max_scale(
        self,
        max_fwhm_kms: float,
        at_wavelength_ang: Optional[float] = None,
        extracted_dispersion_ang_per_px: Optional[float] = None,
    ) -> float:
        """CWT scale (in EXTRACTED-grid pixels) covering a velocity width.

        A CWT scale s corresponds to FWHM ~= s * 2.355 * dispersion, so
        ``s = FWHM_ang / (2.355 * dispersion)``. Pass the dispersion measured
        from the real spectrum when you have it.

        Getting the dispersion wrong here is exactly the 2x error described
        in the module docstring, so the caller is nudged to supply a measured
        value rather than silently inheriting the nominal one.
        """
        disp = (
            extracted_dispersion_ang_per_px
            if extracted_dispersion_ang_per_px is not None
            else self.extracted_dispersion_ang_per_px
        )
        lam = at_wavelength_ang if at_wavelength_ang is not None else self.band_center_ang
        fwhm_ang = (max_fwhm_kms / C_KMS) * lam
        return float(fwhm_ang / (SIGMA_TO_FWHM * disp))

    def velocity_fwhm_for_cwt_scale(
        self,
        scale_px: float,
        at_wavelength_ang: Optional[float] = None,
        extracted_dispersion_ang_per_px: Optional[float] = None,
    ) -> float:
        """Inverse of :meth:`cwt_max_scale` -- what velocity does a scale mean?

        Use this to audit an existing hand-tuned CWT_MAX_SCALE against the
        velocity range it is supposed to cover.
        """
        disp = (
            extracted_dispersion_ang_per_px
            if extracted_dispersion_ang_per_px is not None
            else self.extracted_dispersion_ang_per_px
        )
        lam = at_wavelength_ang if at_wavelength_ang is not None else self.band_center_ang
        fwhm_ang = scale_px * SIGMA_TO_FWHM * disp
        return float(fwhm_ang / lam * C_KMS)

    @property
    def band_center_ang(self) -> float:
        lo, hi = self.bandpass_ang
        return 0.5 * (lo + hi)

    def covers(self, wavelength_ang: float) -> bool:
        lo, hi = self.bandpass_ang
        return bool(lo <= wavelength_ang <= hi)

    def bracketing_filters(
        self, rest_ang: float, z: float
    ) -> Tuple[Optional[str], Optional[str]]:
        """Filters falling just blueward/redward of a rest feature at z.

        Generalizes ``phot_evidence``'s hardcoded F200W/F115W Balmer-break
        proxy: which bands bracket a break is a function of the filter set
        and the redshift, not a constant.
        """
        obs = rest_ang * (1.0 + z)
        blue, red = None, None
        blue_sep, red_sep = np.inf, np.inf
        for fname, (flo, fhi) in self.photometry_filters.items():
            fcen = 0.5 * (flo + fhi)
            if fcen < obs and (obs - fcen) < blue_sep:
                blue, blue_sep = fname, obs - fcen
            elif fcen >= obs and (fcen - obs) < red_sep:
                red, red_sep = fname, fcen - obs
        return blue, red


# --------------------------------------------------------------------- helpers


def measure_extracted_dispersion(wavelength_ang) -> float:
    """Median spacing (Angstrom/pixel) of a real extracted wavelength grid.

    The file is the authority. Use this in preference to a profile's nominal
    value whenever a spectrum is in hand -- the whole point of this module is
    that a stored constant can quietly drift from the data it describes.
    """
    wl = np.asarray(wavelength_ang, dtype=float)
    wl = wl[np.isfinite(wl)]
    if wl.size < 2:
        raise ValueError(
            f"Need at least 2 finite wavelength samples to measure a "
            f"dispersion, got {wl.size}."
        )
    return float(np.median(np.abs(np.diff(wl))))


def slitless_lsf_model(
    R_point: float,
    detector_dispersion_ang_per_px: float,
    spatial_pixel_arcsec: float,
    R_extended_fallback: float,
) -> Callable[..., float]:
    """LSF model for a SLITLESS dispersive element (WFSS grism).

    With no slit, a spatially extended source disperses into a spectrum
    smeared along the dispersion axis by its own angular size. That is why an
    apparently broad line may not be broad in velocity at all -- the premise
    of the three-model BIC comparison in ``broadline_lsf_bic``.

    Given a measured circularized half-light radius the smear is computed
    directly from the source size via the DETECTOR scales; otherwise it falls
    back to a fixed effective resolving power.

    This model is specific to slitless optics. A slit or microshutter
    instrument needs its own function -- reusing this one there would return
    confident, wrong widths rather than an error.
    """

    def _lsf(wavelength_ang: float, r_circ_mas: Optional[float] = None) -> float:
        point_sigma = lsf_sigma_from_R(wavelength_ang, R_point)
        if r_circ_mas is None:
            return lsf_sigma_from_R(wavelength_ang, R_extended_fallback)
        size_px = r_circ_mas / (spatial_pixel_arcsec * 1000.0)
        extra_sigma = (size_px * detector_dispersion_ang_per_px) / SIGMA_TO_FWHM
        return float(np.hypot(point_sigma, extra_sigma))

    return _lsf


def unsupported_lsf_model(instrument_name: str) -> Callable[..., float]:
    """Placeholder LSF that refuses rather than inventing a width.

    For a profile whose optics nobody has modelled yet. Failing loudly is the
    correct behaviour: a plausible-looking wrong LSF would silently corrupt
    every broad-line verdict, which is far worse than a stopped run.
    """

    def _lsf(wavelength_ang: float, r_circ_mas: Optional[float] = None) -> float:
        raise NotImplementedError(
            f"No LSF model has been written for '{instrument_name}'. The "
            f"broad-line BIC test needs an instrument-specific "
            f"sigma_lsf(lambda, source_size); see slitless_lsf_model for the "
            f"WFSS case. Do not substitute the slitless model for a slit or "
            f"microshutter instrument -- the physics differs and the result "
            f"would be confidently wrong."
        )

    return _lsf


# ------------------------------------------------- per-source real coverage

# Mask bits written by lrd_adapt/converter/grizli_to_forma.py.
MASK_BAD_FLAT = 1
MASK_BAD_ERR = 2
MASK_CONTAMINATED = 4


def measure_valid_coverage(wavelength_ang, mask=None) -> Tuple[float, float]:
    """Wavelength range where a given source actually has usable data.

    A profile's ``bandpass_ang`` is the instrument's NOMINAL coverage. What a
    particular extraction actually calibrates is narrower (or shifted), and
    assuming otherwise produces exactly the error found in SRC04's H1 report
    on 2026-09-13: it stated "No other lines are predicted in the F356W
    window at this redshift" when [S III] 9533 *was* predicted in the nominal
    band at 31706 A. The real j1030_01539 extraction has valid calibration
    only over 33721-40308 A -- below that, flat = 0 and err = 0, so the flux
    is identically zero and no masking change can recover it. The nominal
    band also understated the red end by ~800 A.

    So: read coverage from the file, like dispersion.
    """
    wl = np.asarray(wavelength_ang, dtype=float)
    good = np.isfinite(wl)
    if mask is not None:
        m = np.asarray(mask, dtype=int)
        # bad flat / bad error mean "no data here", as distinct from
        # contamination, which means "data present but untrustworthy".
        good &= (m & (MASK_BAD_FLAT | MASK_BAD_ERR)) == 0
    if not good.any():
        raise ValueError("No pixels with valid calibration in this spectrum.")
    return float(wl[good].min()), float(wl[good].max())


def classify_line_availability(
    obs_ang: float,
    wavelength_ang,
    mask=None,
    profile: Optional[InstrumentProfile] = None,
) -> str:
    """Why a predicted line can or cannot be evaluated.

    Collapsing these into "no other lines are predicted" is what turned a
    data-coverage limit into a claim of irreducible physical degeneracy in
    SRC04's report. They are scientifically different conclusions:
    NO_COVERAGE is a property of this extraction, CONTAMINATED may be
    revisitable, OUT_OF_BAND is a property of the instrument.

    Returns one of: OUT_OF_BAND, NO_COVERAGE, CONTAMINATED, AVAILABLE.
    """
    if profile is not None and not profile.covers(obs_ang):
        return "OUT_OF_BAND"

    wl = np.asarray(wavelength_ang, dtype=float)
    lo, hi = measure_valid_coverage(wl, mask)
    if not (lo <= obs_ang <= hi):
        return "NO_COVERAGE"

    if mask is not None:
        m = np.asarray(mask, dtype=int)
        i = int(np.argmin(np.abs(wl - obs_ang)))
        if m[i] & (MASK_BAD_FLAT | MASK_BAD_ERR):
            return "NO_COVERAGE"
        if m[i] & MASK_CONTAMINATED:
            return "CONTAMINATED"
    return "AVAILABLE"
