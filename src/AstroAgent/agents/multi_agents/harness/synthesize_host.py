"""
Synthesis Host — Final Report Writing Harness.

Runs AFTER the Analysis Auditor.  Takes all upstream outputs (synthesis
verdict, AA verdict, FA structured verdicts, continuum description, cleaned
line tables) and produces a structured 6-section final report + a JSON
comprehensive assessment block.
"""

import json
import os
import re
import csv as _csv
from typing import Optional, Tuple

import numpy as np
from langchain.agents import create_agent
from langchain_core.tools import tool

from AstroAgent.agents.common.state import SpectroState
from AstroAgent.agents.multi_agents.harness.tools import (
    compute_redshift_error, write_report,
)
from AstroAgent.core.llm import (
    _detect_vendor, _build_thinking_extra_body, _create_chat_openai,
)
from AstroAgent.agents.multi_agents.harness.synthesize import _resolve_max_tokens


# ---------------------------------------------------------------------------
# Skill loader
# ---------------------------------------------------------------------------

def _load_skill() -> str:
    skill_path = os.path.join(
        os.path.dirname(__file__), "skills", "synthesize_host_skill.md"
    )
    if os.path.exists(skill_path):
        return open(skill_path, encoding="utf-8").read()
    return ""


# ---------------------------------------------------------------------------
# Wavelength error lookup
# ---------------------------------------------------------------------------

def _lookup_wavelength_errors(harness_dir: str, output_dir: str) -> dict:
    """Look up wavelength_error for each unique wavelength in all line CSVs.

    Reads ``{idx}_lines.csv`` and emission/absorption CSVs, builds a mapping
    from integer-rounded wavelength to wavelength_err.  Also reads
    ``{idx}_lines_cleaned.csv`` for any additional entries.

    Returns a dict: {int(wavelength): wavelength_err}
    """
    errors: dict[int, float] = {}
    spectrum_id = os.path.basename(os.path.normpath(harness_dir)).split("_")[0]

    csv_paths = []
    import glob as _glob
    csv_paths.extend(sorted(_glob.glob(os.path.join(harness_dir, "*_lines.csv"))))
    csv_paths.extend(sorted(_glob.glob(os.path.join(harness_dir, "*_lines_cleaned.csv"))))
    for csv_name in [f"{spectrum_id}_emission.csv", f"{spectrum_id}_absorption.csv"]:
        p = os.path.join(output_dir, csv_name)
        if os.path.exists(p):
            csv_paths.append(p)

    for csv_path in csv_paths:
        if not os.path.exists(csv_path):
            continue
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = _csv.DictReader(f)
            for row in reader:
                wl_str = (row.get("wavelength") or row.get("fitted_center") or "").strip()
                err_str = (row.get("wavelength_err") or row.get("wavelength_error") or row.get("fitted_center_err") or "").strip()
                if not wl_str or not err_str:
                    continue
                try:
                    wl_int = int(float(wl_str))
                    err = float(err_str)
                except (ValueError, TypeError):
                    continue
                if wl_int not in errors:
                    errors[wl_int] = err

    return errors


def _format_confirmed_lines_with_errors(
    confirmed_lines: list, wavelength_errors: dict
) -> str:
    """Format confirmed lines with their wavelength errors.

    confirmed_lines: list of [line_name, wavelength] from AA.
    wavelength_errors: {int(wavelength): error} from _lookup_wavelength_errors.
    """
    if not confirmed_lines:
        return "*(无认证谱线)*\n"

    lines = []
    for entry in confirmed_lines:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        name, wl = entry[0], entry[1]
        wl_int = int(float(wl))
        err = wavelength_errors.get(wl_int)
        err_str = f" ± {err:.4f}" if err is not None else " (误差未知)"
        rest_wl = "?"  # LLM looks up rest wavelength from the line tables in the prompt
        lines.append(f"- {name}: λ_obs = {wl}{err_str}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# User prompt builder
# ---------------------------------------------------------------------------

def _build_user_message(state: SpectroState, harness_dir: str) -> str:
    """Build the user prompt for the Synthesis Host report-writing LLM."""
    rule_analysis = state.get("rule_analysis") or {}
    auditor_json = state.get("auditor_verdict_json") or {}
    continuum = state.get("continuum") or {}
    harness_results = state.get("harness_results") or []

    # ── Spectrum metadata ──
    wl = state["spectrum"]["wavelength"]
    wl_left = float(np.min(wl))
    wl_right = float(np.max(wl))
    snr_val = state["spectrum"].get("snr")
    snr_median = float(np.median(snr_val)) if snr_val is not None else None

    output_dir = os.path.dirname(os.path.normpath(harness_dir))

    parts = []

    parts.append("## 光谱基本信息")
    parts.append("")
    parts.append(f"- 波长范围: {wl_left:.0f} – {wl_right:.0f} Å")
    if snr_median is not None:
        parts.append(f"- 中值 SNR: {snr_median:.1f}")
    parts.append(f"- 蓝端: {wl_left:.0f} – 4000 Å (throughput drop)")
    parts.append(f"- 红端 (OH zone): 7800 – {wl_right:.0f} Å")
    parts.append("")

    # ── Continuum description ──
    continuum_desc = continuum.get("description", "")
    if continuum_desc:
        parts.append("## 连续谱描述 (VisualInterpreter)")
        parts.append("")
        parts.append(continuum_desc)
        parts.append("")

    # ── Synthesis summary ──
    parts.append("## 合成裁决 (Synthesis)")
    parts.append("")
    best_idx = rule_analysis.get("best_hypothesis_idx")
    parts.append(f"- 最佳假设: H{best_idx}, z = {rule_analysis.get('redshift')}")
    parts.append(f"- 分类: {rule_analysis.get('classification', '?')}")
    parts.append(f"- 置信度: {rule_analysis.get('confidence', '?')}")
    parts.append(f"- 锚线: {rule_analysis.get('anchor_line', '?')} at {rule_analysis.get('anchor_wavelength', '?')} Å")
    parts.append(f"- 主要证据: {rule_analysis.get('primary_evidence', '?')}")
    parts.append("")

    excluded = rule_analysis.get("excluded_hypotheses") or []
    if excluded:
        parts.append("被排除的假设:")
        for exc in excluded:
            if isinstance(exc, dict):
                parts.append(f"  - H{exc.get('idx')} (z={exc.get('z')}): {exc.get('reason', '')}")
        parts.append("")

    # ── AA verdict ──
    parts.append("## 审计裁决 (Analysis Auditor)")
    parts.append("")
    parts.append(f"- 裁决: {auditor_json.get('verdict', '?')}")
    parts.append(f"- 校准后置信度: {auditor_json.get('calibrated_confidence', '?')}")
    parts.append(f"- 光谱质量: {auditor_json.get('spectrum_quality', '?')}")
    parts.append(f"- has_real_peak: {auditor_json.get('has_real_peak', '?')}")
    parts.append("")

    key_issues = auditor_json.get("key_issues") or []
    if key_issues:
        parts.append("关键问题:")
        for ki in key_issues:
            parts.append(f"  - {ki}")
        parts.append("")

    # ── Confirmed lines with wavelength errors ──
    confirmed_lines = auditor_json.get("confirmed_lines") or []
    wavelength_errors = _lookup_wavelength_errors(harness_dir, output_dir)
    parts.append("## 认证谱线 (含波长误差)")
    parts.append("")
    parts.append(_format_confirmed_lines_with_errors(confirmed_lines, wavelength_errors))

    # ── Per-hypothesis line tables ──
    from AstroAgent.agents.multi_agents.harness.synthesize import _build_line_tables
    audit_indices = []
    if best_idx is not None:
        audit_indices.append(best_idx)
    for exc in excluded[:2]:
        if isinstance(exc, dict) and exc.get("idx") is not None:
            if exc["idx"] not in audit_indices:
                audit_indices.append(exc["idx"])
    if audit_indices:
        audit_results = [r for r in harness_results if r.get("hypothesis_idx") in audit_indices]
        if audit_results:
            parts.append("## 每假设谱线表 (post-FeatureAuditor)")
            parts.append("")
            parts.append(_build_line_tables(audit_results, harness_dir))

    # ── FA structured verdicts ──
    fa = state.get("feature_audit_verdict") or {}
    composite_v = [v for v in fa.get("composite_profile_verdicts", []) if v.get("hypothesis_idx") in audit_indices]
    doublet_v = [v for v in fa.get("doublet_verdicts", []) if v.get("hypothesis_idx") in audit_indices]
    oii_v = [v for v in fa.get("oii_morphology_verdicts", []) if v.get("hypothesis_idx") in audit_indices]

    if composite_v or doublet_v or oii_v:
        parts.append("## FA 结构化裁决")
        parts.append("")
        if composite_v:
            parts.append("### 复合体判定")
            for cv in composite_v:
                parts.append(f"- H{cv['hypothesis_idx']} {cv.get('species','?')}: composite={cv.get('is_composite')}, {cv.get('notes','')}")
            parts.append("")
        if doublet_v:
            parts.append("### 双线判定")
            for dv in doublet_v:
                parts.append(f"- H{dv['hypothesis_idx']} {dv.get('name_a','?')}+{dv.get('name_b','?')}: ratio_ok={dv.get('ratio_ok')}, {dv.get('notes','')}")
            parts.append("")
        if oii_v:
            parts.append("### [O II] 形态学判定")
            for ov in oii_v:
                parts.append(f"- H{ov['hypothesis_idx']} {ov.get('wl_obs','?')}: detected={ov.get('detected')}, {ov.get('notes','')}")
            parts.append("")

    # ── Task ──
    report_path = os.path.join(harness_dir, "final_report.md")
    parts.append("## 任务")
    parts.append("")
    parts.append(
        f"请按照你的 system prompt 中的 6 节结构撰写最终报告。"
        f"使用 `compute_redshift_error(rest_wavelength, wavelength_error)` "
        f"为每条有波长误差的认证谱线计算 σ_z。"
        f"调用 `write_report(file_path=\"{report_path}\", content=<完整 markdown>)` 保存报告。"
        f"所有判断均已在上游完成——你的任务是**总结和呈现**，不是重新分析。"
    )
    parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _extract_json_from_text(text: str) -> Optional[dict]:
    """Extract a JSON object from LLM output text."""
    if not text:
        return None
    # Try fenced block first
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
    # Try to find a bare JSON object (DOTALL for multi-line)
    m = re.search(r'\{[^{}]*"type"\s*:\s*"[^"]*"[^{}]*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    # Try the whole text as JSON
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    return None


async def arun(
    state: SpectroState,
    harness_dir: str,
    *,
    model: str = "sonnet",
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    temperature: float = 0.3,
    max_turns: int = 30,
    stream_md_path: Optional[str] = None,
) -> Tuple[str, Optional[dict]]:
    """Run the Synthesis Host report-writing agent.

    Returns (final_report_markdown, comprehensive_assessment_json).

    If ``stream_md_path`` is set, writes the full conversation (system prompt,
    tool calls, tool results, and final output) to that file.
    """
    system_prompt = _load_skill()
    user_prompt = _build_user_message(state, harness_dir)

    # ── Streaming setup ──
    md = None
    if stream_md_path:
        os.makedirs(os.path.dirname(stream_md_path) or ".", exist_ok=True)
        md = open(stream_md_path, "w", encoding="utf-8")
        md.write("# Synthesis Host — Final Report Writing\n\n")
        md.write("## System Prompt\n\n```\n")
        md.write(system_prompt)
        md.write("\n```\n\n## User Prompt\n\n```\n")
        md.write(user_prompt)
        md.write("\n```\n\n## Conversation\n\n")

    # ── Build LLM ──
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
        tools=[compute_redshift_error, write_report],
        system_prompt=system_prompt,
    )

    config = {"recursion_limit": max_turns}

    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": user_prompt}]},
        config=config,
    )

    # ── Write stream ──
    messages = result.get("messages", [])
    if md:
        _write_stream(md, messages)
        md.close()

    # ── Extract final text and JSON ──
    final_text = ""
    for msg in reversed(messages):
        content = getattr(msg, "content", "") or ""
        if isinstance(content, str) and content.strip():
            final_text = content
            break

    parsed_json = _extract_json_from_text(final_text)
    return final_text, parsed_json


def _write_stream(md, messages: list) -> None:
    """Write conversation messages to a stream markdown file."""
    for msg in messages:
        role = getattr(msg, "type", "unknown")
        content = getattr(msg, "content", "") or ""
        tool_calls = getattr(msg, "tool_calls", None) or []

        if role == "human":
            continue  # skip the initial user message (already written)

        if role == "ai":
            if content and isinstance(content, str) and content.strip():
                md.write(f"### LLM\n\n{content}\n\n")
            for tc in tool_calls:
                name = tc.get("name", "?")
                args = tc.get("args", {})
                md.write(f"### Tool Call: {name}\n\n```json\n{json.dumps(args, indent=2, ensure_ascii=False)}\n```\n\n")

        elif role == "tool":
            name = getattr(msg, "name", "?")
            if isinstance(content, str) and content.strip():
                md.write(f"### Tool Result: {name}\n\n```\n{content[:2000]}\n```\n\n")
            else:
                md.write(f"### Tool Result: {name}\n\n*(empty)*\n\n")
