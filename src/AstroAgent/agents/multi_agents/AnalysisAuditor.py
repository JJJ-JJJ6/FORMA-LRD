# import json
# import os
# import numpy as np
# import logging

from AstroAgent.agents.common.state import SpectroState
from AstroAgent.agents.common.base_agent import BaseAgent
from AstroAgent.agents.common.result_writer import ResultWriter
from AstroAgent.core.runtime.runtime_container import RuntimeContainer


class AnalysisAuditor(BaseAgent):
    """
    审查分析师：先对每条分析路径进行独立的 critique+patch 讨论，再进行跨类型综合裁决。
    AnalysisAuditor: Runs per-path critique+patch discussion rounds first,
    then performs cross-type verdict on the patched extracts.
    """
    agent_name = "AnalysisAuditor"

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
    RESPONSE_KEYS = {
        "QSO":      "patch_response_QSO",
        "ELG":      "patch_response_ELG",
        "LRG/BGS":  "patch_response_LRG",
    }
    DEBATE_HISTORY_KEYS = {
        "QSO":      "debate_history_QSO",
        "ELG":      "debate_history_ELG",
        "LRG/BGS":  "debate_history_LRG",
    }

    def __init__(self, runtime: RuntimeContainer):
        super().__init__(runtime)
        self._writer = ResultWriter()

    @staticmethod
    def _all_paths_inconclusive(state: SpectroState) -> bool:
        """Return True when every extract path has only null-Hypothesis entries (or is absent)."""
        for key in ('extract_QSO', 'extract_ELG', 'extract_LRG'):
            items = (state.get(key) or {}).get('step_F') or []
            for item in items:
                if item.get('Hypothesis') is not None:
                    return False   # at least one valid hypothesis found
        return True

    async def run(self, state: SpectroState) -> SpectroState:
        """Phase 1: Per-path critique only (patching is handled by RefinementAssistant)."""
        if self._all_paths_inconclusive(state):
            print("[AnalysisAuditor] All paths inconclusive — skipping critique.")
            # Skip all remaining rounds: set counter >= max
            max_rounds = state.get('discussion_rounds') or self.runtime.configs.params.discussion_rounds
            state['current_discussion_round'] = max_rounds
            return state

        await self._run_per_path_critique(state)
        state['current_discussion_round'] = state.get('current_discussion_round', 0) + 1
        return state

    async def run_verdict(self, state: SpectroState) -> SpectroState:
        """Phase 3: Cross-type verdict (uses patched extracts when available)."""
        if self._all_paths_inconclusive(state):
            print("[AnalysisAuditor] All paths inconclusive — skipping verdict.")
            return state

        # Save full discussion record before entering verdict
        self._writer.write_discussion(state)
        await self.auditing_verdict(state)
        await self.verdict_extract(state)
        return state

    async def auditing_verdict(self, state: SpectroState) -> SpectroState:
        """Cross-type verdict: select the best hypothesis from extract_QSO/ELG/LRG.
        Uses raw extracts only; per-hypothesis discussion Q&A is passed as supplementary context."""
        function_name = "auditing_verdict"

        continuum_description = state['continuum']['description']
        feature_description = state['qualitative_analysis']['lines']
        wl_left = state['spectrum']['new_wavelength'][0]
        wl_right = state['spectrum']['new_wavelength'][-1]
        peaks = state['peaks']
        troughs = state['troughs']

        # Use raw extracts only (never patched).
        def _get_raw_extract(state_key: str):
            data = state.get(state_key) or {}
            step_f = data.get('step_F')
            return step_f if step_f else None

        extract_QSO = _get_raw_extract('extract_QSO')
        extract_ELG = _get_raw_extract('extract_ELG')
        extract_LRG = _get_raw_extract('extract_LRG')

        # Collect per-hypothesis discussion Q&A pairs (all rounds)
        def _build_discussion(debate_hist_key: str, critique_key: str, response_key: str):
            """Build discussion list for the verdict template.

            Merges all rounds from debate_history (completed rounds 1..N-1) plus the
            final round's critique/response (which hasn't been accumulated yet into
            debate_history). Returns a list of {critique, response} dicts — one per
            hypothesis. For multi-round, critique/response contain all rounds concatenated.
            """
            debate_hist = state.get(debate_hist_key) or []
            final_critiques = state.get(critique_key) or []
            final_responses = state.get(response_key) or []

            if not debate_hist and not final_critiques:
                return None

            # Single-round shortcut (most common case)
            if not debate_hist:
                result = []
                for i in range(max(len(final_critiques), len(final_responses))):
                    result.append({
                        "critique": final_critiques[i] if i < len(final_critiques) else "",
                        "response": final_responses[i] if i < len(final_responses) else "",
                    })
                return result if result else None

            # Multi-round: determine the number of hypotheses
            max_hypos = max(
                max((len(rd.get("hypotheses", [])) for rd in debate_hist), default=0),
                len(final_critiques),
                len(final_responses),
            )
            if max_hypos == 0:
                return None

            result = []
            for i in range(max_hypos):
                all_critique_parts = []
                all_response_parts = []
                # Previous rounds
                for rd in debate_hist:
                    hypos = rd.get("hypotheses", [])
                    if i < len(hypos):
                        c = hypos[i].get("critique", "")
                        r = hypos[i].get("response", "")
                        if c:
                            all_critique_parts.append(f"[Round {rd['round']}] {c}")
                        if r:
                            all_response_parts.append(f"[Round {rd['round']}] {r}")
                # Final round
                fc = final_critiques[i] if i < len(final_critiques) else ""
                fr = final_responses[i] if i < len(final_responses) else ""
                final_round_num = len(debate_hist) + 1
                if fc:
                    all_critique_parts.append(f"[Round {final_round_num}] {fc}")
                if fr:
                    all_response_parts.append(f"[Round {final_round_num}] {fr}")
                result.append({
                    "critique": "\n\n".join(all_critique_parts),
                    "response": "\n\n".join(all_response_parts),
                })
            return result if result else None

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            continuum_description=continuum_description,
            feature_description=feature_description,
            wl_left=wl_left,
            wl_right=wl_right,
            extract_QSO=extract_QSO,
            extract_ELG=extract_ELG,
            extract_LRG=extract_LRG,
            discussion_QSO=_build_discussion('debate_history_QSO', 'critique_QSO', 'patch_response_QSO'),
            discussion_ELG=_build_discussion('debate_history_ELG', 'critique_ELG', 'patch_response_ELG'),
            discussion_LRG=_build_discussion('debate_history_LRG', 'critique_LRG', 'patch_response_LRG'),
            peaks=peaks,
            troughs=troughs,
        )

        result = await self.call_llm_with_context(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            parse_json=False,
            description="Auditing verdict (cross-type)",
            want_tools=False,
        )

        state['verdict'] = result
        print(f"Auditing verdict:\n{result}")
        self._writer.write_verdict(state)
        return state

    async def verdict_extract(self, state: SpectroState) -> SpectroState:
        """Extract structured List[Dict] from the free-text auditing_verdict output."""
        function_name = "verdict_extract"

        if not state.get('verdict'):
            print("[verdict_extract] verdict is empty, skipping.")
            return state

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            verdict=state['verdict'],
        )

        result = await self.call_llm_with_context(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            parse_json=True,
            description="Verdict extract (structured)",
            want_tools=False,
        )

        # result may be a list directly or wrapped in a dict — normalise
        if isinstance(result, list):
            state['verdict_extract'] = result
        elif isinstance(result, dict):
            # some LLMs wrap the array under a key; try common keys first
            for key in ("result", "data", "items", "verdicts"):
                if key in result and isinstance(result[key], list):
                    state['verdict_extract'] = result[key]
                    break
            else:
                state['verdict_extract'] = [result]   # single-element fallback
        else:
            state['verdict_extract'] = []

        # ── Post-processing filter ──────────────────────────────────────────
        # Drop the 2nd entry when it is not genuinely distinct from the 1st:
        #   • Confidence of 2nd is "low", OR
        #   • |Δz| < 0.05  (same physical interpretation, different bookkeeping)
        VE = state['verdict_extract']
        if len(VE) == 2:
            z1 = VE[0].get("Suggested_redshift") or 0.0
            z2 = VE[1].get("Suggested_redshift") or 0.0
            c2 = (VE[1].get("Confidence") or "low").lower()
            drop = False
            reason = ""
            if c2 == "low":
                drop, reason = True, f"2nd Confidence=low"
            elif abs(z1 - z2) < 0.05:
                drop, reason = True, f"|Δz|={abs(z1-z2):.3f} < 0.05"
            if drop:
                print(f"  [filter] Dropping 2nd verdict entry ({reason})")
                state['verdict_extract'] = VE[:1]
        # ───────────────────────────────────────────────────────────────────

        print(f"Verdict extract ({len(state['verdict_extract'])} item(s)):")
        for i, item in enumerate(state['verdict_extract'], 1):
            print(f"  [{i}] {item.get('Source_path','?')} | {item.get('Confidence','?')} | z={item.get('Suggested_redshift','?')}")
        return state

    # ========================================================================
    # Per-path critique (patching is handled by RefinementAssistant)
    # ========================================================================

    async def _run_per_path_critique(self, state: SpectroState) -> None:
        """Run one round of per-hypothesis critique for all paths that have valid hypotheses.

        Each hypothesis within a path is critiqued independently.
        Critiques are stored as a list in state['critique_QSO/ELG/LRG'].

        Before generating new critiques for round N (N>1), the previous round's
        (critique, response) pairs are accumulated into debate_history so the new
        critique can reference prior discussion.

        debate_history structure:
          [{round: 1, hypotheses: [{critique, response}, ...]}, ...]
        """
        for source_path, state_key in self.PATH_KEYS.items():
            hypotheses = self._get_hypotheses(state, state_key)
            if hypotheses is None:
                print(f"[per-path critique] {source_path}: no valid hypotheses, skipping")
                continue

            # Accumulate previous round's discussion into debate_history
            prev_critiques = state.get(self.CRITIQUE_KEYS[source_path]) or []
            prev_responses = state.get(self.RESPONSE_KEYS[source_path]) or []
            if prev_critiques or prev_responses:
                debate_hist = state.get(self.DEBATE_HISTORY_KEYS[source_path]) or []
                round_num = len(debate_hist) + 1
                round_entry = {
                    "round": round_num,
                    "hypotheses": [
                        {
                            "critique": prev_critiques[j] if j < len(prev_critiques) else "",
                            "response": prev_responses[j] if j < len(prev_responses) else "",
                        }
                        for j in range(max(len(prev_critiques), len(prev_responses)))
                    ],
                }
                debate_hist.append(round_entry)
                state[self.DEBATE_HISTORY_KEYS[source_path]] = debate_hist
                print(f"[per-path critique] {source_path}: debate_history now has {len(debate_hist)} round(s)")

            total = len(hypotheses)
            critiques = []
            full_history = state.get(self.DEBATE_HISTORY_KEYS[source_path]) or []
            for i, hypo in enumerate(hypotheses):
                # Build per-hypothesis debate history: extract entry for hypothesis i from each round
                hypo_debate = []
                for rd in full_history:
                    hypos = rd.get("hypotheses", [])
                    if i < len(hypos) and (hypos[i].get("critique") or hypos[i].get("response")):
                        hypo_debate.append({
                            "round": rd["round"],
                            "critique": hypos[i]["critique"],
                            "response": hypos[i]["response"],
                        })
                print(f"[per-path critique] {source_path}: critiquing hypothesis {i+1}/{total}")
                critique = await self._per_path_auditing_critique(
                    state, source_path, hypo, i, total,
                    debate_history=hypo_debate if hypo_debate else None,
                )
                critiques.append(critique)

            state[self.CRITIQUE_KEYS[source_path]] = critiques

    def _get_hypotheses(self, state: SpectroState, state_key: str):
        """Extract the step_F hypothesis list from the raw extract.
        Always reads from the original (unpatched) extract.
        Returns None if no valid hypotheses exist."""
        data = state.get(state_key) or {}
        step_f = data.get('step_F')
        if step_f and any(item.get('Hypothesis') is not None for item in step_f):
            return step_f
        return None

    async def _per_path_auditing_critique(
        self, state: SpectroState, source_path: str,
        hypothesis: dict, index: int, total: int,
        debate_history: list | None = None,
    ) -> str | None:
        """Critique a single hypothesis within a path.

        Args:
            debate_history: List of {round, critique, response} dicts from
                previous discussion rounds for this specific hypothesis.
                None on the first round.
        """
        function_name = "auditing_critique"

        continuum_description = state['continuum']['description']
        feature_description = state['qualitative_analysis']['lines']
        wl_left = state['spectrum']['new_wavelength'][0]
        wl_right = state['spectrum']['new_wavelength'][-1]

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            continuum_description=continuum_description,
            feature_description=feature_description,
            wl_left=wl_left,
            wl_right=wl_right,
            hypothesis=hypothesis,
            source_path=source_path,
            hypothesis_index=index + 1,
            hypothesis_total=total,
            debate_history=debate_history,
        )

        result = await self.call_llm_with_context(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            parse_json=False,
            description=f"Per-path critique ({source_path} [{index+1}/{total}])",
            want_tools=False,
        )

        print(f"[per-path critique] {source_path} [{index+1}/{total}]:\n{result}")
        return result


# ============================================================================
# DISABLED / LEGACY METHODS — not called in current pipeline
# ============================================================================

# --- auditing (legacy QSO-only debate round) ---
#     async def auditing(self, state: SpectroState) -> SpectroState:
#         function_name = "auditing"
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
#         from AstroAgent.agents.multi_agents.utils.usage import find_overlap_regions, get_wiped_lines
#         rms = state.get("pixel_to_value", {}).get("x", {}).get("rms", 0)
#         tol = self.runtime.configs.params.tol_wavelength
#         rule_analysis = state['rule_analysis_QSO']
#         wl_left = state['spectrum']['new_wavelength'][0]
#         wl_right = state['spectrum']['new_wavelength'][-1]
#         arm_name = self.runtime.configs.params.arm_name
#         arm_wavelength_range = self.runtime.configs.params.arm_wavelength_range
#         if arm_name:
#             overlap_regions = find_overlap_regions(arm_name, arm_wavelength_range)
#             wiped_peaks = get_wiped_lines(state, overlap_regions)
#         debate_history = []
#         num_complete_rounds = min(
#             len(state['auditing_history_QSO']),
#             len(state['refining_history_QSO'])
#         )
#         if num_complete_rounds >= 1:
#             for i in range(num_complete_rounds):
#                 debate_history.append({
#                     "auditing": state['auditing_history_QSO'][i],
#                     "response": state['refining_history_QSO'][i],
#                 })
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
#             tol=tol
#         )
#         response = await self.call_llm_with_context(
#             system_prompt=system_prompt,
#             user_prompt=user_prompt,
#             parse_json=True,
#             description="Auditing",
#             want_tools=True
#         )
#         state['auditing_history_QSO'].append(response)
#         return state
