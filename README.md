# FORMA-LRD

A private adaptation of the upstream [FORMA / LLM-Spectro-Agent](https://github.com/mynamesnoname/FORMA)
multi-agent pipeline for **verifying broad-line identifications in JWST/NIRCam F356W
WFSS spectra of Little Red Dots and classical AGNs** (EIGER survey, Kapoor+26 sample).
The paper's claims are treated as hypotheses to be tested, never as labels to be
reproduced. **This verification pipeline is the core of the project and is what
steps 1–5 below set up.**

Blind search — scanning many sources with no prior claim to find new candidates,
before handing them to the same verification pipeline — is a separate, optional
add-on described under
["Optional: blind search"](#optional-blind-search-finding-new-candidates-first).

- **Branch `lrd` (this branch)**: all of the adaptation work. Branch
  `upstream-baseline` is the untouched upstream commit this work forked from.
- **File-by-file guide to the adaptation work**: [LRD_WORK.md](./LRD_WORK.md)
- **Exactly what was added/changed vs. upstream**:
  [upstream-baseline...lrd compare view](https://github.com/JJJ-JJJ6/FORMA-LRD/compare/upstream-baseline...lrd)

## What FORMA is, and what we adapted

**FORMA** stands for **Formalized Observational Reasoning with Auditable Decisions**
(Wang, Tan et al., Shanghai Astronomical Observatory, Chinese Academy of Sciences).
It was originally built as a verification layer for DESI optical spectra: LLM agents
perform human-like astrophysical inference on 1D spectra — specifically **source
classification** (galaxy: LRG/ELG, QSO) and **redshift estimation for QSOs** — by
generating candidate interpretations, testing them against the spectrum's own
evidence and rival explanations, and returning a credibility score rather than a
bare label. Applied to the DESI EDR expert-review catalogue, it reached 95.5% binary
agreement with expert-adjudicated classifications at medium-or-higher credibility.

For FORMA-LRD, the multi-agent architecture itself is unchanged; what moved is the
domain it verifies. Instead of DESI optical spectra and QSO/ELG/LRG classification,
this fork audits **broad-line identifications and LRD-vs-classical-AGN
classifications in JWST/NIRCam F356W grism spectra** (EIGER survey, Kapoor+26
sample) — replacing the DESI line tables, redshift engine (Redrock), and knowledge
base with rest-frame near-infrared content, and later adding an optional
blind-search stage (below) to find candidates with no prior claim at all.

## Architecture

Six agents — `VisualInterpreter`, `HypothesisAnalyst`, feature/result auditors,
`ReportWriter`, `SelfEvolve` — live in `src/FORMA/agents/multi_agents/`, orchestrated
by `workflow_orchestrator.py` (LangGraph). Each agent pairs a system prompt (skill
files under `harness/skills/`) with callable tools (peak/doublet/BIC fitting,
CSV/report writers) and reasons via calls to the configured `LLM_BASE_URL`/
`LLM_MODEL` endpoint.

## Requirements

- **Python ≥ 3.12** (plain venv)
- **An LLM API key** for any OpenAI-compatible endpoint (developed and tested
  against DeepSeek `deepseek-v4-pro`)
- Input data: grizli extraction products for your sources — either `*.1D.fits`
  (preferred) or `*.stack.fits` (2D)

**Not needed** on this branch: PaddleOCR / Tesseract (PNG input is disabled
upstream), Redrock and its templates (replaced by the hypothesis provider),
VLM/vision credentials, Docker (no Dockerfile in this repo — the venv below
fills that role).

## 1. Install

```bash
git clone https://github.com/JJJ-JJJ6/FORMA-LRD.git
cd FORMA-LRD                    # lrd is the default branch
python -m venv .venv
# activate: .venv\Scripts\activate  (Windows)  |  source .venv/bin/activate  (Linux/macOS)
pip install -e .
```

> If `import langchain` fails complaining about `langgraph.runtime`, the
> pinned langgraph is too old for the installed langchain — run
> `pip install -U langgraph`.

Verify the install:

```bash
python -c "from FORMA.workflow_orchestrator import WorkflowOrchestrator; print('OK')"
```

## 2. Configure `.env`

```bash
cp .env_example .env
```

Then set, in `.env`:

**LRD preset** (from `lrd_adapt/configs/f356w.env` — copy these lines as-is):

```ini
REDROCK=false
HYPOTHESIS_PROVIDER=lrd
ARM_NAME=F356W
ARM_WAVELENGTH_RANGE=31500-39500
CWT_MAX_SCALE=14.0
```

**Your credentials and paths:**

```ini
LLM_API_KEY=<your key>
LLM_BASE_URL=https://api.deepseek.com     # or any OpenAI-compatible endpoint
LLM_MODEL=deepseek-v4-pro
RUN_MODE=s
INPUT_DIR=<absolute path>/data/lrd_input
OUTPUT_DIR=<absolute path>/data/lrd_output
FILE_NAME=                                 # set per run, see step 4
```

Everything else in `.env_example` can keep its default. Never commit a real
`.env` (it is gitignored).

## 3. Convert input data

Both routes produce identical FORMA-readable FITS. Source codes (`SRC02`–`SRC20`)
and the paper's claimed redshifts come from `lrd_adapt/eval/mapping.csv` /
`lrd_adapt/configs/primary_hypotheses.json`.

This is the **verification** case: a source you've already identified, with a
known/claimed redshift to audit, passed as `z_spec` below. (For a source with
*no* prior claim — the output of blind search — just omit `z_spec`; see
["Optional: blind search"](#optional-blind-search-finding-new-candidates-first).)

**1D route (preferred — grizli's flux-calibrated optimal extraction):**

```bash
python -c "from lrd_adapt.converter.grizli_to_forma import convert_grizli_1d_to_forma; \
convert_grizli_1d_to_forma('source.1D.fits', 'data/lrd_input/SRC04.fits', \
arm_name='F356W', source_code='SRC04', z_spec=2.328)"
```

**2D route (when only the `*.stack.fits` 2D spectrogram exists — internal boxcar
extraction; line positions/widths are reliable, absolute fluxes are not):**

```bash
python -m lrd_adapt.converter.stack_to_forma source.stack.fits data/lrd_input/SRC04.fits \
  --arm F356W --source-code SRC04 --z-spec 2.328
```

## 4. Run

```bash
# set FILE_NAME to the converted file's basename (no .fits), then:
python scripts/main.py
```

(`FILE_NAME` can be set in `.env` or as an environment variable, e.g.
`FILE_NAME=SRC04 python scripts/main.py` on Linux/macOS.)

Output lands in `OUTPUT_DIR/<FILE_NAME>/`:

```
final_report.md               ← the 6-section report (+ a PDF copy)
visual_interpreter/           ← CWT feature detection plots/CSVs
single_hypothesis/            ← per-hypothesis agent runs (streams, line tables, plots)
feature_auditor/  hypothesis_synthesis/  result_auditor/  report_writer/
<FILE_NAME>_redshift_hypotheses.txt   ← hypothesis provider scores
```

A source with no detectable features exits early with a placeholder report
(`Unknown`, `human_review=Yes`) — expected, calibrated behavior, not a failure.

## 5. Tests (optional, no pytest needed — run each file directly)

```bash
python lrd_adapt/converter/test_stack_to_forma.py
python lrd_adapt/tools/test_broadline_lsf_bic.py
python lrd_adapt/tools/test_blueshifted_absorption_bic.py
python lrd_adapt/eval/test_anonymizer_isolation.py
python lrd_adapt/eval/test_synthetic_injection.py
python lrd_adapt/eval/test_metrics.py
```

## Optional: blind search (finding new candidates first)

Optional. Steps 1–5 above are the complete, standalone verification pipeline —
skip this if you already have a specific source and a claim to audit. Blind
search is a pre-processing stage that decides which sources are worth feeding
into steps 3–4; it does not change how the core pipeline runs.

1. **Triage** (`lrd_adapt/blind/triage.py`): scans a folder of `*.stack.fits`
   files with **no redshift, no registered claim, no LLM call** — just CWT line
   detection — and flags which sources show a real feature:

   ```bash
   python -m lrd_adapt.blind.triage "path/to/*.stack.fits" \
       -o triage_results.csv --flagged-csv flagged.csv
   ```

2. **Re-extract flagged candidates** with `run_fit=True` on your grizli side
   (outside this repo) to get a real fitted redshift for each.

3. **Convert and run exactly as in steps 3–4 above, with `z_spec` omitted** —
   the hypothesis provider generates and scores all six candidate line
   identities with no bias toward any of them, whether or not a claim exists.

4. **(Automatic, needs no flag)** If a real `{FILE_NAME}.full.fits` sits next to
   the converted input in `INPUT_DIR`, `VisualInterpreter.py` reads its fitted
   redshift (`lrd_adapt/converter/zfit_reader.py`) and passes it in as a soft,
   uncertainty-weighted prior — never a hard override. Verified on real eor1
   data (2026-07-28): a poorly-constrained real fit nudges scores toward its
   neighborhood without collapsing the six-way ambiguity to a false certainty.

5. **(Optional, for Stage B on new candidates)** `lrd_adapt/evidence/` measures
   photometry/colours (`phot_evidence.py`) and compactness
   (`compactness.py`) directly from the user's own grizli/imaging products,
   so a brand-new candidate — one the paper never discusses — can still get an
   LRD-vs-classical-AGN classification instead of defaulting to `Unknown` for
   lack of evidence.

None of this touches `scripts/main.py` or the agent pipeline itself — it only
supplies inputs to it.

## Credits & license

Built on the upstream [FORMA / LLM-Spectro-Agent](https://github.com/mynamesnoname/FORMA)
(MIT license). The upstream project's original documentation is preserved in this
repo's git history and in `Quickstart.md`; note that parts of it (OCR setup,
Redrock, DESI arm configs, PNG input) do not apply to this branch.
A Chinese version of this README is available: [README_Chinese.md](./README_Chinese.md).
