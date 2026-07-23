"""
Reader for grizli *.full.fits redshift-fit products -- the file behind
specvizitor's automated "Redshift" field (produced only by extract()'s
run_fit=True path; see extract_all.py Step 2/3).

Extracts the fitted redshift + uncertainty in the shape the hypothesis
provider's external-z-prior mechanism consumes (state fields
external_z_prior / external_z_prior_sigma / external_z_prior_source,
commit 41d785d): grizli's automated template fit is exactly the kind of
external prior that mechanism exists for -- it is NOT ground truth, and
must never be treated as more than one more piece of evidence.

SCHEMA CAVEAT (do not delete): this reader was written against grizli's
documented .full.fits layout and validated only on synthetic files
(lrd_adapt/eval/synthetic_injection.write_synthetic_full_fits) -- no
real .full.fits existed on this machine when it was built. It therefore
parses DEFENSIVELY: the preferred path is the ZFIT table's own
zgrid/pdf columns (computing MAP + percentile widths from the pdf
itself, no reliance on header keywords), the fallback is a REDSHIFT
header keyword (z only, no width), and anything else raises with a
listing of what the file actually contains instead of guessing.
Re-verify against the first REAL .full.fits from eor1 before trusting
in production -- that check is still open.
"""
from __future__ import annotations

import numpy as np
from astropy.io import fits

# grizli writes ZFIT_STACK (stacked-spectrum fit) and sometimes ZFIT_BEAM;
# prefer the stack fit, but accept any BinTable carrying zgrid+pdf.
_PREFERRED_ZFIT_EXTNAMES = ("ZFIT_STACK", "ZFIT_BEAM")


def _find_zfit_hdu(hdul):
    candidates = [
        h
        for h in hdul
        if isinstance(h, fits.BinTableHDU)
        and h.data is not None
        and {"zgrid", "pdf"}.issubset({n.lower() for n in h.columns.names})
    ]
    for name in _PREFERRED_ZFIT_EXTNAMES:
        for h in candidates:
            if h.name.upper() == name:
                return h
    return candidates[0] if candidates else None


def read_grizli_zfit(path):
    """
    Read a grizli *.full.fits (or schema-compatible) redshift fit.

    Returns
    -------
    dict with keys:
        z            : best redshift (pdf maximum a posteriori when a pdf
                       is present, else the REDSHIFT header value)
        z_sigma      : half the pdf's 16-84 percentile width, or None when
                       only a header redshift was available
        z16, z50, z84: pdf percentiles (None on the header-only path)
        method       : "zfit_pdf" or "header_redshift"
        source_file  : the path read

    Raises
    ------
    ValueError with a listing of the file's actual HDUs/columns if
    neither a zgrid/pdf table nor a REDSHIFT keyword is found -- fail
    loudly, never guess (see module docstring).
    """
    with fits.open(path) as hdul:
        zfit = _find_zfit_hdu(hdul)

        if zfit is not None:
            cols = {n.lower(): n for n in zfit.columns.names}
            zgrid = np.asarray(zfit.data[cols["zgrid"]], dtype=float).ravel()
            pdf = np.asarray(zfit.data[cols["pdf"]], dtype=float).ravel()
            order = np.argsort(zgrid)
            zgrid, pdf = zgrid[order], np.clip(pdf[order], 0, None)
            if len(zgrid) < 3 or not np.any(pdf > 0):
                raise ValueError(
                    f"{path}: ZFIT table '{zfit.name}' has unusable zgrid/pdf "
                    f"(n={len(zgrid)}, max pdf={pdf.max() if len(pdf) else 'n/a'})"
                )
            # Trapezoid CDF over the (possibly non-uniform) grid
            dz = np.diff(zgrid)
            bin_mass = 0.5 * (pdf[1:] + pdf[:-1]) * dz
            cdf = np.concatenate([[0.0], np.cumsum(bin_mass)])
            total = cdf[-1]
            if total <= 0:
                raise ValueError(f"{path}: ZFIT pdf integrates to zero")
            cdf /= total
            z16, z50, z84 = np.interp([0.16, 0.50, 0.84], cdf, zgrid)
            z_map = float(zgrid[int(np.argmax(pdf))])
            return {
                "z": z_map,
                "z_sigma": float((z84 - z16) / 2.0),
                "z16": float(z16),
                "z50": float(z50),
                "z84": float(z84),
                "method": "zfit_pdf",
                "source_file": str(path),
            }

        # Fallback: a bare fitted-redshift header keyword, no pdf
        for hdu in hdul:
            if "REDSHIFT" in hdu.header:
                return {
                    "z": float(hdu.header["REDSHIFT"]),
                    "z_sigma": None,
                    "z16": None,
                    "z50": None,
                    "z84": None,
                    "method": "header_redshift",
                    "source_file": str(path),
                }

        found = [
            f"{h.name}({'table:' + ','.join(h.columns.names) if isinstance(h, fits.BinTableHDU) and h.data is not None else type(h).__name__})"
            for h in hdul
        ]
        raise ValueError(
            f"{path}: no zgrid/pdf ZFIT table and no REDSHIFT keyword found. "
            f"HDUs present: {found}. If this is a real grizli product, its "
            "schema differs from what this reader was built against -- "
            "extend the reader against THIS file rather than working around it."
        )


def zfit_to_state_prior(path):
    """Convenience: read a *.full.fits and return the three values the
    provider's external-z-prior state fields take (z, sigma, source)."""
    result = read_grizli_zfit(path)
    return {
        "external_z_prior": result["z"],
        "external_z_prior_sigma": result["z_sigma"],
        "external_z_prior_source": f"grizli_zfit:{result['method']}",
    }
