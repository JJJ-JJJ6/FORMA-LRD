"""
synthesize.py — LLM-driven redshift hypothesis synthesis.

Equips an LLM with a spectrum-reading tool and a synthesis methodology skill
prompt.  The LLM cross-compares multiple harness reports (one per redshift
hypothesis), optionally reads raw spectrum regions, and delivers a final
combined verdict.

This is Phase 2 of the RuleAnalyst pipeline (Phase 1 = per-hypothesis harness
runs via targeted_search.py).
"""

import json
import os
import re
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain_core.tools import tool

from AstroAgent.core.llm import _detect_vendor, _build_thinking_extra_body
from AstroAgent.agents.multi_agents.utils.RA import (
    prepare_diagnostic_slices,
    build_dn4000_lookup,
    extract_harness_summary,
)
from AstroAgent.agents.multi_agents.harness.tools import grep_kb, write_report, write_synthesis_csv


# ---------------------------------------------------------------------------
# Skill prompt
# ---------------------------------------------------------------------------

SKILL_PATH = Path(__file__).resolve().parent / "skills" / "synthesize_skill.md"


def _load_skill() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# JSON extraction from LLM response
# ---------------------------------------------------------------------------

def _extract_json_block(text: str) -> Optional[Dict[str, Any]]:
    """Extract the final JSON verdict block from the LLM response."""
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try bare { ... } at end
    m = re.search(r"\{[^{}]*\"redshift\"[^{}]*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# User message builder
# ---------------------------------------------------------------------------

def _build_user_message(
    harness_results: list,
    harness_dir: str,
    wl: np.ndarray,
    fl: np.ndarray,
    snr: np.ndarray | None = None,
    summaries: list[str] | None = None,
    mode: str = "nomad",
    report_path: str | None = None,
    csv_path: str | None = None,
) -> str:
    """Build the user prompt with spectrum metadata, harness reports, and
    pre-computed Dn4000 diagnostics.

    Parameters
    ----------
    summaries : list[str] or None
        Pre-built markdown summaries (one per hypothesis). If None, they
        are built on the fly via :func:`extract_harness_summary`.
    mode : str
        "nomad" or "redrock". Redrock-mode reports may contain fit-derived
        measurements alongside CWT features.
    """

    # ── Build Dn4000 lookup ──
    dn4000_lookup = build_dn4000_lookup(wl, fl, harness_results)

    # ── Collect report sections ──
    report_sections = []
    for i, r in enumerate(harness_results):
        idx = r["hypothesis_idx"]
        report_text = r.get("report", "")
        if not report_text:
            report_path = os.path.join(harness_dir, f"{idx}_report.md")
            if os.path.exists(report_path):
                report_text = Path(report_path).read_text(encoding="utf-8")

        # Use pre-built summary if available, otherwise fall back
        if summaries and i < len(summaries):
            report_sections.append(summaries[i])
        else:
            r_with_report = {**r, "report": report_text}
            report_sections.append(
                extract_harness_summary(r_with_report, dn4000_lookup)
            )

        # Full report in collapsible section for Phase 2 deep-dive
        if report_text and not r.get("error"):
            report_sections.append(
                "<details>\n<summary>Full report</summary>\n\n"
                + report_text
                + "\n</details>\n\n"
            )

    # ── Spectrum metadata ──
    spec_wl_range = f"{float(wl[0]):.0f} – {float(wl[-1]):.0f}"
    _snr = np.asarray(snr) if snr is not None else np.array([])
    snr_median = float(np.median(_snr)) if len(_snr) > 0 else None

    # ── Dn4000 diagnostics ──
    diagnostic_slices = prepare_diagnostic_slices(wl, fl, harness_results)

    # ── Mode-specific note ──
    _mode_note = ""
    if mode == "redrock":
        _mode_note = (
            "\n**Note**: These harness reports come from redrock-mode runs. "
            "Some line measurements may be fit-derived (fit_peak, fit_doublet) "
            "rather than CWT-adopted. Fit-derived lines have no ridge_length "
            "or cwt_snr but provide delta_chi2_per_n and local_snr quality "
            "metrics. Treat local_snr > 10 as roughly equivalent to "
            "cwt_snr > 10 + ridge_length >= 5. For doublet fits, the "
            "separation check is the strongest validation — a matched doublet "
            "spacing provides independent confirmation of both the redshift "
            "and the line identification.\n"
        )

    _output_paths = ""
    if report_path:
        _output_paths += f"Output Synthesis Report: {report_path}\n"
    if csv_path:
        _output_paths += f"Output Synthesis CSV: {csv_path}\n"

    return f"""## Spectrum

- Wavelength range: {spec_wl_range} Å
- Median SNR: {f'{snr_median:.1f}' if snr_median else 'N/A'}
- Number of hypotheses tested: {len(harness_results)}
{_mode_note}
{_output_paths}
## Harness Report Summaries

Each summary distills the key structured information from the per-hypothesis
harness run. Expand the <details> sections to read the full report when a
deeper look at a specific hypothesis is needed.

{''.join(report_sections)}

## Pre-computed Dn4000 Diagnostics

{diagnostic_slices}

## Task

Follow the Phase 1 → Phase 2 → Phase 3 → Phase 4 strategy from your system prompt.

Phase 1: Blind review — analyse the harness summaries WITHOUT calling
read_spectrum_region. Build the contradiction matrix from the LIKELY/MARGINAL
line lists, check internal consistency per hypothesis. The contradiction matrix
IS your Phase 2 read list: each wavelength where two hypotheses claim different
rest-frame lines identifies a discriminating window to read later.

If one hypothesis has overwhelming unique advantage, skip Phase 2 and deliver
your verdict.

Phase 2 (only if needed): Use `read_spectrum_region` to examine the specific
wavelength windows from the contradiction matrix that discriminate between
remaining plausible hypotheses. Read as little data as possible — target each
read at a specific question.

Phase 3: Write the synthesis CSV (one row per hypothesis) via `write_synthesis_csv`,
then write the synthesis report via `write_report`.

Phase 4: Output the final verdict as a JSON block per the specification.
"""


# ---------------------------------------------------------------------------
# Formatting helpers (for streaming output)
# ---------------------------------------------------------------------------

def _format_tool_call(tc) -> str:
    """Format a single tool call as readable markdown."""
    name = tc.get("name", "unknown")
    args = tc.get("args", {})
    args_json = json.dumps(args, indent=2, ensure_ascii=False)
    return f"**`{name}`**\n```json\n{args_json}\n```"


def _format_tool_result(msg) -> str:
    """Format a tool result message as readable markdown."""
    content = msg.content if hasattr(msg, "content") else str(msg)
    try:
        parsed = json.loads(content) if isinstance(content, str) else content
        return f"```json\n{json.dumps(parsed, indent=2, ensure_ascii=False)}\n```"
    except (json.JSONDecodeError, TypeError):
        return str(content)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def arun(
    harness_results: list,
    wl: np.ndarray,
    fl: np.ndarray,
    harness_dir: str,
    *,
    snr: np.ndarray | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.1,
    max_turns: int = 30,
    stream_md_path: str | None = None,
    summaries: list[str] | None = None,
    mode: str = "nomad",
    report_path: str | None = None,
    csv_path: str | None = None,
) -> dict:
    """Run the LLM synthesis agent over multiple harness reports.

    Parameters
    ----------
    harness_results : list
        Results from per-hypothesis harness runs. Each dict has keys:
        hypothesis_idx, redshift, report, structured_output,
        feature_catalog, and optionally error.
    wl, fl : np.ndarray
        Wavelength and flux arrays for the spectrum.
    harness_dir : str
        Directory containing per-hypothesis ``{idx}_report.md`` files
        (used as fallback if report text is not in *harness_results*).
    snr : np.ndarray, optional
        SNR array. Used for the median SNR line in the user prompt.
    model, api_key, base_url : str, optional
        LLM configuration. Defaults to environment variables.
    temperature : float
        LLM temperature (default 0.1).
    max_turns : int
        Maximum agent turns / recursion_limit (default 30).
    stream_md_path : str, optional
        If set, stream the full conversation (system prompt + every LLM
        turn + tool calls + tool results) to this .md file in real time.

    Returns
    -------
    dict
        The parsed synthesis verdict with keys: redshift, anchor_line,
        anchor_wavelength, wavelength_error,
        classification, confidence, best_hypothesis_idx, primary_evidence,
        excluded_hypotheses, caveats.
    """
    model = model or os.environ.get("LLM_MODEL", "deepseek-v4-pro")
    api_key = api_key or os.environ.get("LLM_API_KEY")
    base_url = base_url or os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")

    system_prompt = _load_skill()
    user_prompt = _build_user_message(harness_results, harness_dir, wl, fl, snr, summaries,
                                      mode=mode, report_path=report_path, csv_path=csv_path)

    # ── Closure over arrays for zero-copy slicing ────────────────
    _wl = wl
    _fl = fl

    @tool
    def read_spectrum_region(
        wl_min: float,
        wl_max: float,
        stride: int = 1,
    ) -> dict:
        """Read a raw slice of the spectrum for manual inspection.

        Use this to investigate specific wavelength windows identified in
        Phase 2 of the synthesis strategy — e.g. check for the 4000 Å
        break, Ca K/H doublet, Balmer decrement, or continuum shape at
        discriminating wavelengths. Read 100–300 Å windows centered on
        the features that differentiate competing hypotheses.

        Parameters
        ----------
        wl_min, wl_max : float
            Wavelength range of interest (Å).
        stride : int
            Downsampling step. Default 1 (no downsampling). Use 2–5 for
            larger regions to keep output manageable.

        Returns
        -------
        dict with keys:
            wl_range : [float, float]
            n : int
            wl : list[float]  — wavelengths (Å)
            fl : list[float]  — flux values
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

    # ── Build LLM (thinking disabled for multi-turn tool calling) ─
    vendor = _detect_vendor(base_url)
    extra_body = (
        _build_thinking_extra_body("disabled", vendor)
        if vendor != "unknown"
        else None
    )

    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        extra_body=extra_body,
    )

    agent = create_agent(
        model=llm,
        tools=[read_spectrum_region, grep_kb, write_report, write_synthesis_csv],
        system_prompt=system_prompt,
    )

    config = {"recursion_limit": max_turns}

    # ── Streaming path ────────────────────────────────────────────
    if stream_md_path:
        os.makedirs(os.path.dirname(stream_md_path) or ".", exist_ok=True)

        md = open(stream_md_path, "w", encoding="utf-8")
        md.write("# Synthesis\n\n")
        md.write("---\n\n")
        md.write("<details>\n<summary>System Prompt</summary>\n\n")
        md.write(system_prompt)
        md.write("\n</details>\n\n---\n\n")
        md.write(f"### User\n\n{user_prompt}\n\n")
        md.flush()

        accumulated_messages = []
        turn = 0

        try:
            async for event in agent.astream(
                {"messages": [("user", user_prompt)]},
                config=config,
                stream_mode="updates",
            ):
                for _node_name, update in event.items():
                    msgs = update.get("messages", [])
                    for msg in msgs:
                        accumulated_messages.append(msg)
                        msg_type = getattr(msg, "type", None)

                        if msg_type == "ai":
                            turn += 1
                            content = msg.content if hasattr(msg, "content") else ""
                            tool_calls = getattr(msg, "tool_calls", None)
                            lines = []
                            if content:
                                lines.append(content.strip())
                            if tool_calls:
                                for tc in tool_calls:
                                    lines.append(_format_tool_call(tc))
                            if lines:
                                md.write(f"### Assistant (turn {turn})\n\n")
                                md.write("\n\n".join(lines))
                                md.write("\n\n")
                                md.flush()

                        elif msg_type == "tool":
                            md.write("### Tool Result\n\n")
                            md.write(_format_tool_result(msg))
                            md.write("\n\n")
                            md.flush()
        except Exception as exc:
            md.write(f"\n\n> ❌ Synthesis streaming failed: {exc}\n\n")
            md.flush()
            md.close()
            raise
        finally:
            md.close()

        last_msg = accumulated_messages[-1] if accumulated_messages else None
        raw_content = (
            last_msg.content if last_msg and hasattr(last_msg, "content") else ""
        )

        parsed = _extract_json_block(raw_content)
        if parsed is None:
            parsed = {
                "redshift": None,
                "anchor_line": None,
                "anchor_wavelength": None,
                "wavelength_error": None,
                "classification": "Unknown",
                "confidence": "LOW",
                "best_hypothesis_idx": None,
                "primary_evidence": "Failed to parse JSON from synthesis response.",
                "caveats": raw_content[:500],
            }
        return parsed

    # ── Non-streaming path ────────────────────────────────────────
    try:
        result = await agent.ainvoke(
            {"messages": [("user", user_prompt)]},
            config=config,
        )
        messages = result.get("messages", [])
        last_msg = messages[-1]
        raw_content = (
            last_msg.content if hasattr(last_msg, "content") else str(last_msg)
        )

        parsed = _extract_json_block(raw_content)
        if parsed is None:
            parsed = {
                "redshift": None,
                "anchor_line": None,
                "anchor_wavelength": None,
                "wavelength_error": None,
                "classification": "Unknown",
                "confidence": "LOW",
                "best_hypothesis_idx": None,
                "primary_evidence": "Failed to parse JSON from synthesis response.",
                "caveats": raw_content[:500],
            }

    except Exception as exc:
        logging.warning(f"Synthesis agent failed: {exc}")
        parsed = {
            "redshift": None,
            "anchor_line": None,
                "anchor_wavelength": None,
                "wavelength_error": None,
            "classification": "Unknown",
            "confidence": "LOW",
            "best_hypothesis_idx": None,
            "primary_evidence": f"Synthesis failed: {exc}",
            "caveats": "LLM synthesis step errored.",
        }

    return parsed
