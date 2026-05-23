import json
# import os
import asyncio
# import numpy as np
# import matplotlib.pyplot as plt
# import logging

# from scipy.ndimage import gaussian_filter1d

from AstroAgent.agents.common.state import SpectroState
from AstroAgent.agents.common.base_agent import BaseAgent
from AstroAgent.agents.common.result_writer import ResultWriter
from AstroAgent.core.runtime.runtime_container import RuntimeContainer


# ---------------------------------------------------------
# 2. Rule-based Analyst — Responsible for rule-based physical analysis
# ---------------------------------------------------------
class RuleAnalyst(BaseAgent):
    """
    规则驱动型分析师：基于给定的物理与谱线知识进行定性分析
    Rule-based Analyst: Responsible for rule-based physical analysis
    """

    agent_name = "RuleAnalyst"

    def __init__(self, runtime: RuntimeContainer):
        super().__init__(runtime)
        self._writer = ResultWriter()

    async def run(self, state: SpectroState) -> SpectroState:
        await self.qualitative_analysis(state)
        await self.quantitative_analysis(state)

        return state


    async def qualitative_analysis(self, state: SpectroState) -> SpectroState:
        await self._describe_lines(state)
        self._writer.write_qualitative_analysis(state)

        await self._preliminary_classification(state)
        await self._preliminary_classification_monkey(state)
        self._writer.write_preliminary_classification(state)

        return state

    async def _describe_lines(self, state: SpectroState) -> SpectroState:
        function_name = 'describe_lines'

        peaks = state['peaks']
        troughs = state['troughs']

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            peaks=peaks,
            troughs=troughs
        )
        # print('describe_lines')
        # print(system_prompt)
        # print(user_prompt)
        result = await self.call_llm_with_context(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            parse_json=True,
            description="Describe lines",
            want_tools=False
        )

        statement = """
        
**Statement: This description is based on the qualitative output of a preliminary Gaussian-model peak-finding routine and is intended solely as a reference outline for subsequent processing. It does not constitute precise measurement results. The routine may introduce false peaks/valleys due to noise fitting or overfitting. The central wavelengths, widths, and amplitudes of certain peaks/valleys may deviate from actual values. Double-peak structures should be rigorously verified through quantitative analysis in conjunction with theoretical line ratios and redshift consistency. Final redshift determination and line identification must be based on standard line lists, with quantitative analysis taking precedence.**        
"""
        result = result + statement
        state['qualitative_analysis']['lines'] = result
        print(f"lines: \n{state['qualitative_analysis']['lines']}")
        return state

    async def _preliminary_classification(self, state: SpectroState) -> SpectroState:
        function_name = 'preliminary_classification'
        continuum_description = state['continuum']['description']
        feature_description = state['qualitative_analysis']['lines']
        peaks = state['peaks']
        troughs = state['troughs']

        # dataset = self.runtime.configs.params.dataset
        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            continuum_description=continuum_description,
            feature_description=feature_description,
            peaks=peaks,
            troughs=troughs,
        )
        # print('preliminary_classification')
        # print(system_prompt)
        # print(user_prompt)
        result = await self.call_llm_with_context(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_path=None,
            parse_json=True,
            description="Preliminary classification",
            want_tools=False
        )
        state['preliminary_classification'] = result
        print(f"preliminary_classification: \n{state['preliminary_classification']}")
        return state

    async def _preliminary_classification_monkey(self, state: SpectroState) -> SpectroState:
        function_name = 'preliminary_classification_monkey'
        preliminary_classification_with_absention = state['preliminary_classification']
        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            preliminary_classification_with_absention=preliminary_classification_with_absention
        )
        # print('preliminary_classification_with_absention_monkey')
        # print(system_prompt)
        # print(user_prompt)
        result = await self.call_llm_with_context(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_path=None,
            parse_json=True,
            description="Preliminary classification monkey",
            want_tools=False
        )
        # Normalise: LLM may return a single dict instead of a list
        if isinstance(result, dict):
            result = [result]
        state['preliminary_classification_monkey'] = result
        print(f"preliminary_classification_monkey: {state['preliminary_classification_monkey']}")
        return state


    async def quantitative_analysis(self, state: SpectroState) -> SpectroState:
        """ spectrum analysis

        QSO Scheduling:
        - step_0 (one-shot) and step_x (host-AGN) run concurrently — they are
          independent and write to separate dict keys.
        - step_F_pipeline starts only after both finish, so its LLM calls can
          benefit from any prefix-cache warm-up from step_0/step_x.
        """

        # QSO / ELG / LRG 各自独立检查对应的暴力匹配结果
        # 无结果时写入占位信息并跳过对应分支
        _has_qso = bool(state.get('brute_force_matching_qso'))
        _has_elg = bool(state.get('brute_force_matching_elg'))
        _has_lrg_bgs = bool(state.get('brute_force_matching_lrg_bgs'))

        def _no_match_placeholder():
            if not state.get('peaks'):
                return (
                    "No spectral peaks detected in this spectrum. "
                    "Quantitative analysis skipped."
                )
            return (
                "Spectral peaks were detected but no reliable line matches survived "
                "the validity filter (all candidates had fewer than 2 independent "
                "wavelength-line constraints). Quantitative analysis skipped."
            )

        if not _has_qso:
            state.setdefault('rule_analysis_QSO', {})['step_F'] = _no_match_placeholder()

        if not _has_elg:
            state.setdefault('rule_analysis_ELG', {})['step_F'] = _no_match_placeholder()

        if not _has_lrg_bgs:
            state.setdefault('rule_analysis_LRG', {})['step_F'] = _no_match_placeholder()

        # 若所有匹配均为空，无需继续
        if not _has_qso and not _has_elg and not _has_lrg_bgs:
            return state

        peaks = state['peaks']
        troughs = state['troughs']

        if _has_qso:
            await self.QSO_step_F_pipeline(state, peaks, troughs)
            await self.QSO_generic_extract(state, source_step="step_F")
            self._writer.write_rule_analysis_qso(state)

        if _has_elg:
            await self.ELG_step_F_pipeline(state, peaks, troughs)
            await self.ELG_generic_extract(state, source_step="step_F")
            self._writer.write_rule_analysis_elg(state)

        if _has_lrg_bgs:
            await self.LRG_step_F_pipeline(state, peaks, troughs)
            await self.LRG_generic_extract(state, source_step="step_F")
            self._writer.write_rule_analysis_lrg(state)

        return state

    async def QSO_step_F_pipeline(self, state: SpectroState, peaks, troughs) -> SpectroState:
        """QSO Step F map-reduce pipeline: F_a (per-hypothesis) → extract → F_b (synthesis)

        Scheduling strategy:
        - The first hypothesis is always run serially so the shared prompt prefix
          is written into the provider's KV-cache before subsequent requests fire.
        - Remaining hypotheses are dispatched concurrently (bounded by
          STEP_F_CONCURRENCY) to reduce wall-clock time while still benefiting
          from the prefix-cache warm-up done by the first call.
        - F_a and F_extract are pipelined per hypothesis (extract waits for its
          own F_a), but all remaining pairs run in parallel with each other.
        - F_b runs serially after all summaries are collected.
        """

        brute_force_matching = state.get('brute_force_matching_qso', [])
        if not brute_force_matching:
            return state

        concurrency = self.runtime.configs.params.step_f_concurrency
        total = len(brute_force_matching)

        # ── Helper: run F_a + extract for one hypothesis ───────────────────
        async def _run_one(match: dict, idx: int) -> dict:
            f_a_text = await self.QSO_step_F_a(
                state, peaks, troughs,
                match=match,
                hypothesis_index=idx + 1,
                hypothesis_total=total,
            )
            summary = await self.QSO_step_F_extract(
                state,
                f_a_text=f_a_text,
                hypothesis=match.get('Hypothesis', ''),
            )
            # print(f"QSO Step F-a [{idx+1}/{total}] extracted: {summary}")
            return summary

        # ── Step 1: First hypothesis — serial (warm up prefix cache) ───────
        summary_0 = await _run_one(brute_force_matching[0], 0)
        f_a_summaries = [summary_0]

        # ── Step 2: Remaining hypotheses — bounded concurrent ───────────────
        if total > 1:
            # concurrency=1 → fully serial fallback; >1 → parallel
            sem = asyncio.Semaphore(max(1, concurrency - 1))

            async def _bounded(match: dict, idx: int) -> dict:
                async with sem:
                    return await _run_one(match, idx)

            rest_summaries = await asyncio.gather(*[
                _bounded(m, i)
                for i, m in enumerate(brute_force_matching[1:], start=1)
            ])
            f_a_summaries.extend(rest_summaries)
        self._writer.write_f_a_summaries(state, f_a_summaries, label="QSO")

        # ── Step F-b: Synthesis — serial (needs all summaries) ─────────────
        result = await self.QSO_step_F_b(
            state, peaks, troughs,
            f_a_summaries=f_a_summaries,
        )
        state['rule_analysis_QSO']['step_F'] = result
        # print(f"QSO Step F-b (synthesis): {result}")
        return state

    async def QSO_step_F_a(
        self, state: SpectroState, peaks, troughs,
        match: dict, hypothesis_index: int, hypothesis_total: int,
    ) -> str:
        """QSO Step F-a: analyse a single brute-force hypothesis (returns raw LLM text)"""

        function_name = "QSO_step_F"   # 复用 step_F 目录的 prompt

        continuum_description = state['continuum']['description']
        feature_description = state['qualitative_analysis']['lines']
        wl_left = state['spectrum']['wavelength'][0]
        wl_right = state['spectrum']['wavelength'][-1]
        tol_wavelength = self.runtime.configs.params.tol_wavelength_qso
        preliminary_classification = None
        for i in state['preliminary_classification_monkey']:
            if i['Category'] == 'QSO':
                preliminary_classification = i

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            continuum_description=continuum_description,
            feature_description=feature_description,
            peaks=peaks,
            troughs=troughs,
            wl_left=wl_left,
            wl_right=wl_right,
            tol_wavelength=tol_wavelength,
            preliminary_classification=preliminary_classification,
            match=match,
            hypothesis_index=hypothesis_index,
            hypothesis_total=hypothesis_total,
        )

        result = await self.call_llm_with_context(
            system_prompt,
            user_prompt,
            parse_json=False,
            description=f"QSO Step F-a [{hypothesis_index}/{hypothesis_total}]",
            want_tools=False,
        )
        return result

    async def QSO_step_F_extract(
        self, state: SpectroState,
        f_a_text: str, hypothesis: str,
    ) -> dict:
        """QSO Step F-extract: parse F-3 conclusion from F-a reasoning text into JSON"""

        function_name = "QSO_step_F_extract"

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            f_a_text=f_a_text,
            hypothesis=hypothesis,
        )

        result = await self.call_llm_with_context(
            system_prompt,
            user_prompt,
            parse_json=True,
            description="QSO Step F-extract",
            want_tools=False,
        )
        # 若解析失败，保留原始文本以免中断流程
        if not isinstance(result, dict):
            result = {"hypothesis": hypothesis, "raw": result}
        return result

    async def QSO_step_F_b(
        self, state: SpectroState, peaks, troughs,
        f_a_summaries: list,
    ) -> str:
        """QSO Step F-b: synthesise all per-hypothesis summaries and give final verdict"""

        function_name = "QSO_step_F_b"

        continuum_description = state['continuum']['description']
        feature_description = state['qualitative_analysis']['lines']
        wl_left = state['spectrum']['wavelength'][0]
        wl_right = state['spectrum']['wavelength'][-1]
        tol_wavelength = self.runtime.configs.params.tol_wavelength_qso
        preliminary_classification = None
        for i in state['preliminary_classification_monkey']:
            if i['Category'] == 'QSO':
                preliminary_classification = i
        brute_force_matching = state.get('brute_force_matching_qso', [])

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            continuum_description=continuum_description,
            feature_description=feature_description,
            peaks=peaks,
            troughs=troughs,
            wl_left=wl_left,
            wl_right=wl_right,
            tol_wavelength=tol_wavelength,
            preliminary_classification=preliminary_classification,
            brute_force_matching=brute_force_matching,
            f_a_summaries=f_a_summaries,
        )

        # print(f"QSO Step F-b (user prompt): {user_prompt}")

        result = await self.call_llm_with_context(
            system_prompt,
            user_prompt,
            parse_json=False,
            description="QSO Step F-b (synthesis)",
            want_tools=False,
        )
        return result

    async def QSO_generic_extract(self, state: SpectroState, source_step: str) -> SpectroState:
        """Extract structured summary from any step's raw analysis text using a common schema."""

        function_name = "step_generic_extract"

        raw_text = state['rule_analysis_QSO'].get(source_step)
        if not raw_text:
            print(f"QSO generic_extract: no content for {source_step}, skipping")
            return state

        # Convert dict/list results to string for extraction
        if not isinstance(raw_text, str):
            raw_text = json.dumps(raw_text, indent=2, ensure_ascii=False)

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            source_step=source_step,
            analysis_text=raw_text,
        )

        result = await self.call_llm_with_context(
            system_prompt,
            user_prompt,
            parse_json=True,
            description=f"QSO generic_extract ({source_step})",
            want_tools=False,
        )

        state.setdefault('extract_QSO', {})[source_step] = result
        # print(f"QSO generic_extract [{source_step}]: {result}")
        return state

    async def ELG_step_F_pipeline(self, state: SpectroState, peaks, troughs) -> SpectroState:
        """ELG Step F map-reduce pipeline: F_a (per-hypothesis) → extract → F_b (synthesis)

        Scheduling strategy (mirrors QSO_step_F_pipeline exactly):
        - The first hypothesis is always run serially so the shared prompt prefix
          is written into the provider's KV-cache before subsequent requests fire.
        - Remaining hypotheses are dispatched concurrently (bounded by
          STEP_F_CONCURRENCY) to reduce wall-clock time while still benefiting
          from the prefix-cache warm-up done by the first call.
        - F_a and F_extract are pipelined per hypothesis (extract waits for its
          own F_a), but all remaining pairs run in parallel with each other.
        - F_b runs serially after all summaries are collected.
        """

        brute_force_matching = state.get('brute_force_matching_elg', [])
        if not brute_force_matching:
            return state

        concurrency = self.runtime.configs.params.step_f_concurrency
        total = len(brute_force_matching)

        # ── Helper: run F_a + extract for one hypothesis ───────────────────
        async def _run_one(match: dict, idx: int) -> dict:
            f_a_text = await self.ELG_step_F_a(
                state, peaks, troughs,
                match=match,
                hypothesis_index=idx + 1,
                hypothesis_total=total,
            )
            summary = await self.ELG_step_F_extract(
                state,
                f_a_text=f_a_text,
                hypothesis=match.get('Hypothesis', ''),
            )
            # print(f"ELG Step F-a [{idx+1}/{total}] extracted: {summary}")
            return summary

        # ── Step 1: First hypothesis — serial (warm up prefix cache) ───────
        summary_0 = await _run_one(brute_force_matching[0], 0)
        f_a_summaries = [summary_0]

        # ── Step 2: Remaining hypotheses — bounded concurrent ───────────────
        if total > 1:
            # concurrency=1 → fully serial fallback; >1 → parallel
            sem = asyncio.Semaphore(max(1, concurrency - 1))

            async def _bounded(match: dict, idx: int) -> dict:
                async with sem:
                    return await _run_one(match, idx)

            rest_summaries = await asyncio.gather(*[
                _bounded(m, i)
                for i, m in enumerate(brute_force_matching[1:], start=1)
            ])
            f_a_summaries.extend(rest_summaries)
        self._writer.write_f_a_summaries(state, f_a_summaries, label="ELG")

        # ── Step F-b: Synthesis — serial (needs all summaries) ─────────────
        result = await self.ELG_step_F_b(
            state, peaks, troughs,
            f_a_summaries=f_a_summaries,
        )
        state['rule_analysis_ELG']['step_F'] = result
        # print(f"ELG Step F-b (synthesis): {result}")
        return state

    async def ELG_step_F_a(
        self, state: SpectroState, peaks, troughs,
        match: dict, hypothesis_index: int, hypothesis_total: int,
    ) -> str:
        """ELG Step F-a: analyse a single brute-force hypothesis (returns raw LLM text)"""

        function_name = "ELG_step_F"

        continuum_description = state['continuum']['description']
        feature_description = state['qualitative_analysis']['lines']
        wl_left = state['spectrum']['wavelength'][0]
        wl_right = state['spectrum']['wavelength'][-1]
        tol_wavelength = self.runtime.configs.params.tol_wavelength_galaxy
        preliminary_classification = None
        for i in state['preliminary_classification_monkey']:
            if i['Category'] == 'ELG':
                preliminary_classification = i

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            continuum_description=continuum_description,
            feature_description=feature_description,
            peaks=peaks,
            troughs=troughs,
            wl_left=wl_left,
            wl_right=wl_right,
            tol_wavelength=tol_wavelength,
            preliminary_classification=preliminary_classification,
            match=match,
            hypothesis_index=hypothesis_index,
            hypothesis_total=hypothesis_total,
        )

        result = await self.call_llm_with_context(
            system_prompt,
            user_prompt,
            parse_json=False,
            description=f"ELG Step F-a [{hypothesis_index}/{hypothesis_total}]",
            want_tools=False,
        )
        return result

    async def ELG_step_F_extract(
        self, state: SpectroState,
        f_a_text: str, hypothesis: str,
    ) -> dict:
        """ELG Step F-extract: parse F-a conclusion into structured JSON"""

        function_name = "ELG_step_F_extract"

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            f_a_text=f_a_text,
            hypothesis=hypothesis,
        )

        result = await self.call_llm_with_context(
            system_prompt,
            user_prompt,
            parse_json=True,
            description="ELG Step F-extract",
            want_tools=False,
        )
        # 若解析失败，保留原始文本以免中断流程
        if not isinstance(result, dict):
            result = {"hypothesis": hypothesis, "raw": result}
        return result

    async def ELG_step_F_b(
        self, state: SpectroState, peaks, troughs,
        f_a_summaries: list,
    ) -> str:
        """ELG Step F-b: synthesise all per-hypothesis summaries and give final verdict"""

        function_name = "ELG_step_F_b"

        continuum_description = state['continuum']['description']
        feature_description = state['qualitative_analysis']['lines']
        wl_left = state['spectrum']['wavelength'][0]
        wl_right = state['spectrum']['wavelength'][-1]
        tol_wavelength = self.runtime.configs.params.tol_wavelength_galaxy
        preliminary_classification = None
        for i in state['preliminary_classification_monkey']:
            if i['Category'] == 'ELG':
                preliminary_classification = i
        brute_force_matching = state.get('brute_force_matching_elg', [])

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            continuum_description=continuum_description,
            feature_description=feature_description,
            peaks=peaks,
            troughs=troughs,
            wl_left=wl_left,
            wl_right=wl_right,
            tol_wavelength=tol_wavelength,
            preliminary_classification=preliminary_classification,
            brute_force_matching=brute_force_matching,
            f_a_summaries=f_a_summaries,
        )

        result = await self.call_llm_with_context(
            system_prompt,
            user_prompt,
            parse_json=False,
            description="ELG Step F-b (synthesis)",
            want_tools=False,
        )
        return result

    async def ELG_generic_extract(self, state: SpectroState, source_step: str) -> SpectroState:
        """Extract structured summary from ELG step_F raw text using ELG-specific schema."""

        function_name = "ELG_step_generic_extract"

        raw_text = state['rule_analysis_ELG'].get(source_step)
        if not raw_text:
            print(f"ELG generic_extract: no content for {source_step}, skipping")
            return state

        if not isinstance(raw_text, str):
            raw_text = json.dumps(raw_text, indent=2, ensure_ascii=False)

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            source_step=source_step,
            analysis_text=raw_text,
        )

        result = await self.call_llm_with_context(
            system_prompt,
            user_prompt,
            parse_json=True,
            description=f"ELG generic_extract ({source_step})",
            want_tools=False,
        )

        state.setdefault('extract_ELG', {})[source_step] = result
        # print(f"ELG generic_extract [{source_step}]: {result}")
        return state

    # ══════════════════════════════════════════════════════════════
    #  LRG / BGS  Step-F pipeline
    # ══════════════════════════════════════════════════════════════

    async def LRG_step_F_pipeline(self, state: SpectroState, peaks, troughs) -> SpectroState:
        """LRG/BGS Step F map-reduce pipeline: F_a (per-hypothesis) → extract → F_b (synthesis)

        Scheduling strategy mirrors QSO/ELG_step_F_pipeline exactly.
        """

        brute_force_matching = state.get('brute_force_matching_lrg_bgs', [])
        if not brute_force_matching:
            return state

        concurrency = self.runtime.configs.params.step_f_concurrency
        total = len(brute_force_matching)

        # ── Helper: run F_a + extract for one hypothesis ───────────────────
        async def _run_one(match: dict, idx: int) -> dict:
            f_a_text = await self.LRG_step_F_a(
                state, peaks, troughs,
                match=match,
                hypothesis_index=idx + 1,
                hypothesis_total=total,
            )
            summary = await self.LRG_step_F_extract(
                state,
                f_a_text=f_a_text,
                hypothesis=match.get('Hypothesis', ''),
            )
            # print(f"LRG Step F-a [{idx+1}/{total}] extracted: {summary}")
            return summary

        # ── Step 1: First hypothesis — serial (warm up prefix cache) ───────
        summary_0 = await _run_one(brute_force_matching[0], 0)
        f_a_summaries = [summary_0]

        # ── Step 2: Remaining hypotheses — bounded concurrent ───────────────
        if total > 1:
            sem = asyncio.Semaphore(max(1, concurrency - 1))

            async def _bounded(match: dict, idx: int) -> dict:
                async with sem:
                    return await _run_one(match, idx)

            rest_summaries = await asyncio.gather(*[
                _bounded(m, i)
                for i, m in enumerate(brute_force_matching[1:], start=1)
            ])
            f_a_summaries.extend(rest_summaries)
        self._writer.write_f_a_summaries(state, f_a_summaries, label="LRG_BGS")

        # ── Step F-b: Synthesis — serial (needs all summaries) ─────────────
        result = await self.LRG_step_F_b(
            state, peaks, troughs,
            f_a_summaries=f_a_summaries,
        )
        state['rule_analysis_LRG']['step_F'] = result
        # print(f"LRG Step F-b (synthesis): {result}")
        return state

    async def LRG_step_F_a(
        self, state: SpectroState, peaks, troughs,
        match: dict, hypothesis_index: int, hypothesis_total: int,
    ) -> str:
        """LRG/BGS Step F-a: analyse a single brute-force hypothesis (returns raw LLM text)"""

        function_name = "LRG_step_F"

        continuum_description = state['continuum']['description']
        feature_description = state['qualitative_analysis']['lines']
        wl_left = state['spectrum']['wavelength'][0]
        wl_right = state['spectrum']['wavelength'][-1]
        tol_wavelength = self.runtime.configs.params.tol_wavelength_galaxy
        preliminary_classification = None
        for i in state['preliminary_classification_monkey']:
            if i['Category'] == 'LRG/BGS':
                preliminary_classification = i

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            continuum_description=continuum_description,
            feature_description=feature_description,
            peaks=peaks,
            troughs=troughs,
            wl_left=wl_left,
            wl_right=wl_right,
            tol_wavelength=tol_wavelength,
            preliminary_classification=preliminary_classification,
            match=match,
            hypothesis_index=hypothesis_index,
            hypothesis_total=hypothesis_total,
        )

        result = await self.call_llm_with_context(
            system_prompt,
            user_prompt,
            parse_json=False,
            description=f"LRG Step F-a [{hypothesis_index}/{hypothesis_total}]",
            want_tools=False,
        )
        return result

    async def LRG_step_F_extract(
        self, state: SpectroState,
        f_a_text: str, hypothesis: str,
    ) -> dict:
        """LRG/BGS Step F-extract: parse F-a conclusion into structured JSON"""

        function_name = "LRG_step_F_extract"

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            f_a_text=f_a_text,
            hypothesis=hypothesis,
        )

        result = await self.call_llm_with_context(
            system_prompt,
            user_prompt,
            parse_json=True,
            description="LRG Step F-extract",
            want_tools=False,
        )
        if not isinstance(result, dict):
            result = {"hypothesis": hypothesis, "raw": result}
        return result

    async def LRG_step_F_b(
        self, state: SpectroState, peaks, troughs,
        f_a_summaries: list,
    ) -> str:
        """LRG/BGS Step F-b: synthesise all per-hypothesis summaries and give final verdict"""

        function_name = "LRG_step_F_b"

        continuum_description = state['continuum']['description']
        feature_description = state['qualitative_analysis']['lines']
        wl_left = state['spectrum']['wavelength'][0]
        wl_right = state['spectrum']['wavelength'][-1]
        tol_wavelength = self.runtime.configs.params.tol_wavelength_galaxy
        preliminary_classification = None
        for i in state['preliminary_classification_monkey']:
            if i['Category'] == 'LRG/BGS':
                preliminary_classification = i
        brute_force_matching = state.get('brute_force_matching_lrg_bgs', [])

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            continuum_description=continuum_description,
            feature_description=feature_description,
            peaks=peaks,
            troughs=troughs,
            wl_left=wl_left,
            wl_right=wl_right,
            tol_wavelength=tol_wavelength,
            preliminary_classification=preliminary_classification,
            brute_force_matching=brute_force_matching,
            f_a_summaries=f_a_summaries,
        )

        result = await self.call_llm_with_context(
            system_prompt,
            user_prompt,
            parse_json=False,
            description="LRG Step F-b (synthesis)",
            want_tools=False,
        )
        return result

    async def LRG_generic_extract(self, state: SpectroState, source_step: str) -> SpectroState:
        """Extract structured summary from LRG/BGS step_F raw text using LRG-specific schema."""

        function_name = "LRG_step_generic_extract"

        raw_text = state['rule_analysis_LRG'].get(source_step)
        if not raw_text:
            print(f"LRG generic_extract: no content for {source_step}, skipping")
            return state

        if not isinstance(raw_text, str):
            raw_text = json.dumps(raw_text, indent=2, ensure_ascii=False)

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            source_step=source_step,
            analysis_text=raw_text,
        )

        result = await self.call_llm_with_context(
            system_prompt,
            user_prompt,
            parse_json=True,
            description=f"LRG generic_extract ({source_step})",
            want_tools=False,
        )

        state.setdefault('extract_LRG', {})[source_step] = result
        # print(f"LRG generic_extract [{source_step}]: {result}")
        return state
