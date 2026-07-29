"""Optional hint that a source was already flagged by the cheap blind-search
triage scan (triage.py's --flagged-csv output) before verification's own,
independent, contamination-masking-aware feature detection ran.

Purely informational -- this never feeds a fit or changes a verdict. It only
lets the "no features detected" placeholder report say *why* a source that
triage saw signal in still came back Unknown (e.g. the flagged wavelength
falls in a region verification's own masking excluded), instead of reporting
a bare "no signal" as if nothing had ever been seen. See LRD_WORK.md.
"""
import csv
import os
from typing import Optional, TypedDict


class TriageHint(TypedDict):
    wavelength_A: float
    snr: float
    source: str


def lookup_triage_hint(csv_path: Optional[str], file_name: str) -> Optional[TriageHint]:
    """Look up `file_name` as an `id` row in a triage flagged-candidates CSV.

    Always safe to call: returns None if csv_path is unset, the file doesn't
    exist, the row is missing, or the row's numeric columns don't parse.
    """
    if not csv_path or not os.path.exists(csv_path):
        return None
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("id") != file_name:
                continue
            try:
                return {
                    "wavelength_A": float(row["top_peak_wavelength_A"]),
                    "snr": float(row["top_peak_snr"]),
                    "source": csv_path,
                }
            except (KeyError, ValueError):
                return None
    return None
