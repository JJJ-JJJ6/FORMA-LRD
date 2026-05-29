import re
import numpy as np
from scipy.optimize import curve_fit
from langchain_core.tools import tool

# ---------------------------------------------------------------------------
# Rest-frame line tables (single source of truth — keep in sync with VI.py)
# ---------------------------------------------------------------------------

EMISSION_LINES = {
    "Lyα":       1216.0,
    "C IV":      1549.0,
    "He II":     1640.4,
    "C III]":    1909.0,
    "Mg II":     2800.0,
    "Ne [V]":    3426.0,
    "O [II]":    3727.0,
    "Hε":        3970.1,
    "Hδ":        4102.9,
    "Hγ":        4341.7,
    "Hβ":        4862.7,
    "O [III]a":  4960.3,
    "O [III]b":  5008.2,
    "N [II]a":   6549.8,
    "Hα":        6564.6,
    "N [II]b":   6585.3,
    "S [II]a":   6718.3,
    "S [II]b":   6732.7,
}

EMISSION_LINE_WIDTHS = {
    "Lyα":       "broad",
    "C IV":      "broad",
    "C III]":    "broad",
    "He II":     "both",
    "Mg II":     "broad",
    "Hε":        "both",
    "Hδ":        "both",
    "Hγ":        "both",
    "Hβ":        "both",
    "Hα":        "both",
    "Ne [V]":    "narrow",
    "O [II]":    "narrow",
    "O [III]a":  "narrow",
    "O [III]b":  "narrow",
    "N [II]a":   "narrow",
    "N [II]b":   "narrow",
    "S [II]a":   "narrow",
    "S [II]b":   "narrow",
}

ABSORPTION_LINES = {
    "Ca K_abs":      3934.8,
    "Ca H_abs":      3969.6,
    "G-band_abs":    4305.6,
    "Mg_abs":        5176.7,
    "Mg II_abs":     2800.0,
    "Na D_abs":      5895.6,
    "CaT1_abs":      8498.0,
    "CaT2_abs":      8542.0,
    "CaT3_abs":      8662.0,
    "Hε_abs":        3970.1,
    "Hδ_abs":        4102.9,
    "Hγ_abs":        4341.7,
    "Hβ_abs":        4862.7,
    "Hα_abs":        6564.6,
}

_WIDTH_3SIGMA_MAP = {
    "broad":     90.0,
    "narrow":    25.0,
    "both":      50.0,
    "absorption": 20.0,
}


# ---------------------------------------------------------------------------
# Tool 1: load_spectrum
# ---------------------------------------------------------------------------

@tool
def load_spectrum(npz_path: str) -> dict:
    """Load a cleaned spectrum .npz file and return summary statistics.

    The .npz must contain arrays: wavelength, flux, snr (and optionally ivar).

    Parameters
    ----------
    npz_path : str
        Absolute path to the .npz file.

    Returns
    -------
    dict with keys:
        n_points : int          — number of wavelength bins
        wavelength_min : float  — minimum wavelength (Å)
        wavelength_max : float  — maximum wavelength (Å)
        snr_median : float      — median SNR across the spectrum
        n_arms : str or None    — number of distinct bands (if detectable)
    """
    data = np.load(npz_path)
    wl = data["wavelength"]
    snr = data.get("snr")

    # Detect bands by looking for large gaps in wavelength coverage
    gaps = np.diff(wl)
    n_arms = int(np.sum(gaps > 100)) + 1  # >100 Å gap = band boundary

    return {
        "n_points": int(len(wl)),
        "wavelength_min": float(wl[0]),
        "wavelength_max": float(wl[-1]),
        "snr_median": float(np.median(snr)) if snr is not None else None,
        "n_arms": str(n_arms) if n_arms > 1 else None,
    }


# ---------------------------------------------------------------------------
# Tool 2: predict_lines
# ---------------------------------------------------------------------------

@tool
def predict_lines(
    redshift: float,
    line_type: str = "all",
    wavelength_min: float = None,
    wavelength_max: float = None,
) -> dict:
    """Predict observed-frame wavelengths for rest-frame spectral lines at a given redshift.

    Parameters
    ----------
    redshift : float
        The redshift hypothesis z.
    line_type : str
        "emission", "absorption", or "all" (default).
    wavelength_min, wavelength_max : float, optional
        If provided, only return lines whose predicted λ_obs falls within [min, max].

    Returns
    -------
    dict with keys:
        redshift : float
        n_lines : int
        lines : list[dict]
            Each dict has: name, rest_wl, obs_wl, width_class, type
    """
    lines_out = []

    if line_type in ("emission", "all"):
        for name, rest_wl in EMISSION_LINES.items():
            obs_wl = rest_wl * (1.0 + redshift)
            if wavelength_min is not None and wavelength_max is not None:
                if obs_wl < wavelength_min or obs_wl > wavelength_max:
                    continue
            lines_out.append({
                "name": name,
                "rest_wl": round(rest_wl, 1),
                "obs_wl": round(obs_wl, 1),
                "width_class": EMISSION_LINE_WIDTHS.get(name, "narrow"),
                "type": "emission",
            })

    if line_type in ("absorption", "all"):
        for name, rest_wl in ABSORPTION_LINES.items():
            obs_wl = rest_wl * (1.0 + redshift)
            if wavelength_min is not None and wavelength_max is not None:
                if obs_wl < wavelength_min or obs_wl > wavelength_max:
                    continue
            lines_out.append({
                "name": name,
                "rest_wl": round(rest_wl, 1),
                "obs_wl": round(obs_wl, 1),
                "width_class": "absorption",
                "type": "absorption",
            })

    lines_out.sort(key=lambda x: x["obs_wl"])
    return {
        "redshift": redshift,
        "n_lines": len(lines_out),
        "lines": lines_out,
    }


# ---------------------------------------------------------------------------
# Tool 3: fit_peak
# ---------------------------------------------------------------------------

def _gaussian_plus_linear(x, amp, center, sigma, slope, intercept):
    return amp * np.exp(-(x - center) ** 2 / (2 * sigma ** 2)) + slope * x + intercept


@tool
def fit_peak(
    npz_path: str,
    center_guess: float,
    width_3sigma: float,
    line_type: str = "emission",
    window_half: float = 200.0,
) -> dict:
    """Fit a Gaussian + linear baseline model around a predicted line position.

    Parameters
    ----------
    npz_path : str
        Path to the cleaned spectrum .npz file.
    center_guess : float
        Predicted observed wavelength (Å) — initial guess for the Gaussian center.
    width_3sigma : float
        Expected physical width (3σ) of the line in Å. Typical values:
        broad lines (Lyα, C IV, C III], Mg II) → 90
        narrow lines (Ne [V], O [II], O [III])  → 25
        both (Balmer, He II)                     → 50
        absorption                                → 20
    line_type : str
        "emission" (default) or "absorption". Constrains amplitude sign:
        emission → amp ≥ 0, absorption → amp ≤ 0.
    window_half : float
        Half-width of the fitting window in Å (default 200).

    Returns
    -------
    dict with keys:
        center : float            — fitted Gaussian center (Å)
        center_err : float        — 1σ uncertainty on center
        amplitude : float         — fitted amplitude (positive=emission, negative=absorption)
        amplitude_err : float
        sigma : float             — Gaussian σ (Å)
        fwhm : float              — FWHM (Å)
        fwhm_km_s : float         — FWHM (km/s at the fitted center)
        delta_chi2_per_n : float  — Δχ² per data point (positive = Gaussian improves fit)
        local_rms : float         — RMS of fit residuals (Å)
        local_snr : float         — |amplitude| / local_rms
        n_points : int            — number of data points in window
        flags : list[str]         — warnings (empty if all ok)
        message : str             — human-readable summary
    """
    data = np.load(npz_path)
    wl_full = data["wavelength"]
    flux_full = data["flux"]

    # Select window
    mask = (wl_full >= center_guess - window_half) & (wl_full <= center_guess + window_half)
    wl = wl_full[mask]
    flux = flux_full[mask]

    if len(wl) < 10:
        return {
            "center": None, "center_err": None,
            "amplitude": None, "amplitude_err": None,
            "sigma": None, "fwhm": None, "fwhm_km_s": None,
            "delta_chi2_per_n": None, "local_rms": None, "local_snr": None,
            "n_points": len(wl),
            "flags": ["too_few_points"],
            "message": f"Only {len(wl)} points in window — need at least 10.",
        }

    # ── Initial guess ──────────────────────────────────────
    # Quick linear fit for baseline initial guess
    lin_coeffs = np.polyfit(wl, flux, 1)
    slope0, intercept0 = lin_coeffs[0], lin_coeffs[1]

    # Interpolate flux at center_guess for amplitude initial guess
    flux_at_center = np.interp(center_guess, wl, flux)
    linear_at_center = slope0 * center_guess + intercept0
    amp0 = flux_at_center - linear_at_center

    # Clamp amp0 to respect line_type (narrower search space)
    if line_type == "absorption":
        amp0 = min(amp0, -1e-6)
    else:
        amp0 = max(amp0, 1e-6)

    sigma0 = width_3sigma / 3.0
    sigma0 = np.clip(sigma0, 2.0, window_half / 2)

    p0 = [amp0, center_guess, sigma0, slope0, intercept0]

    # ── Bounds ─────────────────────────────────────────────
    if line_type == "absorption":
        amp_lower, amp_upper = -np.inf, 0.0
    else:
        amp_lower, amp_upper = 0.0, np.inf

    bounds = (
        [amp_lower,  center_guess - 50, 1.0,  -np.inf, -np.inf],
        [amp_upper,  center_guess + 50, window_half, np.inf,  np.inf],
    )

    # ── Fit ────────────────────────────────────────────────
    try:
        popt, pcov = curve_fit(
            _gaussian_plus_linear, wl, flux,
            p0=p0, bounds=bounds,
            maxfev=5000,
        )
    except Exception as e:
        return {
            "center": None, "center_err": None,
            "amplitude": None, "amplitude_err": None,
            "sigma": None, "fwhm": None, "fwhm_km_s": None,
            "delta_chi2_per_n": None, "local_rms": None, "local_snr": None,
            "n_points": len(wl),
            "flags": ["fit_failed"],
            "message": f"curve_fit failed: {e}",
        }

    amp, center, sigma, slope, intercept = popt
    perr = np.sqrt(np.diag(pcov)) if pcov is not None else [None] * 5

    # ── Statistics ─────────────────────────────────────────
    fitted = _gaussian_plus_linear(wl, *popt)
    linear_only = slope * wl + intercept
    residuals = flux - fitted

    # Robust RMS estimate (MAD-based)
    local_rms = 1.4826 * np.median(np.abs(residuals - np.median(residuals)))
    if local_rms < 1e-10:
        local_rms = np.std(residuals) or 1e-10

    n = len(wl)
    chi2_full = np.sum((residuals / local_rms) ** 2)
    chi2_linear = np.sum(((flux - linear_only) / local_rms) ** 2)
    delta_chi2_per_n = round((chi2_linear - chi2_full) / n, 3)

    local_snr = round(abs(amp) / local_rms, 2)

    fwhm = sigma * 2.35482
    fwhm_km_s = fwhm / center * 2.99792458e5 if center != 0 else None

    # ── Flags ──────────────────────────────────────────────
    flags = []
    center_dev = abs(center - center_guess)
    if center_dev > 50:
        flags.append("center_deviation_large")
    if delta_chi2_per_n < 0:
        flags.append("negative_delta_chi2")

    return {
        "center": round(center, 3),
        "center_err": round(perr[1], 4) if perr[1] is not None else None,
        "amplitude": round(amp, 6),
        "amplitude_err": round(perr[0], 6) if perr[0] is not None else None,
        "sigma": round(sigma, 3),
        "fwhm": round(fwhm, 3),
        "fwhm_km_s": round(fwhm_km_s, 1) if fwhm_km_s is not None else None,
        "delta_chi2_per_n": delta_chi2_per_n,
        "local_rms": round(local_rms, 6),
        "local_snr": local_snr,
        "n_points": n,
        "flags": flags,
        "message": (
            f"Fit {'OK' if not flags else 'with warnings'}. "
            f"center={center:.2f}±{perr[1]:.3f} Å, amp={amp:.4f}, "
            f"FWHM={fwhm:.1f} Å ({fwhm_km_s:.0f} km/s), "
            f"S/N={local_snr:.1f}, Δχ²/n={delta_chi2_per_n:.1f}"
        ),
    }


# ---------------------------------------------------------------------------
# Tool 4: read_spectrum_region
# ---------------------------------------------------------------------------

@tool
def read_spectrum_region(
    npz_path: str,
    wl_min: float,
    wl_max: float,
    stride: int = 1,
) -> dict:
    """Read a raw slice of the cleaned spectrum for manual inspection.

    Use this to investigate anomalies: DLA damping wings, Lyα forest,
    associated absorption, or any region where fit_peak returns suspicious results.

    Parameters
    ----------
    npz_path : str
        Path to the cleaned spectrum .npz file.
    wl_min, wl_max : float
        Wavelength range of interest (Å).
    stride : int
        Downsampling step. Default 1 (no downsampling). Use 2–5 for large regions.

    Returns
    -------
    dict with keys:
        wl_range : [float, float]  — wavelength bounds (Å)
        n : int                    — number of points returned
        wl : list[float]           — wavelength array (Å, 3 d.p.)
        fl : list[float]           — flux array (4 d.p.)
    """
    data = np.load(npz_path)
    wl_full = data["wavelength"]
    flux_full = data["flux"]

    mask = (wl_full >= wl_min) & (wl_full <= wl_max)
    wl = wl_full[mask][::stride]
    fl = flux_full[mask][::stride]

    return {
        "wl_range": [wl_min, wl_max],
        "n": len(wl),
        "wl": [round(float(w), 3) for w in wl],
        "fl": [round(float(f), 4) for f in fl],
    }


# ---------------------------------------------------------------------------
# Tool 5: write_report
# ---------------------------------------------------------------------------

@tool
def write_report(file_path: str, content: str) -> dict:
    """Write a natural-language analysis report to a markdown file.

    Use this to save your findings as a human-readable report. Write the report
    BEFORE outputting the final JSON block — the JSON is for downstream
    pipelines, the report is for astronomers to read and audit.

    Parameters
    ----------
    file_path : str
        Absolute path for the output .md file.
    content : str
        Markdown content of the report.

    Returns
    -------
    dict with keys:
        path : str        — absolute path of the written file
        size_bytes : int  — file size in bytes
    """
    import os
    os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    return {
        "path": file_path,
        "size_bytes": len(content.encode("utf-8")),
    }


# ---------------------------------------------------------------------------
# Tool 6: write_lines_csv
# ---------------------------------------------------------------------------

@tool
def write_lines_csv(file_path: str, lines: list) -> dict:
    """Write CWT feature evaluation results to a CSV file.

    Each row represents one line that was evaluated (status: LIKELY, MARGINAL,
    NOT_FOUND, or MASKED). The CSV is the definitive structured record of the
    analysis — downstream pipelines read this, not the JSON in the chat response.

    Parameters
    ----------
    file_path : str
        Absolute path for the output .csv file.
    lines : list[dict]
        Each dict must have the following keys:
            name : str              — line name (e.g. "Lyα", "C IV")
            rest_wavelength : float — rest-frame wavelength in Å
            predicted_obs : float   — predicted observed wavelength at this z
            fitted_center : float   — CWT feature wavelength in Å (or null)
            fitted_center_err : float — wavelength uncertainty (or null)
            amplitude : float       — CWT amplitude (or null)
            amplitude_err : float   — amplitude uncertainty (or null)
            fitted_sigma : float    — Gaussian σ in Å (or null). Used by downstream
                                      global fit to model the line profile
            fwhm_km_s : float       — FWHM in km/s (or null)
            ridge_length : int      — CWT ridge persistence (scales spanned, or null)
            cwt_snr : float         — max SNR along the CWT ridge (or null)
            status : str            — LIKELY, MARGINAL, NOT_FOUND, or MASKED

    Returns
    -------
    dict with keys:
        path : str        — absolute path of the written file
        n_lines : int     — number of lines written
    """
    import csv
    import os

    columns = [
        "name", "rest_wavelength", "predicted_obs", "fitted_center",
        "fitted_center_err", "amplitude", "amplitude_err", "fitted_sigma",
        "fwhm_km_s", "ridge_length", "cwt_snr", "status",
    ]

    os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(lines)

    return {
        "path": file_path,
        "n_lines": len(lines),
    }


# ---------------------------------------------------------------------------
# Tool 7: grep_kb — search knowledge base and skill files
# ---------------------------------------------------------------------------

from pathlib import Path as _Path

_SKILLS_DIR = _Path(__file__).resolve().parent / "skills"

_GREP_FILES: dict[str, _Path] = {
    "kb/classification.md": _SKILLS_DIR / "kb" / "classification.md",
    "kb/ionization.md": _SKILLS_DIR / "kb" / "ionization.md",
    "kb/lines.md": _SKILLS_DIR / "kb" / "lines.md",
    "synthesize_skill.md": _SKILLS_DIR / "synthesize_skill.md",
    "targeted_search_skill.md": _SKILLS_DIR / "targeted_search_skill.md",
}

_grep_cache: dict[str, str] | None = None


def _load_grep_files() -> dict[str, str]:
    """Lazy-load and cache the searchable files."""
    global _grep_cache
    if _grep_cache is None:
        _grep_cache = {}
        for name, path in _GREP_FILES.items():
            if path.exists():
                _grep_cache[name] = path.read_text(encoding="utf-8")
    return _grep_cache


@tool
def grep_kb(pattern: str, A: int = 0, B: int = 0, C: int = 0) -> dict:
    """Search the knowledge base and skill files with optional context lines.

    Use this to check what the pipeline methodology, physics rules, and
    classification criteria actually say. Essential for distinguishing
    ``skill_gap`` (rule missing from doc) from ``llm_error`` (LLM ignored
    a documented rule).

    Parameters
    ----------
    pattern : str
        grep-compatible regex pattern (case-insensitive). Examples:
        "abstain", "doublet", "Ca K", "Dn4000", "ELG|LRG|QSO", "fatal"
    A : int (default 0)
        Lines to show AFTER each match (like grep -A).
    B : int (default 0)
        Lines to show BEFORE each match (like grep -B).
    C : int (default 0)
        Lines to show before AND after each match (like grep -C).
        If both C and A/B are given, A and B take precedence.

    Returns
    -------
    dict mapping filename to a list of match blocks, where each block is
    a list of "L<num>: <text>" strings. Returns {"(no matches)": []} if
    nothing matched.
    """
    files = _load_grep_files()
    context_before = max(B, C)
    context_after = max(A, C)

    results: dict[str, list] = {}

    for fname, content in files.items():
        lines = content.split("\n")
        n_lines = len(lines)

        # Find match line numbers (1-indexed)
        match_line_nums: set[int] = set()
        for i, line in enumerate(lines, start=1):
            if re.search(pattern, line, re.IGNORECASE):
                match_line_nums.add(i)

        if not match_line_nums:
            continue

        # Expand to context windows, merge overlapping
        windows: list[tuple[int, int]] = []
        for ln in sorted(match_line_nums):
            start = max(1, ln - context_before)
            end = min(n_lines, ln + context_after)
            windows.append((start, end))

        # Merge overlapping windows
        merged: list[tuple[int, int]] = []
        for start, end in windows:
            if merged and start <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))

        # Format each block
        blocks: list[list[str]] = []
        for start, end in merged:
            block: list[str] = []
            for ln in range(start, end + 1):
                marker = ">" if ln in match_line_nums else " "
                block.append(f"L{ln}{marker}: {lines[ln - 1].strip()[:200]}")
            blocks.append(block)

        # Cap total output per file (30 match lines max, then 20 blocks max)
        total_matches = sum(
            sum(1 for line in block if line[4] == ">") for block in blocks
        )
        if total_matches > 30 or len(blocks) > 20:
            blocks = blocks[:20]

        results[fname] = blocks

    if not results:
        return {"(no matches)": []}

    return results


# ---------------------------------------------------------------------------
# Tool 8: compute_redshift
# ---------------------------------------------------------------------------

@tool
def compute_redshift(observed_wavelength: float, rest_wavelength: float) -> dict:
    """Compute the redshift implied by an observed spectral line.

    z = λ_obs / λ_rest − 1

    Use this to check whether a fitted line center is consistent with the
    verification window: compute z from the fitted center, then check if
    z ∈ [z_min, z_max].

    Parameters
    ----------
    observed_wavelength : float
        The observed wavelength in Å (e.g. the fitted center from fit_peak).
    rest_wavelength : float
        The rest-frame wavelength of the line in Å.

    Returns
    -------
    dict with keys:
        redshift : float       — implied redshift
        rest_wavelength : float
        observed_wavelength : float
        in_window : bool or null — True/False if z_min, z_max are known, else None
    """
    z = observed_wavelength / rest_wavelength - 1.0
    return {
        "redshift": round(z, 6),
        "rest_wavelength": rest_wavelength,
        "observed_wavelength": observed_wavelength,
    }
