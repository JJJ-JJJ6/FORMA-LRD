import csv
import os
import json
from typing import Optional, Dict, Any
from datetime import datetime

from .state import SpectroState


class ResultWriter:
    """
    ResultWriter
    -------------
    将一次 workflow 的 SpectroState 写成可消费的结果文件。
    - 只读 state
    - 不修改分析逻辑
    - 可安全在循环中重复调用
    """

    def __init__(
        self,
        output_dir: Optional[str] = None,
        overwrite: bool = True,
        encoding: str = "utf-8"
    ):
        self.output_dir = output_dir
        self.overwrite = overwrite
        self.encoding = encoding

    # =========================
    # 🚀 Public API
    # =========================

    def write(self, state: SpectroState) -> None:
        """
        写出一次 workflow 的所有结果
        """
        if not state:
            return

        output_dir = self._resolve_output_dir(state)
        os.makedirs(output_dir, exist_ok=True)

        file_name = state.get("file_name", "unknown")

        self._write_rule_analysis(state, output_dir, file_name)
        self._write_summary(state, output_dir, file_name)
        self._write_in_brief(state, output_dir, file_name)
        self._write_snapshot(state, output_dir, file_name)

    def write_qualitative_analysis(self, state: SpectroState) -> None:
        """写出 qualitative_analysis.txt（在 qualitative_analysis 步骤结束后调用）"""
        output_dir, file_name = self._resolve_output_dir(state), state.get("file_name", "unknown")
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{file_name}_qualitative_analysis.txt")
        content = json.dumps(state.get("qualitative_analysis") or {}, indent=2, ensure_ascii=False)
        self._write_text(path, content)

    def write_preliminary_classification(self, state: SpectroState) -> None:
        """写出 preliminary_classification.txt（在 qualitative_analysis 步骤结束后调用）"""
        output_dir, file_name = self._resolve_output_dir(state), state.get("file_name", "unknown")
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{file_name}_preliminary_classification.txt")
        with open(path, "w", encoding=self.encoding) as f:
            for key in ("preliminary_classification", "preliminary_classification_monkey"):
                value = state.get(key)
                if value is None:
                    continue
                f.write(f"{'='*60}\n")
                f.write(f"  {key.upper()}\n")
                f.write(f"{'='*60}\n")
                f.write(value if isinstance(value, str)
                        else json.dumps(value, indent=2, ensure_ascii=False))
                f.write("\n\n")

    def write_rule_analysis_qso(self, state: SpectroState) -> None:
        """写出 rule_analysis_QSO.txt（在 QSO quantitative_analysis 结束后调用）"""
        output_dir, file_name = self._resolve_output_dir(state), state.get("file_name", "unknown")
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{file_name}_rule_analysis_QSO.txt")
        with open(path, "w", encoding=self.encoding) as f:
            step_f_raw = (state.get("rule_analysis_QSO") or {}).get("step_F")
            if step_f_raw:
                f.write(f"{'='*60}\n  STEP_F (RAW SYNTHESIS)\n{'='*60}\n")
                f.write(step_f_raw if isinstance(step_f_raw, str)
                        else json.dumps(step_f_raw, indent=2, ensure_ascii=False))
                f.write("\n\n")

    def write_rule_analysis_lrg(self, state: SpectroState) -> None:
        """写出 rule_analysis_LRG.txt（在 LRG/BGS quantitative_analysis 结束后调用）"""
        e = state.get("rule_analysis_LRG") or {}
        extract = state.get("extract_LRG") or {}
        if not e and not extract:
            return
        output_dir, file_name = self._resolve_output_dir(state), state.get("file_name", "unknown")
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{file_name}_rule_analysis_LRG.txt")
        with open(path, "w", encoding=self.encoding) as f:
            # raw step outputs
            for key, value in e.items():
                f.write(f"{'='*60}\n  {key.upper()} (RAW)\n{'='*60}\n")
                f.write(value if isinstance(value, str)
                        else json.dumps(value, indent=2, ensure_ascii=False))
                f.write("\n\n")
            # structured extract
            step_f_extract = extract.get("step_F")
            if step_f_extract:
                f.write(f"{'='*60}\n  STEP_F (STRUCTURED EXTRACT)\n{'='*60}\n")
                f.write(json.dumps(step_f_extract, indent=2, ensure_ascii=False))
                f.write("\n\n")

    def write_f_a_summaries(
        self, state: SpectroState, summaries: list, label: str = "QSO"
    ) -> None:
        """
        将 Step F-a 各假设的结构化摘要写出为 .txt 文件。
        文件名格式：{file_name}_{label}_f_a_summaries.txt
        每条摘要以 JSON 格式缩进输出，并附有序号标题。

        Parameters
        ----------
        state    : SpectroState
        summaries: list of dict  — QSO/ELG/LRG_step_F_extract 返回的结果列表
        label    : str           — 文件名中的类型标签，如 "QSO"、"ELG"、"LRG"
        """
        if not summaries:
            return
        output_dir = self._resolve_output_dir(state)
        os.makedirs(output_dir, exist_ok=True)
        file_name = state.get("file_name", "unknown")
        path = os.path.join(output_dir, f"{file_name}_{label}_f_a_summaries.txt")
        total = len(summaries)
        with open(path, "w", encoding=self.encoding) as f:
            f.write(f"{'='*60}\n  {label} STEP F-a SUMMARIES  ({total} hypotheses)\n{'='*60}\n\n")
            for idx, summary in enumerate(summaries, start=1):
                f.write(f"--- Hypothesis #{idx} / {total} ---\n")
                f.write(json.dumps(summary, indent=2, ensure_ascii=False))
                f.write("\n\n")

    def write_brute_force_matching(self, state: SpectroState) -> None:
        """
        将 brute_force_matching 写出为 .txt 文件。
        文件名格式：
          {file_name}_brute_force_matching.txt
        """
        output_dir = self._resolve_output_dir(state)
        os.makedirs(output_dir, exist_ok=True)
        file_name = state.get("file_name", "unknown")

        # single unified key
        data = state.get('brute_force_matching')
        if data:
            path = os.path.join(output_dir, f"{file_name}_brute_force_matching.txt")
            with open(path, "w", encoding=self.encoding) as f:
                f.write(f"{'='*60}\n  BRUTE FORCE MATCHING\n{'='*60}\n\n")
                for idx, entry in enumerate(data, start=1):
                    f.write(f"--- Match #{idx} ---\n")
                    for key, value in entry.items():
                        f.write(f"  {key}: {value}\n")
                    f.write("\n")

    def write_redshift_scoring(self, state: SpectroState) -> None:
        """将 redshift_scoring 结果写出为 .txt 和 .csv 文件。"""
        scoring = state.get('redshift_scoring')
        if not scoring:
            return
        output_dir = self._resolve_output_dir(state)
        os.makedirs(output_dir, exist_ok=True)
        file_name = state.get("file_name", "unknown")
        path = os.path.join(output_dir, f"{file_name}_redshift_scoring.txt")
        csv_path = os.path.join(output_dir, f"{file_name}_redshift_scoring.csv")
        split_z = scoring.get('split_z', 1.0)
        top = scoring.get('top', 5)

        # ── TXT ──────────────────────────────────────────────
        with open(path, "w", encoding=self.encoding) as f:
            f.write(f"{'='*60}\n  REDSHIFT SCORING (split at z={split_z:.1f}, top {top})\n{'='*60}\n\n")

            for group_key, group_label in [('low_z', 'Low-z'), ('high_z', 'High-z')]:
                group = scoring.get(group_key, [])
                f.write(f"{'─'*60}\n")
                f.write(f"  {group_label} (z {'<' if 'low' in group_key else '≥'} {split_z:.1f}): "
                        f"{len(scoring.get('all_' + group_key, group))} candidates → top {top}\n")
                f.write(f"{'─'*60}\n")
                if not group:
                    f.write("  (no candidates)\n\n")
                    continue
                f.write(f"{'Rank':<5s} {'z':>8s} {'Score':>10s} {'N_lines':>7s} "
                        f"{'N_em':>5s} {'N_ab':>5s}  Hypothesis\n")
                f.write("-" * 80 + "\n")
                for rank, r in enumerate(group, start=1):
                    hyp_short = r.get('hypothesis', '')[:40]
                    f.write(f"{rank:<5d} {r['z']:8.4f} {r['score']:10.2f} {r['n_lines']:>7d} "
                            f"{r.get('n_em', 0):>5d} {r.get('n_ab', 0):>5d}  {hyp_short}\n")
                f.write("\n")

                # 最优候选逐线诊断
                best = group[0]
                f.write(f"  Best: z={best['z']:.4f}  Score={best['score']:.2f}\n")
                dets = best.get('details', [])
                if dets:
                    f.write(f"  {'Line':12s} {'形态':4s} {'Score':>7s} {'pred_Å':>8s} {'obs_Å':>8s} {'ΔÅ':>6s}\n")
                    f.write("  " + "-" * 50 + "\n")
                    for d in sorted(dets, key=lambda x: -x[2])[:10]:
                        name, morph, sc, pred, obs = d[0], d[1], d[2], d[3], d[4]
                        f.write(f"  {name:12s} {morph:4s} {sc:7.2f} {pred:8.1f} {obs:8.1f} {obs-pred:+6.1f}\n")
                f.write("\n")

        # ── CSV ──────────────────────────────────────────────
        with open(csv_path, "w", newline="", encoding=self.encoding) as cf:
            writer = csv.writer(cf)
            writer.writerow(["file_name", "z_group", "rank", "z", "score", "n_lines",
                             "n_em", "n_ab", "best_z", "hypothesis"])
            for group_key, group_label in [('low_z', 'Low-z'), ('high_z', 'High-z')]:
                group = scoring.get(group_key, [])
                for rank, r in enumerate(group, start=1):
                    writer.writerow([
                        file_name,
                        group_label,
                        rank,
                        f"{r['z']:.4f}",
                        f"{r['score']:.2f}",
                        r['n_lines'],
                        r.get('n_em', 0),
                        r.get('n_ab', 0),
                        f"{group[0]['z']:.4f}" if group else "",
                        r.get('hypothesis', ''),
                    ])

    def write_local_fitting(self, state: SpectroState, result: Dict[str, Any]) -> None:
        """写出 per-hypothesis local fitting CSV 文件。

        文件名格式：{file_name}_temp_localfit/{idx}_lines.csv
        """
        all_rows = result.get("all_rows") if result else []
        if not all_rows:
            return
        output_dir = self._resolve_output_dir(state)
        temp_dir = os.path.join(output_dir, f"{state.get('file_name', 'unknown')}_temp_localfit")
        os.makedirs(temp_dir, exist_ok=True)
        fieldnames = [
            "name", "rest_wavelength", "predicted_obs", "fitted_center",
            "fitted_center_err", "amplitude", "amplitude_err", "fitted_sigma",
            "fwhm_km_s", "snr", "delta_chi2_per_n", "status"
        ]
        for idx, rows in enumerate(all_rows):
            csv_path = os.path.join(temp_dir, f"{idx + 1}_lines.csv")
            with open(csv_path, "w", newline="", encoding=self.encoding) as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
                writer.writeheader()
                writer.writerows(rows)

    def write_harness_results(self, state: SpectroState) -> None:
        """写出 per-hypothesis harness 完整报告（.txt）。"""
        results = state.get('harness_results')
        if not results:
            return
        output_dir = self._resolve_output_dir(state)
        os.makedirs(output_dir, exist_ok=True)
        file_name = state.get("file_name", "unknown")
        path = os.path.join(output_dir, f"{file_name}_harness_results.txt")
        with open(path, "w", encoding=self.encoding) as f:
            f.write(f"{'='*60}\n  HARNESS RESULTS  ({len(results)} hypotheses)\n{'='*60}\n\n")
            for idx, hr in enumerate(results, start=1):
                f.write(f"{'─'*60}\n")
                f.write(f"Hypothesis #{idx}\n")
                f.write(f"{'─'*60}\n")
                f.write(hr.get("report", "(no report)"))
                f.write("\n\n")
                structured = hr.get("structured_output")
                if structured:
                    f.write("--- Structured output (JSON) ---\n")
                    f.write(json.dumps(structured, indent=2, ensure_ascii=False))
                    f.write("\n\n")

    def write_ranked_hypotheses(self, state: SpectroState) -> None:
        """写出 top-5 Δχ²-ranked hypotheses 及其 feature catalog（.txt）。"""
        ranked = state.get('ranked_hypotheses')
        if not ranked:
            return
        output_dir = self._resolve_output_dir(state)
        os.makedirs(output_dir, exist_ok=True)
        file_name = state.get("file_name", "unknown")
        path = os.path.join(output_dir, f"{file_name}_ranked_hypotheses.txt")
        with open(path, "w", encoding=self.encoding) as f:
            f.write(f"{'='*60}\n  RANKED HYPOTHESES  (Δχ² ranking, top {len(ranked)})\n{'='*60}\n\n")
            for r in ranked:
                f.write(f"{'─'*60}\n")
                f.write(f"Hypothesis #{r.get('hypothesis_idx', '?')}  "
                        f"Δχ²={r.get('delta_chi2', '?')}  "
                        f"n_lines={r.get('n_lines_used', 0)}\n")
                f.write(f"{'─'*60}\n")
                for line in r.get("lines", []):
                    f.write(f"  {line.get('name', '?'):12s}  "
                            f"center={line.get('center', '?'):.1f} Å  "
                            f"SNR={line.get('local_snr', '?'):.1f}  "
                            f"Δχ²/n={line.get('delta_chi2_per_n', '?'):.1f}  "
                            f"status={line.get('status', '?')}\n")
                f.write("\n")


    def write_verdict(self, state: SpectroState) -> None:
        """写出 auditing_verdict.txt（在 AnalysisAuditor.auditing_verdict 结束后调用）"""
        verdict = state.get('verdict')
        if not verdict:
            return
        output_dir, file_name = self._resolve_output_dir(state), state.get('file_name', 'unknown')
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{file_name}_verdict.txt")
        with open(path, 'w', encoding=self.encoding) as f:
            f.write(f"{'='*60}\n  AUDITING VERDICT\n{'='*60}\n")
            f.write(verdict if isinstance(verdict, str)
                    else json.dumps(verdict, indent=2, ensure_ascii=False))
            f.write('\n')

    def write_critique(self, state: SpectroState) -> None:
        """写出 auditing_critique.txt（在 AnalysisAuditor.auditing_critique 结束后调用）"""
        critique = state.get('critique')
        if not critique:
            return
        output_dir, file_name = self._resolve_output_dir(state), state.get('file_name', 'unknown')
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{file_name}_auditing_critique.txt")
        with open(path, 'w', encoding=self.encoding) as f:
            f.write(f"{'='*60}\n  AUDITING CRITIQUE\n{'='*60}\n\n")
            f.write(critique if isinstance(critique, str)
                    else json.dumps(critique, indent=2, ensure_ascii=False))
            f.write('\n')

    def write_patched_verdict(self, state: SpectroState) -> None:
        """写出 refining_patch.txt（在 RefinementAssistant.refining_patch 结束后调用）"""
        patched = state.get('patched_verdict')
        if not patched:
            return
        output_dir, file_name = self._resolve_output_dir(state), state.get('file_name', 'unknown')
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{file_name}_refining_patch.txt")
        with open(path, 'w', encoding=self.encoding) as f:
            f.write(f"{'='*60}\n  REFINING PATCH (PATCHED VERDICT)\n{'='*60}\n\n")
            f.write(patched if isinstance(patched, str)
                    else json.dumps(patched, indent=2, ensure_ascii=False))
            f.write('\n')

    def write_discussion(self, state: SpectroState) -> None:
        """写出完整的多轮讨论记录（critique ↔ patch response）。

        按 "假设 → 讨论" 的结构组织：三个路径的所有假设平铺在一个文件里，
        每个假设先输出其 JSON 内容，再输出该假设的全部讨论轮次。

        文件名格式：{file_name}_discussion.txt
        """
        output_dir, file_name = self._resolve_output_dir(state), state.get('file_name', 'unknown')
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{file_name}_discussion.txt")

        _PATHS = {
            "QSO": ("extract_QSO", "debate_history_QSO", "critique_QSO", "patch_response_QSO"),
            "ELG": ("extract_ELG", "debate_history_ELG", "critique_ELG", "patch_response_ELG"),
            "LRG/BGS": ("extract_LRG", "debate_history_LRG", "critique_LRG", "patch_response_LRG"),
        }

        has_content = False
        with open(path, 'w', encoding=self.encoding) as f:
            f.write(f"{'='*60}\n  DISCUSSION RECORD\n{'='*60}\n\n")

            for path_name, (extract_key, hist_key, crit_key, resp_key) in _PATHS.items():
                debate_hist = state.get(hist_key) or []
                final_critiques = state.get(crit_key) or []
                final_responses = state.get(resp_key) or []

                if not debate_hist and not final_critiques:
                    continue

                # Get hypotheses from extract
                extract_data = state.get(extract_key) or {}
                hypotheses = extract_data.get('step_F') or []

                num_hypos = max(
                    len(hypotheses),
                    len(final_critiques),
                    len(final_responses),
                    max((len(rd.get("hypotheses", [])) for rd in debate_hist), default=0),
                )
                if num_hypos == 0:
                    continue

                has_content = True

                for hi in range(num_hypos):
                    # ── Hypothesis header ──
                    f.write(f"{'─'*60}\n")
                    f.write(f"[{path_name}] Hypothesis #{hi+1}\n")
                    f.write(f"{'─'*60}\n")

                    # Hypothesis content
                    if hi < len(hypotheses):
                        f.write(json.dumps(hypotheses[hi], indent=2, ensure_ascii=False))
                    else:
                        f.write("(no hypothesis data)")
                    f.write("\n\n")

                    # ── Discussion for this hypothesis ──
                    # Previous rounds from debate_history
                    for rd in debate_hist:
                        round_num = rd.get("round", "?")
                        hypos_in_rd = rd.get("hypotheses", [])
                        if hi < len(hypos_in_rd):
                            entry = hypos_in_rd[hi]
                            crit = entry.get("critique") or ""
                            resp = entry.get("response") or ""
                        else:
                            crit, resp = "", ""
                        f.write(f"  Round {round_num} Critique:\n")
                        f.write(f"    {crit}\n\n" if not '\n' in crit
                                else "".join(f"    {l}\n" for l in crit.splitlines()) + "\n")
                        f.write(f"  Round {round_num} Response:\n")
                        f.write(f"    {resp}\n\n" if not '\n' in resp
                                else "".join(f"    {l}\n" for l in resp.splitlines()) + "\n")

                    # Final round
                    if final_critiques:
                        final_round_num = len(debate_hist) + 1
                        crit = final_critiques[hi] if hi < len(final_critiques) else ""
                        resp = final_responses[hi] if hi < len(final_responses) else ""
                        f.write(f"  Round {final_round_num} Critique:\n")
                        f.write(f"    {crit}\n\n" if '\n' not in (crit or "")
                                else "".join(f"    {l}\n" for l in (crit or "").splitlines()) + "\n")
                        f.write(f"  Round {final_round_num} Response:\n")
                        f.write(f"    {resp}\n\n" if '\n' not in (resp or "")
                                else "".join(f"    {l}\n" for l in (resp or "").splitlines()) + "\n")

                    f.write("\n")

        if not has_content:
            os.remove(path)

    def write_final_report(self, state: SpectroState) -> None:
        """写出 final_report.txt（在 RefinementAssistant.refining_final 结束后调用）"""
        report = state.get('final_report')
        if not report:
            return
        output_dir, file_name = self._resolve_output_dir(state), state.get('file_name', 'unknown')
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{file_name}_final_report.txt")
        with open(path, 'w', encoding=self.encoding) as f:
            f.write(f"{'='*60}\n  FINAL ANALYSIS REPORT\n{'='*60}\n\n")
            f.write(report)
            f.write('\n')

    def write_verdict_extract(self, state: SpectroState) -> None:
        """写出 verdict_extract.txt（在 AnalysisAuditor.verdict_extract 结束后调用）"""
        items = state.get('verdict_extract')
        if not items:
            return
        output_dir, file_name = self._resolve_output_dir(state), state.get('file_name', 'unknown')
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{file_name}_verdict_extract.txt")
        with open(path, 'w', encoding=self.encoding) as f:
            f.write(f"{'='*60}\n  VERDICT EXTRACT  ({len(items)} item(s))\n{'='*60}\n\n")
            for i, item in enumerate(items, 1):
                f.write(f"--- Result #{i} ---\n")
                f.write(f"  Source_path      : {item.get('Source_path', '?')}\n")
                f.write(f"  Physical_type    : {item.get('Physical_type', '?')}\n")
                f.write(f"  Suggested_redshift: {item.get('Suggested_redshift', '?')}\n")
                f.write(f"  Confidence       : {item.get('Confidence', '?')}\n")
                f.write(f"  Hypothesis       : {item.get('Hypothesis', '?')}\n")
                pairs = item.get('Adopted_pairs') or []
                if pairs:
                    f.write("  Adopted_pairs    :\n")
                    for p in pairs:
                        f.write(f"    {p.get('line','?')} → {p.get('obs_wavelength','?')} Å  (z={p.get('z','?')})\n")
                f.write(f"  Key_evidence     : {item.get('Key_evidence', '?')}\n")
                doubts = item.get('Remaining_doubts') or []
                f.write(f"  Remaining_doubts : {'; '.join(doubts) if doubts else 'none'}\n")
                f.write("\n")

    def write_rule_analysis_elg(self, state: SpectroState) -> None:
        """写出 rule_analysis_ELG.txt（在 ELG quantitative_analysis 结束后调用）"""
        e = state.get("rule_analysis_ELG") or {}
        extract = state.get("extract_ELG") or {}
        if not e and not extract:
            return
        output_dir, file_name = self._resolve_output_dir(state), state.get("file_name", "unknown")
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{file_name}_rule_analysis_ELG.txt")
        with open(path, "w", encoding=self.encoding) as f:
            # raw step outputs
            for key, value in e.items():
                f.write(f"{'='*60}\n  {key.upper()} (RAW)\n{'='*60}\n")
                f.write(value if isinstance(value, str)
                        else json.dumps(value, indent=2, ensure_ascii=False))
                f.write("\n\n")
            # structured extract
            step_f_extract = extract.get("step_F")
            if step_f_extract:
                f.write(f"{'='*60}\n  STEP_F (STRUCTURED EXTRACT)\n{'='*60}\n")
                f.write(json.dumps(step_f_extract, indent=2, ensure_ascii=False))
                f.write("\n\n")

    def write_rule_analysis(self, state: SpectroState) -> None:
        """Write harness-based rule analysis results."""
        output_dir, file_name = self._resolve_output_dir(state), state.get("file_name", "unknown")
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{file_name}_rule_analysis.txt")

        synthesis = state.get('rule_analysis') or {}
        ranked = state.get('harness_ranked') or []
        results = state.get('harness_results') or []

        with open(path, "w", encoding=self.encoding) as f:
            f.write("=" * 60 + "\n")
            f.write("  SYNTHESIS VERDICT\n")
            f.write("=" * 60 + "\n")
            f.write(json.dumps(synthesis, indent=2, ensure_ascii=False))
            f.write("\n\n")

            f.write("=" * 60 + "\n")
            f.write("  Δχ² RANKING (top 5)\n")
            f.write("=" * 60 + "\n")
            for i, r in enumerate(ranked):
                f.write(f"{i+1}. H{r.get('hypothesis_idx','?')}: "
                        f"Δχ²={r.get('delta_chi2','?')}, "
                        f"n_lines={r.get('n_lines_used','?')}\n")
            f.write("\n")

            f.write("=" * 60 + "\n")
            f.write("  PER-HYPOTHESIS REPORTS\n")
            f.write("=" * 60 + "\n")
            for r in results:
                f.write(f"\n--- Hypothesis {r.get('hypothesis_idx','?')} "
                        f"(z={r.get('redshift','?')}) ---\n")
                f.write(r.get('report', '(no report)'))
                f.write("\n")

    # =========================
    # 📄 Artifact Writers
    # =========================

    def _write_rule_analysis(self, state: SpectroState, output_dir: str, file_name: str):
        # rule_analysis_QSO is now a dict written directly by RuleAnalyst.py as .txt files.
        # This .md output is superseded; skip to avoid empty files.
        return

    def _write_summary(self, state: SpectroState, output_dir: str, file_name: str):
        path = os.path.join(output_dir, f"{file_name}_summary.md")
        if not self._can_write(path):
            return

        summary = state.get("summary")
        if not summary:
            return

        self._write_text(path, summary)

    def _write_in_brief(self, state: SpectroState, output_dir: str, file_name: str):
        path = os.path.join(output_dir, f"{file_name}_in_brief.json")
        if not self._can_write(path):
            return

        in_brief = state.get("in_brief")
        if not isinstance(in_brief, dict):
            return

        payload = {
            "file_name": file_name,
            "timestamp": self._now(),
            "in_brief": in_brief,
        }

        self._write_json(path, payload)

    def _write_snapshot(self, state: SpectroState, output_dir: str, file_name: str):
        """
        保存一个安全的 state 快照（用于 debug / 复现）
        """
        path = os.path.join(output_dir, f"{file_name}_snapshot.json")
        if not self._can_write(path):
            return

        snapshot = self._serialize_state(state)
        self._write_json(path, snapshot)

    # =========================
    # 🧰 Helpers
    # =========================

    def _resolve_output_dir(self, state: SpectroState) -> str:
        return self.output_dir or state.get("output_dir") or "outputs"

    def _can_write(self, path: str) -> bool:
        return self.overwrite or not os.path.exists(path)

    def _write_text(self, path: str, content: str):
        with open(path, "w", encoding=self.encoding) as f:
            f.write(content)

    def _write_json(self, path: str, obj: Dict[str, Any]):
        with open(path, "w", encoding=self.encoding) as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)

    def _stringify_list(self, items):
        return [str(x) for x in items if x is not None]

    def _serialize_state(self, state: SpectroState) -> Dict[str, Any]:
        """
        只序列化 JSON-safe 的字段
        """
        safe = {}

        for k, v in state.items():
            if self._is_json_safe(v):
                safe[k] = v
            else:
                safe[k] = f"<non-serializable: {type(v).__name__}>"

        return {
            "file_name": state.get("file_name"),
            "timestamp": self._now(),
            "state": safe,
        }

    def _is_json_safe(self, v: Any) -> bool:
        try:
            json.dumps(v)
            return True
        except Exception:
            return False

    def _now(self) -> str:
        return datetime.utcnow().isoformat() + "Z"
