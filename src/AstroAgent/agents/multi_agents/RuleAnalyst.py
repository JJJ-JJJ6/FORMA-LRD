"""
RuleAnalyst — harness-driven parallel hypothesis verification.

Replaces the old QSO/ELG/LRG step-F pipeline with concurrent LLM agent runs
(one per redshift hypothesis), followed by a final LLM synthesis step.
"""

import os
import asyncio
import logging

import numpy as np

from AstroAgent.agents.common.state import SpectroState
from AstroAgent.agents.common.base_agent import BaseAgent
from AstroAgent.agents.common.result_writer import ResultWriter
from AstroAgent.core.runtime.runtime_container import RuntimeContainer

from AstroAgent.agents.multi_agents.harness import (
    arun as harness_arun,
    synthesize_arun,
)
from AstroAgent.agents.multi_agents.utils.RA import (
    collect_hypotheses,
    build_dn4000_lookup,
    a_extract_harness_summaries,
    extract_verdict_summary,
    analyze_failure,
    _record_failure,
    _read_pending_failures,
    analyze_failure_batch,
)


def _normalise_spectype(raw: str) -> str:
    """Map short FITS-metadata spectral types to the long forms used
    in synthesis classification."""
    mapping = {
        "QSO": "QSO",
        "ELG": "Galaxy (ELG)",
        "LRG": "Galaxy (LRG/BGS)",
        "BGS": "Galaxy (LRG/BGS)",
        "STAR": "Star",
        "GALAXY": "Galaxy",
    }
    return mapping.get(raw.upper().strip(), raw)


class RuleAnalyst(BaseAgent):
    """Harness-based parallel redshift hypothesis verification and ranking."""

    agent_name = "RuleAnalyst"

    def __init__(self, runtime: RuntimeContainer):
        super().__init__(runtime)
        self._writer = ResultWriter()

    # =====================================================================
    # Main entry point
    # =====================================================================

    async def run(self, state: SpectroState) -> SpectroState:
        scoring = state.get('redshift_scoring', {})
        hypotheses = collect_hypotheses(scoring)

        if not hypotheses:
            state['rule_analysis'] = {
                'verdict': 'UNKNOWN',
                'reason': 'No redshift hypotheses to test.',
                'redshift': None,
                'classification': None,
                'confidence': None,
            }
            return state

        # ── Phase 1: concurrent harness runs ─────────────────────
        harness_results = await self._run_targeted_search_batch(state, hypotheses)
        state['harness_results'] = harness_results

        # ── Phase 2: LLM synthesis ───────────────────────────────
        await self._synthesize(state, harness_results)

        # ── Phase 3: ground-truth check (only in self-evolve mode) ──
        if self.runtime.configs.params.self_evolve:
            await self._check_against_ground_truth(state, harness_results)

        # ── Write outputs ────────────────────────────────────────
        self._writer.write_rule_analysis(state)

        return state

    # =====================================================================
    # Concurrent harness execution
    # =====================================================================

    async def _run_targeted_search_batch(
        self, state: SpectroState, hypotheses: list
    ) -> list:
        """Run the first hypothesis serially to warm the KV cache (skill prompt +
        tool definitions), then fan out the rest with bounded concurrency."""
        model_cfg = self.runtime.configs.model.llm
        concurrency = self.runtime.configs.params.harness_concurrency
        sem = asyncio.Semaphore(max(1, concurrency))

        harness_dir = os.path.join(state['output_dir'], f"{state['file_name']}_harness")
        os.makedirs(harness_dir, exist_ok=True)

        overlap = state['spectrum'].get('overlap_regions')

        snr = state['spectrum'].get('snr')
        snr_median = float(np.median(snr)) if snr is not None else None

        async def _run_one(idx: int, hyp: dict) -> dict:
            try:
                spec = state['spectrum']
                result = await harness_arun(
                    fits_path=state['file_path'],
                    redshift=hyp['z'],
                    npz_path=state['spectrum_npz_path'],
                    hypothesis_idx=idx + 1,
                    wavelength_min=float(spec['wavelength'][0]),
                    wavelength_max=float(spec['wavelength'][-1]),
                    snr_median=snr_median,
                    peaks=state.get('peaks', []),
                    troughs=state.get('troughs', []),
                    z_min=round(hyp['z'] - 0.005, 4),
                    z_max=round(hyp['z'] + 0.005, 4),
                    masked_regions=overlap,
                    report_path=os.path.join(harness_dir, f'{idx + 1}_report.md'),
                    csv_path=os.path.join(harness_dir, f'{idx + 1}_lines.csv'),
                    stream_md_path=os.path.join(harness_dir, f'{idx + 1}_stream.md'),
                    model=model_cfg['model'],
                    api_key=model_cfg['api_key'],
                    base_url=model_cfg['base_url'],
                )
                result['hypothesis_meta'] = hyp
                return result

            except Exception as exc:
                logging.warning(
                    f"Harness hypothesis {idx} (z={hyp['z']:.4f}) failed: {exc}"
                )
                return {
                    'hypothesis_idx': idx + 1,
                    'redshift': hyp['z'],
                    'report': f'Harness execution failed: {exc}',
                    'structured_output': None,
                    'feature_catalog': [],
                    'hypothesis_meta': hyp,
                    'error': str(exc),
                }

        results = [None] * len(hypotheses)

        if len(hypotheses) >= 1:
            # ── First hypothesis: serial, warms the KV cache ──────
            results[0] = await _run_one(0, hypotheses[0])

        if len(hypotheses) >= 2:
            # ── Remaining hypotheses: concurrent (cache hits) ─────
            async def _run_with_sem(idx: int, hyp: dict) -> dict:
                async with sem:
                    return await _run_one(idx, hyp)

            tail_tasks = [
                _run_with_sem(i, h) for i, h in enumerate(hypotheses[1:], start=1)
            ]
            tail_results = await asyncio.gather(*tail_tasks)
            for i, r in enumerate(tail_results, start=1):
                results[i] = r

        return results

    # =====================================================================
    # LLM synthesis
    # =====================================================================

    async def _synthesize(
        self, state: SpectroState,
        harness_results: list,
    ) -> None:
        """LLM reviews all harness reports and delivers a final combined
        verdict.  Delegates to harness.synthesize.arun."""
        model_cfg = self.runtime.configs.model.llm
        spec = state['spectrum']
        wl = np.asarray(spec['wavelength'])
        fl = np.asarray(spec['flux'])
        snr = np.asarray(spec.get('snr', []))

        harness_dir = os.path.join(
            state['output_dir'], f"{state['file_name']}_harness"
        )

        # ── LLM-driven structured extraction (middleware) ──
        dn4000_lookup = build_dn4000_lookup(wl, fl, harness_results)
        summaries = await a_extract_harness_summaries(
            harness_results,
            dn4000_lookup,
            harness_dir=harness_dir,
            model=model_cfg['model'],
            api_key=model_cfg['api_key'],
            base_url=model_cfg['base_url'],
        )

        state['rule_analysis'] = await synthesize_arun(
            harness_results=harness_results,
            wl=wl,
            fl=fl,
            harness_dir=harness_dir,
            snr=snr,
            model=model_cfg['model'],
            api_key=model_cfg['api_key'],
            base_url=model_cfg['base_url'],
            stream_md_path=os.path.join(harness_dir, 'synthesis_stream.md'),
            summaries=summaries,
        )

    # =====================================================================
    # Ground-truth comparison & failure recording
    # =====================================================================

    async def _check_against_ground_truth(
        self, state: SpectroState,
        harness_results: list,
    ) -> None:
        """Compare synthesis result against ground truth.

        Ground truth is read from, in priority order:
        1. FITS METADATA (VI_Z / VI_SPECTYPE) — per-file, no .env needed
        2. Environment variables (EXPECTED_Z / EXPECTED_TYPE)

        Z_TOLERANCE is always read from env (default 0.005).
        """
        spec = state.get('spectrum', {})

        # Priority 1: FITS METADATA
        vi_z = spec.get('VI_Z')
        vi_type = spec.get('VI_SPECTYPE')

        # Priority 2: env vars (fallback)
        expected_z_str = os.environ.get("EXPECTED_Z")
        expected_type = os.environ.get("EXPECTED_TYPE")

        # Merge: FITS metadata wins over env
        if vi_z is not None:
            expected_z_str = str(vi_z)
        if vi_type is not None:
            expected_type = _normalise_spectype(vi_type)

        tolerance = self.runtime.configs.params.z_tolerance

        # Both must be available
        if expected_z_str is None or expected_type is None:
            if expected_z_str is None and expected_type is None:
                return
            logging.warning(
                "Both z and type must be available for ground-truth check. "
                "Got: z=%s, type=%s",
                expected_z_str, expected_type,
            )
            return

        try:
            expected_z = float(expected_z_str)
        except ValueError:
            logging.warning("Invalid EXPECTED_Z value: %s", expected_z_str)
            return

        verdict = state.get("rule_analysis", {})
        summary = extract_verdict_summary(verdict)

        # ── Pre-check: is the true redshift in the scoring candidates? ──
        scoring_zs = [r.get("redshift", 0) for r in harness_results
                      if not r.get("error")]
        min_dz = min((abs(z - expected_z) for z in scoring_zs), default=999)
        in_scoring = min_dz <= tolerance

        # ── Compare ──
        result_z = summary["redshift"]
        z_mismatch = (
            True if result_z is None
            else abs(result_z - expected_z) > tolerance
        )
        type_mismatch = summary["classification"] != expected_type

        if not z_mismatch and not type_mismatch:
            logging.info(
                "Ground-truth check PASSED: z=%.4f (expected %.4f, in_scoring=%s), type=%s",
                result_z if result_z else -1, expected_z, in_scoring,
                summary["classification"],
            )
            return  # Correct — nothing to record

        # LOW confidence + error → honest "don't know", skip
        if summary["confidence"] == "LOW":
            logging.info(
                "Ground-truth check: mismatch but confidence=LOW — skipping "
                "(honest abstention).  Result: z=%s type=%s; Expected: z=%.4f type=%s, in_scoring=%s",
                result_z, summary["classification"], expected_z, expected_type, in_scoring,
            )
            return

        # ── Not in scoring + confidence MEDIUM/HIGH → synthesis picked a wrong
        #     candidate instead of rejecting all.  Record for abstention learning.
        if not in_scoring:
            logging.warning(
                "Ground-truth MISMATCH (true z=%.4f NOT in scoring, min_dz=%.4f > tolerance=%.4f): "
                "synthesis picked z=%s type=%s (confidence=%s) instead of rejecting all.",
                expected_z, min_dz, tolerance,
                result_z, summary["classification"], summary["confidence"],
            )

        # ── Meaningful failure — analyze ──
        logging.warning(
            "Ground-truth MISMATCH (confidence=%s, in_scoring=%s): z=%s type=%s vs expected z=%.4f type=%s",
            summary["confidence"], in_scoring, result_z, summary["classification"],
            expected_z, expected_type,
        )

        model_cfg = self.runtime.configs.model.llm
        harness_dir = os.path.join(
            state["output_dir"], f"{state['file_name']}_harness"
        )

        ground_truth = {"z": expected_z, "type": expected_type}
        mismatch_info = {
            "z_mismatch": z_mismatch, "type_mismatch": type_mismatch,
            "in_scoring": in_scoring, "min_dz": min_dz,
        }

        analysis = await analyze_failure(
            synthesis_result=verdict,
            harness_results=harness_results,
            harness_dir=harness_dir,
            ground_truth=ground_truth,
            mismatch_info=mismatch_info,
            model=model_cfg["model"],
            api_key=model_cfg["api_key"],
            base_url=model_cfg["base_url"],
            stream_md_path=os.path.join(harness_dir, "failure_analysis_stream.md"),
        )

        _record_failure(
            synthesis_result=verdict,
            harness_dir=harness_dir,
            ground_truth=ground_truth,
            mismatch_info=mismatch_info,
            analysis=analysis,
            output_dir=state["output_dir"],
        )
        state["_failure_recorded"] = True

