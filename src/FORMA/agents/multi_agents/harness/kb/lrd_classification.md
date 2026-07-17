# LRD-vs-Classical-AGN Classification (Stage B)

Draft source: `lrd_adapt/kb_drafts/lrd_line_identity_rules.md`'s Stage B
section. This file is the counterpart `kb/classification.md` explicitly
deferred: once Stage A confirms a line identity and redshift, and external
evidence is available (`lrd_adapt/evidence/external_evidence.py`, surfaced
in the single-hypothesis user prompt's "External Evidence" block), this is
where the actual object classification gets decided.

## Related Knowledge

- Stage A line-identity diagnostics and scope boundary: see `kb/classification.md`
- Redshift anchoring and broad-line-reality requirement: see `kb/ionization.md`
- He I+Paγ blend: see `kb/composite_profile.md`

## Preconditions — do not attempt this without them

This classification requires ALL of:
1. A confirmed line identity and redshift from Stage A (Hypothesis Synthesis's `classification=LineIDConfirmed`).
2. A broad-line-reality result, if the source's line shows apparent broadening (`_fit_broadline_lsf_bic`, ΔBIC > 10 over the null to trust it).
3. External evidence for this source (`external_evidence` — may be absent; if so, stop at `Unknown`, do not guess from the spectrum alone).

If any of these is missing, the correct output is `Unknown` (or `LineIDAmbiguous`, carried over from Stage A) with a note on what's missing — not a best-effort guess. A wrong LRD/AGN call is worse than an honest "insufficient evidence."

## Primary diagnostic: He I/Paγ excitation ratio

**He I/Paγ > 2.3 → classical AGN** (Brinchmann 2023 calibration). In
Kapoor+26, all paper LRDs fall below this threshold, all paper classical
AGNs above it. Anti-correlates with Balmer-break strength (interpreted as
He I self-absorption at high gas column — a strong Balmer break with a low
He I/Paγ ratio is mutually reinforcing evidence for LRD).

**Requires both He I and Paγ to be confirmed, independent, in-window
detections** (see `kb/classification.md`'s He I+Paγ pair guidance) — a
ratio computed from one confirmed line and one MARGINAL/NOT_FOUND line is
not reliable enough to anchor a classification. If only one component is
solid, note the ratio as indicative but not decisive, and lean on the other
diagnostics below.

## Secondary diagnostic: Balmer break

f_nu,4050/f_nu,3650 ≈ 1–4 across the LRD sample. A strong break (higher in
this range) combined with a low He I/Paγ ratio is the LRD-consistent
picture (self-absorption at high column density). A weak or absent break
does not by itself argue for classical AGN — treat it as corroborating
evidence for whichever way the He I/Paγ ratio already points, not an
independent tiebreaker.

## Weighting-only diagnostic: template fits

LRD-stack vs dust-reddened-QSO template fits, |ΔBIC| > 10, are a
**weighting input, not a decision rule**. Kapoor+26 explicitly found
template fits alone insufficient — 9 of 13 LRD-template-fitted sources were
reclassified as AGN using the fuller diagnostic set, and 2 X-ray-detected
sources were LRD-template-fitted despite X-ray detection being classical-
AGN evidence. **Do not let a template fit override the He I/Paγ ratio or
Balmer break.** If a template result contradicts them, trust the ratio and
break, and note the contradiction as a caveat rather than silently
resolving it either way.

## Weighting-only diagnostic: compactness

r_circ ≈ 100 mas (LRDs) vs 150–200 mas (classical AGN), F356W, no PSF
deconvolution. Corroborating, not decisive on its own — a compact source
with a high He I/Paγ ratio should still be called classical AGN; size adds
context, it doesn't override the excitation-based call.

## Disqualifiers / caveats (never treat absence as evidence)

- **Strong blue-skewed He I outflow wings** (velocities up to >1000 km/s):
  flag explicitly, may indicate a different physical regime than either
  clean LRD or clean classical-AGN — note it, don't force a binary call.
- **X-ray detection**: classical-AGN evidence when present. **Absence of
  X-ray coverage is NOT absence of emission** — some fields (per
  `external_evidence`'s `xray.coverage` flag) have no X-ray data at all.
  Check `coverage` before treating a non-detection as informative. A
  `coverage=false` entry must NEVER be used as evidence for LRD (or
  anything else) — it means "no information," full stop.

## Output

Add a `classification` field (distinct from Stage A's `LineIDConfirmed`/
`LineIDAmbiguous`/`Unknown`) to the Stage B verdict:

- `LRD` — He I/Paγ ratio and available corroborating evidence consistently point this way.
- `ClassicalAGN` — ratio > 2.3 and/or X-ray detected, corroborating evidence consistent.
- `Ambiguous` — diagnostics disagree, or the ratio sits near the 2.3 threshold with no strong corroboration either way.
- `Unknown` — preconditions not met (see above), or no external evidence available for this source.

Every classification must cite which diagnostic(s) drove it and note any
that pointed the other way or weren't available — this is a verification
pipeline auditing a claim, not a black-box classifier; the reasoning trail
matters as much as the label.

## Stress cases (do not tune around them)

Referred to generically per the Anonymization rule — never by J-name in
agent-visible text:

- **A transitional/ambiguous source**: faint+compact but blue, with a high
  inferred He I/Paγ ratio despite otherwise LRD-like properties. Correct
  behavior is `Ambiguous`, not a confident call forced by any single
  diagnostic.
- **A contamination-dominated source**: don't let contamination in the
  continuum (used for Balmer break / photometry) suppress an otherwise
  solid He I/Paγ-based call — but do flag the contamination as a caveat on
  the Balmer-break input specifically.
