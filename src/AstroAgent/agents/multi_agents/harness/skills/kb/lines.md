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
| Ne V | 3426.0 | em | narrow |
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

### Mg II emission + absorption (2800 Å rest)
In AGN host galaxies, broad Mg II emission (BLR) superimposed on narrow Mg II absorption (ISM).
CWT may produce spurious narrow peaks from overfitting the broad profile.
Flag as MARGINAL, note ambiguity for synthesis agent — do not attempt to resolve yourself.
