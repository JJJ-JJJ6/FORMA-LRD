# Classification Diagnostics

## ELG (Emission Line Galaxy)

**Expected features**: Strong narrow emission lines ([O II], Hβ, [O III], Hα). Weak or absent absorption.
**Best systemic anchors**: [O II] 3727 (Priority 2), Hα (Priority 5).
**Ca K/H**: Weak or absent is EXPECTED. Do NOT exclude an ELG hypothesis because Ca K/H are MARGINAL or NOT_FOUND — ELGs have young stellar populations and weak metal absorption.
**Fatal problems**: Missing [O II], no Hβ, [O III] doublet spacing wrong.
**Dn4000**: < 1.4 (young stellar population, consistent).

## LRG/BGS (Luminous Red Galaxy / Bright Galaxy Sample)

**Expected features**: Strong stellar absorption (Ca K/H, G-band, Mg I, Na D). Weak or no emission lines.
**Best systemic anchors**: Ca K/H_abs, G-band_abs (Priority 1).
**Ca K/H**: MUST appear together with Ca K deeper than Ca H. Missing partner → hard exclusion.
**4000 Å break**: Must be present at 4000×(1+z) Å. Dn4000 > 1.6 for old stellar population.
**Fatal problems**: Ca K/H missing (one present without the other AND no 4000 Å break observed), Dn4000 < 1.3 for a claimed LRG, broad emission lines detected.

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

Ne V (3426 Å) is a high-ionization forbidden line almost never present in non-AGN objects. If Ne V is detected with S/N > 3, the AGN hypothesis must be seriously considered regardless of other line classifications.

## Broad Lines in Non-AGN Galaxies

In ELG/LRG, genuine broad emission lines (Lyα, C IV, C III], Mg II) do not appear. If CWT labels a feature as `broad` matching these lines in a galaxy hypothesis, suspect CWT artifact:
- Overfitting of the continuum between absorption troughs
- Fragmentation of a narrow line by noise
- Spurious wide Gaussian from poor baseline fit

Flag such cases for the synthesis agent — do not accept a broad classification in a galaxy hypothesis without noting the caveat.

## Cross-Type Evidence Weight

- **LRG vs LRG**: Absorption lines (Ca K/H, G-band, Mg, Na D) are primary discriminators. Emission lines secondary.
- **ELG vs ELG**: Emission lines ([O II], Hβ, [O III], Hα) are primary discriminators. Absorption lines secondary.
- **Cross-type** (LRG vs ELG vs QSO): Neither evidence type inherently more trustworthy. Judge each hypothesis on its own internal physical consistency, then compare completeness and coherence of line inventories. A hypothesis with 3 LIKELY absorption lines at consistent z is comparable to one with 3 LIKELY emission lines at consistent z — the deciding factor should be physical diagnostics (line ratios, ionization consistency, continuum features), not line count.
