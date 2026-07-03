中文 README 文件见 [README in Chinese](./README_Chinese.md).

 **A related paper is in preparation.** 

# LLM-Spectro-Agent

An LLM-powered agent for human-like analysis of one-dimensional astronomical spectra.

> ⚠️ **Currently supported mode:** FITS input + Redrock redshift hypothesis generation. The PNG input pipeline code has been commented out (2026-06-30) — see OCR section for details.

## Overview

This project use large language models (LLMs) to perform human-like astrophysical inference on 1D spectra, specifically:
- **Source classification** [Only support galaxy (LRG and ELG, output as galaxy), QSO]
- **Redshift estimation** for QSOs

The system mimics the cognitive workflow of a human astronomer:
1. **Visual interpretation** of the spectrum plot (axes, units, features)
2. **Rule-based analysis** using astrophysical knowledge (e.g., Lyα, C IV, Mg II lines)
3. **Multi-agent debate** between an auditor and refinement assistant to improve robustness
4. **Synthesis** of a final report with confidence assessment

The pipeline is currently configured to use the following model via API:
- **Text reasoning**: `deepseek-v4-pro`

> ⚠️ Note: VLM (vision-language model) is temporarily disabled.

> ⚠️ Note: Other LLMs have not been tested and may require adaptation.

> For detailed module documentation, architecture diagrams, and pipeline topology, see [`.repo_info/index.html`](.repo_info/index.html).

---

## Dependencies & Installation
### 1. OCR Engine

> ⚠️ **Note (2026-06-30):** The PNG input channel (which depends on PaddleOCR / Tesseract) has been **commented out** in the source code. The project currently only supports `INPUT_FORMAT=fits`. You may **skip the entire OCR installation section below** and proceed directly to [Python Dependencies](#2-python-dependencies).

This project supports two OCR (Optical Character Recognition) backends: PaddleOCR and Tesseract OCR.
By default, PaddleOCR is used because it generally offers higher accuracy—especially for chart axis labels—but requires a more involved installation process.

In src/utils, we provide two OCR wrapper functions:
```python
_detect_axis_ticks_paddle(state)
```
Uses PaddleOCR.
```python
_detect_axis_ticks_tesseract(state)
```
Uses Tesseract OCR.

You can select your preferred OCR engine by setting the appropriate option in your .env file.

#### 1.1 Installing PaddleOCR

PaddleOCR depends on PaddlePaddle, which must be installed first.

##### 1. Install PaddlePaddle
For CPU-only support, run:

```bash
pip install paddlepaddle==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
```

For GPU support or detailed instructions (including system-specific guidance), refer to the [official PaddlePaddle installation page](https://www.paddlepaddle.org.cn/).

##### 2. Install PaddleOCR
```bash
pip install "paddleocr[all]"
```
##### 3. Compatibility Fix for LangChain (you can do it after installing the Python dependencies)
The current version of PaddleOCR uses legacy imports from older versions of LangChain (langchain.docstore.document, etc.), while this project relies on the newer
* `langchain-core`
* `langchain-text-splitter`

To resolve this conflict, you’ll need to manually patch the PaddleOCR source code after installing the Python dependencies (see Section 2).

Open the following file in your editor (adjust the path to match your Conda environment):
```bash
nano ~/Apps/anaconda3/envs/your_env_name/lib/python3.12/site-packages/paddlex/inference/pipelines/components/retriever/base.py
```
Replace these lines:
```python
from langchain.docstore.document import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
```
with:
```python
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
```
💡 Tip: You can locate your environment path using 
```bash
which python
```
or 
```bash
conda info --envs.
```
#### 1.2 Installing Tesseract OCR

Install it based on your OS:

- **Ubuntu/Debian**:
  ```bash
  sudo apt-get install tesseract-ocr
  ```

- **macOS** (with Homebrew):
  ```bash
  brew install tesseract
  ```

- **Windows**:  
  Download and install from [UB Mannheim Tesseract](https://github.com/UB-Mannheim/tesseract/wiki)

> 📌 Make sure `tesseract` is in your system PATH. Verify with:
> ```bash
> tesseract --version
> ```

### 2. Python Dependencies
Install the required Python packages:
```bash
pip install -r requirements.txt
```

### 3. Environment Setup
Copy the example configuration and fill in your settings:
```bash
cp .env_example .env
```

Edit `.env` to specify:
- `LLM_API_KEY`, `LLM_MODEL`: Text LLM configuration
- `VLM_API_KEY`, `VLM_MODEL`: Vision-language model configuration
- `INPUT_DIR`, `OUTPUT_DIR`: Input and output directories
- `FILE_NAME`: Name of the input FITS file (without `.fits` extension)
- `REDROCK`: Set to `true` to enable Redrock hypothesis generation
- `RR_TEMPLATE_DIR`: Redrock template directory
- and other parameters (see `.env_example` for full list)

### 4. Redrock Installation (optional, for redshift hypothesis generation)

If you set `REDROCK=true` in `.env`, you need to install Redrock — DESI's official redshift fitter.

#### 4.1 Install Redrock

```bash
git clone https://github.com/desihub/redrock
cd redrock
git clone https://github.com/desihub/redrock-templates py/redrock/templates
pip install -e .
pip install desiutil
pip install desispec
```

The templates will be installed with the code. Alternatively, you can place the templates elsewhere and set `RR_TEMPLATE_DIR` in `.env` to that location.

#### 4.2 (Optional) Archetypes Mode

If `USE_ARCHETYPES=true`, clone the archetype repository:

```bash
git clone https://github.com/abhi0395/new-archetypes.git
# or
git clone https://github.com/desihub/redrock-archetypes.git
```

Set `ARCHETYPE_DIR` in `.env` to the cloned directory path.

Verify the installation:

```bash
rrdesi --help
```

---

## Quick Start

See [Quick start](Quickstart.md) for a quick start guide.

### Run the Analysis
Execute the main script:
```bash
python scripts/main.py
```
Results will be saved to the output directory specified in `.env`.
---

## Output Files Description

For an input FITS file `{your_file_name}.fits`, the pipeline uses Redrock to generate redshift hypotheses, then applies multi-agent LLM analysis to verify and refine them. Results are saved to `{OUTPUT_DIR}/{your_file_name}/` with the following structure (using `116.fits` as an example):

```
116/
├── 116_in_brief.json                  # Final brief summary (type, redshift, confidence, lines)
├── final_report.md                    # Final comprehensive report
├── 116_spec_extract.png               # Reconstructed spectrum from OpenCV
├── 116_spectrum.png                   # Extracted spectrum and SNR plot
├── 116_snapshot.json                  # Full runtime state snapshot
├── 116_brute_force_matching.txt       # Brute-force template matching results
├── 116_hypothesis_analysis.txt        # Hypothesis synthesis verdict (JSON)
├── 116_redrock/                       # Redrock external fitting results
│   ├── 116_redrock.fits
│   └── 116_rrdetails.h5
├── visual_interpreter/                # Visual interpretation outputs
│   ├── 116_continuum.png              # Fitted continuum spectrum
│   ├── 116_features.png               # Detected spectral features visualization
│   ├── 116_residual_spectrum.png      # Residual spectrum (data - continuum)
│   ├── 116_emission.csv               # Detected emission lines table
│   ├── 116_absorption.csv             # Detected absorption lines table
│   └── 116_spectrum.npz               # Extracted spectrum data (NumPy)
├── single_hypothesis/                 # Per-hypothesis detailed analysis
│   ├── 1_report.md                    # Hypothesis report
│   ├── 1_features.png                 # Feature visualization at this redshift
│   ├── 1_lines.csv                    # Identified spectral lines
│   ├── 1_lines_cleaned.csv            # Cleaned line list
│   └── 1_stream.md                    # Agent stream log
│   ├── 2_* ...                        # (one set per hypothesis, up to N)
├── hypothesis_synthesis/              # Multi-hypothesis synthesis
│   ├── report.md                      # Synthesis summary report
│   ├── catalog.csv                    # Catalog of all hypotheses
│   └── stream.md                      # Synthesis agent stream log
├── feature_auditor/                   # Feature auditor outputs
│   ├── stream.md                      # Auditor agent stream log
│   └── verdict.json                   # Feature verdict
├── result_auditor/                    # Result auditor outputs
│   └── stream.md                      # Auditor agent stream log
└── report_writer/                     # Report writer outputs
    └── stream.md                      # Writer agent stream log
```

### Key output files

| File | Description |
|------|-------------|
| `{name}_in_brief.json` | Machine-readable summary: type, redshift, confidence, identified lines |
| `final_report.md` | Human-readable final report with full analysis details |
| `hypothesis_synthesis/report.md` | Summary of all tested hypotheses and final verdict |
| `hypothesis_synthesis/catalog.csv` | Table of all hypotheses with line measurements |
| `visual_interpreter/{name}_emission.csv` | All detected emission features (wavelength, flux, SNR, width) |
| `visual_interpreter/{name}_absorption.csv` | All detected absorption features |
| `single_hypothesis/{N}_lines.csv` | Line identifications for each tested redshift

---
## License

This project is for research and educational purposes. 

---