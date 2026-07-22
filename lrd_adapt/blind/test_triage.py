"""
Test the blind-search triage runner on synthetic *.stack.fits files:
a line source (must be flagged, at the right wavelength), a noise-only
source (must not be flagged), and a corrupt file (must produce an error
row without killing the batch). Also checks grizli-id filename parsing
and both CSV outputs.

No pytest dependency -- run directly:
    python lrd_adapt/blind/test_triage.py
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

import numpy as np  # noqa: E402
from astropy.io import fits  # noqa: E402

from lrd_adapt.blind.triage import (  # noqa: E402
    parse_grizli_id,
    triage_batch,
    write_flagged_csv,
    write_results_csv,
)

OUT_DIR = Path(__file__).resolve().parent / "_demo_output" / "triage_test"

N_SPATIAL = 31
N_WAVE = 400
WAVE_START_UM = 3.15
DWAVE_UM = 0.002  # 20 A/pixel, F356W-grism-like
TRACE_ROW = 15
TRACE_SIGMA_PIX = 1.8
LINE_WAVE_UM = 3.6030  # He I 10833 at z=2.326-ish, matches SRC04's real peak
LINE_SIGMA_UM = 0.008
NOISE_SIGMA = 0.05
RNG_SEED = 20260722


def _write_stack(path, with_line):
    rng = np.random.default_rng(RNG_SEED)
    wave_um = WAVE_START_UM + np.arange(N_WAVE) * DWAVE_UM
    rows = np.arange(N_SPATIAL)

    profile = np.exp(-0.5 * ((rows - TRACE_ROW) / TRACE_SIGMA_PIX) ** 2)
    continuum = 0.05
    spec_1d = np.full(N_WAVE, continuum)
    if with_line:
        spec_1d = spec_1d + 1.0 * np.exp(
            -0.5 * ((wave_um - LINE_WAVE_UM) / LINE_SIGMA_UM) ** 2
        )
    sci = profile[:, None] * spec_1d[None, :]
    sci += rng.normal(0.0, NOISE_SIGMA, size=sci.shape)

    wht = np.full_like(sci, 1.0 / NOISE_SIGMA**2)

    hdr = fits.Header()
    hdr["CRVAL1"] = WAVE_START_UM
    hdr["CRPIX1"] = 1.0
    hdr["CD1_1"] = DWAVE_UM
    hdr["CRPIX2"] = TRACE_ROW + 1.0

    fits.HDUList(
        [
            fits.PrimaryHDU(),
            fits.ImageHDU(data=sci, header=hdr, name="SCI"),
            fits.ImageHDU(data=wht, name="WHT"),
        ]
    ).writeto(path, overwrite=True)


def _write_corrupt(path):
    # A FITS with no SCI/WHT at all -- _find_stack_hdus must raise inside
    # triage, which must surface as an error row, not an exception.
    fits.HDUList([fits.PrimaryHDU()]).writeto(path, overwrite=True)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    line_path = str(OUT_DIR / "test_00123.stack.fits")
    noise_path = str(OUT_DIR / "test_00456.stack.fits")
    corrupt_path = str(OUT_DIR / "test_00789.stack.fits")
    _write_stack(line_path, with_line=True)
    _write_stack(noise_path, with_line=False)
    _write_corrupt(corrupt_path)

    assert parse_grizli_id(line_path) == 123
    assert parse_grizli_id("no_id_here.fits") is None

    rows = triage_batch([line_path, noise_path, corrupt_path], verbose=False)
    assert len(rows) == 3, "batch must produce one row per input, always"
    by_file = {Path(r["file"]).name: r for r in rows}

    line_row = by_file["test_00123.stack.fits"]
    assert line_row["status"] == "ok"
    assert line_row["flagged"] is True, "injected line not flagged"
    line_ang = LINE_WAVE_UM * 1e4
    assert abs(line_row["top_peak_wavelength_A"] - line_ang) <= 3 * DWAVE_UM * 1e4, (
        f"flagged peak at {line_row['top_peak_wavelength_A']} A, "
        f"injected at {line_ang:.0f} A"
    )
    assert line_row["grizli_id"] == 123
    assert line_row["all_emission_peaks"], "all-peaks summary must be populated"
    assert "A/snr" in line_row["all_emission_peaks"]

    noise_row = by_file["test_00456.stack.fits"]
    assert noise_row["status"] == "ok"
    assert noise_row["flagged"] is False, (
        f"pure noise wrongly flagged: {noise_row}"
    )

    corrupt_row = by_file["test_00789.stack.fits"]
    assert corrupt_row["status"] == "error", "corrupt file must yield an error row"
    assert corrupt_row["flagged"] is False
    assert "ValueError" in corrupt_row["error"]

    results_csv = OUT_DIR / "triage_results.csv"
    flagged_csv = OUT_DIR / "flagged.csv"
    write_results_csv(rows, str(results_csv))
    n_flagged = write_flagged_csv(rows, str(flagged_csv))
    assert n_flagged == 1

    with open(flagged_csv, newline="", encoding="utf-8") as f:
        flagged_rows = list(csv.DictReader(f))
    assert len(flagged_rows) == 1
    assert flagged_rows[0]["id"] == "BLIND_123"
    assert flagged_rows[0]["grizli_id"] == "123"

    with open(results_csv, newline="", encoding="utf-8") as f:
        result_rows = list(csv.DictReader(f))
    assert len(result_rows) == 3

    print("test_triage: all assertions passed")
    print(
        f"  line source flagged at {line_row['top_peak_wavelength_A']} A "
        f"(injected {line_ang:.0f} A), snr {line_row['top_peak_snr']}"
    )
    print(f"  noise source clean, corrupt file survived as error row")


if __name__ == "__main__":
    main()
