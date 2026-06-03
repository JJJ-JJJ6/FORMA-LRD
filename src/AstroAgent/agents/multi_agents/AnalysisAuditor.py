import json
import os
import re
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from langchain.agents import create_agent
from langchain_core.tools import tool

from AstroAgent.agents.common.state import SpectroState
from AstroAgent.agents.common.base_agent import BaseAgent
from AstroAgent.agents.common.result_writer import ResultWriter
from AstroAgent.core.runtime.runtime_container import RuntimeContainer
from AstroAgent.core.llm import _detect_vendor, _build_thinking_extra_body, _create_chat_openai
from AstroAgent.agents.multi_agents.harness.tools import grep_kb


# ---------------------------------------------------------------------------
# Skill paths
# ---------------------------------------------------------------------------

SKILLS_DIR = Path(__file__).resolve().parent / "harness" / "skills"
CRITIQUE_SKILL_PATH = SKILLS_DIR / "auditor_critique_skill.md"
VERDICT_SKILL_PATH = SKILLS_DIR / "auditor_verdict_skill.md"


def _load_skill(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers (copied from synthesize.py)
# ---------------------------------------------------------------------------

def _resolve_max_tokens() -> int | None:
    env_val = os.environ.get("LLM_MAX_TOKENS", "").strip()
    if env_val:
        try:
            return int(env_val)
        except ValueError:
            pass
    base_url = os.environ.get("LLM_BASE_URL", "")
    if "deepseek" in base_url.lower():
        return 16384
    return None


def _extract_json_block(text: str) -> Optional[Any]:
    """Extract a JSON block from LLM response."""
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try bare array or object at end
    m = re.search(r"(\[.*?\]|\{[^{}]*\"Source_path\"[^{}]*\})", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# User message builders
# ---------------------------------------------------------------------------

def _build_critique_user_message(
    state: SpectroState,
    source_path: str,
    hypothesis: dict,
    index: int,
    total: int,
    debate_history: list | None = None,
) -> str:
    """Build the user prompt for per-hypothesis critique."""
    continuum_description = state['continuum']['description']
    feature_description = state['qualitative_analysis']['lines']
    wl_left = state['spectrum']['wavelength'][0]
    wl_right = state['spectrum']['wavelength'][-1]

    parts = [
        f"## Spectrum Context",
        f"- Wavelength range: {wl_left:.0f} – {wl_right:.0f} Å",
        f"- Continuum description: {continuum_description}",
        f"- Feature description: {feature_description}",
        "",
        f"## Hypothesis under review",
        f"- Source path: **{source_path}**",
        f"- Hypothesis {index + 1} of {total} in this path",
        "",
        "```json",
        json.dumps(hypothesis, indent=2, ensure_ascii=False),
        "```",
    ]

    if debate_history:
        parts.append("")
        parts.append("## Previous Discussion Rounds (this hypothesis)")
        parts.append("")
        for entry in debate_history:
            parts.append(f"### Round {entry['round']}")
            parts.append(f"**Critique:** {entry['critique']}")
            parts.append(f"**Response:** {entry['response']}")
            parts.append("")

    parts.append("")
    parts.append("## Task")
    parts.append("Review this hypothesis from a skeptical perspective. Identify 1–4 specific doubts about physical plausibility, internal consistency, or missing key lines. Do NOT re-validate individual Adopted_pairs entries — focus on high-level structural consistency. Do NOT propose a new classification. Use `grep_kb` if you need to check classification rules.")

    return "\n".join(parts)


def _build_verdict_user_message(state: SpectroState) -> str:
    """Build the user prompt for cross-type verdict adjudication."""
    continuum_description = state['continuum']['description']
    feature_description = state['qualitative_analysis']['lines']
    wl_left = state['spectrum']['wavelength'][0]
    wl_right = state['spectrum']['wavelength'][-1]
    peaks = state.get('peaks', [])
    troughs = state.get('troughs', [])

    # Raw extracts
    def _get_raw_extract(state_key: str):
        data = state.get(state_key) or {}
        step_f = data.get('step_F')
        return step_f if step_f else None

    extract_QSO = _get_raw_extract('extract_QSO')
    extract_ELG = _get_raw_extract('extract_ELG')
    extract_LRG = _get_raw_extract('extract_LRG')

    # Discussion Q&A
    def _build_discussion(debate_hist_key: str, critique_key: str, response_key: str):
        debate_hist = state.get(debate_hist_key) or []
        final_critiques = state.get(critique_key) or []
        final_responses = state.get(response_key) or []

        if not debate_hist and not final_critiques:
            return None

        if not debate_hist:
            result = []
            for i in range(max(len(final_critiques), len(final_responses))):
                result.append({
                    "critique": final_critiques[i] if i < len(final_critiques) else "",
                    "response": final_responses[i] if i < len(final_responses) else "",
                })
            return result if result else None

        max_hypos = max(
            max((len(rd.get("hypotheses", [])) for rd in debate_hist), default=0),
            len(final_critiques), len(final_responses),
        )
        if max_hypos == 0:
            return None

        result = []
        for i in range(max_hypos):
            all_critique_parts = []
            all_response_parts = []
            for rd in debate_hist:
                hypos = rd.get("hypotheses", [])
                if i < len(hypos):
                    c = hypos[i].get("critique", "")
                    r = hypos[i].get("response", "")
                    if c:
                        all_critique_parts.append(f"[Round {rd['round']}] {c}")
                    if r:
                        all_response_parts.append(f"[Round {rd['round']}] {r}")
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

    discussion_QSO = _build_discussion('debate_history_QSO', 'critique_QSO', 'patch_response_QSO')
    discussion_ELG = _build_discussion('debate_history_ELG', 'critique_ELG', 'patch_response_ELG')
    discussion_LRG = _build_discussion('debate_history_LRG', 'critique_LRG', 'patch_response_LRG')

    parts = [
        f"## Spectrum Context",
        f"- Wavelength range: {wl_left:.0f} – {wl_right:.0f} Å",
        f"- Continuum: {continuum_description}",
        f"- Features: {feature_description}",
        "",
    ]

    # Peak/trough summary (abbreviated)
    if peaks:
        parts.append(f"### Peaks ({len(peaks)} total)")
        for p in peaks[:20]:
            parts.append(f"- {p.get('wavelength', '?'):.1f} Å, width={p.get('width_in_km_s', '?'):.0f} km/s")
        parts.append("")

    # Extracts
    for label, ext in [("extract_QSO", extract_QSO), ("extract_ELG", extract_ELG), ("extract_LRG", extract_LRG)]:
        parts.append(f"## {label}")
        if ext:
            parts.append("```json")
            parts.append(json.dumps(ext, indent=2, ensure_ascii=False))
            parts.append("```")
        else:
            parts.append("(no valid hypotheses)")
        parts.append("")

    # Discussion
    for label, disc in [("discussion_QSO", discussion_QSO), ("discussion_ELG", discussion_ELG), ("discussion_LRG", discussion_LRG)]:
        if disc:
            parts.append(f"## {label}")
            for i, d in enumerate(disc):
                parts.append(f"### Hypothesis {i+1}")
                parts.append(f"**Critique:** {d['critique']}")
                parts.append(f"**Response:** {d['response']}")
                parts.append("")

    parts.append("## Task")
    parts.append("Adjudicate across the three paths. Follow the V-1 → V-2 → V-3 methodology. Use `read_spectrum_region` to resolve ambiguities at discriminating wavelengths. Use `grep_kb` to check classification rules. Output your reasoning in free text, then end with the JSON verdict block.")

    return "\n".join(parts)


# ============================================================================
# AnalysisAuditor
# ============================================================================

class AnalysisAuditor(BaseAgent):
    """
    Harness-based auditor: per-path critique and cross-type verdict, now using
    LangChain agents with grep_kb and read_spectrum_region tools.
    """
    agent_name = "AnalysisAuditor"

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
        for key in ('extract_QSO', 'extract_ELG', 'extract_LRG'):
            items = (state.get(key) or {}).get('step_F') or []
            for item in items:
                if item.get('Hypothesis') is not None:
                    return False
        return True

    # ========================================================================
    # Public entry points (called by workflow orchestrator)
    # ========================================================================

    async def run(self, state: SpectroState) -> SpectroState:
        """Phase 1: Per-path critique only."""
        if self._all_paths_inconclusive(state):
            print("[AnalysisAuditor] All paths inconclusive — skipping critique.")
            max_rounds = state.get('discussion_rounds') or self.runtime.configs.params.discussion_rounds
            state['current_discussion_round'] = max_rounds
            return state

        await self._run_per_path_critique(state)
        state['current_discussion_round'] = state.get('current_discussion_round', 0) + 1
        return state

    async def run_verdict(self, state: SpectroState) -> SpectroState:
        """Phase 3: Cross-type verdict with harness agent (tools: grep_kb + read_spectrum_region)."""
        if self._all_paths_inconclusive(state):
            print("[AnalysisAuditor] All paths inconclusive — skipping verdict.")
            return state

        self._writer.write_discussion(state)
        await self.auditing_verdict(state)
        return state

    # ========================================================================
    # Per-path critique
    # ========================================================================

    async def _run_per_path_critique(self, state: SpectroState) -> None:
        """Run one round of per-hypothesis critique for all paths."""
        for source_path, state_key in self.PATH_KEYS.items():
            hypotheses = self._get_hypotheses(state, state_key)
            if hypotheses is None:
                print(f"[per-path critique] {source_path}: no valid hypotheses, skipping")
                continue

            # Accumulate previous round into debate_history
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
        """Critique a single hypothesis using harness agent with grep_kb."""

        system_prompt = _load_skill(CRITIQUE_SKILL_PATH)
        user_prompt = _build_critique_user_message(
            state, source_path, hypothesis, index, total, debate_history,
        )

        # Build LLM
        model = os.environ.get("LLM_MODEL", "deepseek-v4-pro")
        api_key = os.environ.get("LLM_API_KEY")
        base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")

        vendor = _detect_vendor(base_url)
        extra_body = (
            _build_thinking_extra_body("disabled", vendor)
            if vendor != "unknown"
            else None
        )

        llm = _create_chat_openai(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=0.1,
            max_tokens=_resolve_max_tokens(),
            extra_body=extra_body,
        )

        agent = create_agent(
            model=llm,
            tools=[grep_kb],
            system_prompt=system_prompt,
        )

        try:
            result = await agent.ainvoke(
                {"messages": [("user", user_prompt)]},
                config={"recursion_limit": 30},
            )
            messages = result.get("messages", [])
            if messages:
                last_msg = messages[-1]
                critique_text = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
                print(f"[per-path critique] {source_path} [{index+1}/{total}]:\n{critique_text[:500]}...")
                return critique_text
        except Exception as e:
            logging.warning(f"[per-path critique] LLM call failed for {source_path} [{index+1}/{total}]: {e}")
            return f"Critique generation failed: {e}"

        return "No critique generated."

    # ========================================================================
    # Cross-type verdict
    # ========================================================================

    async def auditing_verdict(self, state: SpectroState) -> SpectroState:
        """Cross-type verdict using full harness agent with grep_kb + read_spectrum_region."""

        system_prompt = _load_skill(VERDICT_SKILL_PATH)
        user_prompt = _build_verdict_user_message(state)

        # Extract spectrum arrays for read_spectrum_region closure
        spec = state['spectrum']
        _wl = np.asarray(spec['wavelength'])
        _fl = np.asarray(spec['flux'])

        @tool
        def read_spectrum_region(
            wl_min: float,
            wl_max: float,
            stride: int = 1,
        ) -> dict:
            """Read a raw slice of the spectrum for manual inspection.

            Use this to resolve cross-type ambiguities — when two hypotheses from
            different paths claim different line identifications for the same
            observed feature, read that wavelength region to determine which
            identification is correct. Also read around claimed AGN indicators
            (Mg II ±150 Å, Ne V ±50 Å) to verify feature authenticity.

            Parameters
            ----------
            wl_min, wl_max : float
                Wavelength range (Å).
            stride : int
                Downsampling step. Default 1. Use 2–5 for larger regions.

            Returns
            -------
            dict with keys: wl_range, n, wl, fl
            """
            mask = (_wl >= wl_min) & (_wl <= wl_max)
            wl_slice = _wl[mask][::stride]
            fl_slice = _fl[mask][::stride]
            return {
                "wl_range": [wl_min, wl_max],
                "n": len(wl_slice),
                "wl": [round(float(w), 3) for w in wl_slice],
                "fl": [round(float(f), 4) for f in fl_slice],
            }

        # Build LLM
        model = os.environ.get("LLM_MODEL", "deepseek-v4-pro")
        api_key = os.environ.get("LLM_API_KEY")
        base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")

        vendor = _detect_vendor(base_url)
        extra_body = (
            _build_thinking_extra_body("disabled", vendor)
            if vendor != "unknown"
            else None
        )

        llm = _create_chat_openai(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=0.1,
            max_tokens=_resolve_max_tokens(),
            extra_body=extra_body,
        )

        agent = create_agent(
            model=llm,
            tools=[read_spectrum_region, grep_kb],
            system_prompt=system_prompt,
        )

        try:
            result = await agent.ainvoke(
                {"messages": [("user", user_prompt)]},
                config={"recursion_limit": 100},
            )
            messages = result.get("messages", [])
            if not messages:
                state['verdict_extract'] = []
                return state

            last_msg = messages[-1]
            raw_content = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

            # Store raw text (for debugging / backward compat)
            state['verdict'] = raw_content
            self._writer.write_verdict(state)

            # Extract JSON verdict
            parsed = _extract_json_block(raw_content)
            if parsed is None:
                print("[auditing_verdict] Could not extract JSON from verdict response")
                state['verdict_extract'] = []
                return state

            # Normalise
            if isinstance(parsed, list):
                verdict_list = parsed
            elif isinstance(parsed, dict):
                for key in ("result", "data", "items", "verdicts"):
                    if key in parsed and isinstance(parsed[key], list):
                        verdict_list = parsed[key]
                        break
                else:
                    verdict_list = [parsed]
            else:
                verdict_list = []

            # Post-processing filter (same as before)
            if len(verdict_list) == 2:
                z1 = verdict_list[0].get("Suggested_redshift") or 0.0
                z2 = verdict_list[1].get("Suggested_redshift") or 0.0
                c2 = (verdict_list[1].get("Confidence") or "low").lower()
                drop = False
                reason = ""
                if c2 == "low":
                    drop, reason = True, "2nd Confidence=low"
                elif abs(z1 - z2) < 0.05:
                    drop, reason = True, f"|Δz|={abs(z1 - z2):.3f} < 0.05"
                if drop:
                    print(f"  [filter] Dropping 2nd verdict entry ({reason})")
                    verdict_list = verdict_list[:1]

            state['verdict_extract'] = verdict_list
            self._writer.write_verdict_extract(state)
            print(f"Verdict extract ({len(state['verdict_extract'])} item(s)):")
            for i, item in enumerate(state['verdict_extract'], 1):
                print(f"  [{i}] {item.get('Source_path','?')} | {item.get('Confidence','?')} | z={item.get('Suggested_redshift','?')}")

        except Exception as e:
            logging.warning(f"[auditing_verdict] LLM call failed: {e}")
            state['verdict_extract'] = []

        return state
