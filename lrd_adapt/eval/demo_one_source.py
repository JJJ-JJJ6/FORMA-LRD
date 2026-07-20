"""
A1 plumbing proof: converter + hypothesis provider, one source, end-to-end.

This does NOT run the full FORMA/LangGraph pipeline (that needs Docker +
Redrock-free deps installed, see CLAUDE.md Environment notes) — it proves the
data flow between the two new lrd_adapt modules and FORMA's FITS-loader
contract, using a synthetic grizli-shaped input for one source (SRC01).
Per CLAUDE.md's A1 description, results are expected to be scientifically
representative-only ("we're testing data flow"), not a real classification.

Run: python lrd_adapt/eval/demo_one_source.py
Output: lrd_adapt/eval/_demo_output/SRC01/ (gitignored — no data in git)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
from astropy.io import fits

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lrd_adapt.converter.grizli_to_forma import convert_grizli_1d_to_forma  # noqa: E402
from lrd_adapt.hypothesis.lrd_hypothesis_provider import (  # noqa: E402
    generate_lrd_hypotheses_for_state,
    REST_WAVELENGTHS_ANG,
)

OUT_DIR = Path(__file__).resolve().parent / "_demo_output" / "SRC01"


def make_synthetic_grizli_1d(path, z_spec=2.328, line_rest_ang=REST_WAVELENGTHS_ANG["HeI_Pagamma"]):
    """A minimal *.1D.fits stand-in: one BinTableHDU (wave/flux/err/flat/contam)
    spanning the F356W range with a Gaussian bump at the redshifted He I 10833
    line, matching grizli's documented per-grism schema (CLAUDE.md)."""
    wave_um = np.linspace(3.15, 3.95, 800)  # observed, microns
    line_obs_ang = line_rest_ang * (1 + z_spec)
    line_obs_um = line_obs_ang / 1e4

    continuum = 0.5 + 0.02 * (wave_um - wave_um.mean())
    sigma_um = 0.003  # a few hundred km/s at this wavelength
    line = 3.0 * np.exp(-0.5 * ((wave_um - line_obs_um) / sigma_um) ** 2)
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 0.05, size=wave_um.shape)

    flux = continuum + line + noise
    err = np.full_like(flux, 0.05)
    flat = np.ones_like(flux)
    contam = np.full_like(flux, 0.01)  # low contamination everywhere

    col = fits.ColDefs([
        fits.Column(name="wave", format="D", array=wave_um),
        fits.Column(name="flux", format="D", array=flux),
        fits.Column(name="err", format="D", array=err),
        fits.Column(name="flat", format="D", array=flat),
        fits.Column(name="contam", format="D", array=contam),
    ])
    hdu = fits.BinTableHDU.from_columns(col, name="F356W")
    fits.HDUList([fits.PrimaryHDU(), hdu]).writeto(path, overwrite=True)
    return line_obs_ang


def assert_forma_loadable(fits_path, arm_name="F356W"):
    """Structural check against what utils.VI._load_spectrum_from_fits expects
    (multi-arm image HDUs {ARM}_WAVELENGTH/_FLUX/_IVAR, optional _MASK, plus
    a METADATA table with VI_Z/VI_SPECTYPE) — done directly against astropy
    rather than importing FORMA's VI module, which pulls in the full
    LLM/LangGraph dependency chain not needed for this plumbing check."""
    with fits.open(fits_path) as hdul:
        names = [hdu.name.upper() for hdu in hdul]
        for suffix in ("_WAVELENGTH", "_FLUX", "_IVAR", "_MASK"):
            assert f"{arm_name}{suffix}" in names, f"missing {arm_name}{suffix} in {names}"

        wl = hdul[f"{arm_name}_WAVELENGTH"].data
        fl = hdul[f"{arm_name}_FLUX"].data
        iv = hdul[f"{arm_name}_IVAR"].data
        mk = hdul[f"{arm_name}_MASK"].data
        assert wl.shape == fl.shape == iv.shape == mk.shape, "arm arrays must be same length"
        assert wl.min() > 30000 and wl.max() < 40000, f"expected F356W-ish Angstrom range, got {wl.min()}-{wl.max()}"

        assert "METADATA" in names, "expected a METADATA HDU with VI_Z/VI_SPECTYPE"
        meta = hdul["METADATA"].data
        assert "VI_Z" in meta.dtype.names


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw_1d = OUT_DIR / "SRC01.1D.fits"
    forma_fits = OUT_DIR / "SRC01.fits"

    true_line_obs_ang = make_synthetic_grizli_1d(raw_1d)
    print(f"[1/4] synthetic grizli 1D written: {raw_1d}")

    summary = convert_grizli_1d_to_forma(
        input_fits=str(raw_1d),
        output_fits=str(forma_fits),
        arm_name="F356W",
        source_code="SRC01",
        z_spec=2.328,
        spectype="LRD_candidate",
    )
    print(f"[2/4] converted -> {forma_fits} :: {summary}")

    assert_forma_loadable(forma_fits)
    print("[3/4] structural check against FORMA's _load_spectrum_from_fits contract: OK")

    # Fake the slice of pipeline state that would exist by the time the
    # hypothesis-generation call site runs (peaks already detected upstream
    # by the CWT feature finder) — anchor on the true injected line so this
    # is a meaningful plumbing check, not a tautology.
    fake_state = {
        "file_name": "SRC01",
        "peaks": [{"wavelength": true_line_obs_ang, "amplitude": 3.0}],
    }
    hyps = generate_lrd_hypotheses_for_state(fake_state, params=None)
    (OUT_DIR / "redshift_hypotheses.json").write_text(json.dumps(hyps, indent=2))
    print(f"[4/4] hypotheses written -> {OUT_DIR / 'redshift_hypotheses.json'}")

    print("\nCandidate hypotheses (data-derived, no privileged answer):")
    for h in hyps["hypotheses"]:
        matches = h["_lrd_provenance"]["matches_registered_claim"]
        print(f"  score={h['score']:6.1f}  matches_registered_claim={matches!s:5s}  {h['Hypothesis']}")

    # The registered claim (He I+Pagamma) must still appear as a candidate.
    # It does NOT necessarily score highest: with only one detected peak,
    # window-centering alone is a weak discriminator (the six redshift
    # windows are similar widths, so some other line's window often centers
    # marginally closer) -- see lrd_adapt/hypothesis/test_lrd_hypothesis_provider.py
    # for why near-degenerate single-peak scores are the expected, honest
    # behavior, not a bug. This is a plumbing check (does the registered
    # claim survive as a live candidate with a comparable score), not a
    # claim that scoring alone resolves single-line ambiguity.
    assert any(
        h["_lrd_provenance"]["matches_registered_claim"] for h in hyps["hypotheses"]
    ), "registered claim (HeI_Pagamma) did not even appear as a candidate"
    he1 = next(h for h in hyps["hypotheses"] if h["_lrd_provenance"]["matches_registered_claim"])
    top_score = hyps["hypotheses"][0]["score"]
    assert top_score - he1["score"] < 5.0, (
        f"registered claim scored {he1['score']:.1f} vs top {top_score:.1f} -- "
        "suspiciously far off for a clean single-line synthetic injection"
    )

    print(f"\nOK -- full output tree for one source at {OUT_DIR}")


if __name__ == "__main__":
    main()
