# Redshift Anchoring & Consistency Rules (LRD domain)

LRD domain (Kapoor+26 EIGER F356W survey). Draft source:
`lrd_adapt/kb_drafts/lrd_line_identity_rules.md`.

## Related Knowledge

- Line-identity fatal problems and the redshift-window exclusion rule: see `kb/classification.md`
- Line rest wavelengths, redshift windows, width classes: see `kb/lines.md`

## Why this domain anchors differently than the optical DESI case

The original (optical) ionization-priority table ranked lines by ionization
potential because a typical DESI spectrum shows *many* lines at once, and
the lowest-ionization one is the most reliable systemic-z anchor. This
sample is different: each source is a single dominant broad-line detection
(Kapoor+26 Table 1), usually with at most one or two companion lines in
band. There usually isn't a choice of anchor line — the detected line IS
the anchor, and the open question is *which rest-frame line it is*, not
*which of several detected lines to trust*.

## The redshift-window constraint (primary anchoring rule)

Each candidate identity carries a hard F356W redshift window (`kb/lines.md`).
An implied z outside that window is not weak evidence against a hypothesis —
it is a redshift-arithmetic impossibility, since the line's rest wavelength
could not have produced the observed wavelength at that z. Apply this before
any other consistency check. See `kb/classification.md` for the exclusion
procedure.

## Broad-line-reality as a consistency check

Before treating an apparent line width as evidence of anything (real BLR
gas, outflow, disqualifying a narrow-only hypothesis), confirm it with the
`_fit_broadline_lsf_bic` tool. It compares:

- `narrow_extended_lsf` — the null: apparent width is spatial-extent
  smearing, not real velocity structure.
- `narrow_broad_point_lsf` / `mixed` — real broad component, ΔBIC > 10 over
  the null required to accept.

Extended-source LSF effectively softens the resolving power from R~1600
(point source) to R~400-600 (Kapoor+26's quoted effective range) — enough
that a spatially-extended source's narrow line alone can *look* broadened
without any real velocity structure. This is the LRD-domain analog of the
old "width mismatch" check, but it requires a model comparison, not a
visual width estimate.

## Velocity-offset consistency (Kapoor+26 SS4.2)

When both a narrow and a broad component are fit for the same line (or
for He I and Pa-gamma together — see `kb/composite_profile.md`), their
velocity offsets should be consistent, bounded to +/-300 km/s. He I and
Pa-gamma's broad components (and separately their narrow components)
should share offsets, since they arise from the same kinematic gas system.
A broad component with an offset far outside this range, or offsets that
disagree wildly between He I and Pa-gamma, is a red flag for the
identification (possibly a different line entirely, or a fitting
degeneracy) — but as with everything else in this domain, verify with the
BIC tool rather than eyeballing a plot.

## Blueshifted He I absorption (rare, do not expect by default)

A subset of sources show blueshifted He I absorption (outflow-like,
P-Cygni-style), modeled as a negative Gaussian component. Kapoor+26 find
this in only 2 of 19 sources — treat it as a real but uncommon feature,
requiring its own ΔBIC > 10 to accept, not something every He I detection
should be checked for by default. See `kb/composite_profile.md`.

## What is explicitly NOT a Stage A consistency rule

The He I/Pa-gamma amplitude ratio, Balmer-break strength, template fits,
compactness (r_circ), and X-ray coverage are all Stage B (LRD-vs-classical-
AGN) inputs, not Stage A (line-identity/redshift) consistency checks. Do
not use them to accept or reject a line-identity hypothesis here — see
`kb/classification.md`'s scope note and `kb_drafts/lrd_line_identity_rules.md`
for why this boundary matters (build order keeps classification-with-
external-evidence as a separate, later stage, deliberately).

## Grism contamination

See `kb/classification.md`'s "Grism contamination" section. A detected
feature in a masked/contaminated pixel region is not automatically
unreliable — real signal can and does appear under contamination.
