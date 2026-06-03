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

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage

from AstroAgent.core.llm import _detect_vendor, _build_thinking_extra_body, _create_chat_openai
from AstroAgent.agents.multi_agents.utils.RA import (
    prepare_diagnostic_slices,
    build_dn4000_lookup,
    extract_harness_summary,
)


def _resolve_max_tokens() -> int | None:
    """Resolve max_tokens from env ``LLM_MAX_TOKENS``.

    When the env var is empty / unset and the provider is DeepSeek, we default
    to a generous value (16 384) to prevent output truncation.
    """
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


def _find_last_ai_message(messages: list):
    """Return the last AI message, skipping tool/other message types."""
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "ai":
            return msg
    return None


def _is_truncated(messages: list) -> bool:
    """Check whether the last AI message was truncated by max_tokens."""
    last_ai = _find_last_ai_message(messages)
    if last_ai is None:
        return False
    return last_ai.response_metadata.get("finish_reason") == "length"


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
# Adopted feature catalog builder
# ---------------------------------------------------------------------------

def _build_adopted_catalog(harness_results: list, harness_dir: str) -> str:
    """Build a unified feature catalog from per-hypothesis lines.csv files.

    Collects all LIKELY + MARGINAL features, sorts by |amplitude| descending,
    and pre-computes the median amplitude baseline.  The table intentionally
    omits line names — the LLM first verifies which features are real, then
    maps them to hypotheses.
    """
    import csv as _csv

    all_features = []
    for r in harness_results:
        idx = r["hypothesis_idx"]
        csv_path = os.path.join(harness_dir, f"{idx}_lines.csv")
        if not os.path.exists(csv_path):
            continue
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = _csv.DictReader(f)
            for row in reader:
                status = (row.get("status") or "").strip()
                if status not in ("LIKELY", "MARGINAL"):
                    continue
                try:
                    wl = float(row.get("fitted_center", 0) or 0)
                except (ValueError, TypeError):
                    wl = 0.0
                try:
                    amp = float(row.get("amplitude", 0) or 0)
                except (ValueError, TypeError):
                    amp = 0.0
                snr = row.get("cwt_snr") or row.get("local_snr") or "—"
                ridge = row.get("ridge_length") or "—"
                all_features.append(
                    {
                        "hypothesis_idx": idx,
                        "wavelength": wl,
                        "amplitude": abs(amp),
                        "cwt_snr": snr,
                        "ridge_length": ridge,
                        "status": status,
                    }
                )

    if not all_features:
        return (
            "\n## Adopted Feature Catalog\n\n"
            "*No adopted features found across any hypothesis.*\n"
        )

    # Sort by |amplitude| descending
    all_features.sort(key=lambda f: f["amplitude"], reverse=True)

    amplitudes = [f["amplitude"] for f in all_features]
    median_amp = float(np.median(amplitudes))

    lines = [
        "## Adopted Feature Catalog\n",
        "All LIKELY + MARGINAL features from every hypothesis, sorted by "
        "|amplitude| descending. **No line identifications** — use "
        "`read_spectrum_region` to verify which (if any) of the high-amplitude "
        "outliers are real spectral features, then map them to hypotheses.\n",
        f"**Median |amplitude|: {median_amp:.3f}** "
        f"— features near or below this are at the noise floor.",
        f"**Top quartile threshold: {amplitudes[max(0, len(amplitudes)//4)]:.3f}**\n",
        "| Rank | λ_obs (Å) | \\|Amp\\| | SNR | Ridge | Status | H |",
        "|------|----------|---------|-----|-------|--------|---|",
    ]

    for i, f in enumerate(all_features, 1):
        snr_str = (
            f"{float(f['cwt_snr']):.1f}"
            if _is_numeric(f["cwt_snr"])
            else str(f["cwt_snr"])
        )
        lines.append(
            f"| {i} | {f['wavelength']:.1f} | {f['amplitude']:.3f} | "
            f"{snr_str} | {f['ridge_length']} | {f['status']} | "
            f"H{f['hypothesis_idx']} |"
        )

    return "\n".join(lines)


def _is_numeric(val) -> bool:
    try:
        float(val)
        return True
    except (ValueError, TypeError):
        return False


def _build_line_tables(harness_results: list, harness_dir: str) -> str:
    """Build per-hypothesis line tables from lines.csv with ALL columns.

    The synthesis agent needs the raw measurement data (fitted_center_err,
    fitted_sigma, fwhm_km_s, etc.) to populate write_synthesis_csv accurately.
    These tables provide every column that the CSV output requires.
    """
    import csv as _csv

    sections = []

    for r in harness_results:
        idx = r["hypothesis_idx"]
        csv_path = os.path.join(harness_dir, f"{idx}_lines.csv")
        if not os.path.exists(csv_path):
            sections.append(
                f"### H{idx}\n\n*No lines.csv found.*\n"
            )
            continue

        rows = []
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = _csv.DictReader(f)
            for row in reader:
                rows.append(row)

        if not rows:
            sections.append(
                f"### H{idx}\n\n*No lines evaluated.*\n"
            )
            continue

        # Identify columns present (some may be absent in nomad vs redrock)
        cols = list(rows[0].keys())

        header = (
            "| " + " | ".join(cols) + " |\n"
            "|" + "|".join(["------"] * len(cols)) + "|"
        )

        body_lines = []
        for row in rows:
            vals = []
            for c in cols:
                v = (row.get(c) or "").strip()
                vals.append(v if v else "—")
            body_lines.append("| " + " | ".join(vals) + " |")

        sections.append(
            f"### H{idx}\n\n"
            f"{header}\n"
            + "\n".join(body_lines)
            + "\n"
        )

    if not sections:
        return "\n## Per-Hypothesis Line Data\n\n*No line catalogs available.*\n"

    return (
        "\n## Per-Hypothesis Line Data\n\n"
        "Full measurement tables from each harness run.  Reference these when "
        "calling `write_synthesis_csv` — every column the CSV requires is "
        "present here.\n\n"
        + "\n".join(sections)
    )


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

    # ── Adopted feature catalog ──
    adopted_catalog = _build_adopted_catalog(harness_results, harness_dir)

    # ── Per-hypothesis full line tables (for CSV population) ──
    line_tables = _build_line_tables(harness_results, harness_dir)

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

{adopted_catalog}

{line_tables}

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
    max_turns: int = 150,
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
        Maximum agent turns / recursion_limit (default 150).
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

    llm = _create_chat_openai(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=_resolve_max_tokens(),
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
            raise
        finally:
            md.close()

        # ── Truncation detection (streaming path) ─
        if _is_truncated(accumulated_messages):
            logging.warning(
                "Synthesis output truncated (finish_reason=length). "
                "Requesting continuation..."
            )
            try:
                _ctn_md = open(stream_md_path, "a", encoding="utf-8")
                continuation_msgs = list(accumulated_messages)
                continuation_msgs.append(
                    HumanMessage(
                        content=(
                            "Your previous response was truncated. Continue EXACTLY "
                            "from where you stopped. Do NOT repeat anything. Output "
                            "ONLY the remaining content — do not wrap in markdown fences."
                        )
                    )
                )
                async for event in agent.astream(
                    {"messages": continuation_msgs},
                    config=config,
                    stream_mode="updates",
                ):
                    for _node_name, update in event.items():
                        for msg in update.get("messages", []):
                            msg_type = getattr(msg, "type", None)
                            if msg_type == "ai":
                                content = (
                                    msg.content
                                    if hasattr(msg, "content")
                                    else ""
                                )
                                _ctn_md.write(
                                    "### Assistant (continuation)\n\n"
                                    f"{content.strip()}\n\n"
                                )
                                _ctn_md.flush()
                            accumulated_messages.append(msg)
                _ctn_md.close()
                logging.info("Synthesis continuation completed successfully.")
            except Exception as exc:
                logging.warning(
                    f"Synthesis continuation retry failed: {exc}. "
                    "Returning truncated result."
                )
                try:
                    _ctn_md.close()
                except Exception:
                    pass

        last_msg = _find_last_ai_message(accumulated_messages)
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

        # ── Truncation detection (non‑streaming path) ─
        messages = result.get("messages", [])
        if _is_truncated(messages):
            logging.warning(
                "Synthesis output truncated (finish_reason=length). "
                "Requesting continuation..."
            )
            try:
                continuation_msgs = list(messages)
                continuation_msgs.append(
                    HumanMessage(
                        content=(
                            "Your previous response was truncated. Continue EXACTLY "
                            "from where you stopped. Do NOT repeat anything. Output "
                            "ONLY the remaining content."
                        )
                    )
                )
                continuation_result = await agent.ainvoke(
                    {"messages": continuation_msgs}, config=config
                )
                messages.extend(continuation_result.get("messages", []))
                logging.info("Synthesis continuation completed successfully.")
            except Exception as exc:
                logging.warning(
                    f"Synthesis continuation retry failed: {exc}. "
                    "Returning truncated result."
                )

        last_msg = _find_last_ai_message(messages)
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
