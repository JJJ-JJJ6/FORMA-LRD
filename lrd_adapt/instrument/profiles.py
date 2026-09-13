"""
Concrete instrument profiles and the lookup registry.

F356W_EIGER is the reference profile: it must reproduce the pipeline's
existing hardcoded behaviour exactly, which
``lrd_adapt/instrument/test_profile.py`` asserts.
"""
from __future__ import annotations

from typing import Dict

from lrd_adapt.instrument.profile import (
    InstrumentProfile,
    slitless_lsf_model,
    unsupported_lsf_model,
)

# --------------------------------------------------------------------- F356W

# Detector-frame scales (CLAUDE.md science constants, finding #10). These pair
# with each other: 1 NIRCam LW detector pixel is 0.063" spatially and disperses
# ~9.8 A in F356W. Used for slitless-LSF physics ONLY.
_F356W_DETECTOR_DISPERSION = 9.8
_F356W_SPATIAL_PIXEL_ARCSEC = 0.063

# Extracted-grid spacing, MEASURED 2026-09-13 from a real grizli product
# (j1030_01539.1D.fits, median |diff(wave)| = 19.778 A). grizli's extraction
# grid is ~2x binned relative to the detector. This -- not the 9.8 above -- is
# the number that governs CWT scales, and conflating the two is what made
# f356w.env's CWT_MAX_SCALE comment wrong by a factor of 2.
_F356W_EXTRACTED_DISPERSION = 19.78

F356W_EIGER = InstrumentProfile(
    name="F356W",
    description="EIGER / JWST NIRCam F356W wide-field slitless spectroscopy",
    bandpass_ang=(31500.0, 39500.0),
    detector_dispersion_ang_per_px=_F356W_DETECTOR_DISPERSION,
    spatial_pixel_arcsec=_F356W_SPATIAL_PIXEL_ARCSEC,
    extracted_dispersion_ang_per_px=_F356W_EXTRACTED_DISPERSION,
    R_point_source=1600.0,
    lsf_sigma_ang=slitless_lsf_model(
        R_point=1600.0,
        detector_dispersion_ang_per_px=_F356W_DETECTOR_DISPERSION,
        spatial_pixel_arcsec=_F356W_SPATIAL_PIXEL_ARCSEC,
        # midpoint of CLAUDE.md's quoted 400-600 effective range
        R_extended_fallback=500.0,
    ),
    photometry_filters={
        # JWST NIRCam approximate passbands (Angstrom), for the evidence
        # tools' bracketing logic. Working values.
        "F090W": (7960.0, 10120.0),
        "F115W": (10130.0, 12830.0),
        "F150W": (13310.0, 16710.0),
        "F200W": (17550.0, 22270.0),
        "F277W": (24230.0, 31430.0),
        "F356W": (31350.0, 39810.0),
        "F444W": (38810.0, 49820.0),
    },
    data_quality_model="wfss_contamination",
)


# ------------------------------------------------------------------ registry

_REGISTRY: Dict[str, InstrumentProfile] = {
    F356W_EIGER.name.upper(): F356W_EIGER,
}


def register(profile: InstrumentProfile, overwrite: bool = False) -> None:
    """Add a profile to the registry."""
    key = profile.name.upper()
    if key in _REGISTRY and not overwrite:
        raise ValueError(
            f"An instrument profile named '{profile.name}' is already "
            f"registered; pass overwrite=True to replace it."
        )
    _REGISTRY[key] = profile


def get_profile(name: str) -> InstrumentProfile:
    """Look up a profile by ARM_NAME, case-insensitively."""
    key = str(name).upper()
    if key not in _REGISTRY:
        raise KeyError(
            f"No instrument profile registered for '{name}'. Known profiles: "
            f"{sorted(_REGISTRY)}. Add one in lrd_adapt/instrument/profiles.py "
            f"-- note that lsf_sigma_ang must be a real per-instrument model, "
            f"not a copy of the slitless one (see profile.py)."
        )
    return _REGISTRY[key]


def available_profiles() -> list[str]:
    return sorted(_REGISTRY)


def profile_from_env(env: dict | None = None) -> InstrumentProfile:
    """Resolve the active profile from ARM_NAME, defaulting to F356W.

    Keeps existing single-arm .env setups working untouched.
    """
    import os

    src = env if env is not None else os.environ
    return get_profile(src.get("ARM_NAME", "F356W"))
