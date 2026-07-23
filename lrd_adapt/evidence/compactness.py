"""
Compactness (r_circ) from the DSCI direct-image cutout of a grizli
*.full.fits -- the measurement behind Kapoor+26's Fig. 5 criterion
(r_circ ~ 100 mas for LRDs vs 150-200 mas for classical AGNs, F356W,
explicitly NO PSF deconvolution).

Method, deliberately as simple as the paper's: half-light radius by
curve of growth. Centroid from flux-weighted moments in a background-
subtracted cutout, cumulative flux in growing circular apertures,
r_circ = radius enclosing 50% of the total within r_max. Pixel scale
from the DSCI WCS (CD matrix or CDELT), converted to mas.

SCHEMA CAVEAT (standing rule): written against grizli's documented
DSCI layout, validated on synthetic cutouts only. Real .full.fits from
eor1 (j1030/Extractions/) is the re-verification gate. Fails loudly
listing HDUs when DSCI is absent -- our own minimal synthetic
.full.fits files lacked DSCI until write_synthetic_full_fits grew one.
"""
from __future__ import annotations

import numpy as np
from astropy.io import fits

MAS_PER_DEG = 3.6e6


def _pixel_scale_mas(header):
    """Pixel scale in mas/px from a cutout header: CD matrix, PC+CDELT,
    or CDELT alone. Raises ValueError when none is present."""
    if "CD1_1" in header:
        cd11 = header["CD1_1"]
        cd12 = header.get("CD1_2", 0.0)
        scale_deg = float(np.hypot(cd11, cd12))
    elif "CDELT1" in header:
        scale_deg = abs(float(header["CDELT1"]))
        if "PC1_1" in header:
            scale_deg *= float(np.hypot(header["PC1_1"], header.get("PC1_2", 0.0)))
    else:
        raise ValueError(
            "No CD1_1/CDELT1 in DSCI header -- cannot determine pixel "
            f"scale. Header keys: {sorted(set(header.keys()))}"
        )
    return scale_deg * MAS_PER_DEG


def half_light_radius_pix(data, r_max_pix=None, bg_annulus_frac=0.9,
                          supersample=4):
    """
    Curve-of-growth half-light radius in pixels.

    Background is estimated as the median outside `bg_annulus_frac` of
    the maximum usable radius and subtracted first. Apertures are
    evaluated on a `supersample`x-finer subpixel grid -- at NIRCam LW's
    63 mas/px a compact LRD's r50 is only ~1.6 px, and whole-pixel
    apertures bias r50 high by ~15% there. Returns (r50_pix,
    diagnostics dict). Raises ValueError on non-positive total flux
    (no detectable source in the cutout).
    """
    data = np.asarray(data, dtype=float)
    data = np.where(np.isfinite(data), data, 0.0)
    ny, nx = data.shape

    yy, xx = np.mgrid[0:ny, 0:nx]
    r_edge = min(nx, ny) / 2.0 - 1.0
    if r_max_pix is None:
        r_max_pix = r_edge

    # centroid from a first-pass window around the peak
    peak_y, peak_x = np.unravel_index(np.argmax(data), data.shape)
    w = max(3, int(r_max_pix // 4))
    y1, y2 = max(0, peak_y - w), min(ny, peak_y + w + 1)
    x1, x2 = max(0, peak_x - w), min(nx, peak_x + w + 1)
    stamp = np.clip(data[y1:y2, x1:x2], 0, None)
    if stamp.sum() <= 0:
        raise ValueError("No positive flux around the peak; empty cutout?")
    cy = float((np.mgrid[y1:y2, x1:x2][0] * stamp).sum() / stamp.sum())
    cx = float((np.mgrid[y1:y2, x1:x2][1] * stamp).sum() / stamp.sum())

    r = np.hypot(yy - cy, xx - cx)
    bg = float(np.median(data[r > bg_annulus_frac * r_edge]))
    clean = data - bg

    # subpixel grid: each pixel split into supersample^2 equal cells,
    # so aperture membership is decided per subpixel, not per pixel.
    # Subpixel center k (0-based, along one axis) sits at (k + 0.5)/s - 0.5
    # in original pixel coordinates.
    s = int(supersample)
    clean_ss = np.repeat(np.repeat(clean, s, axis=0), s, axis=1) / (s * s)
    coords_y = (np.arange(ny * s) + 0.5) / s - 0.5
    coords_x = (np.arange(nx * s) + 0.5) / s - 0.5
    r_ss = np.hypot(coords_y[:, None] - cy, coords_x[None, :] - cx)

    in_ap = r_ss <= r_max_pix
    if float(clean_ss[in_ap].sum()) <= 0:
        raise ValueError(
            "Non-positive background-subtracted flux inside r_max -- "
            "no measurable source."
        )

    # exact growth curve: sort subpixels by radius, raw cumulative flux
    order = np.argsort(r_ss[in_ap].ravel())
    r_sorted = r_ss[in_ap].ravel()[order]
    cum_flux = np.cumsum(clean_ss[in_ap].ravel()[order])

    def _r50_within(r_lim):
        n_in = int(np.searchsorted(r_sorted, r_lim, side="right"))
        total = float(cum_flux[n_in - 1])
        if total <= 0:
            raise ValueError("Non-positive flux inside adaptive aperture.")
        return float(np.interp(0.5 * total, cum_flux[:n_in], r_sorted[:n_in])), total

    # Adaptive aperture: a compact source in a large cutout accumulates
    # sky noise over the whole aperture area, biasing r50 high (the
    # noiseless measurement is exact to ~3%). Standard curve-of-growth
    # practice: refine the total-flux aperture to 4 x r50.
    r50, total = _r50_within(r_max_pix)
    for _ in range(2):
        r_lim = max(4.0 * r50, 3.0)
        if r_lim >= r_max_pix:
            break
        r50, total = _r50_within(r_lim)

    return r50, {
        "centroid_xy": (cx, cy),
        "background": bg,
        "total_flux": total,
        "r_max_pix": float(r_max_pix),
        "supersample": s,
    }


def measure_compactness(full_fits_path, extname="DSCI"):
    """
    r_circ (half-light radius, mas) from a grizli *.full.fits DSCI
    cutout. Returns a provenance-tagged dict for external evidence.
    Raises ValueError (listing HDUs present) when the extension is
    missing -- fail loudly, never guess.
    """
    with fits.open(full_fits_path) as hdul:
        try:
            hdu = hdul[extname]
        except KeyError:
            found = [h.name for h in hdul]
            raise ValueError(
                f"{full_fits_path}: no {extname} extension (HDUs present: "
                f"{found}). Real grizli run_fit=True products carry DSCI; "
                "minimal synthetic files may not."
            ) from None
        data = np.asarray(hdu.data, dtype=float)
        scale_mas = _pixel_scale_mas(hdu.header)

    r50_pix, diag = half_light_radius_pix(data)
    r_circ_mas = r50_pix * scale_mas

    return {
        "provenance": "ours",
        "source_file": str(full_fits_path),
        "r_circ_mas": round(float(r_circ_mas), 1),
        "r50_pix": round(r50_pix, 2),
        "pixel_scale_mas": round(float(scale_mas), 2),
        "method": (
            "curve-of-growth half-light radius on the DSCI cutout, no PSF "
            "deconvolution (matches Kapoor+26 Fig. 5 convention: "
            "LRD ~100 mas vs classical AGN 150-200 mas in F356W)"
        ),
        **{f"diag_{k}": v for k, v in diag.items()},
    }
