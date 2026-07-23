"""
Test the DSCI compactness (r_circ) measurement against synthetic
cutouts with known half-light radii -- an LRD-like 100 mas source and a
classical-AGN-like 180 mas source (Kapoor+26 Fig. 5 regimes) -- plus
loud-failure behavior on a .full.fits without DSCI.

No pytest dependency -- run directly:
    python lrd_adapt/evidence/test_compactness.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lrd_adapt.evidence.compactness import measure_compactness  # noqa: E402
from lrd_adapt.eval.synthetic_injection import write_synthetic_full_fits  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "_demo_output" / "compactness_test"

CASES = {"lrd_like": 100.0, "agn_like": 180.0}  # injected r_circ, mas
TOLERANCE_FRAC = 0.15


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for label, r_true in CASES.items():
        path = str(OUT_DIR / f"{label}.full.fits")
        write_synthetic_full_fits(
            2.328, path, source_id=1, dsci_r_circ_mas=r_true, dsci_seed=42
        )
        result = measure_compactness(path)
        r_meas = result["r_circ_mas"]
        assert abs(r_meas - r_true) <= TOLERANCE_FRAC * r_true, (
            f"{label}: measured r_circ {r_meas} mas vs injected {r_true} mas"
        )
        assert result["provenance"] == "ours"
        assert result["pixel_scale_mas"] == 63.0
        print(f"  {label}: injected {r_true} mas, measured {r_meas} mas")

    # the two regimes must stay separable after measurement
    r_lrd = measure_compactness(str(OUT_DIR / "lrd_like.full.fits"))["r_circ_mas"]
    r_agn = measure_compactness(str(OUT_DIR / "agn_like.full.fits"))["r_circ_mas"]
    assert r_lrd < 140.0 < r_agn, (
        f"measured radii ({r_lrd}, {r_agn}) do not separate the Kapoor "
        "compact/extended regimes"
    )

    # loud failure on a minimal .full.fits without DSCI
    no_dsci = str(OUT_DIR / "no_dsci.full.fits")
    write_synthetic_full_fits(2.328, no_dsci, source_id=2)
    try:
        measure_compactness(no_dsci)
    except ValueError as e:
        assert "DSCI" in str(e) and "ZFIT_STACK" in str(e), (
            f"error should name the missing ext and list HDUs: {e}"
        )
    else:
        raise AssertionError("missing DSCI did not raise")

    print("test_compactness: all assertions passed")


if __name__ == "__main__":
    main()
