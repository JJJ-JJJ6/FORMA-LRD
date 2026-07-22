"""
Blind-search triage: cheap CWT-only scan over many grizli *.stack.fits
files, with no redshift, no hypothesis, and no LLM involvement.

This is the first stage of the blind-search funnel (supervisor pivot,
2026-07-20/22): instead of verifying a pre-selected target list, scan
every extracted source in a field for line-like features and emit a
flagged-candidate list. Only flagged candidates then get the expensive
grizli run_fit=True re-extraction and the full FORMA verification
pipeline. This mirrors Kapoor+26's own two-stage funnel (their "Allegro"
first pass narrowed ~120k sources to 834 before any profile fitting).

Per source, the scan is exactly the pipeline's own machinery, not a
reimplementation:
  - lrd_adapt.converter.stack_to_forma.extract_boxcar_1d  (2D -> 1D)
  - FORMA's own cwt_feature_finder.find_features_cwt      (line detection)

Defaults below deliberately MATCH the live LRD .env preset
(CWT_SNR_THRESH=8.0, CWT_MIN_RIDGE_LENGTH=4, CWT_N_SCALES=24,
CWT_MIN_SCALE=1.0, CWT_MAX_SCALE=14.0) so triage flags with the same
sensitivity the full pipeline would see. If the .env preset changes,
change these to match or pass overrides on the command line.

Flagging is deliberately over-inclusive: ANY emission detection flags the
source. A false positive costs one wasted re-extraction and is then
filtered by the BIC tools and LLM audit downstream; a false negative here
is unrecoverable -- the source is never looked at again. Do not tighten
the flag criterion without re-running the known-source recall check
(all 19 Kapoor+26 sources must stay flagged).

Caveats, same as the stack_to_forma route this builds on:
  - boxcar count/s flux, flat=1.0 -- detection-grade only, uncalibrated;
  - at catalog scale (thousands of sources) some pure-noise flags are
    EXPECTED from multiple testing, this is by design, not a bug.

Usage (run wherever the *.stack.fits files live, e.g. eor1):
    python -m lrd_adapt.blind.triage "j1030/Extractions/*.stack.fits" \
        -o j1030_triage.csv --flagged-csv j1030_flagged.csv
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import re
import time

import numpy as np

from lrd_adapt.converter.stack_to_forma import (
    DEFAULT_HALF_WINDOW,
    extract_boxcar_1d,
)
from FORMA.agents.multi_agents.utils.cwt_feature_finder import find_features_cwt

MICRON_TO_ANGSTROM = 1e4

# Live LRD .env preset values (see module docstring before changing).
DEFAULT_CWT_PARAMS = {
    "snr_thresh": 8.0,
    "min_ridge_length": 4,
    "n_scales": 24,
    "min_scale": 1.0,
    "max_scale": 14.0,
}

# Same contamination-domination convention as grizli_to_forma's mask bit 4.
DEFAULT_CONTAM_FRAC = 0.5

_GRIZLI_ID_RE = re.compile(r"_(\d+)\.stack\.fits$")

RESULT_COLUMNS = [
    "file",
    "grizli_id",
    "status",
    "flagged",
    "n_emission",
    "n_absorption",
    "top_peak_wavelength_A",
    "top_peak_FWHM_km_s",
    "top_peak_snr",
    "top_peak_width_class",
    "top_peak_in_contam",
    "all_emission_peaks",
    "contam_frac_pixels",
    "n_nonfinite_pixels",
    "elapsed_s",
    "error",
]


def parse_grizli_id(path):
    """grizli names stacks {root}_{id:05d}.stack.fits; None if no match."""
    m = _GRIZLI_ID_RE.search(os.path.basename(path))
    return int(m.group(1)) if m else None


def triage_stack_file(
    stack_path,
    cwt_params=None,
    half_window=DEFAULT_HALF_WINDOW,
    contam_frac=DEFAULT_CONTAM_FRAC,
):
    """
    Boxcar-extract one *.stack.fits and run blind CWT detection on it.

    Returns a dict in RESULT_COLUMNS shape. Never raises for per-source
    data problems -- those come back as status="error" rows so a batch
    survives individual bad files.
    """
    params = dict(DEFAULT_CWT_PARAMS)
    if cwt_params:
        params.update(cwt_params)

    row = {c: "" for c in RESULT_COLUMNS}
    row["file"] = stack_path
    row["grizli_id"] = parse_grizli_id(stack_path)
    t0 = time.perf_counter()

    try:
        extraction = extract_boxcar_1d(stack_path, half_window=half_window)
        wavelength = np.asarray(extraction["wave_um"], dtype=float) * MICRON_TO_ANGSTROM
        flux = np.asarray(extraction["flux"], dtype=float)
        contam = np.asarray(extraction["contam"], dtype=float)

        nonfinite = ~np.isfinite(flux)
        row["n_nonfinite_pixels"] = int(np.count_nonzero(nonfinite))
        flux = np.where(nonfinite, 0.0, flux)

        contam_bad = np.abs(contam) > contam_frac * np.abs(flux)
        row["contam_frac_pixels"] = round(float(np.mean(contam_bad)), 4)

        records_em, records_ab = find_features_cwt(
            wavelength,
            flux,
            snr_thresh=params["snr_thresh"],
            min_ridge_length=params["min_ridge_length"],
            n_scales=params["n_scales"],
            min_scale=params["min_scale"],
            max_scale=params["max_scale"],
            verbose=False,
        )

        row["n_emission"] = len(records_em)
        row["n_absorption"] = len(records_ab)
        row["flagged"] = bool(records_em)
        row["status"] = "ok"

        if records_em:
            top = max(records_em, key=lambda r: r["snr"])
            row["top_peak_wavelength_A"] = round(float(top["wavelength"]), 1)
            row["top_peak_FWHM_km_s"] = round(float(top["FWHM_km_s"]), 1)
            row["top_peak_snr"] = round(float(top["snr"]), 2)
            row["top_peak_width_class"] = top["width_class"]
            nearest = int(np.argmin(np.abs(wavelength - top["wavelength"])))
            row["top_peak_in_contam"] = bool(contam_bad[nearest])
            # All peaks, not just the strongest: triage runs unmasked, so
            # the top-SNR peak can be a contamination residual the full
            # pipeline would mask -- recall checks against known sources
            # need to see every detection to find the real line among them.
            row["all_emission_peaks"] = ";".join(
                f"{r['wavelength']:.0f}A/snr{r['snr']:.1f}/{r['width_class']}"
                for r in sorted(records_em, key=lambda r: r["wavelength"])
            )
    except Exception as exc:  # noqa: BLE001 -- batch must survive any bad file
        row["status"] = "error"
        row["flagged"] = False
        row["error"] = f"{type(exc).__name__}: {exc}"

    row["elapsed_s"] = round(time.perf_counter() - t0, 3)
    return row


def triage_batch(
    stack_paths,
    cwt_params=None,
    half_window=DEFAULT_HALF_WINDOW,
    contam_frac=DEFAULT_CONTAM_FRAC,
    verbose=True,
):
    """Run triage_stack_file over many paths; returns list of row dicts."""
    rows = []
    n = len(stack_paths)
    for i, path in enumerate(stack_paths, 1):
        row = triage_stack_file(
            path,
            cwt_params=cwt_params,
            half_window=half_window,
            contam_frac=contam_frac,
        )
        rows.append(row)
        if verbose:
            tag = (
                "FLAGGED" if row["flagged"]
                else ("error " if row["status"] == "error" else "clean  ")
            )
            extra = (
                f" peak {row['top_peak_wavelength_A']} A"
                f" snr {row['top_peak_snr']}"
                if row["flagged"]
                else (f" {row['error']}" if row["status"] == "error" else "")
            )
            print(f"[{i}/{n}] {tag} {os.path.basename(path)}{extra}")
    return rows


def write_results_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def write_flagged_csv(rows, out_path):
    """
    Flagged-candidate subset in the id/grizli_id shape the eor1 extraction
    scripts consume (extract_all.py --ids-csv), so this file feeds the
    expensive run_fit=True re-extraction directly.
    """
    flagged = [r for r in rows if r["flagged"] and r["grizli_id"] is not None]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["id", "grizli_id", "top_peak_wavelength_A", "top_peak_snr", "file"]
        )
        for r in flagged:
            writer.writerow(
                [
                    f"BLIND_{r['grizli_id']}",
                    r["grizli_id"],
                    r["top_peak_wavelength_A"],
                    r["top_peak_snr"],
                    r["file"],
                ]
            )
    return len(flagged)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Blind CWT triage over grizli *.stack.fits files. "
        "Flags line-candidate sources for full re-extraction; no redshift, "
        "no hypothesis, no LLM."
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="*.stack.fits paths, globs (quote them), or directories",
    )
    parser.add_argument("-o", "--out-csv", default="triage_results.csv")
    parser.add_argument(
        "--flagged-csv",
        default=None,
        help="also write the flagged subset in extract_all.py --ids-csv shape",
    )
    parser.add_argument(
        "--snr-thresh", type=float, default=DEFAULT_CWT_PARAMS["snr_thresh"]
    )
    parser.add_argument(
        "--min-ridge-length",
        type=int,
        default=DEFAULT_CWT_PARAMS["min_ridge_length"],
    )
    parser.add_argument(
        "--n-scales", type=int, default=DEFAULT_CWT_PARAMS["n_scales"]
    )
    parser.add_argument(
        "--min-scale", type=float, default=DEFAULT_CWT_PARAMS["min_scale"]
    )
    parser.add_argument(
        "--max-scale", type=float, default=DEFAULT_CWT_PARAMS["max_scale"]
    )
    parser.add_argument("--half-window", type=int, default=DEFAULT_HALF_WINDOW)
    parser.add_argument("--contam-frac", type=float, default=DEFAULT_CONTAM_FRAC)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    paths = []
    for inp in args.inputs:
        if os.path.isdir(inp):
            paths.extend(sorted(glob.glob(os.path.join(inp, "*.stack.fits"))))
        elif any(ch in inp for ch in "*?["):
            paths.extend(sorted(glob.glob(inp)))
        else:
            paths.append(inp)
    if not paths:
        parser.error("no *.stack.fits files matched the given inputs")

    cwt_params = {
        "snr_thresh": args.snr_thresh,
        "min_ridge_length": args.min_ridge_length,
        "n_scales": args.n_scales,
        "min_scale": args.min_scale,
        "max_scale": args.max_scale,
    }

    t0 = time.perf_counter()
    rows = triage_batch(
        paths,
        cwt_params=cwt_params,
        half_window=args.half_window,
        contam_frac=args.contam_frac,
        verbose=not args.quiet,
    )
    elapsed = time.perf_counter() - t0

    write_results_csv(rows, args.out_csv)

    n_ok = sum(1 for r in rows if r["status"] == "ok")
    n_err = sum(1 for r in rows if r["status"] == "error")
    n_flag = sum(1 for r in rows if r["flagged"])
    print(
        f"\n{len(rows)} source(s) in {elapsed:.1f}s "
        f"({elapsed / len(rows):.2f} s/source): "
        f"{n_flag} flagged, {n_ok - n_flag} clean, {n_err} error(s)"
    )
    print(f"Full results: {args.out_csv}")

    if args.flagged_csv is not None:
        n_written = write_flagged_csv(rows, args.flagged_csv)
        print(
            f"Flagged candidates for re-extraction: {args.flagged_csv} "
            f"({n_written} row(s)) -- feed to: "
            f"extract_all.py <field> --ids-csv {args.flagged_csv}"
        )


if __name__ == "__main__":
    main()
