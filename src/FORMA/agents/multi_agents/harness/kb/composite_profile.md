# Emission–Absorption Composite Profile

## Motivation

CWT and line-finding algorithms frequently identify emission peaks and absorption troughs independently. When an absorption feature is superposed on an emission line of the same species, the resulting profile may be fragmented into a central trough identified as an absorption line, and one or more adjacent maxima identified as emission lines — when in reality they are two halves of a single physical system.

Whenever an emission line and an absorption line of the same species are detected at nearly the same wavelength, evaluate them as a possible composite profile.

## Physical Interpretation

A composite profile occurs when absorbing material along the line of sight removes flux near the emission line center, producing an emission feature with a central depression. The absorption does NOT necessarily represent an independent spectral feature — it may merely modify the shape of a single underlying emission line.

## Morphological Signature

A genuine emission–absorption composite profile forms a broad "M" shape in the spectrum: two emission wings flanking a central absorption trough, with smooth flux continuity between all components and approximate symmetry around a common center.

## Diagnostic Criteria

### Criterion 1: Common Central Wavelength
The absorption trough should lie near the center of the emission structure: λ_abs ≈ (λ_left + λ_right) / 2. Large offsets (> half the emission FWHM) weaken the composite interpretation.

### Criterion 2: Broad and Smooth Wings
The two emission wings should be broad, smooth, and continuous — not narrow spikes. Genuine composites preserve the morphology of the parent emission line. Spike–valley–spike patterns (two narrow peaks flanking a trough) are commonly produced by noise fluctuations and do NOT support a physical composite.

### Criterion 3: Approximate Symmetry
The left and right wings should have comparable width, curvature, and amplitude. Severe asymmetry often indicates noise contamination, incorrect line identification, or blending with another transition.

### Criterion 4: Continuum Connectivity
The wings should merge naturally into the surrounding continuum. Profiles composed of isolated spikes with no broad structure are unreliable.

## Warning Signs

**Noise-Induced False Composite**: Multiple local extrema without a coherent broad profile. Both emission and absorption detections are likely spurious.

**Spike–Valley–Spike**: Two narrow peaks surrounding a narrow trough. Commonly produced by noise — does NOT support a physical composite system. This is the most important false-positive pattern to recognize.

**Strongly Asymmetric**: Only one side exhibits a broad emission wing. May indicate line blending with another species, incorrect absorption assignment, or noise on one side of the emission profile. Should not automatically be interpreted as a composite.

## Recommended Agent Behavior

When an emission and absorption line of the same species are detected:

1. Search for a shared morphological structure across both detections.
2. Estimate the center of the combined profile.
3. Evaluate center consistency, wing broadness and smoothness, symmetry, and continuum connectivity.
4. If a coherent composite "M" profile is present: treat the detections as a **single physical system** and increase confidence in the line identification.
5. If the profile is dominated by spikes, asymmetry, or noise: reduce confidence for both detections. Default to treating them as independent (low-confidence) rather than linked.

## Examples

### He I + Paγ blend (10833 / 10941 Å rest, LRD domain)

**Not a composite in the emission+absorption sense above** — these are two
*separate* emission lines from different species, 108 Å apart rest-frame,
well resolved at R~1600 (~7 Å instrumental FWHM). Unlike Mg II/Hα/Hβ below,
expect two distinguishable peaks, not one blended "M" profile. Use
`fit_doublet` (or two `fit_peak` calls) to measure both independently. The
diagnostic quantity here is the He I/Paγ **amplitude ratio itself**
(Kapoor+26 §4.4: >2.3 → classical AGN) — this is a Stage B input, not a
Stage A reality check; see `kb/classification.md`'s scope note. At Stage A,
just confirm both components are real, independent, in-window detections.

**Shared kinematics check (§4.2)**: If both broad and narrow components are
fit for He I and Paγ, their velocity offsets should agree between the two
species (±300 km/s), since both trace the same gas system. See
`kb/ionization.md`.

### Blueshifted He I absorption (rare — do not expect by default)

A minority of sources (2 of 19 in Kapoor+26) show a blueshifted He I
absorption component, modeled as a **negative Gaussian** superposed on the
He I emission — a genuine emission-plus-absorption composite in the sense
of this document's general methodology above. Apply the same morphological
tests (center consistency, wing broadness, symmetry) to judge whether a
claimed absorption dip is a real outflow signature or a spike/noise
artifact riding on the emission profile. Requires ΔBIC > 10 via
`_fit_blueshifted_absorption_bic` (`lrd_adapt/tools/blueshifted_absorption_bic.py`)
to accept — do not accept a visually suggestive dip without that check,
and do not treat its absence as unusual (most sources don't show it).

## General Principle

A line should not be classified solely from individual local extrema. Whenever an emission feature and an absorption feature of the same transition coexist at nearly identical wavelengths, **the morphology of the entire profile takes precedence over the individual detections.**
