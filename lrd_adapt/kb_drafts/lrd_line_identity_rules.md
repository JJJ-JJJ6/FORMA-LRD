# LRD Domain KB Draft — Line-Identity & Redshift Verification (Stage A)

Provenance-tagged rule extraction for A4 (CLAUDE.md build order). Source
material is CLAUDE.md's own "Science constants" section, which is itself
extracted from Kapoor et al. 2026 (arXiv:2607.00084). Each rule below is
tagged `[paper]` (a claim/measurement from Kapoor+26), `[ours]` (a
methodological choice made adapting FORMA to this domain), or `[inferred]`
(reasonable extrapolation not explicitly stated in either source — flagged
for verification). Installed into `harness/kb/*.md` and the skill prompts
by this same session; re-derive from the two papers directly before the
KB freeze rather than trusting this file long-term.

## The six candidate line identities [paper, CLAUDE.md SS3.2]

Any single broad emission line detected in the F356W grism (3.15-3.95 um
observed) is intrinsically ambiguous among six rest-frame identities, each
implying a different systemic redshift:

| Identity | Rest lambda (vacuum, A) | z window in F356W |
|----------|------------------------|--------------------|
| Pa-alpha | 18756 | 0.68-1.10 |
| Pa-beta | 12822 | 1.45-2.08 |
| He I + Pa-gamma (blended) | 10833 / 10941 | 1.91-2.64 |
| [S III] | 9071, 9533 | 2.30-3.14 |
| O I 8446 | 8448.7 (vacuum; air label 8446) | 2.73-3.68 |
| H-alpha | 6564.6 | 3.80-5.02 |

`[ours]`: these six identities are exactly `lrd_adapt/hypothesis/lrd_hypothesis_provider.py`'s REST_WAVELENGTHS_ANG/REDSHIFT_WINDOWS (A1). The z-window is a hard constraint, not a soft prior — a hypothesis whose implied z falls outside its own line's window is a redshift-math contradiction, not a weak case.

## Broad-line reality test [paper, CLAUDE.md SS3.1/4.2; ours, A3 tool]

A claimed broad component is only real if `lrd_adapt/tools/broadline_lsf_bic.py`
(`fit_broadline_lsf_bic` / the `_fit_broadline_lsf_bic` agent tool) picks a
model other than `narrow_extended_lsf` with ΔBIC > 10 over that null. This
is the central Stage A check for any line flagged `both` width class in
`utils/line_tables.py` that visually looks broad. Do not accept "it looks
broad" without running the tool — extended-source LSF smearing (point R~1600,
extended effective R~400-600) can mimic real broadening from spatial extent
alone.

Observed LRD broad FWHM range: 1400-2100 km/s `[paper]`. A "broad_line_real"
verdict with a recovered FWHM far outside this range (order of magnitude off)
should be treated as suspicious — re-check the fit, not just accept it.

## Line characterization structure [paper, CLAUDE.md SS4.2]

- Broad component FWHM > 600 km/s (note: slightly different from the >500
  km/s reality threshold in SS3.1 — the >500 test is "is there a real broad
  component at all", the >600 figure characterizes it once confirmed).
- Narrow + broad Gaussians, velocity offsets bounded +/-300 km/s.
- He I and Pa-gamma's broad AND narrow components share the same velocity
  offsets (they're the same kinematic system, just different transitions).
- Optional blueshifted He I absorption (P-Cygni-like), modeled as a negative
  Gaussian, ΔBIC > 10 to accept. `[paper]`: only 2 of 19 sources show this
  (J1030_2735, J1148_21539) — do not expect it by default.
- Power-law continuum (not the fixed linear baseline `fit_peak`/`fit_doublet`
  use elsewhere in this codebase for optical lines) `[ours, A3]`: the BIC
  tool implements this as a 2-parameter power law pivoted at line center.

## AGN excitation demarcation — Stage B, not Stage A [paper, CLAUDE.md SS4.4]

**Installed into `kb/lrd_classification.md`** (Stage B build). The
sections below are kept here for provenance/traceability; treat
`kb/lrd_classification.md` as the source of truth for the actual
classification logic going forward.

He I/Pa-gamma > 2.3 => classical AGN (Brinchmann 2023 calibration); all
paper LRDs fall below this, all paper classical AGNs above. Anti-correlates
with Balmer-break strength (interpreted as He I self-absorption at high gas
column). `[ours]`: this ratio is reported (see `DOUBLET_DEFS` in
AnalysisAuditor.py, the He I/Paγ entry added for A4) but Stage A must NOT
gate any verdict on it — it's the input to Stage B's LRD-vs-classical-AGN
call once the external-evidence channel (CLAUDE.md build order B) exists.
Encoding it now, in Stage A, would be scope creep into work explicitly
deferred to Stage B.

## Templates, compactness, disqualifiers — also Stage B [paper, CLAUDE.md]

- LRD-stack vs dust-reddened-QSO template fits, |ΔBIC| > 10, but the paper
  itself says template fits alone are insufficient (9/13 template-LRD
  sources reclassified as AGN). Weighting rule, not decision rule.
- Compactness: r_circ ~100 mas (LRDs) vs 150-200 mas (classical AGN), F356W,
  no PSF deconvolution. `[ours, A3]`: the BIC tool's `r_circ_mas` parameter
  is wired to accept this directly if/when it's measured per source.
- X-ray detection, with the important caveat that absence of X-ray coverage
  != absence of X-ray emission — J159 and J0148 fields have NO X-ray
  coverage at all.
- Balmer break f_nu,4050/f_nu,3650 ~ 1-4 across the LRD sample.

None of the above belongs in Stage A verdicts. It's recorded here so it
isn't lost before Stage B, and so Stage A content doesn't accidentally
smuggle in a classification call it has no evidence to make yet.

## Stress cases — Stage A must handle these honestly [paper, CLAUDE.md]

- **J1030_9732**: the paper's own "transitional" source (faint+compact but
  blue, high He I/Pa-gamma). Correct Stage A behavior: calibrated
  uncertainty (UNCERTAIN or NEEDS-REVISION with low confidence), not a
  confident line-ID call either way.
- **J159_6107**: continuum dominated by grism contamination; the line is
  real. Tests contamination-vs-feature reasoning — see the converter's
  contam-derived mask bit (`lrd_adapt/converter/grizli_to_forma.py`, mask
  bit 4) and the FeatureAuditor's contamination-screening step.
- **J0148_9325**: z=3.18, no He I+Pa-gamma coverage (only O I + high-order
  Paschen visible). Tests reasoning under missing/incomplete evidence — the
  agent must not force-fit a He I+Pa-gamma identity onto data that can't
  show it.

## Anonymization reminder [paper/project rule, CLAUDE.md]

Agent-visible content (prompts, FITS headers, evidence blocks) carries only
anonymized SRC codes, never J-names/coordinates/field IDs — arXiv:2607.00084
is public and the base LLM may have memorized Table 1. This constrains how
skill prompts reference the stress cases above: name them generically
("a transitional/ambiguous source", "a contamination-dominated source") in
agent-facing text, not by J-name, even though this internal draft uses the
paper's own names for traceability.
