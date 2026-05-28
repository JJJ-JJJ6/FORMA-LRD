import os

from typing import Optional, Dict, Any, List
from pydantic import BaseModel

from AstroAgent.core.config._utils import getenv_int, getenv_int_list, getenv_float, getenv_optional_float


# ------------------------
# 分析参数配置
# ------------------------

class ParamsConfig(BaseModel):
    arm_name: Optional[List[str]]
    arm_wavelength_range: Optional[List[List[float]]]
    ocr: str
    tol_wavelength: int
    # [deprecated] kept for rollback reference — replaced by single tol_wavelength above
    # tol_wavelength_qso: int
    # tol_wavelength_galaxy: int
    num_peaks: int
    num_troughs: int
    min_qso_redshift: float
    min_galaxy_redshift: float
    step_f_concurrency: int
    harness_concurrency: int
    discussion_rounds: int
    scoring_workers: int  # redshift scoring 并行数，0=自动
    redshift_scoring_enabled: bool  # redshift scoring 开关
    redshift_scoring_top_k: int  # 开启时每组 low_z/high_z 各取前 top-K
    redshift_scoring_v3: bool  # True=v3 (CWT feature based), False=v2 (raw flux argmin/argmax)
    stop_after_vi: bool  # 在 VisualInterpreter 后停止（批量测试 scoring 用）
    feature_finder: str  # "simple" (multi-scale consensus) or "cwt" (wavelet ridge detection)
    self_evolve: bool  # 自进化/反思模式：开启 ground-truth 对比与失败分析
    failure_batch_size: int  # 每 N 个失败 sample 触发一次批量根因分析
    z_tolerance: float  # ground-truth 红移对比容差

    # ── CWT 特征检测参数 ──
    cwt_snr_thresh: float
    cwt_min_ridge_length: int
    cwt_n_scales: int
    cwt_min_scale: float
    cwt_max_scale: float

    # ── 吸收线检测参数 ──
    abs_window_width: int
    abs_window_overlap: int
    abs_delta_chi2_base: float
    abs_dynamic_threshold_factor: float
    abs_global_delta_chi2_threshold: float
    abs_fwhm_min: float
    abs_fwhm_max: float
    abs_snr_depth_threshold: float
    smooth_sigma_trough: float
    smooth_prominence_frac_trough: float

    # ── 发射线检测参数 ──
    em_window_width: int
    em_window_overlap: int
    em_delta_chi2_base: float
    em_dynamic_threshold_factor: float
    em_global_delta_chi2_threshold: float
    em_sys_err_frac: float
    em_fwhm_min: float
    em_fwhm_max: float
    smooth_sigma: float
    smooth_prominence_frac: float

    @classmethod
    def from_env(cls) -> "ParamsConfig":
        arm_name = cls._parse_arm_name()
        arm_wavelength_range = cls._parse_arm_wavelength_range()

        # -------- 核心一致性校验 --------
        if arm_name is None and arm_wavelength_range is None:
            pass  # ✅ 允许
        elif arm_name is None or arm_wavelength_range is None:
            raise ValueError(
                "ARM_NAME and ARM_WAVELENGTH_RANGE must both be set or both be None."
            )
        elif len(arm_name) != len(arm_wavelength_range):
            raise ValueError(
                "ARM_NAME and ARM_WAVELENGTH_RANGE must have the same length."
            )

        return cls(
            arm_name=arm_name,
            arm_wavelength_range=arm_wavelength_range,
            ocr=os.getenv("OCR") or "paddle",
            tol_wavelength=getenv_int("TOL_WAVELENGTH", 80),
            # [deprecated] kept for rollback reference
            # tol_wavelength_qso=getenv_int("TOL_WAVELENGTH_QSO", 100),
            # tol_wavelength_galaxy=getenv_int("TOL_WAVELENGTH_GALAXY", 30),
            num_peaks=getenv_int("PEAKS_NUMBER", 10),
            num_troughs=getenv_int("TROUGHS_NUMBER", 15),
            min_qso_redshift=getenv_optional_float("MIN_QSO_REDSHIFT") or float('-inf'),
            min_galaxy_redshift=getenv_optional_float("MIN_GALAXY_REDSHIFT") or float('-inf'),
            step_f_concurrency=getenv_int("STEP_F_CONCURRENCY", 4),
            harness_concurrency=getenv_int("HARNESS_CONCURRENCY", 3),
            discussion_rounds=getenv_int("DISCUSSION_ROUNDS", 1),
            scoring_workers=getenv_int("SCORING_WORKERS", 1),
            redshift_scoring_enabled=os.getenv("REDSHIFT_SCORING_ENABLED", "true").lower() in ("true", "1", "yes"),
            redshift_scoring_top_k=getenv_int("REDSHIFT_SCORING_TOP_K", 5),
            redshift_scoring_v3=os.getenv("REDSHIFT_SCORING_V3", "true").lower() in ("true", "1", "yes"),
            stop_after_vi=os.getenv("STOP_AFTER_VI", "false").lower() in ("true", "1", "yes"),
            feature_finder=os.getenv("FEATURE_FINDER", "simple"),
            self_evolve=os.getenv("SELF_EVOLVE", "false").lower() in ("true", "1", "yes"),
            failure_batch_size=getenv_int("FAILURE_BATCH_SIZE", 5),
            z_tolerance=getenv_float("Z_TOLERANCE", 0.005),

            # ── CWT 特征检测参数 ──
            cwt_snr_thresh=getenv_float("CWT_SNR_THRESH", 5.0),
            cwt_min_ridge_length=getenv_int("CWT_MIN_RIDGE_LENGTH", 2),
            cwt_n_scales=getenv_int("CWT_N_SCALES", 24),
            cwt_min_scale=getenv_float("CWT_MIN_SCALE", 1.0),
            cwt_max_scale=getenv_float("CWT_MAX_SCALE", 80.0),

            # ── 吸收线检测参数 ──
            abs_window_width=getenv_int("ABS_WINDOW_WIDTH", 100),
            abs_window_overlap=getenv_int("ABS_WINDOW_OVERLAP", 60),
            abs_delta_chi2_base=getenv_float("ABS_DELTA_CHI2_BASE", 0.02),
            abs_dynamic_threshold_factor=getenv_float("ABS_DYNAMIC_THRESHOLD_FACTOR", 100.0),
            abs_global_delta_chi2_threshold=getenv_float("ABS_GLOBAL_DELTA_CHI2_THRESHOLD", 0.5),
            abs_fwhm_min=getenv_float("ABS_FWHM_MIN", 5.0),
            abs_fwhm_max=getenv_float("ABS_FWHM_MAX", 100.0),
            abs_snr_depth_threshold=getenv_float("ABS_SNR_DEPTH_THRESHOLD", 3.0),
            smooth_sigma_trough=getenv_float("SMOOTH_SIGMA_TROUGH", 16.0),
            smooth_prominence_frac_trough=getenv_float("SMOOTH_PROMINENCE_FRAC_TROUGH", 0.01),

            # ── 发射线检测参数 ──
            em_window_width=getenv_int("EM_WINDOW_WIDTH", 500),
            em_window_overlap=getenv_int("EM_WINDOW_OVERLAP", 300),
            em_delta_chi2_base=getenv_float("EM_DELTA_CHI2_BASE", 0.03),
            em_dynamic_threshold_factor=getenv_float("EM_DYNAMIC_THRESHOLD_FACTOR", 100.0),
            em_global_delta_chi2_threshold=getenv_float("EM_GLOBAL_DELTA_CHI2_THRESHOLD", 0.05),
            em_sys_err_frac=getenv_float("EM_SYS_ERR_FRAC", 0.05),
            em_fwhm_min=getenv_float("EM_FWHM_MIN", 3.0),
            em_fwhm_max=getenv_float("EM_FWHM_MAX", 380.0),
            smooth_sigma=getenv_float("SMOOTH_SIGMA", 160.0),
            smooth_prominence_frac=getenv_float("SMOOTH_PROMINENCE_FRAC", 0.01),
        )

    # ------------------------
    # 解析 ARM_NAME
    # ------------------------
    @staticmethod
    def _parse_arm_name():
        raw = os.getenv("ARM_NAME")
        if raw is None or raw.strip() == "":
            return None
        return [name.strip() for name in raw.split(",")]

    # ------------------------
    # 解析 ARM_WAVELENGTH_RANGE
    # ------------------------
    @staticmethod
    def _parse_arm_wavelength_range():
        raw = os.getenv("ARM_WAVELENGTH_RANGE")
        if raw is None or raw.strip() == "":
            return None

        result = []
        for rng in raw.split(","):
            parts = rng.strip().split("-")
            if len(parts) != 2:
                raise ValueError(
                    f"Invalid wavelength range format: {rng}"
                )
            try:
                result.append([float(parts[0]), float(parts[1])])
            except ValueError:
                raise ValueError(
                    f"Invalid numeric wavelength range: {rng}"
                )

        return result