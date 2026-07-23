"""
Test the photometric-evidence reader against a synthetic catalog only
(the real j1030_phot.fits on eor1 is the schema re-verification gate).

Covers: break-proxy recovery for a strong-break source vs a no-break
source, the z-validity window (proxy flagged unreliable outside it),
color signs, missing-source and missing-column loud failures.

No pytest dependency -- run directly:
    python lrd_adapt/evidence/test_phot_evidence.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from astropy.table import Table  # noqa: E402

from lrd_adapt.evidence.phot_evidence import measure_phot_evidence  # noqa: E402
from lrd_adapt.eval.synthetic_injection import write_synthetic_phot_catalog  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "_demo_output" / "phot_evidence_test"

Z_VALID = 2.40    # inside BREAK_PROXY_Z_RANGE
Z_INVALID = 1.93  # SRC02-like; F115W pivot rest is already above the break


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cat_path = str(OUT_DIR / "synth_phot.fits")

    write_synthetic_phot_catalog(
        [
            {"id": 1, "z": Z_VALID, "break_factor": 3.0},
            {"id": 2, "z": Z_VALID, "break_factor": 1.0},
            {"id": 3, "z": Z_INVALID, "break_factor": 3.0},
        ],
        cat_path,
    )

    strong = measure_phot_evidence(cat_path, 1, z=Z_VALID)
    none_ = measure_phot_evidence(cat_path, 2, z=Z_VALID)
    invalid = measure_phot_evidence(cat_path, 3, z=Z_INVALID)

    assert strong["provenance"] == "ours"
    assert strong["balmer_break_proxy_reliable"] is True
    assert none_["balmer_break_proxy_reliable"] is True
    # the injected factor-3 break must push the proxy well above the
    # no-break source's continuum-only ratio
    ratio = strong["balmer_break_proxy"] / none_["balmer_break_proxy"]
    assert 2.5 < ratio < 3.5, (
        f"break proxy ratio {ratio} does not recover injected factor 3 "
        f"(strong={strong['balmer_break_proxy']}, none={none_['balmer_break_proxy']})"
    )

    # outside the z window: proxy computed but explicitly unreliable
    assert invalid["balmer_break_proxy"] is not None
    assert invalid["balmer_break_proxy_reliable"] is False
    assert "OUTSIDE" in invalid["balmer_break_note"]

    # a break makes F115W fainter -> F115W-F200W color more positive (redder)
    assert strong["color_f115w_f200w_mag"] > none_["color_f115w_f200w_mag"]

    # loud failure: unknown source id
    try:
        measure_phot_evidence(cat_path, 999, z=Z_VALID)
    except ValueError as e:
        assert "999" in str(e)
    else:
        raise AssertionError("unknown source id did not raise")

    # loud failure: catalog with unrecognized columns must list them
    alien_path = str(OUT_DIR / "alien_phot.fits")
    Table({"id": [1], "weird_column": [1.0]}).write(alien_path, overwrite=True)
    try:
        measure_phot_evidence(alien_path, 1, z=Z_VALID)
    except ValueError as e:
        assert "weird_column" in str(e)
        assert "extend _FLUX_COL_PATTERNS" in str(e)
    else:
        raise AssertionError("unrecognized catalog schema did not raise")

    print("test_phot_evidence: all assertions passed")
    print(f"  strong-break proxy {strong['balmer_break_proxy']} vs "
          f"no-break {none_['balmer_break_proxy']} (injected factor 3.0)")
    print(f"  out-of-window case correctly flagged unreliable at z={Z_INVALID}")


if __name__ == "__main__":
    main()
