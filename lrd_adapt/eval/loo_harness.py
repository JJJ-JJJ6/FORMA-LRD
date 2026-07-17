"""
Leave-one-out evaluation harness (CLAUDE.md build order C).

"Leave-one-out" here means: each source is run through the pipeline with
access to ONLY its own data (its own spectrum, its own primary_hypotheses.json
entry, its own external_evidence.json entry) -- never a merged view of all
19 sources' answers, which could let a run "see" the answer key for the
source under test. This isolation is already structurally guaranteed by
lrd_adapt/hypothesis and lrd_adapt/evidence's per-SRC-code loaders (they
take one source_code and return one entry -- see test_anonymizer_isolation.py's
test_external_evidence_loader_returns_single_source_only et al.); this
module's job is to orchestrate running that already-isolated pipeline once
per source and collect the results, not to build a fresh isolation
mechanism.

Actually invoking the FORMA pipeline (calling scripts/main.py, or whatever
Docker entrypoint runs it) requires the langchain/langgraph stack and
Docker, neither available in this dev environment. run_source() below
builds and validates the run configuration either way; the actual
subprocess invocation is implemented but has not been exercised end-to-end
-- see the module docstring of lrd_adapt/eval/test_synthetic_injection.py
and every prior A1-C commit for the same caveat.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_json(relpath):
    path = os.path.join(REPO_ROOT, relpath)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def registered_loo_sources():
    """SRC codes with a primary hypothesis registered -- these are the ones
    that can actually be run (the hypothesis provider raises without one).
    Excludes the demo/synthetic-only SRC01 by convention if a real source
    list exists; for now (only SRC01 registered), returns it too."""
    return sorted(_load_json("lrd_adapt/configs/primary_hypotheses.json").keys())


def build_run_config(src_code, input_dir, output_dir, extra_env=None):
    """
    Build the environment-variable overrides for a single-source LOO run.

    Parameters
    ----------
    src_code : str
        Anonymized code (e.g. "SRC01"). This becomes FILE_NAME -- the
        pipeline never sees anything else identifying this source.
    input_dir : str
        Directory containing <src_code>.fits (the converter's output, A1).
    output_dir : str
        Where this source's run output tree goes (isolated per source).
    extra_env : dict, optional
        Additional overrides (e.g. LLM_MODEL, HARNESS_CONCURRENCY).

    Returns
    -------
    dict
        Environment variables for this run, layered on os.environ by the
        caller (run_source) or by hand for a manual Docker invocation.
    """
    fits_path = os.path.join(input_dir, f"{src_code}.fits")
    env = {
        "RUN_MODE": "s",
        "FILE_NAME": src_code,
        "INPUT_DIR": input_dir,
        "INPUT_FORMAT": "fits",
        "OUTPUT_DIR": output_dir,
        "HYPOTHESIS_PROVIDER": "lrd",
        "REDROCK": "false",
    }
    env.update(extra_env or {})
    return {"env": env, "fits_path": fits_path, "expected_report_path": os.path.join(output_dir, src_code, "final_report.md")}


def run_source(src_code, input_dir, output_dir, extra_env=None, dry_run=True, timeout=None):
    """
    Run (or validate the configuration for) a single LOO source.

    Parameters
    ----------
    dry_run : bool
        If True (default), only validates the config and checks the input
        FITS exists -- does not invoke the pipeline. Set False once Docker
        + the full dependency stack are available; this path is untested
        in this environment.

    Returns
    -------
    dict
        {"src_code", "config", "status", ...}. status is "dry_run_ok",
        "input_missing", "ran", or "failed".
    """
    config = build_run_config(src_code, input_dir, output_dir, extra_env)

    if not os.path.exists(config["fits_path"]):
        return {"src_code": src_code, "config": config, "status": "input_missing"}

    if dry_run:
        return {"src_code": src_code, "config": config, "status": "dry_run_ok"}

    env = dict(os.environ)
    env.update(config["env"])
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "scripts", "main.py")],
            env=env, cwd=REPO_ROOT, capture_output=True, text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"src_code": src_code, "config": config, "status": "failed", "error": "timeout"}

    if proc.returncode != 0:
        return {
            "src_code": src_code, "config": config, "status": "failed",
            "error": proc.stderr[-2000:],
        }

    return {"src_code": src_code, "config": config, "status": "ran", "stdout_tail": proc.stdout[-2000:]}


def run_loo_batch(input_dir, output_dir, src_codes=None, extra_env=None, dry_run=True):
    """Run (or dry-run) every registered LOO source. Returns {src_code: result}."""
    codes = src_codes if src_codes is not None else registered_loo_sources()
    return {code: run_source(code, input_dir, output_dir, extra_env, dry_run) for code in codes}
