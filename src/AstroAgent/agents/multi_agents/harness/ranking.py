"""
ranking.py — Global Gaussian model fitting and Δχ²-based hypothesis ranking.

Reads per-hypothesis *_lines.csv files written by the harness, filters out
NOT_FOUND lines, then builds a global continuum + multi-Gaussian model,
computes Δχ² improvement over continuum-only, and ranks hypotheses.
"""

import csv
import os
import numpy as np
from typing import Any, Dict, List, Tuple


def _read_csv_lines(csv_path: str) -> List[Dict[str, Any]]:
    """Read a harness *_lines.csv and return rows with status != NOT_FOUND.

    Maps CSV columns to the internal format expected by _global_fit_one and
    write_ranked_hypotheses (center, sigma, local_snr, amplitude, etc.).
    """
    lines = []
    if not os.path.exists(csv_path):
        return lines
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            status = (row.get("status") or "").strip()
            if status == "NOT_FOUND" or not status:
                continue
            center_str = row.get("fitted_center", "")
            sigma_str = row.get("fitted_sigma", "")
            if not center_str or not sigma_str:
                continue
            try:
                center = float(center_str)
                sigma = float(sigma_str)
            except (ValueError, TypeError):
                continue
            lines.append({
                "name": row.get("name", "?"),
                "center": center,
                "sigma": sigma,
                "amplitude": float(row.get("amplitude", 0)) if row.get("amplitude") else 0.0,
                "local_snr": float(row.get("snr", 0)) if row.get("snr") else 0.0,
                "delta_chi2_per_n": float(row.get("delta_chi2_per_n", 0)) if row.get("delta_chi2_per_n") else 0.0,
                "status": status,
            })
    return lines


def _gaussian_unit(x, center, sigma):
    """Unit-amplitude Gaussian."""
    return np.exp(-0.5 * ((x - center) / sigma) ** 2)


def _global_fit_one(
    wavelength: np.ndarray,
    flux: np.ndarray,
    continuum_flux: np.ndarray,
    confirmed_lines: List[Dict[str, Any]],
) -> Tuple[float, float, Dict[str, float]]:
    """Fit continuum + Σ A_i * Gaussian_i to the spectrum.

    Centers and sigmas are fixed at harness values; only amplitudes are free.
    This is a linear least-squares problem.

    Returns
    -------
    chi2_full : float
        χ² of the full model.
    delta_chi2 : float
        χ²_continuum - χ²_full (positive = improvement).
    fitted_amps : dict[str, float]
        Fitted amplitudes keyed by line name (from fit_peak 'name' field or center).
    """
    residual = flux - continuum_flux  # what the Gaussians need to explain
    n_pts = len(wavelength)

    if not confirmed_lines:
        return float(np.sum(residual ** 2)), 0.0, {}

    # Design matrix: each column is a unit-amplitude Gaussian
    n_lines = len(confirmed_lines)
    design = np.zeros((n_pts, n_lines))
    labels = []

    for i, line in enumerate(confirmed_lines):
        center = line["center"]
        sigma = line["sigma"]
        design[:, i] = _gaussian_unit(wavelength, center, sigma)
        labels.append(f"{line.get('name', '?')}@{center:.1f}")

    # Linear least squares
    amps, residuals_lsq, rank, sv = np.linalg.lstsq(design, residual, rcond=None)

    fitted = design @ amps
    chi2_full = float(np.sum((residual - fitted) ** 2))
    chi2_continuum = float(np.sum(residual ** 2))
    delta_chi2 = chi2_continuum - chi2_full

    fitted_amps = {labels[i]: float(amps[i]) for i in range(n_lines)}

    return chi2_full, delta_chi2, fitted_amps


def rank_hypotheses(
    wavelength: List[float],
    flux: List[float],
    continuum_flux: List[float],
    harness_results: List[Dict[str, Any]],
    csv_dir: str,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """Rank redshift hypotheses by global Δχ² improvement.

    For each hypothesis, reads its *_lines.csv (written by the harness),
    selects rows whose status is not NOT_FOUND, performs a global multi-Gaussian
    fit (centers and sigmas fixed at CSV values, amplitudes refit globally),
    and computes Δχ² relative to continuum-only.

    Parameters
    ----------
    wavelength : list[float]
        Full spectrum wavelength array.
    flux : list[float]
        Full spectrum flux array.
    continuum_flux : list[float]
        Continuum-only flux array (same length).
    harness_results : list[dict]
        Output of harness.run() for each hypothesis. Each dict must have
        'hypothesis_idx' (int) to locate the corresponding CSV file.
    csv_dir : str
        Directory containing the *_lines.csv files.
    top_k : int
        Number of top-ranked hypotheses to return.

    Returns
    -------
    list[dict], sorted by delta_chi2 descending. Each dict has:
        hypothesis_idx : int
        delta_chi2 : float
        n_lines_used : int
        fitted_amps : dict
        lines : list[dict] — the usable lines from CSV
        harness_result : dict — the original harness result
    """
    wl = np.asarray(wavelength, dtype=np.float64)
    fl = np.asarray(flux, dtype=np.float64)
    cont = np.asarray(continuum_flux, dtype=np.float64)

    scored = []

    for idx, hr in enumerate(harness_results):
        hyp_idx = hr.get("hypothesis_idx", idx)
        csv_path = os.path.join(csv_dir, f"{hyp_idx}_lines.csv")
        usable_lines = _read_csv_lines(csv_path)

        if not usable_lines:
            scored.append({
                "hypothesis_idx": hyp_idx,
                "delta_chi2": float("-inf"),
                "n_lines_used": 0,
                "fitted_amps": {},
                "lines": [],
                "harness_result": hr,
            })
            continue

        _, delta_chi2, fitted_amps = _global_fit_one(wl, fl, cont, usable_lines)

        scored.append({
            "hypothesis_idx": hyp_idx,
            "delta_chi2": round(delta_chi2, 2),
            "n_lines_used": len(usable_lines),
            "fitted_amps": fitted_amps,
            "lines": usable_lines,
            "harness_result": hr,
        })

    scored.sort(key=lambda x: x["delta_chi2"], reverse=True)
    return scored[:top_k]
