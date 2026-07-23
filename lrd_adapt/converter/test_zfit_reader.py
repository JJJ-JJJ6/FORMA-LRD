"""
Test the *.full.fits redshift-fit reader against synthetic files only --
no real grizli fit product existed when this was written (see
zfit_reader.py's schema caveat; re-verification against the first real
file from eor1 is still an open item).

Covers: pdf round-trip (z and width recovered), header-only fallback,
loud failure on a schema-mismatched file, and the state-prior mapping.

No pytest dependency -- run directly:
    python lrd_adapt/converter/test_zfit_reader.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
from astropy.io import fits  # noqa: E402

from lrd_adapt.converter.zfit_reader import (  # noqa: E402
    read_grizli_zfit,
    zfit_to_state_prior,
)
from lrd_adapt.eval.synthetic_injection import write_synthetic_full_fits  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "_demo_output" / "zfit_reader_test"

Z_TRUE = 2.328
Z_SIGMA_TRUE = 0.004


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # --- pdf round-trip ---------------------------------------------------
    full_path = str(OUT_DIR / "synth_00004.full.fits")
    write_synthetic_full_fits(Z_TRUE, full_path, z_sigma=Z_SIGMA_TRUE, source_id=4)
    result = read_grizli_zfit(full_path)
    assert result["method"] == "zfit_pdf", result
    assert abs(result["z"] - Z_TRUE) < 1e-3, f"z {result['z']} != {Z_TRUE}"
    assert abs(result["z_sigma"] - Z_SIGMA_TRUE) < 0.2 * Z_SIGMA_TRUE, (
        f"sigma {result['z_sigma']} vs injected {Z_SIGMA_TRUE}"
    )
    assert result["z16"] < result["z50"] < result["z84"]

    prior = zfit_to_state_prior(full_path)
    assert abs(prior["external_z_prior"] - Z_TRUE) < 1e-3
    assert prior["external_z_prior_sigma"] == result["z_sigma"]
    assert prior["external_z_prior_source"] == "grizli_zfit:zfit_pdf"

    # --- header-only fallback --------------------------------------------
    header_only = str(OUT_DIR / "header_only.full.fits")
    primary = fits.PrimaryHDU()
    primary.header["REDSHIFT"] = Z_TRUE
    fits.HDUList([primary]).writeto(header_only, overwrite=True)
    result2 = read_grizli_zfit(header_only)
    assert result2["method"] == "header_redshift"
    assert abs(result2["z"] - Z_TRUE) < 1e-9
    assert result2["z_sigma"] is None

    # --- schema mismatch must fail loudly, naming what it found ----------
    alien = str(OUT_DIR / "alien.fits")
    tbl = fits.BinTableHDU.from_columns(
        fits.ColDefs([fits.Column(name="wavelength", format="D",
                                  array=np.linspace(1, 2, 5))]),
        name="SOMETHING",
    )
    fits.HDUList([fits.PrimaryHDU(), tbl]).writeto(alien, overwrite=True)
    try:
        read_grizli_zfit(alien)
    except ValueError as e:
        assert "SOMETHING" in str(e), "error must list the HDUs it found"
        assert "extend the reader" in str(e)
    else:
        raise AssertionError("schema-mismatched file did not raise")

    print("test_zfit_reader: all assertions passed")
    print(f"  pdf round-trip: z={result['z']:.4f} "
          f"sigma={result['z_sigma']:.4f} (injected {Z_TRUE}, {Z_SIGMA_TRUE})")


if __name__ == "__main__":
    main()
