# Spectral Line Reference

## Rest-Frame Line Table

| Name | λ_rest (Å) | Type | Width Class |
|------|-----------|------|-------------|
| Lyα | 1216.0 | em | broad |
| C IV | 1549.0 | em | broad |
| He II | 1640.4 | em | both |
| C III] | 1909.0 | em | broad |
| Mg II | 2800.0 | em | broad |
| Mg II_abs | 2800.0 | abs | absorption |
| [Ne V] | 3426.0 | em | narrow |
| [O II] | 3727.0 | em | narrow |
| Ca K_abs | 3934.8 | abs | absorption |
| Ca H_abs | 3969.6 | abs | absorption |
| Hε | 3970.1 | em | both |
| Hε_abs | 3970.1 | abs | absorption |
| Hδ | 4102.9 | em | both |
| Hδ_abs | 4102.9 | abs | absorption |
| G-band_abs | 4305.6 | abs | absorption |
| Hγ | 4341.7 | em | both |
| Hγ_abs | 4341.7 | abs | absorption |
| Hβ | 4862.7 | em | both |
| Hβ_abs | 4862.7 | abs | absorption |
| [O III]a | 4960.3 | em | narrow |
| [O III]b | 5008.2 | em | narrow |
| Mg I_abs | 5176.7 | abs | absorption |
| Na D_abs | 5895.6 | abs | absorption |
| [N II]a | 6549.8 | em | narrow |
| Hα | 6564.6 | em | both |
| Hα_abs | 6564.6 | abs | absorption |
| [N II]b | 6585.3 | em | narrow |
| [S II]a | 6718.3 | em | narrow |
| [S II]b | 6732.7 | em | narrow |
| CaT1_abs | 8498.0 | abs | absorption |
| CaT2_abs | 8542.0 | abs | absorption |
| CaT3_abs | 8662.0 | abs | absorption |

## Doublet Rules

### [O III] (4960.3 / 5008.2 Å)
- Rest separation: 47.9 Å. Observed separation: 47.9 × (1+z) Å.
- Spacing check: |λ_obs(b) − λ_obs(a) − 47.9×(1+z)| < 5 Å. Wrong spacing → not [O III].
- Amplitude ratio: b : a ≈ 3:1. Reversed ratio (a > b) disqualifies.

### [S II] (6718.3 / 6732.7 Å)
- Rest separation: 14.4 Å. Observed: 14.4 × (1+z) Å.
- Amplitude ratio: a ≈ b (both similar strength).

### [N II] (6549.8 / 6585.3 Å)
- Rest separation: 35.5 Å. Observed: 35.5 × (1+z) Å.
- Amplitude ratio: a : b ≈ 1:3 (b brighter).

### Ca K/H absorption (3934.8 / 3969.6 Å)
- Rest separation: 34.8 Å. Observed: 34.8 × (1+z) Å.
- Ca K MUST be deeper than Ca H. A single absorption without its partner is likely a misidentification.

## Line Blend Disentanglement

### Hβ + [O III] complex (rest 4820–5050 Å)
Hβ 4862.7, [O III]a 4960.3, [O III]b 5008.2.
```
wavelength_1 → Hβ        (shortest)
wavelength_2 → [O III]a  (middle)
wavelength_3 → [O III]b  (longest)
```
If only 2 peaks detected: Hβ+[O III]b (a too weak), or [O III]a+[O III]b (Hβ blended).

### Hα + [N II] complex (rest 6520–6620 Å)
[N II]a 6549.8, Hα 6564.6, [N II]b 6585.3. Separated ~15–20 Å each.
May blend into single asymmetric profile. Always check [N II]a/b presence alongside Hα.

### Ca K/H + Hε absorption (rest 3930–3975 Å)
Ca K_abs 3934.8, Ca H_abs 3969.6, Hε_abs 3970.1. Ca H+Hε separated by 0.5 Å → always blended.
```
wavelength_1 → Ca K_abs  (shortest, ~3935 Å)
wavelength_2 → Ca H_abs  (middle, ~3970 Å)
wavelength_3 → Hε_abs    (longest, ~3970 Å — blended with Ca H)
```

### Mg II Emission vs Absorption Coexistence (2800 Å rest)

Mg II 2800 Å can appear as both broad emission (QSO BLR, FWHM > 2000 km/s) and narrow absorption (ISM, FWHM < 1000 km/s). In AGN host galaxies, broad Mg II emission may be superimposed on narrow Mg II absorption. However, this is easily confused by CWT pipeline artifacts.

**When both Mg II emission AND Mg II_abs are claimed near the same observed wavelength:**

1. **Center coincidence**: The emission and absorption centers must fall within each other's FWHM. If `|λ_em − λ_abs| > max(FWHM_em, FWHM_abs)`, the two features are physically unrelated — one is a misidentification.

2. **Absorption-dominant false emission**: If the CWT feature at the predicted Mg II position is NARROW and in ABSORPTION (FWHM < 1000 km/s, negative amplitude), the nearby broad "Mg II emission" is likely a CWT artifact from:
   - Overfitting the continuum between absorption troughs
   - Broad noise on the wings of the absorption feature being fitted as a separate Gaussian
   - A spurious broad Gaussian from poor baseline subtraction

3. **Default to absorption**: In ambiguous cases, prefer the Mg II absorption interpretation. Mg II ISM absorption is ubiquitous; Mg II BLR emission requires a genuine QSO. The emission claim requires POSITIVE evidence: clearly broad profile (FWHM > 2000 km/s), clearly distinct from the absorption feature, and supported by at least one other AGN indicator ([Ne V], C III], C IV).

4. **CWT broad-line artifact**: CWT may produce spurious narrow peaks from overfitting a genuinely broad profile. Conversely, it may fit a broad Gaussian to noise adjacent to a narrow absorption feature. Both failure modes must be considered.
Flag ambiguous cases as MARGINAL and note the ambiguity for the synthesis agent.

## CWT Artifacts at Low SNR

When per-pixel SNR is low (< 3), the CWT pipeline becomes increasingly unreliable. The following failure modes are common and MUST be actively guarded against:

### Noise-Blur Broadening

A narrow noise spike, convolved across multiple CWT wavelet scales, can appear as a "broad" emission or absorption feature. The wavelet decomposition detects the same noise structure at adjacent scales, producing a high ridge_length. Key indicators of noise-blur broadening:
- CWT FWHM is suspiciously large relative to the visual width of any feature in the raw spectrum
- The feature has moderate-to-high ridge_length but near-zero amplitude
- The "broad" feature sits in a region where adjacent ±100 Å shows multiple features of similar amplitude

### Phantom Doublets

Two unrelated noise peaks whose observed separation happens to match a known doublet spacing (e.g., [O III] rest separation 47.9 Å → observed ~86 Å at z=0.8). The CWT pipeline may flag them as a confirmed doublet. Counter-check: do the peaks have similar amplitude (expected for [S II]) or a 1:3 ratio (expected for [O III])? If ratio is wrong, the "doublet" is coincidental noise alignment.

### Edge Transients

Wavelet boundary effects at the spectrum edges produce artificial peaks within ~100 Å of the wavelength limits (both blue and red cutoffs). These are NOT real spectral features — they are mathematical artifacts of the wavelet transform encountering a data boundary.

### Single-Scale Fluctuations

A feature with ridge_length=1 or 2 is a single-scale CWT fluctuation — it appears at one wavelet dilation but not at adjacent scales. This is the CWT equivalent of a 1σ noise spike. Treat with maximum skepticism regardless of SNR.

## Blue Edge Noise Zone (λ_obs < 4000 Å for DESI)

The DESI blue arm (3600–4000 Å) has the lowest throughput and highest noise in the spectrograph. Spectral features detected in this region require additional scrutiny:

1. **Throughput drop**: DESI blue-arm sensitivity falls steeply below 4000 Å. By 3600 Å, the flux calibration is unreliable.
2. **Noise characteristics**: The noise in the blue arm is non-Gaussian with frequent outliers that CWT interprets as real peaks.
3. **Evaluation rules**:
   - Any line with λ_pred < 4000 Å: flag with "blue edge risk" in caveats
   - If the feature's SNR < 10 or ridge < 5 in the blue zone, cap status at MARGINAL (never LIKELY)
   - If SNR < 5 in the blue zone, assign NOT_FOUND regardless of CWT offset
   - **Must read spectrum ±150 Å** before accepting any blue-zone line as evidence for QSO/AGN

Lines at higher redshift that naturally fall in this region include: Lyα (1216), C IV (1549), He II (1640), C III] (1909). These are high-ionization AGN indicators that are routinely claimed in low-SNR blue data — treat them as **presumptively unreliable until visually confirmed**.

## Red Edge Noise Zone (λ_obs > 9000 Å for DESI)

The DESI red arm beyond ~9000 Å has elevated skyline contamination from OH airglow:

1. **Skyline density**: OH emission lines are dense and bright beyond 9000 Å. The CWT detector cannot distinguish astrophysical features from skylines.
2. **Skyline residuals**: Even after sky subtraction, residual OH lines appear as narrow emission/absorption features at fixed observed wavelengths.
3. **Evaluation rules**:
   - Any line with λ_pred > 9000 Å: flag with "red edge risk" in caveats
   - If λ_pred falls within 10 Å of a known bright skyline, assign NOT_FOUND
   - If SNR < 10 or ridge < 5 in the red zone, cap status at MARGINAL
   - **Must read spectrum ±150 Å** before accepting any red-zone line

Lines that may fall in this region at higher redshift include: [O II] (3727), Ca K/H (3935/3970), Hδ (4103) — rest-frame optical features that are key diagnostics for galaxy classification. When these fall beyond 9000 Å, their reliability is compromised and this must be noted in the synthesis verdict.
