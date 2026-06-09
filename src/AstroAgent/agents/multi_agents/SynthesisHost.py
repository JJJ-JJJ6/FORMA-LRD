"""
Synthesis Host — Final Report Writing.

Runs AFTER the Analysis Auditor.  Calls the new harness
(``harness/synthesize_host.py``) to produce a structured 6-section final
report.  The LLM writes the report via ``write_report`` tool and outputs a
JSON comprehensive assessment block — no separate extraction step needed.
"""

import json
import logging
import os

from AstroAgent.agents.common.state import SpectroState
from AstroAgent.agents.common.base_agent import BaseAgent
from AstroAgent.core.runtime.runtime_container import RuntimeContainer


class SynthesisHost(BaseAgent):
    """
    Synthesis Host: writes the final report and extracts structured summary.

    Calls ``synthesize_host.arun()`` which returns both the report Markdown
    and the parsed JSON comprehensive assessment.
    """

    agent_name = "SynthesisHost"

    def __init__(self, runtime: RuntimeContainer):
        super().__init__(runtime)

    async def run(self, state: SpectroState) -> SpectroState:
        """Write final report and extract structured summary."""
        from AstroAgent.agents.multi_agents.harness import synthesize_host

        harness_dir = state.get("harness_dir")
        if not harness_dir or not os.path.isdir(harness_dir):
            print("[SynthesisHost] No harness_dir — skipping.")
            state["final_report"] = None
            state["in_brief"] = {}
            return state

        # ── Build LLM kwargs from runtime config ──
        llm_cfg = self.runtime.configs.model.llm
        llm_kwargs = {
            "model": llm_cfg.get("model", "sonnet"),
            "api_key": llm_cfg.get("api_key"),
            "base_url": llm_cfg.get("base_url"),
            "temperature": 0.3,
        }

        print("[SynthesisHost] Writing final report ...")
        try:
            final_report, in_brief = await synthesize_host.arun(
                state, harness_dir, **llm_kwargs
            )
        except Exception as e:
            logging.warning(f"[SynthesisHost] Report writing failed: {e}")
            state["final_report"] = None
            state["in_brief"] = {}
            return state

        state["final_report"] = final_report
        state["in_brief"] = in_brief or {}
        print(f"[SynthesisHost] Report written. in_brief: {json.dumps(state['in_brief'], ensure_ascii=False) if state['in_brief'] else 'null'}")
        return state
