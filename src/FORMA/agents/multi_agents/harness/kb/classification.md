# Line-Identity Diagnostics (F356W Broad-Line Sample)

LRD domain (Kapoor+26 EIGER F356W survey). Draft source:
`lrd_adapt/kb_drafts/lrd_line_identity_rules.md`.

## Related Knowledge

- Redshift anchoring priority and consistency rules: see `kb/ionization.md`
- Line rest wavelengths, redshift windows, width classes: see `kb/lines.md`
- He I + Pa-gamma blend and blueshifted He I absorption: see `kb/composite_profile.md`

## Scope of this file — Stage A only

This pipeline verifies **line identity and redshift** (Stage A). It does
**not** decide LRD vs classical AGN — that call needs the external-evidence
channel (photometry, Balmer break, compactness, X-ray), which is a
*separate* stage (`kb/lrd_classification.md`, Stage B) that runs after this
one, using this stage's confirmed line identity as an input. Do not let the
He I/Pa-gamma ratio, template fits, compactness, or X-ray coverage drive a
Stage A verdict, even now that Stage B exists — keeping the two questions
separate is what makes each one auditable on its own terms.

## The six candidate identities

Every source in this sample is a known broad-line source (Kapoor+26 Table
1) with one dominant detected line. That line's observed wavelength is
consistent, in principle, with six different rest-frame identities — each
implying a different systemic redshift. See `kb/lines.md` for the exact
rest wavelengths and z-windows. In identity order (short to long rest
wavelength):

1. **O I 8446** (z window 2.73-3.68)
2. **[S III]** 9071/9533 (z window 2.30-3.14)
3. **He I + Pa-gamma** blend, 10833/10941 (z window 1.91-2.64)
4. **Pa-beta** 12822 (z window 1.45-2.08)
5. **Pa-alpha** 18756 (z window 0.68-1.10)
6. **H-alpha** 6564.6 (z window 3.80-5.02) — the one optical rival still
   reachable in this band at high z; if claimed, its usual companions
   ([N II]a/b, [S II]a/b) should be checked exactly as in the original
   DESI-domain diagnostics.

## Fatal problem: redshift-window violation

**This is the primary, almost mechanical, exclusion rule for this domain.**
Each candidate identity's rest wavelength and the source's tested redshift
together imply an observed wavelength; conversely, the observed wavelength
of the detected line implies a redshift *for each candidate identity*. A
hypothesis's implied z **must** fall inside that identity's F356W window
(`kb/lines.md`). If it doesn't, the hypothesis is a straightforward
redshift-arithmetic contradiction — reject it outright, no further
discussion needed. (The `lrd_adapt` hypothesis provider already filters to
in-window identities when generating rivals — a hypothesis reaching you
with an out-of-window z indicates an upstream bug, not just a weak case;
flag it as such rather than silently excluding it.)

## Fatal problem: broad-line-reality failure

If a hypothesis's identity depends on treating the detected line's width as
real velocity broadening (rather than spatial-extent smearing), that claim
**must** be checked with the `_fit_broadline_lsf_bic` tool (see
`kb/ionization.md` and the single-hypothesis skill for when to call it).
A "both" width-class line (see `kb/lines.md`) that fails this test — i.e.
the null model (`narrow_extended_lsf`) wins, or ΔBIC over the null is < 10
— means the apparent broadening is not established. This does not
necessarily kill the line-identity hypothesis (a narrow He I+Pa-gamma
detection is still a valid identity), but it does mean any downstream
reasoning that assumed "this is a genuine BLR line" is unsupported.

## He I + Pa-gamma blend

He I 10833 and Pa-gamma 10941 sit only 108 A apart (rest-frame) — well
resolved at R~1600 (~7 A instrumental FWHM at these wavelengths), unlike
[O II]'s sub-pixel doublet in the optical domain. Expect two distinguishable
components, not an unresolved single blob. See `kb/composite_profile.md`
for the blend-disentanglement procedure and the blueshifted-He I-absorption
case. **There is no fixed expected amplitude ratio between He I and
Pa-gamma** — unlike [O III]a/b or [N II]a/b, the ratio is itself the
Stage B diagnostic (He I/Pa-gamma > 2.3 => classical AGN). Do not treat an
unusual ratio as evidence against the identification at Stage A.

## Grism contamination (this domain's edge-zone analog)

JWST is space-based — there is no OH/OI atmospheric airglow and no
blue/red throughput-edge noise the way ground-based optical spectroscopy
has. The equivalent data-quality hazard here is **grism contamination**:
overlapping spectral traces from neighboring sources in the WFSS field.
`lrd_adapt/converter/grizli_to_forma.py` flags contamination-dominated
pixels in the FITS mask (bit 4, |contam| > contam_frac x |flux|). A
detected feature sitting in a masked/contaminated region should be treated
with the same caution DESI-domain analysis gave OH-zone features: real
astrophysical signal is possible even under contamination (see the
contamination-dominated-source stress case in
`kb_drafts/lrd_line_identity_rules.md` — real line, contaminated
continuum), so contamination is a caveat, not an automatic disqualifier.

## Stress cases (handle honestly, do not tune around them)

Referred to generically here per the Anonymization rule (CLAUDE.md) — never
by J-name in agent-visible text:

- **A transitional/ambiguous source**: faint and compact but blue, with an
  unusually high inferred He I/Pa-gamma ratio for its other properties.
  Correct behavior is calibrated uncertainty — do not force a confident
  line-ID or redshift call just because *some* line is detectable.
- **A contamination-dominated source**: continuum dominated by grism
  contamination, but the line itself is real. Do not let contamination in
  the continuum region bias the line-reality judgment downward.
- **A source with incomplete line coverage**: at some redshifts, only O I +
  high-order Paschen lines fall in-band (no He I/Pa-gamma coverage). Do not
  force-fit a He I+Pa-gamma identity onto a spectrum that structurally
  cannot show it — check which lines are even in the observed wavelength
  range before treating their absence as evidence.

## Final Classification Mapping (Stage A)

The final JSON verdict's classification-equivalent field must use one of:
- `LineIDConfirmed` — one identity uniquely and consistently explains the
  detected line(s) and redshift.
- `LineIDAmbiguous` — more than one identity remains viable after applying
  the redshift-window and broad-line-reality checks.
- `Unknown` — no candidate identity is credible given the data (e.g. no
  real feature detected at all, or the region is fully masked/contaminated).

Do NOT output an LRD/classical-AGN verdict in this field, even though
Stage B now exists — that call belongs in the separate `classification`
field the result-auditor stage adds (see `kb/lrd_classification.md`), not
here. Keeping them in different fields is what lets each be audited
independently.
