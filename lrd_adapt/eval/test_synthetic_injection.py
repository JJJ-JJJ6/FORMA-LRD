"""
Integration test: synthetic injection -> A1 converter -> A3 BIC tool,
checking the recovered broad_line_real verdict against the injection's
known ground truth. Exercises three of A1/A3/C's pieces together, not just
each in isolation.

No pytest dependency -- run directly:
    python lrd_adapt/eval/test_synthetic_injection.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from astropy.io import fits  # noqa: E402

from lrd_adapt.converter.grizli_to_forma import convert_grizli_1d_to_forma  # noqa: E402
from lrd_adapt.eval.synthetic_injection import make_synthetic_case, write_synthetic_1d_fits  # noqa: E402
from lrd_adapt.tools.broadline_lsf_bic import fit_broadline_lsf_bic  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "_demo_output" / "synthetic_injection_test"


def _run_case(case, tag):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = OUT_DIR / f"{tag}.1D.fits"
    converted = OUT_DIR / f"{tag}.fits"
    write_synthetic_1d_fits(case, str(raw))
    convert_grizli_1d_to_forma(str(raw), str(converted), arm_name="F356W")

    with fits.open(converted) as h:
        wl = h["F356W_WAVELENGTH"].data
        fl = h["F356W_FLUX"].data

    gt = case["ground_truth"]
    result = fit_broadline_lsf_bic(wl, fl, 10833.0, gt["z"])
    return gt, result


def test_no_real_broadening_case():
    case = make_synthetic_case("HeI_Pagamma", z=2.328, broad_fwhm_kms=None, seed=1)
    gt, result = _run_case(case, "no_broadening")
    assert gt["broad_line_real"] is False
    assert result["broad_line_real"] is False
    assert result["best_model"] == "narrow_extended_lsf"


def test_real_broadening_case_within_lrd_range():
    case = make_synthetic_case("HeI_Pagamma", z=2.328, broad_fwhm_kms=1800.0, seed=2)
    gt, result = _run_case(case, "real_broadening")
    assert gt["broad_line_real"] is True
    assert result["broad_line_real"] is True
    assert result["best_model"] in ("narrow_broad_point_lsf", "mixed")
    # order-of-magnitude sanity, not exact recovery -- the fit blends narrow+
    # broad flux under noise, see the module docstring's honest caveat
    assert 800.0 < result["broad_fwhm_kms"] < 3000.0


def test_contamination_flagging_survives_conversion():
    case = make_synthetic_case(
        "HeI_Pagamma", z=2.328, broad_fwhm_kms=None, seed=3,
        contam_regions=[[35800.0, 36200.0]],
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = OUT_DIR / "contam.1D.fits"
    converted = OUT_DIR / "contam.fits"
    write_synthetic_1d_fits(case, str(raw))
    summary = convert_grizli_1d_to_forma(str(raw), str(converted), arm_name="F356W", contam_frac=0.5)
    assert summary["n_masked"] > 0


if __name__ == "__main__":
    test_no_real_broadening_case()
    test_real_broadening_case_within_lrd_range()
    test_contamination_flagging_survives_conversion()
    print("OK -- synthetic injection integration tests passed.")
