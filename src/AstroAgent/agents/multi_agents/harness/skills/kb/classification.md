# Classification Diagnostics

## ELG (Emission Line Galaxy)

**Expected features**: Strong narrow emission lines ([O II], Hβ, [O III], Hα). Weak or absent absorption.
**Best systemic anchors**: [O II] 3727 (Priority 2), Hα (Priority 5).
**Ca K/H**: Weak or absent is EXPECTED. Do NOT exclude an ELG hypothesis because Ca K/H are MARGINAL or NOT_FOUND — ELGs have young stellar populations and weak metal absorption.
**Fatal problems**: Missing [O II], no Hβ, [O III] doublet spacing wrong.
**Dn4000**: Typically < 1.4 (young stellar population). This is a reference continuum metric, NOT a classification criterion.

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

## Ne V as AGN Indicator

Ne V (3426 Å) is a high-ionization forbidden line almost never present in non-AGN objects. However, it is a weak line easily mimicked by noise. Before using Ne V as AGN evidence:
1. **Read the spectrum ±50 Å** around the predicted observed wavelength using `read_spectrum_region`
2. Verify the feature is a genuine emission peak rising clearly above the local continuum, not a continuum wiggle or noise spike at the detection limit
3. **Weigh against Galaxy features**: Check whether Galaxy indicators (Ca K/H absorption doublet, narrow emission lines with correct doublet spacing) or AGN indicators dominate the spectrum. **Ne V CAN independently support QSO classification** — but only if the peak is visually convincing, not merely a CWT detection at the noise limit. If Galaxy features are clear and self-consistent while the Ne V feature is marginal, default to Galaxy.

## Mg II Emission vs Absorption Coexistence

Mg II (2800 Å) can appear as both broad emission (QSO BLR, FWHM > 2000 km/s) and narrow absorption (ISM, FWHM < 1000 km/s). When BOTH Mg II emission AND Mg II_abs are claimed near the same observed wavelength:

1. **Center coincidence check**: The emission and absorption centers must fall within each other's FWHM. If the centers are separated by more than the larger FWHM, the two features are unrelated — one is a misidentification.
2. **Absorption-dominant region**: If the CWT feature at the predicted Mg II position is narrow and in absorption (FWHM < 1000 km/s, negative amplitude), the nearby broad "Mg II emission" is likely a CWT artifact from overfitting continuum noise. In such cases, do NOT use Mg II emission as AGN evidence.
3. **Default rule**: In ambiguous cases, default to the absorption interpretation. Mg II ISM absorption is far more common than Mg II BLR emission in non-QSO objects. The Mg II emission claim requires POSITIVE evidence (clearly broad, clearly distinct from absorption).

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
