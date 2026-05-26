"""
targeted_search.py — LLM-driven targeted line search harness.

Equips an LLM with 5 spectrum-analysis tools and a methodology skill prompt.
The LLM autonomously confirms or refutes predicted spectral lines at a given
redshift hypothesis through targeted Gaussian fitting.

Usage as module:
    from harness import run
    result = run(fits_path="/data/spectrum.fits", redshift=2.3,
                 npz_path="/data/spectrum.npz")
    print(result["report"])
    print(result["feature_catalog"])

Usage as CLI:
    python targeted_search.py /data/spectrum.fits 2.3 --npz /data/spectrum.npz
"""

import json
import os
import re
import time
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_openai import ChatOpenAI
from langchain.agents import create_agent

from AstroAgent.core.llm import _detect_vendor, _build_thinking_extra_body
from .tools import (
    fit_peak,
    write_report, write_lines_csv, compute_redshift,
    EMISSION_LINES, ABSORPTION_LINES, EMISSION_LINE_WIDTHS,
)

# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------

_HTTP_RETRIES = int(os.environ.get("MAX_TRIES", 3))
_HTTP_DELAYS = [2, 5, 10, 15, 20][:_HTTP_RETRIES]  # truncate to match MAX_TRIES
_EMPTY_RETRIES = 1

_RETRYABLE_KW = (
    "429", "rate limit", "too many requests",
    "503", "502", "500", "server error", "service unavailable",
    "timeout", "connection", "reset", "refused", "eof", "broken pipe",
)


def _is_retryable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(kw in msg for kw in _RETRYABLE_KW)


def _has_fit_calls(messages: List[Any]) -> bool:
    for msg in messages:
        if hasattr(msg, "name") and msg.name == "fit_peak":
            return True
        for tc in getattr(msg, "tool_calls", []) or []:
            if tc.get("name") == "fit_peak":
                return True
    return False

# ---------------------------------------------------------------------------
# Skill prompt
# ---------------------------------------------------------------------------

SKILL_PATH = Path(__file__).resolve().parent / "targeted_search_skill.md"


def _load_skill() -> str:
    with open(SKILL_PATH) as f:
        return f.read()


# ---------------------------------------------------------------------------
# Tool list
# ---------------------------------------------------------------------------

TOOLS = [fit_peak, compute_redshift, write_report, write_lines_csv]


# ---------------------------------------------------------------------------
# JSON extraction from LLM response
# ---------------------------------------------------------------------------

def _extract_json_block(text: str) -> Optional[Dict[str, Any]]:
    """Extract the final JSON block from the LLM report."""
    # Try ```json ... ``` fence
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try bare { ... } block at end
    m = re.search(r"\{[^{}]*\"consensus_redshift\"[^{}]*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Feature catalog: collect structured fit_peak results from tool calls
# ---------------------------------------------------------------------------

def _collect_fit_results(messages: List[Any]) -> List[Dict[str, Any]]:
    """Walk message history and collect all fit_peak tool results."""
    results = []
    for msg in messages:
        # LangGraph messages: check for tool messages
        if hasattr(msg, "name") and msg.name == "fit_peak":
            try:
                content = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                results.append(content)
            except (json.JSONDecodeError, TypeError):
                pass
    return results


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _find_nearby_features(
    rest_wl: float, obs_wl: float, z_min: float, z_max: float,
    peaks: list, troughs: list,
) -> str:
    """Find peaks/troughs whose wavelength falls within the z-verification window
    for a given rest-frame line:  [λ_rest×(1+z_min), λ_rest×(1+z_max)].

    Results are sorted by distance to ``obs_wl`` (closest first).

    Returns a compact string like ``peak@5001.2(amp=15.3)`` or ``—`` if none.
    """
    lo = rest_wl * (1.0 + z_min)
    hi = rest_wl * (1.0 + z_max)
    nearby = []
    for p in (peaks or []):
        pw = p.get('wavelength', 0)
        if lo <= pw <= hi:
            dist = abs(pw - obs_wl)
            fwhm_a = p.get('FWHM_A', None)
            fwhm_k = p.get('FWHM_km_s', None)
            width_str = ""
            if fwhm_a is not None and fwhm_k is not None:
                width_str = f", FWHM={fwhm_a:.1f}Å/{fwhm_k:.0f}km/s"
            nearby.append((dist, f"peak@{pw:.1f}(amp={p.get('amplitude', 0):.1f}{width_str})"))
    for t in (troughs or []):
        tw = t.get('wavelength', 0)
        if lo <= tw <= hi:
            dist = abs(tw - obs_wl)
            fwhm_a = t.get('FWHM_A', None)
            fwhm_k = t.get('FWHM_km_s', None)
            width_str = ""
            if fwhm_a is not None and fwhm_k is not None:
                width_str = f", FWHM={fwhm_a:.1f}Å/{fwhm_k:.0f}km/s"
            nearby.append((dist, f"trough@{tw:.1f}(amp={t.get('amplitude', 0):.1f}{width_str})"))
    nearby.sort(key=lambda x: x[0])
    return ", ".join(n[1] for n in nearby) if nearby else "—"


def _build_predictions_section(
    redshift: float,
    z_min: float,
    z_max: float,
    wl_min: float,
    wl_max: float,
    peaks: list,
    troughs: list,
) -> str:
    """Pre-compute predicted lines and cross-reference with detected features
    using the z-verification window for each line."""
    rows = []

    for name, rest_wl in EMISSION_LINES.items():
        obs_wl = rest_wl * (1.0 + redshift)
        if wl_min is not None and obs_wl < wl_min:
            continue
        if wl_max is not None and obs_wl > wl_max:
            continue
        rows.append((
            obs_wl,
            f"| {name} | {rest_wl:.1f} | {obs_wl:.1f} | em | "
            f"{EMISSION_LINE_WIDTHS.get(name, 'narrow')} | "
            f"{_find_nearby_features(rest_wl, obs_wl, z_min, z_max, peaks, troughs)} |",
        ))

    for name, rest_wl in ABSORPTION_LINES.items():
        obs_wl = rest_wl * (1.0 + redshift)
        if wl_min is not None and obs_wl < wl_min:
            continue
        if wl_max is not None and obs_wl > wl_max:
            continue
        rows.append((
            obs_wl,
            f"| {name} | {rest_wl:.1f} | {obs_wl:.1f} | abs | "
            f"absorption | "
            f"{_find_nearby_features(rest_wl, obs_wl, z_min, z_max, peaks, troughs)} |",
        ))

    if not rows:
        return "\n## Predicted Lines\n\n(No predicted lines fall within the observed wavelength range.)\n"

    rows.sort(key=lambda r: r[0])

    lo_ex = 1216.0 * (1.0 + z_min)
    hi_ex = 1216.0 * (1.0 + z_max)
    header = (
        f"| Name | λ_rest (Å) | λ_obs (Å) | Type | Width | Features in z-window "
        f"[{lo_ex:.1f}..{hi_ex:.1f} Å for Lyα] (amp, FWHM) |\n"
        "|------|-----------|----------|------|-------|----------------------------------------------|"
    )
    return "\n## Predicted Lines at z = {:.4f}\n\n{}\n{}\n".format(
        redshift, header, "\n".join(r[1] for r in rows)
    )


def _build_user_message(
    redshift: float,
    fits_path: str,
    npz_path: str,
    *,
    wavelength_min: float = None,
    wavelength_max: float = None,
    snr_median: float = None,
    peaks: list = None,
    troughs: list = None,
    z_min: float = None,
    z_max: float = None,
    masked_regions: list = None,
    report_path: str = None,
    csv_path: str = None,
) -> str:
    _z_min = z_min if z_min is not None else round(redshift - 0.1, 4)
    _z_max = z_max if z_max is not None else round(redshift + 0.1, 4)

    # ── Spectrum summary (replaces load_spectrum tool call) ──
    spec_lines = [
        f"| Wavelength range | {wavelength_min:.1f} – {wavelength_max:.1f} Å |"
        if wavelength_min is not None and wavelength_max is not None
        else "| Wavelength range | (not provided) |",
    ]
    if snr_median is not None:
        spec_lines.append(f"| Median SNR | {snr_median:.1f} |")

    _masked_msg = ""
    if masked_regions:
        regions_desc = ", ".join(f"[{s:.1f}, {e:.1f}]" for s, e in masked_regions)
        spec_lines.append(f"| Masked regions | {regions_desc} |")
        _masked_msg = (
            f"\nThese regions were removed due to arm overlap or quality cuts. "
            f"The spectrum has NO data in these ranges — do not expect to find lines there.\n"
        )

    spectrum_summary = (
        "## Spectrum Summary\n\n"
        + "\n".join(spec_lines)
        + "\n"
    )

    predictions_section = _build_predictions_section(
        redshift, _z_min, _z_max, wavelength_min, wavelength_max, peaks, troughs,
    )

    return (
        spectrum_summary
        + f"\nVerify the redshift hypothesis z ≈ {redshift}.\n\n"
        f"Verification window: z ∈ [{_z_min}, {_z_max}]\n"
        f"A fitted line supports the hypothesis ONLY if its implied redshift falls within this window. "
        f"Use `compute_redshift` with the fitted center and rest wavelength to check.\n"
        + _masked_msg
        + f"\nFITS file: {fits_path}\n"
        f"Cleaned spectrum: {npz_path}\n"
        + (f"Output Report file: {report_path}\n" if report_path else "")
        + (f"Output CSV file: {csv_path}\n" if csv_path else "")
        + predictions_section
        + "\nThe table above shows predicted lines at this redshift and any features "
        "already detected by the peak/trough finder whose observed wavelength falls "
        "within the z-verification window for that line (sorted by |λ − λ_obs|, closest first). "
        "Adopt CWT pre-detected features when they meet the criteria in Phase 2 Step 4. "
        "For remaining lines, call `fit_peak` once each — no retries. "
        "Batch ALL adoptions and fit_peak calls in a single parallel turn.\n"
        + "\nFollow the skill prompt phases: adopt/batch-fit all lines → write CSV → write report → JSON block."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(
    fits_path: str,
    redshift: float,
    npz_path: str,
    *,
    hypothesis_idx: int = 0,
    wavelength_min: float = None,
    wavelength_max: float = None,
    snr_median: float = None,
    peaks: list = None,
    troughs: list = None,
    z_min: float = None,
    z_max: float = None,
    masked_regions: list = None,
    report_path: str = None,
    csv_path: str = None,
    model: str = None,
    api_key: str = None,
    base_url: str = None,
    temperature: float = 0.1,
    max_turns: int = 500,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Run the LLM harness for a single redshift hypothesis.

    Parameters
    ----------
    fits_path : str
        Path to the original FITS file (for context — actual data comes from npz_path).
    redshift : float
        The central redshift hypothesis to test.
    npz_path : str
        Path to the cleaned spectrum .npz file (wavelength, flux, snr arrays).
    z_min, z_max : float, optional
        Verification window. A fitted line is considered to support the
        hypothesis only if its implied redshift falls within [z_min, z_max].
        If not provided, defaults to redshift ± 0.005.
    model : str, optional
        Model name. Defaults to LLM_MODEL env var or "deepseek-v4-pro".
    api_key : str, optional
        API key. Defaults to LLM_API_KEY env var.
    base_url : str, optional
        API base URL. Defaults to LLM_BASE_URL env var.
    temperature : float
        LLM temperature (default 0.1).
    max_turns : int
        Maximum agent turns (recursion_limit). Default 30.
    verbose : bool
        If True, log intermediate messages.

    Returns
    -------
    dict with keys:
        report : str
            Full LLM text response.
        structured_output : dict or None
            Parsed JSON block from the LLM report (redshift, classification, lines, etc.).
        feature_catalog : list[dict]
            Raw fit_peak results collected from tool calls.
        messages : list
            Full message history (for debugging).
    """
    model = model or os.environ.get("LLM_MODEL", "deepseek-v4-pro")
    api_key = api_key or os.environ.get("LLM_API_KEY")
    base_url = base_url or os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")

    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    # Always disable thinking — multi-turn tool calling can't pass back reasoning_content
    _vendor = _detect_vendor(base_url)
    _extra_body = _build_thinking_extra_body("disabled", _vendor) if _vendor != "unknown" else None
    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        extra_body=_extra_body,
    )

    system_prompt = _load_skill()

    agent = create_agent(
        model=llm,
        tools=TOOLS,
        system_prompt=system_prompt,
    )

    user_message = _build_user_message(
        redshift, fits_path, npz_path,
        wavelength_min=wavelength_min, wavelength_max=wavelength_max,
        snr_median=snr_median,
        peaks=peaks, troughs=troughs,
        z_min=z_min, z_max=z_max, masked_regions=masked_regions,
        report_path=report_path, csv_path=csv_path,
    )
    config = {"recursion_limit": max_turns}

    # ── HTTP-level retry ──────────────────────────────────────
    last_exc = None
    for attempt in range(_HTTP_RETRIES + 1):
        try:
            result = agent.invoke({"messages": [("user", user_message)]}, config=config)
            break
        except Exception as e:
            last_exc = e
            if not _is_retryable(e) or attempt >= _HTTP_RETRIES:
                raise
            delay = _HTTP_DELAYS[min(attempt, len(_HTTP_DELAYS) - 1)]
            logging.warning(f"HTTP retry {attempt + 1}/{_HTTP_RETRIES} in {delay}s: {e}")
            time.sleep(delay)
    else:
        raise last_exc

    messages = result.get("messages", [])
    feature_catalog = _collect_fit_results(messages)

    # ── Empty catalog retry ──────────────────────────────────
    for empty_attempt in range(_EMPTY_RETRIES + 1):
        if feature_catalog or not _has_fit_calls(messages):
            break
        logging.warning(f"Empty catalog retry {empty_attempt + 1}/{_EMPTY_RETRIES} — LLM called fit_peak but results missing")
        try:
            result = agent.invoke({"messages": [("user", user_message)]}, config=config)
        except Exception as e:
            if not _is_retryable(e):
                raise
            logging.warning(f"Empty-catalog retry HTTP error: {e}")
            break
        messages = result.get("messages", [])
        feature_catalog = _collect_fit_results(messages)

    last_msg = messages[-1]
    report = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
    structured_output = _extract_json_block(report)

    return {
        "hypothesis_idx": hypothesis_idx,
        "redshift": redshift,
        "report": report,
        "structured_output": structured_output,
        "feature_catalog": feature_catalog,
        "messages": messages,
    }


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


async def arun(
    fits_path: str,
    redshift: float,
    npz_path: str,
    *,
    hypothesis_idx: int = 0,
    wavelength_min: float = None,
    wavelength_max: float = None,
    snr_median: float = None,
    peaks: list = None,
    troughs: list = None,
    z_min: float = None,
    z_max: float = None,
    masked_regions: list = None,
    report_path: str = None,
    csv_path: str = None,
    model: str = None,
    api_key: str = None,
    base_url: str = None,
    temperature: float = 0.1,
    max_turns: int = 100,
    verbose: bool = False,
    stream_md_path: str = None,
) -> Dict[str, Any]:
    """Run LLM harness for a single redshift hypothesis (async, optional streaming).

    Parameters
    ----------
    stream_md_path : str, optional
        If set, stream the full conversation (system prompt + every LLM turn +
        tool calls + tool results) to this .md file in real time. Parent
        directories are created if needed.
    """
    model = model or os.environ.get("LLM_MODEL", "deepseek-v4-pro")
    api_key = api_key or os.environ.get("LLM_API_KEY")
    base_url = base_url or os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")

    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    # Always disable thinking — multi-turn tool calling can't pass back reasoning_content
    _vendor = _detect_vendor(base_url)
    _extra_body = _build_thinking_extra_body("disabled", _vendor) if _vendor != "unknown" else None
    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        extra_body=_extra_body,
    )

    system_prompt = _load_skill()
    agent = create_agent(model=llm, tools=TOOLS, system_prompt=system_prompt)

    user_message = _build_user_message(
        redshift, fits_path, npz_path,
        wavelength_min=wavelength_min, wavelength_max=wavelength_max,
        snr_median=snr_median,
        peaks=peaks, troughs=troughs,
        z_min=z_min, z_max=z_max, masked_regions=masked_regions,
        report_path=report_path, csv_path=csv_path,
    )

    # ── Streaming path ──────────────────────────────────────────
    if stream_md_path:
        os.makedirs(os.path.dirname(stream_md_path) or ".", exist_ok=True)

        config = {"recursion_limit": max_turns}

        # HTTP-level retry for the streaming call
        last_exc = None
        for attempt in range(_HTTP_RETRIES + 1):
            try:
                md = open(stream_md_path, "w", encoding="utf-8")
                md.write(f"# Hypothesis — z={redshift}\n\n")
                md.write(f"**FITS**: `{fits_path}`\n\n")
                md.write(f"**NPZ**: `{npz_path}`\n\n")
                md.write("---\n\n")
                md.write("<details>\n<summary>System Prompt</summary>\n\n")
                md.write(system_prompt)
                md.write("\n</details>\n\n---\n\n")
                md.write(f"### User\n\n{user_message}\n\n")
                if attempt > 0:
                    md.write(f"\n> ⚠ Retry attempt {attempt} after error: {last_exc}\n\n")
                md.flush()

                accumulated_messages = []
                turn = 0

                async for event in agent.astream(
                    {"messages": [("user", user_message)]},
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
                                md.write(f"### Tool Result\n\n")
                                md.write(_format_tool_result(msg))
                                md.write("\n\n")
                                md.flush()

                md.close()
                break  # success

            except Exception as e:
                last_exc = e
                try:
                    md.close()
                except Exception:
                    pass
                if not _is_retryable(e) or attempt >= _HTTP_RETRIES:
                    raise
                delay = _HTTP_DELAYS[min(attempt, len(_HTTP_DELAYS) - 1)]
                logging.warning(f"HTTP retry {attempt + 1}/{_HTTP_RETRIES} in {delay}s: {e}")
                await asyncio.sleep(delay)
        else:
            raise last_exc

        last_msg = accumulated_messages[-1] if accumulated_messages else None
        report = last_msg.content if last_msg and hasattr(last_msg, "content") else ""
        feature_catalog = _collect_fit_results(accumulated_messages)

        # ── Empty catalog retry ────────────────────────────────
        for empty_attempt in range(_EMPTY_RETRIES + 1):
            if feature_catalog or not _has_fit_calls(accumulated_messages):
                break
            logging.warning(f"Empty catalog retry {empty_attempt + 1}/{_EMPTY_RETRIES} in streaming mode")
            try:
                config = {"recursion_limit": max_turns}
                result = await agent.ainvoke({"messages": [("user", user_message)]}, config=config)
            except Exception as e:
                if not _is_retryable(e):
                    logging.warning(f"Empty-catalog retry failed: {e}")
                    break
                logging.warning(f"Empty-catalog retry HTTP error: {e}")
                break
            accumulated_messages = result.get("messages", [])
            feature_catalog = _collect_fit_results(accumulated_messages)

        return {
            "hypothesis_idx": hypothesis_idx,
            "redshift": redshift,
            "report": report,
            "structured_output": _extract_json_block(report),
            "feature_catalog": feature_catalog,
            "messages": accumulated_messages,
        }

    # ── Non-streaming path ──────────────────────────────────────
    config = {"recursion_limit": max_turns}

    last_exc = None
    for attempt in range(_HTTP_RETRIES + 1):
        try:
            result = await agent.ainvoke({"messages": [("user", user_message)]}, config=config)
            break
        except Exception as e:
            last_exc = e
            if not _is_retryable(e) or attempt >= _HTTP_RETRIES:
                raise
            delay = _HTTP_DELAYS[min(attempt, len(_HTTP_DELAYS) - 1)]
            logging.warning(f"HTTP retry {attempt + 1}/{_HTTP_RETRIES} in {delay}s: {e}")
            await asyncio.sleep(delay)
    else:
        raise last_exc

    messages = result.get("messages", [])
    feature_catalog = _collect_fit_results(messages)

    # ── Empty catalog retry ────────────────────────────────────
    for empty_attempt in range(_EMPTY_RETRIES + 1):
        if feature_catalog or not _has_fit_calls(messages):
            break
        logging.warning(f"Empty catalog retry {empty_attempt + 1}/{_EMPTY_RETRIES}")
        try:
            result = await agent.ainvoke({"messages": [("user", user_message)]}, config=config)
        except Exception as e:
            if not _is_retryable(e):
                raise
            logging.warning(f"Empty-catalog retry HTTP error: {e}")
            break
        messages = result.get("messages", [])
        feature_catalog = _collect_fit_results(messages)

    last_msg = messages[-1]
    report = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

    return {
        "hypothesis_idx": hypothesis_idx,
        "redshift": redshift,
        "report": report,
        "structured_output": _extract_json_block(report),
        "feature_catalog": feature_catalog,
        "messages": messages,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="LLM-driven targeted line search harness")
    parser.add_argument("fits_path", help="Path to the original FITS file")
    parser.add_argument("redshift", type=float, help="Redshift hypothesis z")
    parser.add_argument("--npz", required=True, help="Path to cleaned spectrum .npz")
    parser.add_argument("--model", default=None, help="Model name override")
    parser.add_argument("--json", action="store_true", help="Output structured JSON only")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    result = run(
        fits_path=args.fits_path,
        redshift=args.redshift,
        npz_path=args.npz,
        model=args.model,
        verbose=args.verbose,
    )

    if args.json:
        output = {
            "report": result["report"],
            "structured_output": result["structured_output"],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(result["report"])


if __name__ == "__main__":
    main()
