# Classification Diagnostics

## ELG (Emission Line Galaxy)

**Expected features**: Strong narrow emission lines ([O II], Hβ, [O III], Hα). Weak or absent absorption.
**Best systemic anchors**: [O II] 3727 (Priority 2), Hα (Priority 5).
**Ca K/H**: Weak or absent is EXPECTED. Do NOT exclude an ELG hypothesis because Ca K/H are MARGINAL or NOT_FOUND — ELGs have young stellar populations and weak metal absorption.
**Fatal problems**:
- **Missing [O II]** — see ionization consistency rule below ([O III] cannot exist without [O II])
- **[O III] doublet spacing wrong** — both components present but separation doesn't match 47.9×(1+z) Å
- **[O III] doublet orphan** — only one [O III] component detected (typically [O III]b) while the other is NOT_FOUND in a clean, unmasked region. With the expected b:a ≈ 3:1 ratio, if [O III]b is bright enough to be KEEP, [O III]a MUST be detectable. A missing partner means either the feature is NOT [O III] (wrong line ID, possibly OH skyline or different species) or the identification is physically unsound. **An orphan [O III] doublet IS a fatal problem — treat it with the same weight as wrong spacing.** Do not invoke "extreme [O III] ratio" to rescue the identification; extreme ratios are physically rare and the simpler explanation is misidentification.

**Balmer lines (Hβ, Hγ, Hδ)**: Frequently weak or undetectable in ELGs due to moderate SFR, dust extinction, or low SNR. A missing Balmer line is NOT a fatal problem and should NOT be used to reject an ELG hypothesis. Note the absence as a caveat, not a disqualifier.

**Ca K/H absorption**: Weak or absent is EXPECTED in ELGs — young stellar populations have weak metal absorption. Do NOT reject an ELG hypothesis because Ca K/H features are MARGINAL, NOT_FOUND, have wrong separation, or show inverted K/H ratio. Ca K/H is a PRIMARY diagnostic for LRG/BGS only; for ELG it carries zero exclusion weight. An ELG hypothesis with perfect emission-line consistency but failed Ca K/H should NOT be penalized — the emission lines drive the classification.
**Dn4000**: Typically < 1.4 (young stellar population). This is a reference continuum metric, NOT a classification criterion.

### [O III] without [O II] — Ionization Inconsistency

[O II] 3727 requires singly-ionized oxygen (O⁺, ~13.6 eV). [O III] 4960/5008 requires doubly-ionized oxygen (O⁺⁺, ~35.1 eV). Producing O⁺⁺ **requires** passing through the O⁺ ionization stage. A physical ELG spectrum cannot have [O III] detected while [O II] is genuinely absent — the ionizing source that creates O⁺⁺ must also create abundant O⁺.

When a hypothesis claims [O III] as LIKELY but [O II] is NOT_FOUND or MASKED:

1. **The [O III] identification is suspect.** Either the features are not actually [O III] (wrong redshift, wrong line ID, or OH skylines with coincidentally correct spacing), or [O II] is hiding in a masked region or low-SNR zone.
2. **If [O II] falls in an unmasked, readable region** (not at the spectrum edge, not in a known bad-pixel zone), the absence of [O II] is a strong argument against the ELG hypothesis. This should be weighted similarly to a doublet spacing failure.
3. **Do not accept the [O III] doublet spacing as sufficient** if [O II] is missing from clean spectral regions. The doublet spacing match can be coincidental — OH skylines in the red zone can mimic [O III] spacing at certain redshifts. [O II] provides independent confirmation.
4. **Exception**: If [O II] falls in a masked region or at the extreme blue/red edge where SNR is known to be poor, note the caveat but do not reject on this basis alone.

## LRG/BGS (Luminous Red Galaxy / Bright Galaxy Sample)

**Expected features**: Strong stellar absorption (Ca K/H, G-band, Mg I, Na D). Weak or no emission lines.
**Best systemic anchors**: Ca K/H_abs, G-band_abs (Priority 1).
**Ca K/H**: MUST appear together with Ca K deeper than Ca H. Missing partner → hard exclusion.
**4000 Å break**: Should be present at 4000×(1+z) Å. Dn4000 > 1.6 is characteristic of old stellar populations but is a REFERENCE metric only — a low Dn4000 does NOT disqualify LRG/BGS classification. Note any Dn4000 inconsistency in caveats.
**Fatal problems**: Ca K/H missing (one present without the other AND no 4000 Å break observed), broad emission lines with FWHM > 2000 km/s detected by AGN line checks (see below).

## QSO (Quasar / Type 1 AGN)

**Expected features**: Broad emission lines (Lyα, C IV, C III], Mg II) with FWHM > 2000 km/s. Narrow forbidden lines may also be present.
**Best systemic anchors**: Mg II 2800 (Priority 6), [O II] (Priority 2) if narrow component visible.
**Lyα**: May be fragmented by IGM absorption into 2–3 apparent peaks — all support the hypothesis.
**Amplitude ordering**: Lyα > C IV > C III] > Mg II. Deviations suggest misidentification.
**Fatal problems**: All claimed broad lines are narrow (FWHM < 1000 km/s), Lyα and C IV missing at predicted positions.

## Star

**Expected features**: Broad absorption lines, no emission lines, may show 4000 Å break.
**Distinction from LRG**: Star spectra show broader and deeper Balmer absorption than LRGs.
**If suspected**: Flag as UNKNOWN rather than committing if evidence is marginal.

## [Ne V] as AGN Indicator

[Ne V] (3426 Å) is a high-ionization forbidden line almost never present in non-AGN objects. However, it is a weak line easily mimicked by noise. Before using [Ne V] as AGN evidence:
1. **Read the spectrum ±50 Å** around the predicted observed wavelength using `read_spectrum_region`
2. Verify the feature is a genuine emission peak rising clearly above the local continuum, not a continuum wiggle or noise spike at the detection limit
3. **Weigh against Galaxy features**: Check whether Galaxy indicators (Ca K/H absorption doublet, narrow emission lines with correct doublet spacing) or AGN indicators dominate the spectrum. **[Ne V] CAN independently support QSO classification** — but only if the peak is visually convincing, not merely a CWT detection at the noise limit. If Galaxy features are clear and self-consistent while the [Ne V] feature is marginal, default to Galaxy.

## Mg II Emission vs Absorption Coexistence

Mg II (2800 Å) can appear as both broad emission (QSO BLR, FWHM > 2000 km/s) and narrow absorption (ISM, FWHM < 1000 km/s). When BOTH Mg II emission AND Mg II_abs are claimed near the same observed wavelength, they may form an **emission–absorption composite profile** — a broad emission line split by a central absorption trough, producing a characteristic "M" shape in the spectrum.

**Do NOT evaluate Mg II emission and Mg II_abs as independent features.** They may be the two halves of a single physical system. For the complete diagnostic criteria, see `kb/composite_profile.md`. Key principles:

1. **Morphology over individual detections**: Read the full region (±200 Å). A genuine composite shows a broad, symmetric "M" with smooth wings. Spike–valley–spike patterns or asymmetric structures are likely noise.
2. **Default rule**: In ambiguous cases (no clear "M" shape), default to the absorption interpretation. Mg II ISM absorption is far more common than Mg II BLR emission in non-QSO objects. The Mg II emission claim requires POSITIVE morphological evidence.

This composite-profile logic also applies to Hα + Hα_abs and Hβ + Hβ_abs systems.

## Broad Lines in Non-AGN Galaxies

In ELG/LRG, genuine broad emission lines (Lyα, C IV, C III], Mg II) do not appear. If CWT labels a feature as `broad` matching these lines in a galaxy hypothesis, suspect CWT artifact:
- Overfitting of the continuum between absorption troughs
- Fragmentation of a narrow line by noise
- Spurious wide Gaussian from poor baseline fit

Flag such cases for the synthesis agent — do not accept a broad classification in a galaxy hypothesis without noting the caveat.

## Final Classification Mapping

The knowledge base uses sub-type labels (ELG, LRG/BGS, Host Galaxy dominated AGN) for internal reasoning and physical diagnostics. The final JSON verdict classification must map to ONLY these top-level categories:
- All galaxy sub-types (ELG, LRG, BGS, LRG/BGS, composite) → `Galaxy`
- Host Galaxy dominated AGN → `QSO`
- Pure QSO / Type 1 AGN → `QSO`
- Unclassifiable → `Unknown`

## Cross-Type Evidence Weight

- **LRG vs LRG**: Absorption lines (Ca K/H, G-band, Mg, Na D) are primary discriminators. Emission lines secondary.
- **ELG vs ELG**: Emission lines ([O II], Hβ, [O III], Hα) are primary discriminators. Absorption lines secondary.
- **Cross-type** (LRG vs ELG vs QSO): Neither evidence type inherently more trustworthy. Judge each hypothesis on its own internal physical consistency, then compare completeness and coherence of line inventories. A hypothesis with 3 LIKELY absorption lines at consistent z is comparable to one with 3 LIKELY emission lines at consistent z — the deciding factor should be physical diagnostics (line ratios, ionization consistency, continuum features), not line count.
