"""
Synthetic recall check for the blind-search triage: reverse-engineer a
specvizitor-style *.stack.fits for every registered source from its
paper-derived line identity + z_spec (primary_hypotheses.json), then
verify triage flags every one of them with a peak at the right observed
wavelength.

This closes the "known-source recall check" the triage docstring demands
before any flag-criterion tightening -- without waiting for the real
eor1 extractions of all 19 sources.

What a PASS here actually proves (be honest about this):
  - the triage code path detects every line identity / redshift
    combination in the sample (Pabeta at z=1.55 near the blue edge,
    O I 8446 at z=3.18, He I across z=1.9-2.5, ...) at the assumed SNR,
    with the flagged peak at the correct observed wavelength;
  - i.e. wavelength coverage, unit handling, and detection wiring are
    right across the sample's whole parameter range.

What it does NOT prove:
  - real-world detectability. Noise structure, contamination, trace
    curvature, and per-source line SNR here are assumed, not measured --
    Kapoor+26 gives identities/redshifts/FWHM ranges but not our data's
    per-source noise. The real recall check on real extractions still
    has to happen once eor1 products exist. A synthetic source is
    constructed to be detectable; passing is necessary, never sufficient.

Generated files are named synth_*.stack.fits and live under
_demo_output/ (gitignored) so they can never be mistaken for real grizli
products.

Run directly (also asserts, test-style):
    python lrd_adapt/blind/synthetic_recall.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

import numpy as np  # noqa: E402
from astropy.io import fits  # noqa: E402

from lrd_adapt.blind.triage import triage_batch, write_results_csv  # noqa: E402
from lrd_adapt.converter.grizli_to_forma import convert_grizli_1d_to_forma  # noqa: E402
from lrd_adapt.converter.zfit_reader import read_grizli_zfit  # noqa: E402
from lrd_adapt.eval.synthetic_injection import (  # noqa: E402
    make_synthetic_case,
    write_synthetic_1d_fits,
    write_synthetic_full_fits,
)

OUT_DIR = Path(__file__).resolve().parent / "_demo_output" / "synthetic_recall"
HYPOTHESES_PATH = REPO_ROOT / "lrd_adapt" / "configs" / "primary_hypotheses.json"

# Mid Kapoor+26 observed LRD broad-FWHM range (1400-2100 km/s). One value
# for every source deliberately: this check is about wavelength/identity
# coverage, not per-source width fidelity (ground_truth.json stays
# untouched here -- same isolation rule as everywhere else in lrd_adapt).
BROAD_FWHM_KMS = 1700.0
SNR = 25.0  # make_synthetic_case's default; assumed, not measured

# 2D stack geometry, matching test_triage's synthetic convention
N_SPATIAL = 31
TRACE_ROW = 15
TRACE_SIGMA_PIX = 1.8

PEAK_TOLERANCE_ANG = 100.0


def case_to_stack(case, path):
    """Broadcast a synthetic 1D case onto a Gaussian spatial trace and
    write it as a grizli-style *.stack.fits (summed SCI/WHT + WCS)."""
    wave_um = case["wave_um"]
    flux = case["flux"]
    err = case["err"]

    rows = np.arange(N_SPATIAL)
    profile = np.exp(-0.5 * ((rows - TRACE_ROW) / TRACE_SIGMA_PIX) ** 2)
    sci = profile[:, None] * flux[None, :]
    # Per-2D-pixel sigma consistent with the 1D err scaled by the profile;
    # floor avoids infinite WHT in far wings.
    sigma_2d = np.maximum(profile[:, None] * err[None, :], 1e-3 * float(np.max(err)))
    wht = 1.0 / sigma_2d**2

    hdr = fits.Header()
    hdr["CRVAL1"] = float(wave_um[0])
    hdr["CRPIX1"] = 1.0
    hdr["CD1_1"] = float(np.median(np.diff(wave_um)))
    hdr["CRPIX2"] = TRACE_ROW + 1.0
    hdr["COMMENT"] = (
        "SYNTHETIC stack reverse-engineered from paper line+z "
        "(lrd_adapt/blind/synthetic_recall.py) -- not a real grizli product."
    )

    fits.HDUList(
        [
            fits.PrimaryHDU(),
            fits.ImageHDU(data=sci, header=hdr, name="SCI"),
            fits.ImageHDU(data=wht, name="WHT"),
        ]
    ).writeto(path, overwrite=True)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(HYPOTHESES_PATH, encoding="utf-8") as f:
        hypotheses = json.load(f)

    expected = {}  # grizli-style numeric id -> (src_code, center_obs_ang)
    paths = []
    for src_code, entry in sorted(hypotheses.items()):
        num = int(src_code.removeprefix("SRC"))
        case = make_synthetic_case(
            entry["line"],
            entry["z_spec"],
            broad_fwhm_kms=BROAD_FWHM_KMS,
            snr=SNR,
            seed=num,  # per-source noise realization, reproducible
        )
        path = OUT_DIR / f"synth_{num:05d}.stack.fits"
        case_to_stack(case, str(path))
        expected[num] = (src_code, case["ground_truth"]["center_obs_ang"])
        paths.append(str(path))

        # Also fake the other two specvizitor-visible grizli products, so
        # every reader in the project gets exercised on this source:
        # .1D.fits (read by grizli_to_forma) and .full.fits (read by
        # zfit_reader). Synthetic-only validation -- see both modules'
        # caveats about real-file re-verification.
        write_synthetic_1d_fits(case, str(OUT_DIR / f"synth_{num:05d}.1D.fits"))
        write_synthetic_full_fits(
            entry["z_spec"],
            str(OUT_DIR / f"synth_{num:05d}.full.fits"),
            source_id=num,
        )

    rows = triage_batch(paths, verbose=False)
    write_results_csv(rows, str(OUT_DIR / "synthetic_recall_results.csv"))

    n_flagged = 0
    failures = []
    print(f"{'src':6} {'line':12} {'z':6} {'expected A':>10} "
          f"{'flagged':>7} {'nearest peak A':>14}")
    for row in rows:
        num = row["grizli_id"]
        src_code, center = expected[num]
        entry = hypotheses[src_code]

        peaks = []
        if row["all_emission_peaks"]:
            peaks = [
                float(p.split("A/")[0]) for p in row["all_emission_peaks"].split(";")
            ]
        nearest = min(peaks, key=lambda p: abs(p - center)) if peaks else None
        hit = (
            row["flagged"]
            and nearest is not None
            and abs(nearest - center) <= PEAK_TOLERANCE_ANG
        )
        n_flagged += bool(row["flagged"])
        if not hit:
            failures.append((src_code, row))
        print(
            f"{src_code:6} {entry['line']:12} {entry['z_spec']:<6} "
            f"{center:10.0f} {str(bool(row['flagged'])):>7} "
            f"{nearest if nearest is not None else '--':>14}"
        )

    print(
        f"\nRecall: {n_flagged}/{len(rows)} flagged; "
        f"{len(rows) - len(failures)}/{len(rows)} with a peak within "
        f"{PEAK_TOLERANCE_ANG:.0f} A of the paper-implied position "
        f"(assumed SNR={SNR}, broad FWHM={BROAD_FWHM_KMS} km/s -- see "
        "module docstring for what this does and does not prove)"
    )
    assert not failures, (
        "Triage recall failure on synthetic paper-derived sources: "
        + ", ".join(code for code, _ in failures)
    )

    # --- the other two product readers, over every source ----------------
    n_full_ok = 0
    for num, (src_code, _) in expected.items():
        zfit = read_grizli_zfit(str(OUT_DIR / f"synth_{num:05d}.full.fits"))
        z_true = hypotheses[src_code]["z_spec"]
        assert abs(zfit["z"] - z_true) < 1e-3, (
            f"{src_code}: .full.fits round-trip z {zfit['z']} != {z_true}"
        )
        n_full_ok += 1
    conv = convert_grizli_1d_to_forma(
        str(OUT_DIR / "synth_00004.1D.fits"),
        str(OUT_DIR / "synth_00004_forma.fits"),
        source_code="SRC04",
    )
    assert conv["n_pixels"] > 0

    print(f".full.fits reader round-trip: {n_full_ok}/{len(expected)} "
          "(synthetic schema only -- real-file verification still open)")
    print(".1D.fits converter spot-check: ok (SRC04)")
    print("synthetic_recall: all sources recovered")


if __name__ == "__main__":
    main()
