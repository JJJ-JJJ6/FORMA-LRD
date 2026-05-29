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
    collect_hypotheses_from_bfm,
    build_dn4000_lookup,
    a_extract_harness_summaries,
)
from AstroAgent.agents.multi_agents.utils.plot import plot_harness_candidate


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
        hypotheses = collect_hypotheses_from_bfm(
            state.get('brute_force_matching', {})
        )

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

        # ── Phase 1b: plot adopted features for each candidate ──
        self._plot_harness_candidates(state, harness_results)

        # ── Phase 2: LLM synthesis ───────────────────────────────
        await self._synthesize(state, harness_results)

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
    # Plot harness candidate features
    # =====================================================================

    def _plot_harness_candidates(
        self, state: SpectroState, harness_results: list,
    ) -> None:
        """为每个 harness candidate 绘制采纳的 nearby features 图像。"""
        spec = state['spectrum']
        wavelength = spec['wavelength']
        flux = spec['flux']
        continuum = state.get('continuum', {})
        continuum_flux = continuum.get('flux', spec['flux'])

        harness_dir = os.path.join(
            state['output_dir'], f"{state['file_name']}_harness"
        )

        for result in harness_results:
            if result is None:
                continue
            idx = result.get('hypothesis_idx', 0)
            z = result.get('redshift', 0)
            csv_path = os.path.join(harness_dir, f"{idx}_lines.csv")
            output_path = os.path.join(harness_dir, f"{idx}_features.png")

            try:
                plot_harness_candidate(
                    wavelength=wavelength,
                    flux=flux,
                    continuum_flux=continuum_flux,
                    lines_csv_path=csv_path,
                    output_path=output_path,
                    redshift=z,
                    title=f"Candidate {idx}",
                )
            except Exception as exc:
                logging.warning(
                    f"Failed to plot harness candidate {idx} (z={z:.4f}): {exc}"
                )

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


