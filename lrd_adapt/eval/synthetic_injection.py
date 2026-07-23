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


def make_pure_noise_case(
    seed=0,
    noise_level=0.05,
    wave_min_um=3.15,
    wave_max_um=3.95,
    n_pixels=800,
):
    """
    A negative-control-adjacent case (CLAUDE.md build order C): continuum +
    noise, no injected line at all. Ground truth: nothing should be
    confirmed as a real broad line here, at any tested position -- used to
    measure the false-positive rate of `_fit_broadline_lsf_bic` under pure
    noise (see lrd_adapt/eval/test_negative_controls.py).

    This is NOT a substitute for the real negative controls CLAUDE.md asks
    for (~100 actual non-LRD catalog sources) -- see
    lrd_adapt/eval/negative_controls.py's docstring for why that needs real
    catalog access this doesn't replace. It only tests one specific failure
    mode: does the deterministic BIC tool itself hallucinate significant
    broadening on pure noise, independent of any LLM/CWT behavior.
    """
    rng = np.random.default_rng(seed)
    wave_um = np.linspace(wave_min_um, wave_max_um, n_pixels)
    continuum = 0.5 + 0.02 * (wave_um - wave_um.mean())
    flux = continuum + rng.normal(0, noise_level, size=wave_um.shape)

    err = np.full_like(flux, noise_level)
    flat = np.ones_like(flux)
    contam = np.zeros_like(flux)

    return {
        "wave_um": wave_um, "flux": flux, "err": err, "flat": flat, "contam": contam,
        "ground_truth": {"line": None, "z": None, "broad_line_real": False},
    }


def write_synthetic_1d_fits(case, path, arm_extname="F356W"):
    """Write a synthetic case (from make_synthetic_case) as a grizli-shaped
    *.1D.fits file, ready for lrd_adapt.converter.grizli_to_forma."""
    col = fits.ColDefs([
        fits.Column(name="wave", format="D", array=case["wave_um"], unit="um"),
        fits.Column(name="flux", format="D", array=case["flux"], unit="count/s"),
        fits.Column(name="err", format="D", array=case["err"], unit="count/s"),
        # grizli's flat-field sensitivity unit -- specvizitor's grizli
        # plugin requires flat to be convertible to '1e19 AA cm2 ct / erg'
        # for its ct/s -> physical flux conversion (verified against
        # sviz-grizli.py; a unitless flat crashes its finalize path).
        # TUNIT must use FITS '10**19' notation: astropy's FITS reader
        # returns UnrecognizedUnit for the '1e19 ...' spelling.
        fits.Column(name="flat", format="D", array=case["flat"],
                    unit="10**19 Angstrom cm2 count / erg"),
        fits.Column(name="contam", format="D", array=case["contam"], unit="count/s"),
    ])
    hdu = fits.BinTableHDU.from_columns(col, name=arm_extname)
    fits.HDUList([fits.PrimaryHDU(), hdu]).writeto(path, overwrite=True)


def write_synthetic_full_fits(z, path, z_sigma=0.003, source_id=0,
                              z_grid_halfwidth=0.5, n_grid=2001,
                              dsci_r_circ_mas=None,
                              dsci_pixel_scale_mas=63.0, dsci_size=100,
                              dsci_seed=0):
    """
    Write a synthetic grizli-*.full.fits-shaped redshift-fit product: a
    primary HDU with a REDSHIFT keyword and a ZFIT_STACK BinTable with
    zgrid / pdf / chi2 columns (Gaussian pdf centered on ``z``).

    When ``dsci_r_circ_mas`` is given, also writes a DSCI ImageHDU: a
    circular Gaussian source whose half-light radius equals that value,
    on a WCS with ``dsci_pixel_scale_mas`` per pixel (default 63 mas =
    the NIRCam LW pixel scale from CLAUDE.md). This is the ground-truth
    target for lrd_adapt.evidence.compactness.

    Exists so lrd_adapt.converter.zfit_reader has a round-trip test target
    before any real .full.fits is available. The layout follows grizli's
    documented convention but has NOT been checked against a real file --
    the reader's schema caveat applies equally here. Never place these
    where they could be mistaken for real fit products.
    """
    z_lo = max(0.01, z - z_grid_halfwidth)
    zgrid = np.linspace(z_lo, z + z_grid_halfwidth, n_grid)
    pdf = np.exp(-0.5 * ((zgrid - z) / z_sigma) ** 2)
    chi2 = ((zgrid - z) / z_sigma) ** 2  # parabolic minimum at z, floor 0

    primary = fits.PrimaryHDU()
    primary.header["ID"] = source_id
    primary.header["REDSHIFT"] = float(z)
    primary.header["COMMENT"] = (
        "SYNTHETIC redshift-fit product "
        "(lrd_adapt/eval/synthetic_injection.write_synthetic_full_fits) "
        "-- not a real grizli fit."
    )

    zfit = fits.BinTableHDU.from_columns(
        fits.ColDefs([
            fits.Column(name="zgrid", format="D", array=zgrid),
            fits.Column(name="pdf", format="D", array=pdf),
            fits.Column(name="chi2", format="D", array=chi2),
        ]),
        name="ZFIT_STACK",
    )
    hdus = [primary, zfit]

    if dsci_r_circ_mas is not None:
        # Gaussian half-light radius = 1.17741 sigma
        sigma_pix = (dsci_r_circ_mas / dsci_pixel_scale_mas) / 1.17741
        rng = np.random.default_rng(dsci_seed)
        c = (dsci_size - 1) / 2.0
        yy, xx = np.mgrid[0:dsci_size, 0:dsci_size]
        img = np.exp(-0.5 * ((xx - c) ** 2 + (yy - c) ** 2) / sigma_pix**2)
        img += rng.normal(0.0, 0.01, size=img.shape)  # faint sky noise

        hdr = fits.Header()
        hdr["CD1_1"] = -dsci_pixel_scale_mas / 3.6e6  # deg/px, RA flips sign
        hdr["CD1_2"] = 0.0
        hdr["CD2_1"] = 0.0
        hdr["CD2_2"] = dsci_pixel_scale_mas / 3.6e6
        hdus.append(fits.ImageHDU(data=img, header=hdr, name="DSCI"))

    fits.HDUList(hdus).writeto(path, overwrite=True)


# grizli-like photometric catalog columns per band; only ratios are
# consumed downstream, so the absolute scale is arbitrary "uJy-like".
_PHOT_BANDS = ("f115w", "f200w", "f356w")
_PHOT_PIVOT_ANG = {"f115w": 11540.0, "f200w": 19890.0, "f356w": 35680.0}
_BALMER_BREAK_REST_ANG = 3645.0


def write_synthetic_phot_catalog(rows, path):
    """
    Write a synthetic {root}_phot.fits-style field catalog.

    rows : list of dicts with keys
        id            : int
        z             : float (used to place the break between bands)
        break_factor  : float >= 1 -- f_nu suppression blueward of the
                        rest-frame Balmer break (1 = no break;
                        Kapoor+26 LRD range ~1-4)
        red_slope     : optional f_nu power-law slope in lambda
                        (default 0.5, mildly red)

    Fluxes are a toy SED evaluated at each band's pivot wavelength:
    flat-ish f_nu with the blueward side divided by break_factor. The
    injected break_factor is the ground truth for
    lrd_adapt.evidence.phot_evidence's proxy recovery test. Column
    names follow the f{band}_flux_aper_1 pattern our reader tries
    first; the real eor1 catalog is the schema re-verification gate.
    """
    from astropy.table import Table

    ids, cols = [], {f"{b}_flux_aper_1": [] for b in _PHOT_BANDS}
    for row in rows:
        ids.append(int(row["id"]))
        z = float(row["z"])
        bf = float(row.get("break_factor", 1.0))
        slope = float(row.get("red_slope", 0.5))
        for b in _PHOT_BANDS:
            rest = _PHOT_PIVOT_ANG[b] / (1.0 + z)
            fnu = (rest / 5000.0) ** slope  # mild red continuum
            if rest < _BALMER_BREAK_REST_ANG:
                fnu /= bf
            cols[f"{b}_flux_aper_1"].append(fnu)

    table = Table({"id": ids, **cols})
    table.meta["COMMENT"] = (
        "SYNTHETIC photometric catalog "
        "(lrd_adapt/eval/synthetic_injection.write_synthetic_phot_catalog)"
        " -- not a real grizli catalog."
    )
    table.write(path, overwrite=True)
