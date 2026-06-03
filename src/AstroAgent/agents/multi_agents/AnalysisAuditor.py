"""
AnalysisAuditor — adversarial second review of the synthesis verdict.

Runs after the harness pipeline (targeted_search + synthesize).  Takes the
synthesis verdict and winning hypothesis, then independently stress-tests
every key claim by reading the raw spectrum.  Outputs a calibrated confidence
assessment: CONFIRM, DOWNGRADE, REJECT, or UNCERTAIN.
"""

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
# Skill path
# ---------------------------------------------------------------------------

SKILLS_DIR = Path(__file__).resolve().parent / "harness" / "skills"
AUDIT_SKILL_PATH = SKILLS_DIR / "auditor_audit_skill.md"


def _load_skill() -> str:
    return AUDIT_SKILL_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
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


def _extract_json_block(text: str) -> Optional[dict]:
    """Extract the JSON verdict block from the LLM response."""
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try bare { ... } at end
    m = re.search(r"\{[^{}]*\"verdict\"[^{}]*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# User message builder
# ---------------------------------------------------------------------------

def _build_audit_user_message(state: SpectroState, harness_dir: str) -> str:
    """Build the user prompt for synthesis audit.

    Includes the synthesis verdict, winning hypothesis details, and the
    2nd-best hypothesis for quick alternative checking.
    """
    rule_analysis = state.get("rule_analysis") or {}
    harness_results = state.get("harness_results") or []

    # ── Spectrum metadata ──
    wl = state["spectrum"]["wavelength"]
    wl_left = float(wl[0])
    wl_right = float(wl[-1])

    spec_lines = [
        f"## Spectrum",
        f"- Wavelength range: {wl_left:.0f} – {wl_right:.0f} Å",
    ]

    # Try to get median SNR from harness result metadata
    if harness_results:
        first_meta = harness_results[0].get("hypothesis_meta") or {}
        snr = first_meta.get("snr_median")
        if snr is not None:
            spec_lines.append(f"- Median SNR: {float(snr):.1f}")

    # ── Blue/red edge thresholds ──
    spec_lines.append(f"- **Blue edge**: {wl_left:.0f} – 4000 Å (throughput drop, non-Gaussian noise)")
    spec_lines.append(f"- **Red edge**: 9000 – {wl_right:.0f} Å (OH skyline residuals)")

    parts = ["\n".join(spec_lines), ""]

    # ── Synthesis verdict ──
    parts.append("## Synthesis Verdict (from synthesize.py)")
    parts.append("")
    parts.append("```json")
    parts.append(json.dumps(rule_analysis, indent=2, ensure_ascii=False, default=str))
    parts.append("```")
    parts.append("")

    # ── Winning hypothesis line data ──
    best_idx = rule_analysis.get("best_hypothesis_idx")
    if best_idx is not None and harness_results:
        # Find the winning harness result
        winner = None
        for r in harness_results:
            if r.get("hypothesis_idx") == best_idx:
                winner = r
                break

        if winner:
            parts.append("## Winning Hypothesis (H{}) — z={}".format(
                best_idx,
                winner.get("redshift", "?"),
            ))
            parts.append("")

            # Include lines.csv data if available
            csv_path = os.path.join(harness_dir, f"{best_idx}_lines.csv")
            if os.path.exists(csv_path):
                import csv as _csv
                rows = []
                with open(csv_path, newline="", encoding="utf-8") as f:
                    for row in _csv.DictReader(f):
                        rows.append(row)

                if rows:
                    # Show LIKELY lines first, then MARGINAL
                    likely = [r for r in rows if r.get("status", "").strip() == "LIKELY"]
                    marginal = [r for r in rows if r.get("status", "").strip() == "MARGINAL"]
                    other = [r for r in rows if r.get("status", "").strip() not in ("LIKELY", "MARGINAL")]

                    cols = list(rows[0].keys())
                    header = "| " + " | ".join(cols) + " |\n|" + "|".join(["------"] * len(cols)) + "|"

                    def _fmt_rows(rlist):
                        lines = []
                        for row in rlist:
                            vals = [(row.get(c) or "").strip() or "—" for c in cols]
                            lines.append("| " + " | ".join(vals) + " |")
                        return "\n".join(lines)

                    parts.append(f"### LIKELY ({len(likely)} lines)")
                    parts.append("")
                    parts.append(header)
                    parts.append(_fmt_rows(likely))
                    parts.append("")

                    if marginal:
                        parts.append(f"### MARGINAL ({len(marginal)} lines)")
                        parts.append("")
                        parts.append(header)
                        parts.append(_fmt_rows(marginal))
                        parts.append("")

                    if other:
                        parts.append(f"### Other ({len(other)} lines)")
                        parts.append("")
                        parts.append(header)
                        parts.append(_fmt_rows(other))
                        parts.append("")
                else:
                    parts.append("*(no line data found)*")
                    parts.append("")
            else:
                # Fall back to the report text
                report = winner.get("report", "")
                if report:
                    parts.append("<details>")
                    parts.append("<summary>Full harness report</summary>")
                    parts.append("")
                    parts.append(report)
                    parts.append("</details>")
                    parts.append("")

    # ── 2nd-best hypothesis ──
    excluded = rule_analysis.get("excluded_hypotheses") or []
    if excluded:
        parts.append("## Rejected Alternative (2nd-best)")
        parts.append("")
        # The first excluded hypothesis is typically the closest competitor
        first_excluded = excluded[0] if isinstance(excluded, list) else excluded
        if isinstance(first_excluded, dict):
            idx2 = first_excluded.get("idx")
            z2 = first_excluded.get("z")
            reason2 = first_excluded.get("reason", "no reason given")
            parts.append(f"- H{idx2} at z={z2}: {reason2}")
            parts.append("")

            # Include its lines.csv if available
            if idx2 is not None:
                csv_path2 = os.path.join(harness_dir, f"{idx2}_lines.csv")
                if os.path.exists(csv_path2):
                    import csv as _csv
                    likely2 = []
                    with open(csv_path2, newline="", encoding="utf-8") as f:
                        for row in _csv.DictReader(f):
                            if row.get("status", "").strip() == "LIKELY":
                                likely2.append(row)
                    if likely2:
                        parts.append(f"Its LIKELY lines ({len(likely2)}):")
                        for r in likely2:
                            name = r.get("name", "?")
                            wl_pred = r.get("predicted_obs", "?")
                            parts.append(f"  - {name} at λ_pred={wl_pred} Å")
                        parts.append("")

    # ── Task ──
    parts.append("## Task")
    parts.append("")
    parts.append(
        "Follow the Step 1 → Step 6 methodology from your system prompt. "
        "Your value is independent spectrum verification — you MUST call "
        "`read_spectrum_region` for every key claim. Read BOTH edge zones "
        "in full. Calibrate the confidence level. Output your reasoning in "
        "free text, then end with the JSON verdict block."
    )

    return "\n".join(parts)


# ============================================================================
# AnalysisAuditor
# ============================================================================

class AnalysisAuditor(BaseAgent):
    """Adversarial second reviewer for the synthesis verdict.

    Takes the synthesis output (rule_analysis + harness_results) and
    independently stress-tests the winning hypothesis by reading the raw
    spectrum.  Outputs a calibrated verdict: CONFIRM / DOWNGRADE / REJECT
    / UNCERTAIN.
    """

    agent_name = "AnalysisAuditor"

    def __init__(self, runtime: RuntimeContainer):
        super().__init__(runtime)
        self._writer = ResultWriter()

    # ========================================================================
    # Public entry point
    # ========================================================================

    async def run(self, state: SpectroState) -> SpectroState:
        """Run the synthesis audit.

        Reads ``state['rule_analysis']`` (the synthesis verdict) and
        ``state['harness_results']`` (per-hypothesis reports), then spawns
        an LLM agent with ``read_spectrum_region`` + ``grep_kb`` tools to
        independently verify the winning hypothesis.

        Writes ``state['auditor_verdict']`` (raw LLM response) and
        ``state['auditor_verdict_json']`` (parsed JSON).
        """
        rule_analysis = state.get("rule_analysis") or {}
        harness_results = state.get("harness_results") or []

        # ── Guard: no synthesis results ──
        if not rule_analysis or rule_analysis.get("redshift") is None:
            print("[AnalysisAuditor] No valid synthesis verdict — skipping audit.")
            state["auditor_verdict"] = "SKIPPED: no synthesis verdict"
            state["auditor_verdict_json"] = {
                "verdict": "UNCERTAIN",
                "calibrated_confidence": "LOW",
                "spectrum_quality": "unknown",
                "key_issues": ["No synthesis verdict to audit."],
                "recommendation": "Synthesis pipeline did not produce a valid result.",
            }
            return state

        if not harness_results:
            print("[AnalysisAuditor] No harness results — skipping audit.")
            state["auditor_verdict"] = "SKIPPED: no harness results"
            state["auditor_verdict_json"] = {
                "verdict": "UNCERTAIN",
                "calibrated_confidence": "LOW",
                "spectrum_quality": "unknown",
                "key_issues": ["No harness results available for audit."],
                "recommendation": "Harness pipeline did not produce results.",
            }
            return state

        print(
            "[AnalysisAuditor] Auditing synthesis verdict: z={}, classification={}, "
            "confidence={}".format(
                rule_analysis.get("redshift"),
                rule_analysis.get("classification", "?"),
                rule_analysis.get("confidence", "?"),
            )
        )

        # ── Resolve harness directory ──
        harness_dir = state.get("harness_dir")
        if not harness_dir:
            # Reconstruct from runtime config
            params = self.runtime.configs.params
            output_dir = params.output_dir or ""
            file_name = params.file_name or ""
            if output_dir and file_name:
                harness_dir = os.path.join(output_dir, f"{file_name}_harness")
            else:
                harness_dir = "."

        # ── Build prompts ──
        system_prompt = _load_skill()
        user_prompt = _build_audit_user_message(state, harness_dir)

        # ── Closure over spectrum arrays ──
        spec = state["spectrum"]
        _wl = np.asarray(spec["wavelength"])
        _fl = np.asarray(spec["flux"])

        @tool
        def read_spectrum_region(
            wl_min: float,
            wl_max: float,
            stride: int = 1,
        ) -> dict:
            """Read a raw slice of the spectrum for manual inspection.

            Use this to independently verify the winning hypothesis's key
            claims.  Read ±80 Å around each key line, and read BOTH edge
            zones in full (blue: λ_min→4000, red: 9000→λ_max).

            Parameters
            ----------
            wl_min, wl_max : float
                Wavelength range (Å).
            stride : int
                Downsampling step. Default 1. Use 2–5 for edge zones.

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

        # ── Build LLM ──
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

        # ── Run agent ──
        try:
            result = await agent.ainvoke(
                {"messages": [("user", user_prompt)]},
                config={"recursion_limit": 100},
            )
            messages = result.get("messages", [])
            if not messages:
                print("[AnalysisAuditor] LLM returned no messages.")
                state["auditor_verdict"] = "ERROR: no messages"
                state["auditor_verdict_json"] = {
                    "verdict": "UNCERTAIN",
                    "calibrated_confidence": "LOW",
                    "spectrum_quality": "unknown",
                    "key_issues": ["Auditor LLM returned no output."],
                    "recommendation": "Audit failed — LLM produced no messages.",
                }
                return state

            last_msg = messages[-1]
            raw_content = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

            state["auditor_verdict"] = raw_content

            # ── Parse JSON ──
            parsed = _extract_json_block(raw_content)
            if parsed is None:
                print("[AnalysisAuditor] Could not extract JSON from audit response.")
                state["auditor_verdict_json"] = {
                    "verdict": "UNCERTAIN",
                    "calibrated_confidence": "LOW",
                    "spectrum_quality": "unknown",
                    "key_issues": ["Failed to parse JSON from auditor response."],
                    "recommendation": f"Raw response (first 500 chars): {raw_content[:500]}",
                }
                return state

            state["auditor_verdict_json"] = parsed

            print(
                "[AnalysisAuditor] Verdict: {} | Confidence: {} | Quality: {}".format(
                    parsed.get("verdict", "?"),
                    parsed.get("calibrated_confidence", "?"),
                    parsed.get("spectrum_quality", "?"),
                )
            )
            if parsed.get("key_issues"):
                for issue in parsed["key_issues"]:
                    print(f"  ⚠ {issue}")

        except Exception as exc:
            logging.warning(f"[AnalysisAuditor] LLM call failed: {exc}")
            state["auditor_verdict"] = f"ERROR: {exc}"
            state["auditor_verdict_json"] = {
                "verdict": "UNCERTAIN",
                "calibrated_confidence": "LOW",
                "spectrum_quality": "unknown",
                "key_issues": [f"Auditor LLM failed: {exc}"],
                "recommendation": "Audit step errored — review manually.",
            }

        return state
