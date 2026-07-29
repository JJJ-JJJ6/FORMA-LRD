# ============================================================
# FORMA (LLM-Spectro-Agent) - dependency setup script (Windows)
# Run this from inside your cloned FORMA repo directory in PowerShell.
# Requires: Python >= 3.12, pip
# ============================================================

$ErrorActionPreference = "Stop"

Write-Host "==> Checking Python version..."
python --version

Write-Host "==> Creating virtual environment (.venv)..."
python -m venv .venv

Write-Host "==> Activating virtual environment..."
& .\.venv\Scripts\Activate.ps1

Write-Host "==> Upgrading pip..."
python -m pip install --upgrade pip

Write-Host "==> Installing Python dependencies from requirements.txt..."
pip install -r requirements.txt

Write-Host "==> Installing the FORMA package itself (editable mode, from pyproject.toml)..."
pip install -e .

Write-Host "============================================================"
Write-Host " NOTE: Optional / commented-out dependencies NOT installed:"
Write-Host "   - PaddleOCR / PaddlePaddle / paddlex / pytesseract"
Write-Host "     (only needed if you re-enable the PNG input pipeline)"
Write-Host "   - fastmcp / langchain-mcp-adapters / mcp"
Write-Host "     (only needed if you re-enable MCP support)"
Write-Host "   - Redrock (DESI redshift fitter) - separate git install,"
Write-Host "     required if REDROCK=true in your .env. See README.md"
Write-Host "     section 4 for instructions."
Write-Host "============================================================"

Write-Host "==> Setting up .env file..."
if ((Test-Path ".env_example") -and -not (Test-Path ".env")) {
    Copy-Item ".env_example" ".env"
    Write-Host "Created .env from .env_example - edit it before running."
} else {
    Write-Host "Skipped (.env already exists or .env_example not found)."
}

Write-Host "==> Done. Activate your environment with:"
Write-Host "    .\.venv\Scripts\Activate.ps1"
Write-Host "Then run the pipeline with:"
Write-Host "    python scripts\main.py"
