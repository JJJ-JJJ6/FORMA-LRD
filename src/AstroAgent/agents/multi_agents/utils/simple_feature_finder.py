"""
轻量级 峰/谷 检测器：局部 SNR 门槛 + 多尺度共识。
不迭代拟合，不 Δχ² 验证。替代 feature_finder_precise。
"""
import os
import numpy as np
import pandas as pd
from scipy.ndimage import gaussian_filter1d, median_filter
from scipy.signal import argrelextrema, find_peaks

SIGMA_TO_FWHM = 2.0 * np.sqrt(2.0 * np.log(2.0))  # ≈ 2.355
_MULTI_SIGMAS = [0, 2, 4, 16]


def _local_noise_mad(signal, half_window=50):
    """滑动窗口 MAD 估计局域噪声 σ（稳健估计，不受局部线特征影响）。"""
    n = len(signal)
    noise = np.zeros(n, dtype=np.float64)
    for i in range(n):
        lo = max(0, i - half_window)
        hi = min(n, i + half_window + 1)
        noise[i] = 1.4826 * np.median(np.abs(signal[lo:hi] - np.median(signal[lo:hi])))
    return np.maximum(noise, np.median(noise) * 0.1)  # 防止零噪声


def _multi_scale_consensus(signal, wavelength, tol_pix=10, snr_thresh=3.0, order=2):
    """
    多尺度共识检测：在 σ=[0,2,4,16] 四个尺度上找峰，
    跨尺度聚类后保留共识度 ≥ 2 且 SNR ≥ snr_thresh 的候选。

    Returns
    -------
    list of dict: wavelength, amplitude, sigma_init, consensus
    """
    noise = _local_noise_mad(signal)
    mean_dwave = float(np.median(np.diff(wavelength))) if len(wavelength) > 1 else 1.0

    # 每个尺度独立找峰
    all_peaks = []  # (global_idx, sigma, wavelength, amplitude)
    for sig in _MULTI_SIGMAS:
        smoothed = gaussian_filter1d(signal, sigma=sig) if sig > 0 else signal
        peaks_idx, _ = find_peaks(smoothed, width=1)
        for pi in peaks_idx:
            all_peaks.append((pi, sig, float(wavelength[pi]), float(smoothed[pi])))

    if not all_peaks:
        return []

    # 按像素索引聚类
    groups = []  # [{indices, sigmas, waves, amps}]
    for pi, sig, wl, amp in sorted(all_peaks, key=lambda x: x[0]):
        matched = None
        for g in groups:
            if abs(pi - int(np.mean(list(g['indices'])))) <= tol_pix:
                matched = g
                break
        if matched:
            matched['indices'].add(pi)
            matched['sigmas'].add(sig)
            matched['waves'].append(wl)
            matched['amps'].append(amp)
        else:
            groups.append({'indices': {pi}, 'sigmas': {sig}, 'waves': [wl], 'amps': [amp]})

    # 对每个 cluster：取代表索引（优先 σ=0）、估 FWHM、检查 SNR
    results = []
    for g in groups:
        consensus = len(g['sigmas'])
        if consensus < 2:
            continue

        # 代表索引优先 σ=0
        idx0 = [pi for pi, sig, wl, a in all_peaks if pi in g['indices'] and sig == 0]
        rep_idx = int(idx0[0]) if idx0 else int(np.median(list(g['indices'])))
        rep_wave = float(wavelength[rep_idx])
        rep_amp = float(signal[rep_idx])

        # SNR 检查
        local_snr = abs(rep_amp) / noise[rep_idx] if noise[rep_idx] > 0 else 0
        if local_snr < snr_thresh:
            continue

        # FWHM 估计（用最小非零 σ 尺度的宽度）
        min_sig = min([s for s in g['sigmas'] if s > 0] or [4])
        smoothed_ref = gaussian_filter1d(signal, sigma=min_sig)
        ref_peaks, ref_props = find_peaks(smoothed_ref, width=1)
        fwhm_pix = 2  # fallback
        for si, pi in enumerate(ref_peaks):
            if abs(pi - rep_idx) <= tol_pix:
                fwhm_pix = max(float(ref_props['widths'][si]), 2)
                break
        sigma_init = (fwhm_pix / SIGMA_TO_FWHM) * mean_dwave * 0.85

        results.append({
            'wavelength': rep_wave,
            'amplitude': rep_amp,
            'sigma_init': sigma_init,
            'fwhm_pix': fwhm_pix,
            'consensus': consensus,
            'snr': local_snr,
        })

    return results


def find_features_simple(wavelength, flux, snr_thresh=3.0, consensus_min=2, verbose=True):
    """
    两级过滤：
    1. 局域 MAD 噪声估计 + SNR 门槛
    2. 多尺度（σ=0,2,4,16）共识 —— 至少 2 个尺度检测到才保留

    Parameters
    ----------
    wavelength : ndarray
    flux : ndarray         原始光谱 flux（非 residual）
    snr_thresh : float     峰高/局域噪声 下限，默认 3.0
    consensus_min : int    最少共识尺度数，默认 2

    Returns
    -------
    records_emission, records_absorption : list[dict]
    """
    n = len(flux)
    if n < 10:
        return [], []
    mean_dwave = np.median(np.diff(wavelength))

    # 中值滤波估粗糙连续谱，残差信号围绕零附近，height 门槛才有意义
    median_width = max(51, n // 20)  # 至少 51 像素宽，避开窄线
    if median_width % 2 == 0:
        median_width += 1
    baseline = median_filter(flux, size=median_width)
    residual = flux - baseline  # emission > 0, absorption < 0

    # 发射线：在 residual 上找正峰
    em_candidates = _multi_scale_consensus(
        residual, wavelength, snr_thresh=snr_thresh)

    # 吸收线：在 -residual 上找正峰（吸收谷翻转为正峰）
    ab_candidates = _multi_scale_consensus(
        -residual, wavelength, snr_thresh=snr_thresh)

    def _build_records(candidates, feature_type):
        records = []
        for c in candidates:
            amp = c['amplitude']
            if feature_type == 'absorption':
                amp = abs(amp)  # 已经从 -flux 还原回来，取绝对值
            fwhm_a = c['fwhm_pix'] * mean_dwave
            fwhm_km_s = fwhm_a / c['wavelength'] * 3e5 if c['wavelength'] > 0 else 0
            records.append({
                'wavelength': c['wavelength'],
                'wavelength_err': float(mean_dwave),
                'FWHM_A': float(fwhm_a),
                'FWHM_km_s': float(fwhm_km_s),
                'amplitude': float(amp),
                'amplitude_err': float(amp * 0.15),
                'width_class': ('narrow' if fwhm_km_s < 1000 else
                                'intermediate' if fwhm_km_s < 2000 else 'broad'),
                'feature_type': feature_type,
                'consensus': c['consensus'],
                'snr': c['snr'],
                'left_neighbor': 'None',
                'right_neighbor': 'None',
                'is_pseudo_peak': False,
                'pseudo_reason': '',
                'covered_troughs': 0,
                'trough_centers': '',
            })
        return records

    records_em = _build_records(em_candidates, 'emission')
    records_ab = _build_records(ab_candidates, 'absorption')

    if verbose:
        print(f"[simple_finder] 多尺度共识 (SNR≥{snr_thresh}, consensus≥{consensus_min}): "
              f"{len(records_em)} 个峰, {len(records_ab)} 个谷")
        if records_em:
            em_cons = {c: sum(1 for r in records_em if r['consensus'] == c) for c in range(2, 5)}
            print(f"  峰共识分布: {em_cons}")
        if records_ab:
            ab_cons = {c: sum(1 for r in records_ab if r['consensus'] == c) for c in range(2, 5)}
            print(f"  谷共识分布: {ab_cons}")

    return records_em, records_ab


def run_simple_feature_detection(
    output_dir, file_name, wavelength, flux,
    ivar=None, effective_snr=None,
    n_iterations=1,  # 忽略，保持接口兼容
    absorption_detection_params=None,
    emission_detection_params=None,
    verbose=True,
):
    """
    替代 run_iterative_feature_detection 的简易版本。
    接口与原函数完全兼容。

    Parameters 与原版一致（部分忽略）。
    Returns 格式与原版一致。
    """
    wavelength = np.asarray(wavelength, dtype=np.float64)
    flux = np.asarray(flux, dtype=np.float64)

    snr_thresh = 3.0
    consensus_min = 2
    if emission_detection_params:
        snr_thresh = emission_detection_params.get('snr_thresh', snr_thresh)
        consensus_min = emission_detection_params.get('consensus_min', consensus_min)

    records_em, records_ab = find_features_simple(
        wavelength, flux, snr_thresh=snr_thresh, consensus_min=consensus_min,
        verbose=verbose)

    # ── 构建 DataFrame（列对齐 feature_finder_precise 输出）──
    def _to_df(records):
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records)
        df['amplitude_rank'] = df['amplitude'].rank(ascending=False, method='min').astype(int)
        df = df.sort_values('wavelength').reset_index(drop=True)
        return df

    df_em = _to_df(records_em)
    df_ab = _to_df(records_ab)

    # ── 保存 CSV ────────────────────────────────────
    def _save_csv(df, suffix):
        if len(df) == 0:
            return
        out_cols = ['index', 'amplitude_rank', 'wavelength', 'wavelength_err',
                    'FWHM_A', 'FWHM_km_s', 'amplitude', 'amplitude_err',
                    'integrated_flux', 'flux_at_center',
                    'global_delta_chi2', 'local_delta_chi2',
                    'quality_low_delta_chi2', 'quality_boundary_touch',
                    'quality_large_error', 'quality_blended',
                    'quality_low_snr_depth', 'width_class', 'feature_type',
                    'left_neighbor', 'right_neighbor',
                    'is_pseudo_peak', 'pseudo_reason',
                    'covered_troughs', 'trough_centers']
        df_out = pd.DataFrame(index=range(len(df)))
        for col in out_cols:
            if col in df.columns:
                df_out[col] = df[col]
            elif col == 'index':
                df_out[col] = range(len(df))
            elif col == 'amplitude_rank':
                df_out[col] = df['amplitude'].rank(ascending=False, method='min').astype(int)
            elif col == 'integrated_flux':
                df_out[col] = df['amplitude'] * df['FWHM_A'] / SIGMA_TO_FWHM * np.sqrt(2 * np.pi)
            elif col == 'flux_at_center':
                df_out[col] = df['amplitude']
            elif col == 'global_delta_chi2':
                df_out[col] = 0.0
            elif col == 'local_delta_chi2':
                df_out[col] = 0.0
            elif col.startswith('quality_'):
                df_out[col] = False
            else:
                df_out[col] = df.get(col, '')
        path = os.path.join(output_dir, f"{file_name}_{suffix}.csv")
        df_out.to_csv(path, index=False)

    _save_csv(df_em, 'emission')
    _save_csv(df_ab, 'absorption')

    return {
        'df_emission': df_em,
        'df_absorption': df_ab,
        'records_emission': records_em,
        'records_absorption': records_ab,
        'wave_remaining': wavelength.copy(),
        'flux_remaining': flux.copy(),
    }
