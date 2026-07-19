# FORMA-LRD

A private adaptation of the upstream [FORMA / LLM-Spectro-Agent](https://github.com/mynamesnoname/FORMA)
multi-agent pipeline for **verifying broad-line identifications in JWST/NIRCam F356W
WFSS spectra of Little Red Dots and classical AGNs** (EIGER survey, Kapoor+26 sample).
The paper's claims are treated as hypotheses to be tested, never as labels to be
reproduced.

- **Branch `lrd` (this branch)**: all of the adaptation work. Branch
  `upstream-baseline` is the untouched upstream commit this work forked from.
- **File-by-file guide to the adaptation work**: [LRD_WORK.md](./LRD_WORK.md)
- **Exactly what was added/changed vs. upstream**:
  [upstream-baseline...lrd compare view](https://github.com/JJJ-JJJ6/FORMA-LRD/compare/upstream-baseline...lrd)

Everything below is what is strictly necessary to run FORMA-LRD.

## Requirements

- **Python ≥ 3.12** (plain venv — no Docker involved)
- **An LLM API key** for any OpenAI-compatible endpoint (developed and tested
  against DeepSeek `deepseek-v4-pro`)
- Input data: grizli extraction products for your sources — either `*.1D.fits`
  (preferred) or `*.stack.fits` (2D)

**Explicitly NOT needed** (all upstream features that are disabled or replaced on
this branch): PaddleOCR / Tesseract (the PNG input channel is commented out
upstream — skip any OCR setup), Redrock and its templates (replaced by the
paper-claim-driven hypothesis provider), VLM/vision credentials, Docker.

## 1. Install

```bash
git clone https://github.com/JJJ-JJJ6/FORMA-LRD.git
cd FORMA-LRD                    # lrd is the default branch
python -m venv .venv
# activate: .venv\Scripts\activate  (Windows)  |  source .venv/bin/activate  (Linux/macOS)
pip install -e .
```

> Known quirk: if `import langchain` fails complaining about
> `langgraph.runtime`, the pinned langgraph is too old for the installed
> langchain — run `pip install -U langgraph`.

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
(`Unknown / human_review=Yes`) — that is calibrated behavior, not a failure.

## 5. Tests (optional, no pytest needed — run each file directly)

```bash
python lrd_adapt/converter/test_stack_to_forma.py
python lrd_adapt/tools/test_broadline_lsf_bic.py
python lrd_adapt/tools/test_blueshifted_absorption_bic.py
python lrd_adapt/eval/test_anonymizer_isolation.py
python lrd_adapt/eval/test_synthetic_injection.py
python lrd_adapt/eval/test_metrics.py
```

## Credits & license

Built on the upstream [FORMA / LLM-Spectro-Agent](https://github.com/mynamesnoname/FORMA)
(MIT license). The upstream project's original documentation is preserved in this
repo's git history and in `README_Chinese.md` / `Quickstart.md`; note that parts
of those documents (OCR setup, Redrock, DESI arm configs, PNG input) do not apply
to this branch.
