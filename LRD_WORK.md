# FORMA-LRD: guide to the adaptation work

This document maps out everything on the `lrd` branch that is adaptation work, i.e.
not inherited from upstream FORMA. The exact machine-readable version of this list is
the [upstream-baseline...lrd compare view](https://github.com/JJJ-JJJ6/FORMA-LRD/compare/upstream-baseline...lrd).

**Goal**: verify the broad-line identifications and LRD-vs-classical-AGN
classifications claimed for the 19 broad-line sources of Kapoor+26 (EIGER
JWST/NIRCam F356W WFSS, 3.15–3.95 µm), treating the paper's claims as hypotheses
to be tested — never as labels to be reproduced. FORMA's multi-agent architecture
(orchestrator, hypothesis agents, auditors, report writer) is used unchanged; the
optical/DESI domain content it shipped with is replaced with the rest-NIR LRD
domain, and its Redrock redshift engine is replaced with a hypothesis provider
that scores all six candidate line identities the same way whether or not a
claim is registered for the source.

**This verification pipeline is the core of the project.** `lrd_adapt/blind/`
(below) is a later, strictly optional addition: a pre-processing stage that
scans many sources with no prior claim to decide which are worth feeding into
the same, unmodified verification pipeline. It does not change how Stage
A/B/C or the agents work — see the README's "Optional: blind search" section
for how to use or skip it.

The work lives in two layers.

## Layer 1 — `lrd_adapt/` (all new code)

### `lrd_adapt/converter/` — getting EIGER data into FORMA

| File | Purpose |
|---|---|
| `grizli_to_forma.py` | **1D route.** Reads grizli `*.1D.fits` (wave/flux/err/flat/contam BinTable), flat-calibrates, converts µm→Å and err→inverse variance, flags contamination-dominated pixels (mask bit 4), writes FORMA's `{ARM}_WAVELENGTH/FLUX/IVAR/MASK` image HDUs plus an anonymized `METADATA` HDU. Preferred route: grizli's optimal extraction is flux-calibrated. |
| `stack_to_forma.py` | **2D route.** Reads a grizli `*.stack.fits` 2D spectrogram directly, boxcar-extracts a 1D spectrum around the auto-detected trace row (per-visit CONTAM carried through), then chains into the 1D converter — output layout identical to the 1D route. Caveat: flux stays in count/s (no flat-field curve in a stack file), so line positions/widths/identities are trustworthy but absolute fluxes are not. |
| `masked_regions.py` | Reconstructs masked wavelength intervals from the output MASK HDU so contamination actually reaches the LLM-facing "masked regions" channel (upstream only computed multi-arm overlap, which is always empty for a single F356W arm). |
| `test_stack_to_forma.py` | Synthetic 2D-stack round-trip test (line recovery, contam masking, metadata passthrough). |
| `zfit_reader.py` (+ `test_zfit_reader.py`) | Reads grizli `*.full.fits` redshift-fit products (the file behind specvizitor's automated "Redshift" field) into the hypothesis provider's external-z-prior shape — z from the ZFIT table's own pdf (MAP + 16/84 width), header-REDSHIFT fallback, loud schema-mismatch failure. **Validated on synthetic files only** — re-verify against the first real `.full.fits` from eor1 before production use. |

### `lrd_adapt/hypothesis/` — replaces Redrock

| File | Purpose |
|---|---|
| `lrd_hypothesis_provider.py` | For each source: primary hypothesis = the paper's claimed line identity + z_spec; rivals = every other line identity consistent with the strongest detected peak's observed wavelength. Emits the same schema Redrock would, so downstream agents are unaware of the swap. Wired into `VisualInterpreter.py` behind `HYPOTHESIS_PROVIDER=lrd`. |

### `lrd_adapt/tools/` — agent-callable fitting tools

| File | Purpose |
|---|---|
| `broadline_lsf_bic.py` | The core "is the broad width real velocity broadening?" test: models observed width as intrinsic ⊗ LSF and compares three profile models (narrow ⊗ extended-source LSF vs narrow+broad ⊗ point LSF vs mixed) via BIC, mirroring Kapoor+26 §3.1/§4.2. Registered as an agent tool. |
| `blueshifted_absorption_bic.py` | Negative-Gaussian composite fit for the rare blueshifted He I absorption signature (2/19 sources). Registered as an agent tool. |
| `test_broadline_lsf_bic.py`, `test_blueshifted_absorption_bic.py` | Synthetic-profile recovery tests (e.g. an injected 600 km/s blueshift recovered to ~2 km/s). |

### `lrd_adapt/evidence/` — external-evidence channel (Stage B)

| File | Purpose |
|---|---|
| `external_evidence.py` (+ `test_external_evidence.py`) | Loads, validates, and formats the per-source "External Evidence" block (photometry, Balmer-break estimate, compactness r_circ, X-ray coverage) injected into agent prompts. Every item is provenance-tagged `ours` vs `paper`. |
| `phot_evidence.py` (+ `test_phot_evidence.py`) | **Measures** photometric evidence from a grizli field catalog (`{root}_phot.fits`): band fluxes, colors, and a Balmer-break proxy (f_ν(F200W)/f_ν(F115W)) with an explicit z-validity window outside which it self-flags unreliable. Defensive column resolution; fails loudly listing actual columns. Provenance `ours` — this is what lets blind-search candidates (absent from the paper) get classified. Synthetic-catalog validation only until checked against the real eor1 `j1030_phot.fits`. |
| `compactness.py` (+ `test_compactness.py`) | **Measures** r_circ from the DSCI direct-image cutout of a `*.full.fits`: curve-of-growth half-light radius, subpixel apertures, adaptive 4×r50 total-flux aperture, no PSF deconvolution (Kapoor+26 Fig. 5 convention). Recovers injected 100/180 mas to a few percent on synthetics; real-DSCI verification still open. |

### `lrd_adapt/configs/` — run presets and per-source inputs

| File | Purpose |
|---|---|
| `f356w.env` | The LRD run preset: `HYPOTHESIS_PROVIDER=lrd`, `REDROCK=false`, `ARM_NAME=F356W`, wavelength range 31500–39500 Å, CWT widths tuned for broad lines at 3.5 µm. |
| `primary_hypotheses.json` | The paper claims under test (line identity + z_spec) for all 19 sources, keyed by anonymous SRC code. |
| `external_evidence.json` | Provenance-tagged external-evidence data per source. |

### `lrd_adapt/eval/` — evaluation harness (Stage C)

| File | Purpose |
|---|---|
| `mapping.csv` | Real ID ↔ anonymous SRC code mapping. Deliberately outside anything agents can reach. |
| `ground_truth.json` | Scoring truth (5 LRD / 14 classical AGN, from Kapoor+26 Table 1). |
| `anonymizer.py` | Strips real IDs/coordinates when preparing agent-visible inputs. |
| `loo_harness.py` | Leave-one-out per-source run orchestration. |
| `metrics.py` | Scoring, including the line-name vocabulary mapping (e.g. `HeI_Pagamma` ↔ "He I"/"Paγ") and pre/post-KB-freeze tracking via git tag. |
| `synthetic_injection.py` | Synthetic spectrum generator with known ground truth, round-trip-tested through the real converter and BIC tool. |
| `negative_controls.py` (+ `negative_controls_manifest.json`) | Scaffolding only — blocked on catalog access; two candidate designs documented in the docstring. |
| `demo_one_source.py` | The original single-source synthetic end-to-end demo. |
| `test_anonymizer_isolation.py` | Regression test: no real source ID is reachable from any agent-visible KB, skill, or config content. |
| `test_loo_harness.py`, `test_metrics.py`, `test_negative_controls.py`, `test_synthetic_injection.py` | Unit/integration tests for the above. |

### `lrd_adapt/blind/` — blind-search triage (supervisor pivot, 2026-07)

| File | Purpose |
|---|---|
| `triage.py` (+ `test_triage.py`) | Cheap CWT-only scan over many grizli `*.stack.fits` files — no redshift, no hypothesis, no LLM. First stage of the blind-search funnel (mirrors Kapoor+26's own Allegro first pass): flag line-candidate sources, then spend the expensive `run_fit=True` re-extraction and full FORMA verification only on the flagged subset. Reuses the pipeline's own boxcar extractor and CWT detector with the live `.env` preset values; deliberately over-inclusive (any emission detection flags; runs unmasked, so contamination residuals can flag too — filtered downstream, never lost). `--flagged-csv` output feeds the eor1 `extract_all.py --ids-csv` re-extraction directly. |

### `lrd_adapt/kb_drafts/`

| File | Purpose |
|---|---|
| `lrd_line_identity_rules.md` | Provenance-tagged rule extraction from Kapoor+26 — the source document from which the installed KB/skill content below was adapted. |

## Layer 2 — targeted edits inside upstream (`src/FORMA/`, `scripts/`)

- **Knowledge base** (`src/FORMA/agents/multi_agents/harness/kb/`): `lines.md`,
  `classification.md`, `ionization.md`, `composite_profile.md` rewritten from the
  optical/DESI domain to rest-NIR LRD content; new `lrd_classification.md` with the
  He I/Paγ > 2.3 LRD-vs-classical-AGN diagnostic and its preconditions.
- **Skill prompts** (`harness/skills/`): five rewritten — both HypothesisAnalyst
  skills, `feature_audit_skill.md`, `result_auditor_skill.md` (including a Layer-3
  classification verdict that only fires when a line identity is confirmed AND
  external evidence exists; otherwise `classification="Unknown"` by design), and
  `report_writer_skill.md`.
- **`utils/line_tables.py`** (new): single deduplicated source for the rest-NIR
  emission/absorption line tables (upstream had them triplicated); `utils/VI.py`
  consumes it.
- **Wiring and bug fixes**, all found by tracing reachability under
  `HYPOTHESIS_PROVIDER=lrd` or by running the real pipeline:
  - `VisualInterpreter.py` — LRD provider dispatch + masked-regions channel;
  - `HypothesisAnalyst.py` — fitting tools were silently disabled under the LRD flag;
  - `AnalysisAuditor.py` — contradiction matrix fixes (DESI blue/red-edge assumptions
    disabled for LRD runs; empty-features early-return carried config through);
  - `harness/single_hypothesis.py` — External Evidence prompt block;
  - `harness/tools.py` — registers the two BIC tools;
  - `agents/common/state.py`, `core/config/params_config.py` — new state/config fields;
  - `scripts/main.py` — Windows UTF-8 console fix and a pre-existing upstream crash
    (`ResultWriter.write()` never existed) that fired on any run reaching completion.
- **Housekeeping**: `.env_example` (LRD preset documented), `.gitignore` (keeps
  `data/` run outputs out of git), `README.md` (reviewer orientation section).

## Running it

1. Create `.env` from `.env_example` with the `lrd_adapt/configs/f356w.env` preset
   values, your LLM credentials, and `INPUT_DIR`/`OUTPUT_DIR`.
2. Convert a source (either route):
   - 1D: `python -m lrd_adapt.converter.grizli_to_forma` (see module docstring), or
   - 2D: `python -m lrd_adapt.converter.stack_to_forma <file>.stack.fits INPUT_DIR/SRCxx.fits --arm F356W --source-code SRCxx --z-spec <z>`
3. Set `FILE_NAME=SRCxx` and run `python scripts/main.py`. Output lands in
   `OUTPUT_DIR/SRCxx/` (per-agent streams + `final_report.md`).

## Validation status

- All tools/converters have synthetic-data tests (run each `test_*.py` directly;
  no pytest needed).
- Real-data validation so far: boxcar-extracted real J1030 spectra run through the
  full converter + CWT + hypothesis chain recover the paper's He I identification
  for SRC04 at the ~1-pixel level; SRC05 (fainter) correctly lands on
  `Unknown / human review` via the no-features branch.
- The full leave-one-out evaluation over all 19 sources awaits grizli's proper
  flux-calibrated `.1D.fits` extractions.
