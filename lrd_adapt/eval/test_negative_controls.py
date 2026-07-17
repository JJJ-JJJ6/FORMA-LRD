"""
Tests for the negative-controls scaffolding. Two things here:

1. The manifest loader -- no real entries yet (see negative_controls.py's
   module docstring for why: no catalog access, unresolved design question
   for a source with no paper claim to test).
2. A false-positive-rate check for _fit_broadline_lsf_bic under pure noise
   -- NOT a substitute for real negative controls (it only tests the
   deterministic BIC tool in isolation, not the LLM/CWT pipeline that would
   actually decide whether to call the tool on a real non-LRD source), but
   a real, useful robustness check that doesn't need catalog access.

    python lrd_adapt/eval/test_negative_controls.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astropy.io import fits  # noqa: E402

from lrd_adapt.converter.grizli_to_forma import convert_grizli_1d_to_forma  # noqa: E402
from lrd_adapt.eval.negative_controls import load_negative_controls  # noqa: E402
from lrd_adapt.eval.synthetic_injection import make_pure_noise_case, write_synthetic_1d_fits  # noqa: E402
from lrd_adapt.tools.broadline_lsf_bic import fit_broadline_lsf_bic  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "_demo_output" / "negative_control_noise_test"


def test_default_manifest_loads_without_error():
    manifest = load_negative_controls()
    assert isinstance(manifest, dict)


def test_missing_manifest_returns_empty_dict():
    assert load_negative_controls(path="/nonexistent/path.json") == {}


def test_broadline_tool_false_positive_rate_under_pure_noise(n_trials=20, max_false_positive_rate=0.15):
    """Run _fit_broadline_lsf_bic at a plausible He I line position on N
    independent pure-noise realizations (no injected line at all). None of
    them have a real line, so broad_line_real=True on any of them is a
    false positive. A handful of false positives by chance is expected
    (this is a statistical test, not a hard guarantee) -- the ΔBIC>10
    threshold is not calibrated to be zero-false-positive at any N -- but
    the rate should stay low."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    z_test = 2.328
    n_false_positive = 0
    n_ok = 0

    for seed in range(n_trials):
        case = make_pure_noise_case(seed=seed)
        raw = OUT_DIR / f"noise_{seed}.1D.fits"
        converted = OUT_DIR / f"noise_{seed}.fits"
        write_synthetic_1d_fits(case, str(raw))
        convert_grizli_1d_to_forma(str(raw), str(converted), arm_name="F356W")

        with fits.open(converted) as h:
            wl = h["F356W_WAVELENGTH"].data
            fl = h["F356W_FLUX"].data

        try:
            result = fit_broadline_lsf_bic(wl, fl, 10833.0, z_test)
        except (RuntimeError, ValueError):
            # a fit failing to converge on pure noise is a fine outcome --
            # it just means the tool correctly found nothing fittable
            n_ok += 1
            continue

        if result["broad_line_real"]:
            n_false_positive += 1
        else:
            n_ok += 1

    rate = n_false_positive / n_trials
    print(f"Pure-noise false-positive rate: {n_false_positive}/{n_trials} = {rate:.2f}")
    assert rate <= max_false_positive_rate, (
        f"broad_line_real=True on pure noise in {n_false_positive}/{n_trials} trials "
        f"({rate:.2f}) -- exceeds the {max_false_positive_rate} tolerance"
    )


if __name__ == "__main__":
    test_default_manifest_loads_without_error()
    test_missing_manifest_returns_empty_dict()
    test_broadline_tool_false_positive_rate_under_pure_noise()
    print("OK -- negative controls scaffolding + BIC-tool noise robustness check passed.")
