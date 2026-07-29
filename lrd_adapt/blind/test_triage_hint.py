"""
Test the triage-hint lookup helper (triage_hint.py).

Covers: found row, missing csv, csv without a matching id, and a malformed
numeric column -- all must return None rather than raise, since this hint
is purely informational and must never break a run when absent/bad.

No pytest dependency -- run directly:
    python lrd_adapt/blind/test_triage_hint.py
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lrd_adapt.blind.triage_hint import lookup_triage_hint  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "_demo_output" / "triage_hint_test"

FIELDNAMES = ["id", "grizli_id", "top_peak_wavelength_A", "top_peak_snr", "file"]


def _write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # --- found row ---------------------------------------------------------
    good_csv = OUT_DIR / "flagged.csv"
    _write_csv(good_csv, [
        {"id": "BLIND_1539", "grizli_id": 1539, "top_peak_wavelength_A": 36030.0,
         "top_peak_snr": 15.6, "file": "j1030_01539.stack.fits"},
        {"id": "BLIND_759", "grizli_id": 759, "top_peak_wavelength_A": 36710.0,
         "top_peak_snr": 151.21, "file": "j1030_00759.stack.fits"},
    ])
    hint = lookup_triage_hint(str(good_csv), "BLIND_1539")
    assert hint is not None
    assert hint["wavelength_A"] == 36030.0
    assert hint["snr"] == 15.6
    assert hint["source"] == str(good_csv)
    print("PASS: found row returns the expected hint")

    # --- csv unset -----------------------------------------------------------
    assert lookup_triage_hint(None, "BLIND_1539") is None
    print("PASS: unset csv_path returns None")

    # --- csv missing ---------------------------------------------------------
    assert lookup_triage_hint(str(OUT_DIR / "does_not_exist.csv"), "BLIND_1539") is None
    print("PASS: missing csv file returns None")

    # --- id not in csv ---------------------------------------------------------
    assert lookup_triage_hint(str(good_csv), "SRC04") is None
    print("PASS: id absent from csv returns None")

    # --- malformed numeric column ---------------------------------------------
    bad_csv = OUT_DIR / "flagged_bad.csv"
    _write_csv(bad_csv, [
        {"id": "BLIND_9999", "grizli_id": 9999, "top_peak_wavelength_A": "not_a_number",
         "top_peak_snr": 10.0, "file": "x.stack.fits"},
    ])
    assert lookup_triage_hint(str(bad_csv), "BLIND_9999") is None
    print("PASS: malformed numeric column returns None instead of raising")

    print("\nAll triage_hint tests passed.")


if __name__ == "__main__":
    main()
