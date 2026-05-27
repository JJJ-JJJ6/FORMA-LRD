import os
import numpy as np
# import pandas as pd
import logging


from AstroAgent.agents.common.state import SpectroState
from AstroAgent.agents.common.base_agent import BaseAgent
from AstroAgent.agents.common.result_writer import ResultWriter

from AstroAgent.core.runtime.runtime_container import RuntimeContainer

from AstroAgent.agents.multi_agents.utils.usage import safe_to_bool, find_overlap_regions
from AstroAgent.agents.multi_agents.utils.VI import (
    _detect_chart_border, _crop_img,
    _remap_to_cropped_canvas, _pixel_tickvalue_fitting,
    _convert_to_spectrum,
    _detect_axis_ticks_tesseract,
    _detect_axis_ticks_paddle,
    run_continuum_fitting_masked,
    brute_force_line_matching,
    _load_spectrum_from_fits,
    run_local_fitting,
    run_redshift_scoring,
    run_redshift_scoring_v2,
)
from AstroAgent.agents.multi_agents.utils.simple_feature_finder import (
    run_simple_feature_detection,
)
from AstroAgent.agents.multi_agents.utils.cwt_feature_finder import (
    run_cwt_feature_detection,
)
from AstroAgent.agents.multi_agents.utils.plot import (
    plot_spec_extract,
    plot_spectrum_snr,
    plot_continuum,
    plot_residual_spectrum,
    plot_features,
)


# ---------------------------------------------------------
# 1. Visual Assistant — 负责图像理解与坐标阅读
# ---------------------------------------------------------
class VisualInterpreter(BaseAgent):
    """
    从科学光谱图中自动提取坐标轴刻度、边框、像素映射、峰/谷等信息
    """

    agent_name = "VisualInterpreter"

    def __init__(self, runtime: RuntimeContainer):
        super().__init__(runtime)

    # ======================================================
    # LLM 交互方法
    # ======================================================

    def load_spectrum_from_fits(self, state: SpectroState) -> SpectroState:
        """从 FITS 文件加载光谱数据，波段信息来自 .env 中的 ARM_NAME / ARM_WAVELENGTH_RANGE"""
        params = self.runtime.configs.params
        state['spectrum'] = _load_spectrum_from_fits(
            state['file_path'],
            arm_names=params.arm_name,
            arm_wavelength_ranges=params.arm_wavelength_range,
        )
        return state

    async def detect_axis_ticks(self, state: SpectroState) -> SpectroState:
        """调用 VLM 检测坐标轴刻度"""
        function_name = "detect_axis_ticks"

        if not state['file_path'] or not os.path.exists(state['file_path']):
            print(state['file_path'])
            logging.error("No image provided or image path does not exist")
            raise

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name
        )

        axis_info = await self.call_llm_with_context(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_path=state['file_path'],
            parse_json=True,
            description="Axis information",
            want_tools=False
        )
        if axis_info == "非光谱图" or axis_info == "Non-spectrum":
            logging.error("The input image is not a spectral plot. LLM output: %s", axis_info)
            raise
        state["axis_info"] = axis_info
        return state

    async def detect_axis_ticks_OCR(self, state: SpectroState) -> SpectroState:
        """调用 OCR 检测坐标轴刻度"""
        OCR = self.runtime.configs.params.ocr
        print(f"OCR: {OCR}")
        if OCR == 'paddle':
            state['OCR_detected_ticks'] = _detect_axis_ticks_paddle(state)
        else:
            state['OCR_detected_ticks'] = _detect_axis_ticks_tesseract(state)
        print(state["OCR_detected_ticks"])
        return state

    async def combine_axis_mapping(self, state: SpectroState) -> SpectroState:
        """结合视觉结果与 OCR 结果生成像素-数值映射"""
        function_name = "combine_axis_mapping"

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            axis_info=state['axis_info'],
            ocr=state['OCR_detected_ticks']
        )

        tick_pixel_raw = await self.call_llm_with_context(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_path=None,
            parse_json=True,
            description="Tick pixel mapping",
            want_tools=False
        )
        state["tick_pixel_raw"] = tick_pixel_raw
        return state

    async def revise_axis_mapping(self, state: SpectroState) -> SpectroState:
        """检查并修正刻度值与像素位置匹配关系"""
        function_name = "revise_axis_mapping"

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name,
            axis_mapping=state['tick_pixel_raw']
        )

        tick_pixel_revised = await self.call_llm_with_context(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_path=None,
            parse_json=True,
            description="Revised tick mapping",
            want_tools=False
        )
        state["tick_pixel_raw"] = tick_pixel_revised
        return state

    async def check_border(self, state: SpectroState):
        """调用 LLM 判断裁剪边界是否干净"""
        function_name = "check_border"

        system_prompt, user_prompt = self.runtime.prompt_manager.load(
            state=state,
            agent_name=self.agent_name,
            function_name=function_name
        )

        response = await self.call_llm_with_context(
            system_prompt,
            user_prompt,
            image_path=[state['file_path'], state['crop_path']],
            parse_json=True,
            description='Check cropping',
            want_tools=False
        )
        result = {}
        for key in ['top', 'right', 'bottom', 'left']:
            try:
                val = safe_to_bool(response.get(key, 'false'))
                result[key] = val == 'true'
            except Exception:
                logging.warning(f"Error parsing {key} border, defaulting to False: {response}")
                result[key] = False
        return result

    async def border_detection_and_cropping(self, state: SpectroState) -> SpectroState:
        """迭代调整边距，直到 LLM 确认四边干净"""
        state['margin'] = {
            'top': 20, 'right': 10, 'bottom': 15, 'left': 10,
        }
        MAX_MARGIN = 30
        INCREMENT = 2

        stop = False
        while not stop:
            state["chart_border"] = _detect_chart_border(state['file_path'], state['margin'])
            _crop_img(state['file_path'], state["chart_border"], state['crop_path'])

            box_new = await self.check_border(state)

            if all(box_new.values()):
                stop = True
                break
            elif any(state['margin'][k] >= MAX_MARGIN for k in state['margin']):
                stop = True
                logging.info(f"Reached maximum margin, stopping cropping: {state['margin']}")
                break
            else:
                for k, clean in box_new.items():
                    if not clean:
                        state['margin'][k] += INCREMENT

        return state

    # ======================================================
    # 主流程
    # ======================================================

    async def run(self, state: SpectroState, plot: bool = True):
        """执行完整视觉分析流程"""
        params = self.runtime.configs.params
        try:
            if self.runtime.configs.io.input_format == 'fits':
                self.load_spectrum_from_fits(state)
            else:  
                # === Phase A: 坐标轴检测与校准 ===
                await self.detect_axis_ticks(state)
                await self.detect_axis_ticks_OCR(state)
                await self.combine_axis_mapping(state)
                await self.revise_axis_mapping(state)

                # === Phase B: 图像裁剪与像素映射 ===
                await self.border_detection_and_cropping(state)
                state["tick_pixel_remap"] = _remap_to_cropped_canvas(
                    state['tick_pixel_raw'], state["chart_border"]
                )
                state["pixel_to_value"] = _pixel_tickvalue_fitting(state['tick_pixel_remap'])

                # === Phase C: 光谱重建 ===
                arm_name = self.runtime.configs.params.arm_name
                arm_wavelength_range = self.runtime.configs.params.arm_wavelength_range
                state["spectrum"] = _convert_to_spectrum(
                    state['crop_path'], state['pixel_to_value'], arm_name, arm_wavelength_range
                )

            # ── Compute and store overlap regions ──────────────────────
            overlap = find_overlap_regions(params.arm_name, params.arm_wavelength_range)
            if overlap:
                state['spectrum']['overlap_regions'] = list(overlap.values())

            plot_spec_extract(state)
            plot_spectrum_snr(state)

            # 保存光谱数组
            spec = state["spectrum"]
            save_data = {k: spec[k] for k in ("wavelength", "flux", "snr") if k in spec}
            if spec.get("ivar") is not None:
                save_data["ivar"] = spec["ivar"]
            np.savez_compressed(
                os.path.join(state['output_dir'], f"{state['file_name']}_spectrum.npz"),
                **save_data,
            )
            state['spectrum_npz_path'] = os.path.join(
                state['output_dir'], f"{state['file_name']}_spectrum.npz"
            )

            # === Phase D: 迭代特征检测（在 continuum fitting 之前）===
            spec = state["spectrum"]
                        
            # 优先使用 ivar，其次使用 snr
            ivar_data = spec.get("ivar", None)
            effective_snr_data = spec.get("snr", 7.0) if ivar_data is None else None
                        
            # ── 吸收线检测参数（覆盖 find_absorption_lines 默认值）──
            absorption_detection_params = {
                # 窗口设置
                'window_width': params.abs_window_width,
                'window_overlap': params.abs_window_overlap,
                # 显著性阈值
                'delta_chi2_base': params.abs_delta_chi2_base,
                'dynamic_threshold_factor': params.abs_dynamic_threshold_factor,
                'global_delta_chi2_threshold': params.abs_global_delta_chi2_threshold,
                # 谱线宽度限制
                'fwhm_min': params.abs_fwhm_min,
                'fwhm_max': params.abs_fwhm_max,
                # 信噪比深度阈值
                'snr_depth_threshold': params.abs_snr_depth_threshold,
                # Smoothed 候选谷参数（宽低振幅谷的先验）
                'smooth_sigma_trough': params.smooth_sigma_trough,
                'smooth_prominence_frac_trough': params.smooth_prominence_frac_trough,
            }
                        
            # ── 发射线检测参数（覆盖 find_emission_lines 默认值）──
            emission_detection_params = {
                # 窗口设置
                'window_width': params.em_window_width,
                'window_overlap': params.em_window_overlap,
                # 显著性阈值
                'delta_chi2_base': params.em_delta_chi2_base,
                'dynamic_threshold_factor': params.em_dynamic_threshold_factor,
                'global_delta_chi2_threshold': params.em_global_delta_chi2_threshold,
                # 系统误差分量（参考 DLA-Toolkit var_lss 思路）
                'sys_err_frac': params.em_sys_err_frac,
                # 谱线宽度限制
                'fwhm_min': params.em_fwhm_min,
                'fwhm_max': params.em_fwhm_max,
                # Smoothed 候选峰参数（宽低振幅峰的先验）
                'smooth_sigma': params.smooth_sigma,
                'smooth_prominence_frac': params.smooth_prominence_frac,
            }
                        
            # 根据 FEATURE_FINDER 配置选择算法
            if params.feature_finder == "cwt":
                run_feature_detection = run_cwt_feature_detection
                emission_detection_params.pop('smooth_sigma', None)
                emission_detection_params.pop('smooth_prominence_frac', None)
                emission_detection_params['snr_thresh'] = params.cwt_snr_thresh
                emission_detection_params['min_ridge_length'] = params.cwt_min_ridge_length
                emission_detection_params['n_scales'] = params.cwt_n_scales
                emission_detection_params['min_scale'] = params.cwt_min_scale
                emission_detection_params['max_scale'] = params.cwt_max_scale
            else:
                run_feature_detection = run_simple_feature_detection

            feature_result = run_feature_detection(
                output_dir=state['output_dir'],
                file_name=state['file_name'],
                wavelength=spec["wavelength"],
                flux=spec["flux"],
                ivar=ivar_data,
                effective_snr=effective_snr_data,
                n_iterations=params.n_iterations if hasattr(params, 'n_iterations') else 3,
                absorption_detection_params=absorption_detection_params,
                emission_detection_params=emission_detection_params,
                verbose=True,
            )
                        
            # 保存结果到 state（发射线 → peaks，吸收线 → troughs）
            # 注意：返回値已按波长从小到大排序，包含 amplitude_rank 字段
            df_em = feature_result['df_emission']
            df_ab = feature_result['df_absorption']
                        
            state['peaks'] = df_em.to_dict('records') if len(df_em) > 0 else []
            state['troughs'] = df_ab.to_dict('records') if len(df_ab) > 0 else []
                        
            # records 同样已按波长排序
            state['absorption_records'] = feature_result['records_absorption']
            state['emission_records'] = feature_result['records_emission']

            # === Phase E: Continuum 拟合（将已检测峰/谷区域 mask掉后再拟合）===
            state['continuum'], state['residual_spectrum'] = run_continuum_fitting_masked(
                spec["wavelength"],
                spec["flux"],
                peaks=state['emission_records'],
                troughs=state['absorption_records'],
                chebyshev_degree=params.chebyshev_degree if hasattr(params, 'chebyshev_degree') else None,
                chebyshev_min_degree=params.chebyshev_min_degree if hasattr(params, 'chebyshev_min_degree') else 1,
                chebyshev_max_degree=params.chebyshev_max_degree if hasattr(params, 'chebyshev_max_degree') else 10,
            )
            plot_continuum(state)
            plot_residual_spectrum(state)

            plot_features(state)

            tol_wavelength = self.runtime.configs.params.tol_wavelength
            state['brute_force_matching'] = brute_force_line_matching(
                state, tol_wavelength,
            )

            ResultWriter().write_brute_force_matching(state)

            # Phase E2: Redshift scoring — rank hypotheses for LLM triage
            if state['brute_force_matching']:
                scoring = run_redshift_scoring_v2(
                    wavelength=spec["wavelength"],
                    flux=spec["flux"],
                    continuum_flux=state['continuum']['flux'],
                    snr=spec["snr"],
                    brute_force_matches=state['brute_force_matching'],
                    split_z=1.0,
                    top=5,
                    peak_tol=30.0,
                    scoring_workers=self.runtime.configs.params.scoring_workers,
                )
                state['redshift_scoring'] = scoring
                ResultWriter().write_redshift_scoring(state)

                # ── Check if true redshift is within scoring candidates ──
                vi_z = spec.get('VI_Z')
                if vi_z is not None:
                    tolerance = self.runtime.configs.params.z_tolerance
                    expected_z = float(vi_z)
                    all_zs = [h['z'] for h in scoring.get('low_z', []) + scoring.get('high_z', [])]
                    min_dz = min((abs(z - expected_z) for z in all_zs), default=999)
                    in_scoring = min_dz <= tolerance

                    if not in_scoring:
                        logging.info(
                            f"[VisualInterpreter] True z={expected_z:.4f} NOT in scoring "
                            f"(min_dz={min_dz:.4f} > tol={tolerance:.4f}, "
                            f"n_candidates={len(all_zs)}) — skipping harness + synthesis."
                        )
                        state['skip_synthesis'] = True
                    else:
                        state['skip_synthesis'] = False

            # Phase F: Local line fitting → new peaks/troughs + plot
            # if state['brute_force_matching']:
            #     result = run_local_fitting(
            #         spec["wavelength"], spec["flux"], state['brute_force_matching']
            #     )
            #     ResultWriter().write_local_fitting(state, result)
            #     state['peaks'] = result["peaks"]
            #     state['troughs'] = result["troughs"]

            # plot_features(state)

            return state
        except Exception as e:
            print(f"run pipeline terminated with error: {e}")
            raise
