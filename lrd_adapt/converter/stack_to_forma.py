"""
Convert grizli *.stack.fits (2D combined spectrograms — the 2D panel shown
in specvizitor) into FORMA-readable FITS, via a local boxcar extraction
chained into the existing 1D converter.

This promotes the previously-external stopgap boxcar extractor into a
supported input option, for cases where only the 2D stack is in hand.
When grizli's own *.1D.fits exists for a source, prefer that route
(grizli_to_forma.convert_grizli_1d_to_forma) — grizli's optimal extraction
is flux-calibrated and profile-weighted; this one is not.

METHOD (deliberately simple, matches the validated stopgap):
  - Wavelength axis from the summed SCI HDU's WCS (CRVAL1/CRPIX1/CD1_1,
    microns) — grizli's own wavelength solution, not derived here.
  - Cross-dispersion center found empirically as the row with the largest
    all-wavelength-summed signal (overridable via center_row).
  - Flux: straight boxcar SUM over a fixed-width spatial window per
    wavelength column — NOT profile-weighted (Horne) optimal extraction.
  - Error: sqrt(sum of 1/WHT) over the same window (WHT = inverse
    variance per pixel, grizli's convention, pixels independent).
  - Contam: same boxcar sum from the first CONTAM extension found (the
    summed HDU set has none; with a single visit the per-visit map IS the
    full contamination map).
  - flat: 1.0 uniformly. The flat-field sensitivity curve lives in
    grizli's beam machinery, not in *.stack.fits, so output flux stays in
    the 2D SCI array's own units (count/s), NOT erg/s/cm2/A. Line
    *detection* (CWT), profile-width fitting, and the BIC tools only need
    relative flux, so the pipeline runs fine — but absolute fluxes /
    equivalent widths from this route are not physically calibrated.

The extracted 1D is written as an intermediate table in grizli's own
wave/flux/err/flat/contam column convention (kept on disk, suffixed
`.1D_boxcar.fits`, so it can be inspected and is never confused with a
real grizli product), then run through convert_grizli_1d_to_forma so the
FORMA-side output is byte-identical in layout to the 1D route.
"""
from __future__ import annotations

import numpy as np
from astropy.io import fits

from .grizli_to_forma import convert_grizli_1d_to_forma

DEFAULT_HALF_WINDOW = 5  # cross-dispersion half-width, pixels either side of center


def _find_stack_hdus(hdul):
    """Locate the summed SCI/WHT (EXTVER without a visit ',' suffix) and the
    first per-visit CONTAM extension."""
    sci_hdu = wht_hdu = contam_hdu = None
    for h in hdul:
        if h.name == "SCI" and "," not in str(h.header.get("EXTVER", "")):
            sci_hdu = h
        if h.name == "WHT" and "," not in str(h.header.get("EXTVER", "")):
            wht_hdu = h
        if h.name == "CONTAM" and contam_hdu is None:
            contam_hdu = h
    if sci_hdu is None or wht_hdu is None:
        raise ValueError("Could not find summed SCI/WHT extensions in stack file")
    return sci_hdu, wht_hdu, contam_hdu


def extract_boxcar_1d(stack_fits, half_window=DEFAULT_HALF_WINDOW, center_row=None):
    """
    Boxcar-extract a 1D spectrum from a grizli *.stack.fits.

    Returns
    -------
    dict with arrays wave_um/flux/err/flat/contam plus extraction
    bookkeeping (center_row, rows_used, wcs_crpix2_row) for logging/tests.
    """
    with fits.open(stack_fits) as hdul:
        sci_hdu, wht_hdu, contam_hdu = _find_stack_hdus(hdul)

        sci = np.asarray(sci_hdu.data, dtype=np.float64)
        wht = np.asarray(wht_hdu.data, dtype=np.float64)
        contam = (
            np.asarray(contam_hdu.data, dtype=np.float64)
            if contam_hdu is not None
            else np.zeros_like(sci)
        )

        hdr = sci_hdu.header
        n_spatial, n_wave = sci.shape
        # FITS 1-indexed convention: numpy column j is FITS pixel j+1.
        wave_um = hdr["CRVAL1"] + (np.arange(n_wave) + 1 - hdr["CRPIX1"]) * hdr["CD1_1"]

        crpix2 = hdr.get("CRPIX2")
        if center_row is None:
            center_row = int(np.argmax(sci.sum(axis=1)))

        lo = max(0, center_row - half_window)
        hi = min(n_spatial, center_row + half_window + 1)

        flux = sci[lo:hi, :].sum(axis=0)
        contam_1d = contam[lo:hi, :].sum(axis=0)

        wht_window = wht[lo:hi, :]
        with np.errstate(divide="ignore"):
            var_per_pix = np.where(wht_window > 0, 1.0 / wht_window, 0.0)
        err = np.sqrt(var_per_pix.sum(axis=0))

    return {
        "wave_um": wave_um,
        "flux": flux,
        "err": err,
        "flat": np.ones_like(flux),
        "contam": contam_1d,
        "center_row": center_row,
        "rows_used": (lo, hi),
        "wcs_crpix2_row": (crpix2 - 1) if crpix2 is not None else None,
    }


def write_boxcar_1d_fits(result, out_path, arm_extname="F356W"):
    """Write the extraction as a grizli-convention 1D BinTable FITS."""
    col = fits.ColDefs(
        [
            fits.Column(name="wave", format="D", array=result["wave_um"], unit="um"),
            fits.Column(name="flux", format="D", array=result["flux"], unit="count/s"),
            fits.Column(name="err", format="D", array=result["err"], unit="count/s"),
            fits.Column(name="flat", format="D", array=result["flat"]),
            fits.Column(name="contam", format="D", array=result["contam"], unit="count/s"),
        ]
    )
    hdu = fits.BinTableHDU.from_columns(col, name=arm_extname)
    hdu.header["METHOD"] = "boxcar_stack"
    hdu.header["COMMENT"] = (
        "Boxcar-extracted from .stack.fits by lrd_adapt/converter/stack_to_forma.py "
        "-- NOT grizli's optimal extraction; flux in count/s, not calibrated."
    )
    fits.HDUList([fits.PrimaryHDU(), hdu]).writeto(out_path, overwrite=True)


def convert_stack_to_forma(
    input_stack,
    output_fits,
    arm_name="F356W",
    half_window=DEFAULT_HALF_WINDOW,
    center_row=None,
    intermediate_1d=None,
    source_code=None,
    z_spec=None,
    spectype=None,
    contam_frac=0.5,
):
    """
    2D route: grizli *.stack.fits → boxcar 1D → FORMA-readable FITS.

    Parameters mirror convert_grizli_1d_to_forma where shared; extras:

    half_window : int
        Cross-dispersion extraction half-width in pixels.
    center_row : int, optional
        0-indexed spatial row to center the window on; auto-detected from
        peak summed signal if None.
    intermediate_1d : str, optional
        Where to write the boxcar 1D table. Defaults to the input path
        with `.stack.fits` replaced by `.1D_boxcar.fits`. Kept on disk
        deliberately for inspection.

    Returns
    -------
    dict
        The 1D converter's summary plus extraction bookkeeping.
    """
    if intermediate_1d is None:
        if input_stack.endswith(".stack.fits"):
            intermediate_1d = input_stack[: -len(".stack.fits")] + ".1D_boxcar.fits"
        else:
            intermediate_1d = input_stack + ".1D_boxcar.fits"

    extraction = extract_boxcar_1d(
        input_stack, half_window=half_window, center_row=center_row
    )
    write_boxcar_1d_fits(extraction, intermediate_1d, arm_extname=arm_name)

    summary = convert_grizli_1d_to_forma(
        intermediate_1d,
        output_fits,
        arm_name=arm_name,
        grism_extname=arm_name,
        source_code=source_code,
        z_spec=z_spec,
        spectype=spectype,
        contam_frac=contam_frac,
    )
    summary.update(
        {
            "extraction_method": "boxcar_stack",
            "center_row": extraction["center_row"],
            "rows_used": extraction["rows_used"],
            "wcs_crpix2_row": extraction["wcs_crpix2_row"],
            "intermediate_1d": intermediate_1d,
        }
    )
    return summary


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Convert a grizli *.stack.fits (2D) into FORMA-readable FITS "
        "via boxcar extraction. Prefer the *.1D.fits route when available."
    )
    parser.add_argument("input_stack", help="grizli *.stack.fits path")
    parser.add_argument("output_fits", help="FORMA-readable FITS to write")
    parser.add_argument("--arm", default="F356W", dest="arm_name")
    parser.add_argument("--half-window", type=int, default=DEFAULT_HALF_WINDOW)
    parser.add_argument(
        "--center-row", type=int, default=None, help="0-indexed spatial row override"
    )
    parser.add_argument("--source-code", default=None, help='e.g. "SRC01" (never a real ID)')
    parser.add_argument("--z-spec", type=float, default=None)
    parser.add_argument("--spectype", default=None)
    parser.add_argument("--contam-frac", type=float, default=0.5)
    args = parser.parse_args(argv)

    summary = convert_stack_to_forma(
        args.input_stack,
        args.output_fits,
        arm_name=args.arm_name,
        half_window=args.half_window,
        center_row=args.center_row,
        source_code=args.source_code,
        z_spec=args.z_spec,
        spectype=args.spectype,
        contam_frac=args.contam_frac,
    )
    lo, hi = summary["rows_used"]
    print(f"Extracted rows [{lo}, {hi}) around row {summary['center_row']}")
    if summary["wcs_crpix2_row"] is not None:
        print(f"  (WCS CRPIX2 implies row {summary['wcs_crpix2_row']:.1f} for comparison)")
    print(f"Intermediate boxcar 1D: {summary['intermediate_1d']}")
    print(
        f"Wrote {summary['output_fits']}: {summary['n_pixels']} pixels, "
        f"{summary['n_masked']} masked, "
        f"wavelength {summary['wavelength_range_ang'][0]:.0f}-"
        f"{summary['wavelength_range_ang'][1]:.0f} A"
    )


if __name__ == "__main__":
    main()
