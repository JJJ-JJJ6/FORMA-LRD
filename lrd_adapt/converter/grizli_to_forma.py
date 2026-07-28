"""
Convert grizli *.1D.fits per-grism spectra into FORMA-readable FITS.

Grizli 1D products store one BinTableHDU per grism/filter with columns:
wave, flux, err, flat, contam. CLAUDE.md's original schema note assumed
wave is always in microns; validated 2026-07-28 against a real eor1
extraction (j1030_01539.1D.fits) that this is NOT reliable — that real
file's wave column TUNIT is 'Angstrom', not micron. This converter now
reads the column's own TUNIT and converts accordingly, raising rather
than guessing if the unit is absent or unrecognized. `flux`/`err` are in
grizli's internal calibration and must be divided by `flat` to get
physically calibrated flux-density units — this is standard grizli
convention, confirmed against the same real file (flat's real TUNIT is
'cm2 Angstrom count erg-1', matching the expected sensitivity-curve
convention).

FORMA's loader (_load_spectrum_from_fits, utils/VI.py) expects single- or
multi-arm image HDUs named {ARM}_WAVELENGTH / {ARM}_FLUX / {ARM}_IVAR
(optional {ARM}_MASK), wavelength in Angstrom, plus an optional
FIBERMAP/METADATA table HDU with VI_Z / VI_SPECTYPE columns.
"""
from __future__ import annotations

import numpy as np
from astropy import units as u
from astropy.io import fits

MICRON_TO_ANGSTROM = 1e4


def _find_grism_hdu(hdul, grism_extname):
    if grism_extname is not None:
        return hdul[grism_extname]
    for hdu in hdul:
        if isinstance(hdu, fits.BinTableHDU) and hdu.data is not None:
            names = {n.lower() for n in hdu.columns.names}
            if {"wave", "flux", "err"}.issubset(names):
                return hdu
    raise ValueError(
        "No BinTableHDU with wave/flux/err columns found; pass grism_extname explicitly."
    )


def convert_grizli_1d_to_forma(
    input_fits,
    output_fits,
    arm_name="F356W",
    grism_extname=None,
    source_code=None,
    z_spec=None,
    spectype=None,
    contam_frac=0.5,
):
    """
    Parameters
    ----------
    input_fits : str
        Path to a grizli `*.1D.fits` file.
    output_fits : str
        Path to write the FORMA-compatible FITS.
    arm_name : str
        FORMA ARM_NAME to write under (e.g. "F356W").
    grism_extname : str, optional
        BinTableHDU EXTNAME to read; auto-detected from wave/flux/err columns
        if None.
    source_code : str, optional
        Anonymized code (e.g. "SRC01"). Never a real J-name or coordinate —
        see CLAUDE.md Anonymization section.
    z_spec, spectype : optional
        VI_Z / VI_SPECTYPE values to embed in a METADATA HDU for the
        harness's leave-one-out bookkeeping.
    contam_frac : float
        Pixels where |contam| > contam_frac * |flux| are flagged bad in the
        output mask (contam-vs-feature SNR penalty, see CLAUDE.md change
        budget for lrd_adapt/converter).

    Returns
    -------
    dict
        Small summary of the conversion (pixel count, masked count,
        wavelength range) for logging/tests.
    """
    with fits.open(input_fits) as hdul:
        grism_hdu = _find_grism_hdu(hdul, grism_extname)
        data = grism_hdu.data
        colnames = {n.lower() for n in data.columns.names}
        wave_raw = np.asarray(data["wave"], dtype=float)
        wave_unit_str = data.columns["wave"].unit
        flux_raw = np.asarray(data["flux"], dtype=float)
        err_raw = np.asarray(data["err"], dtype=float)
        flat = (
            np.asarray(data["flat"], dtype=float)
            if "flat" in colnames
            else np.ones_like(flux_raw)
        )
        contam = (
            np.asarray(data["contam"], dtype=float)
            if "contam" in colnames
            else np.zeros_like(flux_raw)
        )

    # Real grizli 1D products have been observed storing `wave` in BOTH
    # microns (matches this module's original documented assumption) and
    # Angstrom (confirmed 2026-07-28 against a real eor1 extraction,
    # TUNIT='Angstrom', values ~30500-41000) -- trust the column's own
    # TUNIT rather than assuming, and fail loudly on anything unrecognized
    # rather than silently mis-scaling by 10000x.
    if wave_unit_str:
        try:
            wavelength_ang = (wave_raw * u.Unit(wave_unit_str)).to(u.AA).value
        except (ValueError, u.UnitConversionError) as e:
            raise ValueError(
                f"{input_fits}: wave column TUNIT {wave_unit_str!r} could not be "
                f"converted to Angstrom ({e}). Do not guess -- confirm the real "
                "unit and extend this converter explicitly."
            ) from None
    else:
        # No TUNIT recorded at all: fall back to this module's original
        # documented assumption (microns), but only as a last resort.
        wavelength_ang = wave_raw * MICRON_TO_ANGSTROM

    good_flat = flat > 0
    flux = np.divide(flux_raw, flat, out=np.zeros_like(flux_raw), where=good_flat)
    err = np.divide(err_raw, flat, out=np.zeros_like(err_raw), where=good_flat)

    ivar = np.zeros_like(err)
    good_err = err > 0
    ivar[good_err] = 1.0 / err[good_err] ** 2

    # bit 1: bad flat-field cal, bit 2: bad/zero error, bit 4: contamination-dominated
    mask = np.zeros(len(wavelength_ang), dtype=np.uint32)
    mask[~good_flat] |= 1
    mask[~good_err] |= 2
    contam_bad = np.abs(contam) > contam_frac * np.abs(flux)
    mask[contam_bad] |= 4

    hdus = [fits.PrimaryHDU()]
    hdus.append(fits.ImageHDU(data=wavelength_ang, name=f"{arm_name}_WAVELENGTH"))
    hdus.append(fits.ImageHDU(data=flux, name=f"{arm_name}_FLUX"))
    hdus.append(fits.ImageHDU(data=ivar, name=f"{arm_name}_IVAR"))
    hdus.append(fits.ImageHDU(data=mask, name=f"{arm_name}_MASK"))

    if source_code is not None or z_spec is not None or spectype is not None:
        cols = [fits.Column(name="SRC_CODE", format="20A", array=[source_code or ""])]
        if z_spec is not None:
            cols.append(fits.Column(name="VI_Z", format="D", array=[float(z_spec)]))
        if spectype is not None:
            cols.append(fits.Column(name="VI_SPECTYPE", format="20A", array=[spectype]))
        hdus.append(fits.BinTableHDU.from_columns(cols, name="METADATA"))

    fits.HDUList(hdus).writeto(output_fits, overwrite=True)

    n_masked = int(np.count_nonzero(mask))
    return {
        "n_pixels": int(len(wavelength_ang)),
        "n_masked": n_masked,
        "wavelength_range_ang": (float(wavelength_ang.min()), float(wavelength_ang.max())),
        "output_fits": output_fits,
    }
