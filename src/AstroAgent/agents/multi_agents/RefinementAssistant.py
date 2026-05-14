# import json
# import os
# import logging

from AstroAgent.agents.common.state import SpectroState
from AstroAgent.agents.common.base_agent import BaseAgent
from AstroAgent.agents.common.result_writer import ResultWriter
from AstroAgent.core.runtime.runtime_container import RuntimeContainer


class RefinementAssistant(BaseAgent):
    """
    完善助手：负责 per-path 假设修补 + 最终报告撰写。
    Refinement Assistant: per-path hypothesis patching + final report synthesis.
    """
    agent_name = "RefinementAssistant"

    # Map source_path names to state keys
    PATH_KEYS = {
        "QSO":      "extract_QSO",
        "ELG":      "extract_ELG",
        "LRG/BGS":  "extract_LRG",
    }
    CRITIQUE_KEYS = {
        "QSO":      "critique_QSO",
        "ELG":      "critique_ELG",
        "LRG/BGS":  "critique_LRG",
    }
    PATCHED_KEYS = {
        "QSO":      "patched_extract_QSO",
        "ELG":      "patched_extract_ELG",
        "LRG/BGS":  "patched_extract_LRG",
    }
    RESPONSE_KEYS = {
        "QSO":      "patch_response_QSO",
        "ELG":      "patch_response_ELG",
        "LRG/BGS":  "patch_response_LRG",
    }

    def __init__(self, runtime: RuntimeContainer):
        super().__init__(runtime)
        self._writer = ResultWriter()

    async def run(self, state: SpectroState) -> SpectroState:
        """Per-path patching phase: iterate all paths and patch hypotheses based on critiques."""
        await self._per_path_refining_patch(state)
        return state

    async def run_final(self, state: SpectroState) -> SpectroState:
        """Final report phase: generate the complete analysis report."""
        await self.refining_final(state)
        self._writer.write_final_report(state)
        return state

    # ========================================================================
    # Per-path refining patch
    # ========================================================================

    async def _per_path_refining_patch(self, state: SpectroState) -> None:
        """Iterate QSO/ELG/LRG paths and respond to per-hypothesis critiques.

        For each hypothesis that has a critique, calls the refining_patch prompt
        to produce a text response. Responses are stored in patch_response_*.
        No hypothesis fields are modified — only critique responses are generated.
        """
        for source_path, state_key in self.PATH_KEYS.items():
            critiques = state.get(self.CRITIQUE_KEYS[source_path])
            if not critiques:
                print(f"[per-path patch] {source_path}: no critiques, skipping")
                continue

            hypotheses = self._get_hypotheses(state, state_key)
            if hypotheses is None:
                print(f"[per-path patch] {source_path}: no valid hypotheses, skipping")
                continue

            total = len(hypotheses)
            responses = []
            for i, (hypo, critique) in enumerate(zip(hypotheses, critiques)):
                print(f"[per-path patch] {source_path}: responding for hypothesis {i+1}/{total}")
                response = await self._respond_to_critique(state, source_path, hypo, critique, i, total)
                responses.append(response if response else "")

            response_key = self.RESPONSE_KEYS[source_path]
            state[response_key] = responses
            print(f"[per-path patch] {source_path}: stored {len(responses)} response(s)")

    def _get_hypotheses(self, state: SpectroState, state_key: str):
        """Extract the step_F hypothesis list from the raw extract.
        Always reads from the original (unpatched) extract."""
        data = state.get(state_key) or {}
        step_f = data.get('step_F')
        if step_f and any(item.get('Hypothesis') is not None for item in step_f):
            return step_f
        return None

    async def _respond_to_critique(
        self, state: SpectroState, source_path: str,
        hypothesis: dict, critique: str, index: int, total: int,
    ) -> str | None:
        """Respond to a single critique for a single hypothesis.
        Returns a natural-language response text (not a modified hypothesis)."""
        function_name = "refining_patch"

        continuum_description = state['continuum']['description']
        feature_description = state['qualitative_analysis']['lines']
        wl_left = state['spectrum']['new_wavelength'][0]
        wl_right = state['spectrum']['new_wavelength'][-1]
        peaks = state['peaks'] or []
        troughs = state['troughs'] or []

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            continuum_description=continuum_description,
            feature_description=feature_description,
            wl_left=wl_left,
            wl_right=wl_right,
            peaks=peaks,
            troughs=troughs,
            hypothesis=hypothesis,
            source_path=source_path,
            critique=critique,
            hypothesis_index=index + 1,
            hypothesis_total=total,
        )

        result = await self.call_llm_with_context(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            parse_json=False,
            description=f"Per-path patch response ({source_path} [{index+1}/{total}])",
            want_tools=False,
        )

        print(f"[per-path patch] {source_path} [{index+1}/{total}]:\n{result}")
        return result

    async def refining_final(self, state: SpectroState) -> SpectroState:
        """Generate the complete final analysis report from all pipeline stages."""
        function_name = "refining_final"

        # Run final report as long as there is at least one upstream artifact to summarise.
        has_upstream = (
            state.get('verdict')
            or state.get('extract_QSO') or state.get('extract_ELG') or state.get('extract_LRG')
            or state.get('preliminary_classification_monkey')
        )
        if not has_upstream:
            print("[refining_final] No upstream output available, skipping.")
            return state

        def _get_extract(key: str):
            data = state.get(key) or {}
            return data.get('step_F')

        continuum_description = state['continuum']['description']
        feature_description   = state['qualitative_analysis']['lines']
        wl_left  = state['spectrum']['new_wavelength'][0]
        wl_right = state['spectrum']['new_wavelength'][-1]
        peaks   = state['peaks'] or []
        troughs = state['troughs'] or []

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            wl_left=wl_left,
            wl_right=wl_right,
            continuum_description=continuum_description,
            feature_description=feature_description,
            preliminary_classification_monkey=state.get('preliminary_classification_monkey'),
            extract_QSO=_get_extract('extract_QSO'),
            extract_ELG=_get_extract('extract_ELG'),
            extract_LRG=_get_extract('extract_LRG'),
            verdict=state.get('verdict'),
            peaks=peaks,
            troughs=troughs,
        )

        result = await self.call_llm_with_context(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            parse_json=False,
            description="Refining final report",
            want_tools=False,
        )

        state['final_report'] = result
        print(f"Refining final report:\n{result}")
        return state


# ============================================================================
# DISABLED / LEGACY METHODS — not called in current pipeline
# ============================================================================

# --- refining (legacy QSO-only debate round) ---
#     async def refining(self, state: SpectroState) -> SpectroState:
#         function_name = "refining"
#
#         num_peaks = self.runtime.configs.params.num_peaks
#         num_troughs = self.runtime.configs.params.num_troughs
#
#         peak_list = [
#             {
#                 "wavelength": pe.get('wavelength'),
#                 "flux": pe.get('mean_flux'),
#                 "width": pe.get('width_mean'),
#                 "width_in_km_s": pe.get('width_in_km_s'),
#                 "prominance": pe.get('max_prominence'),
#                 "seen_in_max_global_smoothing_scale_sigma": pe.get('max_global_sigma_seen', None),
#                 "seen_in_max_local_smoothing_scale_sigma": pe.get('max_roi_sigma_seen', None),
#                 "describe": pe.get('describe')
#             }
#             for pe in state.get('cleaned_peaks', [])[:num_peaks]
#         ]
#         trough_list = [
#             {
#                 "wavelength": tr.get('wavelength'),
#                 "flux": tr.get('mean_flux'),
#                 "width": tr.get('width_mean'),
#                 "seen_in_scales_of_sigma": tr.get('seen_in_scales_of_sigma')
#             }
#             for tr in state.get('cleaned_troughs', [])[:num_troughs]
#         ]
#
#         rms = state.get("pixel_to_value", {}).get("x", {}).get("rms", 0)
#         tol = self.runtime.configs.params.tol_wavelength
#         rule_analysis = state['rule_analysis_QSO']
#         wl_left = state['spectrum']['new_wavelength'][0]
#         wl_right = state['spectrum']['new_wavelength'][-1]
#
#         arm_name = self.runtime.configs.params.arm_name
#         arm_wavelength_range = self.runtime.configs.params.arm_wavelength_range
#
#         if arm_name:
#             overlap_regions = find_overlap_regions(arm_name, arm_wavelength_range)
#             wiped_peaks = get_wiped_lines(state, overlap_regions)
#
#         rule_analysis = state['rule_analysis_QSO']
#
#         debate_history = []
#         num_complete_rounds = min(
#             len(state['auditing_history_QSO']),
#             len(state['refining_history_QSO'])
#         )
#         if num_complete_rounds >= 1:
#             for i in range(num_complete_rounds):
#                 auditing_history = state['auditing_history_QSO'][i]
#                 response_history = state['refining_history_QSO'][i]
#                 history = {
#                     "auditing": auditing_history,
#                     "response": response_history
#                 }
#                 debate_history.append(history)
#
#         latest_auditing = state['auditing_history_QSO'][-1]
#
#         system_prompt, user_prompt = self.runtime.prompt_manager.load(
#             state=state,
#             agent_name=self.agent_name,
#             function_name=function_name,
#             wl_left=wl_left,
#             wl_right=wl_right,
#             peak_list=peak_list,
#             trough_list=trough_list,
#             overlap_regions=overlap_regions if arm_name else None,
#             wiped_peaks=wiped_peaks if arm_name else None,
#             rule_analysis=rule_analysis,
#             debate_history=debate_history,
#             rms=rms,
#             tol=tol,
#             latest_auditing=latest_auditing
#         )
#
#         response = await self.call_llm_with_context(
#             system_prompt=system_prompt,
#             user_prompt=user_prompt,
#             parse_json=True,
#             description="Auditing",
#             want_tools=True
#         )
#
#         state['refining_history_QSO'].append(response)
#         return state
