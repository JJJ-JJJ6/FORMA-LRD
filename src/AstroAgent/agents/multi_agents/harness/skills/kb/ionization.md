# Ionization & Velocity Rules

## Systemic Redshift Anchoring Priority

Use the lowest-ionization LIKELY line to anchor systemic z. Priority from lowest to highest ionization:

| Priority | Lines | Ionization | Notes |
|----------|-------|------------|-------|
| 1 | Ca K/H_abs, G-band_abs, Mg I_abs, Na D_abs | Neutral | Stellar absorption — most reliable |
| 2 | [O II] 3727 | O⁺ (~13.6 eV) | Best emission anchor for ELG |
| 3 | [S II]a/b 6718/6733 | S⁺ | |
| 4 | [N II]a/b 6550/6585 | N⁺ | |
| 5 | Hα/Hβ/Hγ/Hδ/Hε | H (~13.6 eV) | |
| 6 | Mg II 2800 | Mg⁺ (~15.0 eV) | May show outflow blueshift |
| 7 | [O III]a/b 4960/5008 | O⁺⁺ (~35.1 eV) | Weakest anchor, often blueshifted |

**Excluded** (must NOT anchor systemic z): He II (He⁺, 54.4 eV), C III] (C⁺⁺, 47.9 eV), C IV (C⁺⁺⁺, 64.5 eV), Ne V (Ne⁺⁺⁺⁺, 97.1 eV), Lyα (1216).
These high-ionization lines are routinely blueshifted by AGN outflows (hundreds of km/s). If only excluded lines are available, flag the redshift as potentially biased.

## ELG Exception

In emission-line dominated objects (strong [O II], Hβ, [O III]; Ca K/H weak or absent), Ca K/H absorption is often intrinsically weak and may not reach LIKELY or MARGINAL. Anchor systemic z on [O II] (Priority 2) instead. Do NOT downgrade an otherwise strong ELG hypothesis because Ca K/H are MARGINAL.

## Outflow Blueshift Rule

High-ionization lines blueshifted relative to low-ionization by 0–1000 km/s is physically normal.
Velocity offset: Δv = (z_high − z_low) / (1 + z_low) × c. Negative Δv = blueshift.
If a high-ionization line gives a LOWER z than a low-ionization line, suspect misidentification (not outflow reversal).

## Width Mismatch Policy

- A `narrow` feature matching a `broad` line (Lyα, C IV, C III], Mg II) → flag, do not use for systemic z
- A `broad` feature matching a `narrow` line ([O II], [O III], [N II], [S II]) → flag, suspect spurious CWT feature
- Balmer lines (Hα–Hδ) and He II are `both` class → width checks do not apply
- Flag mismatches but do not veto an entire hypothesis on one width mismatch alone

## QSO Broad-Line Amplitude Ordering

Typical QSO: Lyα > C IV > C III] > Mg II in amplitude.
If Lyα or C IV amplitude is significantly lower than Mg II, question whether the Mg II identification is correct.

## Lyα Multi-Peak Fragmentation

IGM absorption can split broad Lyα into 2–3 apparent peaks along the line of sight. Each fragment may match Lyα at slightly different implied z. This is physically normal — the true Lyα center lies among the detected fragments. Multiple narrow/intermediate Lyα matches at nearby wavelengths can still support a QSO hypothesis.
