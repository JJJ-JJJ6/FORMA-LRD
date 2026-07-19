"""
Test the 2D route: synthetic grizli-style *.stack.fits -> boxcar extraction
-> FORMA FITS, checking wavelength calibration, line recovery at the
injected position, contamination masking, and METADATA passthrough.

No pytest dependency -- run directly:
    python lrd_adapt/converter/test_stack_to_forma.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
from astropy.io import fits  # noqa: E402

from lrd_adapt.converter.stack_to_forma import convert_stack_to_forma  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "_demo_output" / "stack_to_forma_test"

N_SPATIAL = 31
N_WAVE = 400
WAVE_START_UM = 3.15
DWAVE_UM = 0.002  # 20 A/pixel, F356W-grism-like
TRACE_ROW = 15
TRACE_SIGMA_PIX = 1.8
LINE_WAVE_UM = 3.6030  # He I 10833 at z=2.326-ish, matches SRC04's real peak
LINE_SIGMA_UM = 0.008
CONTAM_LO_UM, CONTAM_HI_UM = 3.30, 3.34


def _make_synthetic_stack(path):
    wave_um = WAVE_START_UM + np.arange(N_WAVE) * DWAVE_UM
    rows = np.arange(N_SPATIAL)

    profile = np.exp(-0.5 * ((rows - TRACE_ROW) / TRACE_SIGMA_PIX) ** 2)
    continuum = 0.05
    line = 1.0 * np.exp(-0.5 * ((wave_um - LINE_WAVE_UM) / LINE_SIGMA_UM) ** 2)
    sci = profile[:, None] * (continuum + line)[None, :]

    contam = np.zeros_like(sci)
    contam_cols = (wave_um >= CONTAM_LO_UM) & (wave_um <= CONTAM_HI_UM)
    contam[:, contam_cols] = 10.0  # swamps the 0.05 continuum there

    wht = np.full_like(sci, 400.0)  # per-pixel sigma 0.05

    hdr = fits.Header()
    hdr["CRVAL1"] = WAVE_START_UM
    hdr["CRPIX1"] = 1.0
    hdr["CD1_1"] = DWAVE_UM
    hdr["CRPIX2"] = TRACE_ROW + 1.0  # FITS 1-indexed

    hdus = [
        fits.PrimaryHDU(),
        fits.ImageHDU(data=sci, header=hdr, name="SCI"),
        fits.ImageHDU(data=wht, name="WHT"),
        fits.ImageHDU(data=contam, name="CONTAM"),
    ]
    hdus[3].header["EXTVER"] = "visit1,1"  # per-visit CONTAM, grizli-style
    fits.HDUList(hdus).writeto(path, overwrite=True)
    return wave_um


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stack_path = str(OUT_DIR / "synthetic.stack.fits")
    forma_path = str(OUT_DIR / "synthetic_forma.fits")
    wave_um = _make_synthetic_stack(stack_path)

    summary = convert_stack_to_forma(
        stack_path,
        forma_path,
        arm_name="F356W",
        source_code="SRC99",
        z_spec=2.326,
        spectype="TEST",
    )

    assert summary["center_row"] == TRACE_ROW, (
        f"auto-detected center row {summary['center_row']} != trace row {TRACE_ROW}"
    )
    assert summary["intermediate_1d"].endswith(".1D_boxcar.fits")
    assert Path(summary["intermediate_1d"]).exists()

    with fits.open(forma_path) as h:
        wl = np.asarray(h["F356W_WAVELENGTH"].data)
        fl = np.asarray(h["F356W_FLUX"].data)
        ivar = np.asarray(h["F356W_IVAR"].data)
        mask = np.asarray(h["F356W_MASK"].data)
        meta = h["METADATA"].data

    assert len(wl) == N_WAVE
    expected_ang = wave_um * 1e4
    assert np.allclose(wl, expected_ang), "wavelength axis not converted to Angstrom"

    peak_ang = wl[np.argmax(fl)]
    line_ang = LINE_WAVE_UM * 1e4
    assert abs(peak_ang - line_ang) <= DWAVE_UM * 1e4, (
        f"recovered peak {peak_ang:.1f} A not at injected line {line_ang:.1f} A"
    )

    assert np.all(ivar > 0), "uniform positive WHT should give positive IVAR everywhere"

    contam_cols = (wave_um >= CONTAM_LO_UM) & (wave_um <= CONTAM_HI_UM)
    assert np.all(mask[contam_cols] & 4), "contamination-dominated pixels not masked"
    assert not np.any(mask[~contam_cols] & 4), "clean pixels wrongly contam-masked"

    assert meta["SRC_CODE"][0] == "SRC99"
    assert abs(float(meta["VI_Z"][0]) - 2.326) < 1e-9

    print("test_stack_to_forma: all assertions passed")
    print(f"  peak recovered at {peak_ang:.1f} A (injected {line_ang:.1f} A)")
    print(f"  {int(np.count_nonzero(mask & 4))} contamination-masked pixels")


if __name__ == "__main__":
    main()
