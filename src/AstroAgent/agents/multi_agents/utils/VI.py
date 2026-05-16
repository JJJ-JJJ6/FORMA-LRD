import os
import json
import logging
import cv2
import numpy as np
from astropy.io import fits
from scipy.ndimage import gaussian_filter1d
import pandas as pd
# import matplotlib.pyplot as plt
import pytesseract
from collections import defaultdict
from paddleocr import PaddleOCR
from typing import Dict
from scipy.optimize import curve_fit
# from scipy.stats import mode

from AstroAgent.agents.common.state import SpectroState
from AstroAgent.agents.multi_agents.utils.usage import find_overlap_regions


# ===========================================================
# Step 1.2: OCR / Axis Tick Detection
# ===========================================================

def _detect_axis_ticks_tesseract(state: SpectroState, config=None):
    # Tesseract is not good. I prefer paddle.
    if config is None:
        # config = r'--oem 3 --psm 5 -c tessedit_char_whitelist=0123456789.-eE+ '
        config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789.-eE+ '
        # config = r'--oem 3 --psm 11 -c tessedit_char_whitelist=0123456789.-eE+ '
    image_path = state['file_path']
    img = cv2.imread(image_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    sharpened = cv2.addWeighted(gray, 1.5, blurred, -0.5, 0)
    # # _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
    thresh = cv2.adaptiveThreshold(sharpened, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2)

    data = pytesseract.image_to_data(
        thresh, config=config, output_type=pytesseract.Output.DICT
    )

    tick_values = []
    for i in range(len(data['text'])):
        text = data['text'][i].strip()
        if text != "":
            try:
                value = float(text)
                (x, y, w, h) = (data['left'][i], data['top'][i],
                                data['width'][i], data['height'][i])
                cx, cy = x + w//2, y + h//2
                tick_values.append({
                    "value": value,
                    "position": [cx, cy],
                    "bounding-box-scale": [w, h]
                })
            except ValueError:
                pass

    return tick_values


def _detect_axis_ticks_paddle(state: SpectroState):
    ocr = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False
    )
    result = ocr.predict(state['file_path'])
    for res in result:
        # res.print()
        res.save_to_img(state['output_dir'])
        res.save_to_json(state['output_dir'])
    data = []
    for i in range(len(result[-1]['rec_texts'])):
        pos = result[-1]['rec_polys'][i]
        center = [
            int((pos[0][0] + pos[2][0]) / 2),
            int((pos[0][1] + pos[2][1]) / 2),
        ]
        width = int((pos[1][0] - pos[0][0] + pos[2][0] - pos[3][0]) / 2)
        height = int((pos[3][1] - pos[0][1] + pos[2][1] - pos[1][1]) / 2)
        info = {
            'value': result[-1]['rec_texts'][i],
            'position': center,
            'bounding-box-scale': [width, height]
        }
        data.append(info)
    return data


# ===========================================================
# Step 1.5: Border Detection & Image Cropping
# ===========================================================

def _detect_chart_border(
        image_path: str,
        margin: Dict = {
            'top': 10,
            'right': 10,
            'bottom': 10,
            'left': 10
        }
) -> dict:
    """
    检测图像中图表的外围边框，并微调尺寸。

    参数:
        image_path: 图像文件路径
        margin: 调整边框的像素量（正数表示收缩边框）

    返回:
        dict 包含边框位置: {"x": int, "y": int, "w": int, "h": int}
    """
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"无法读取图像: {image_path}")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 二值化
    _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)

    # 形态学操作去噪
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    morph = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)

    # 查找轮廓
    contours, _ = cv2.findContours(morph, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError("未找到任何轮廓")

    # 找到最大轮廓
    contours = sorted(contours, key=cv2.contourArea, reverse=True)
    x, y, w, h = cv2.boundingRect(contours[0])

    # 微调边框
    x += margin['left']
    y += margin['top']
    w -= (margin['left'] + margin['right'])
    h -= (margin['top'] + margin['bottom'])

    return {"x": x, "y": y, "w": w, "h": h}


def _crop_img(image_path: str, border_info: dict, save_path: str) -> str:
    """
    裁剪图像指定区域并保存。

    参数:
        image_path: 输入图像路径
        save_path: 保存裁剪后图像路径

    返回:
        save_path
    """
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"无法读取图像: {image_path}")

    x = border_info['x']
    y = border_info['y']
    w = border_info['w']
    h = border_info['h']

    img_cropped = img[y:y+h, x:x+w]
    cv2.imwrite(save_path, img_cropped)

    print(f'cropped image is saved to {save_path}')

    return save_path


# ===========================================================
# Step 1.6~1.7: Pixel Coordinate Remapping & Fitting
# ===========================================================

def _remap_to_cropped_canvas(old_info, chart_border):
    """
    将原图坐标映射到裁剪后的画布上，自动处理 None 坐标。
    """
    x0, y0, w, h = chart_border["x"], chart_border["y"], chart_border["w"], chart_border["h"]

    new_info = []
    for d in old_info:
        ox = d.get("position_x") if d.get("position_x") is not None else None
        oy = d.get("position_y") if d.get("position_y") is not None else None

        new_d = d.copy()

        # 重映射到裁剪画布
        nx = ox - x0 if ox is not None else None
        ny = oy - y0 if oy is not None else None
        # 越界裁剪
        nx = max(0, min(nx, w - 1)) if ox is not None else None
        ny = max(0, min(ny, h - 1)) if oy is not None else None
        new_d["position_x"] = nx
        new_d["position_y"] = ny

        new_info.append(new_d)

    return new_info


def _linear_func(x, a, b):
    return a * x + b


def _pixel_tickvalue_fitting(arr: list) -> dict:
    """
    对刻度数据做加权线性拟合（支持 x/y 轴分开）。
    输入: Python list，每个元素为 dict
    输出: dict 包含各轴拟合结果
    """
    results = {}
    for axis in ["x", "y"]:
        # 提取有效数据
        values, pixels, sigmas, confs = [], [], [], []
        for d in arr:
            if d["axis"] == axis and d[f'position_{axis}'] is not None:
                values.append(float(d["value"]))
                pixels.append(float(d["position_x"] if axis == 'x' else d["position_y"]))
                sigmas.append(float(d["sigma_pixel"]) if d["sigma_pixel"] is not None else np.inf)
                confs.append(float(d["conf_llm"]) if d["conf_llm"] is not None else 1.0)

        if len(values) < 2:
            continue

        values = np.array(values, dtype=float)
        pixels = np.array(pixels, dtype=float)
        sigmas = np.array(sigmas, dtype=float)
        confs = np.array(confs, dtype=float)

        # 有效 sigma
        sigma_eff = sigmas / np.sqrt(confs)

        # 拟合
        popt, _ = curve_fit(
            _linear_func,
            pixels,
            values,
            sigma=sigma_eff,
            absolute_sigma=True
        )
        a_fit, b_fit = popt
        value_fit = _linear_func(pixels, a_fit, b_fit)
        residual = values - value_fit
        rms = np.sqrt(np.mean(residual**2))

        results[axis] = {
            "a": float(a_fit),
            "b": float(b_fit),
            "rms": float(rms),
            "residuals": residual.tolist()
        }
        print(f"{axis}: {results[axis]}")

    return results


# ===========================================================
# Step 1.8: Curve Extraction & Spectrum Reconstruction
# ===========================================================

def _process_and_extract_curve_points(input_path: str):
    """
    读取图像，去除背景并转换为二值图像，提取曲线的像素点云。

    参数：
    - input_path：原始图像文件路径

    返回：
    - curve_points: 曲线像素点云（列表形式，包含每个点的(x, y)坐标）
    - curve_gray_values: 曲线像素灰度值（列表形式）
    """
    # 1. 读取原始图像
    img = cv2.imread(input_path)

    # 2. 转为灰度图像
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 3. 二值化处理，背景为白色，曲线为黑色
    _, binary = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)

    # 4. 提取白色曲线像素点（黑色曲线会变为白色，背景变为黑色）
    curve_points = []
    curve_gray_values = []

    # 遍历所有像素点，提取白色区域（即曲线部分）
    for y in range(binary.shape[0]):
        for x in range(binary.shape[1]):
            if binary[y, x] == 255:  # 白色区域（曲线）
                curve_points.append([x, y])
                curve_gray_values.append(gray[y, x])

    curve_gray_values = np.array(curve_gray_values, dtype=np.float64)

    return curve_points, curve_gray_values


def average_flux_by_wavelength(wavelength, flux):
    """
    对同一波长的flux进行简单平均。

    参数：
    - wavelength: 一维数组，表示波长
    - flux: 一维数组，表示光谱强度（flux）

    返回：
    - unique_wavelength: 每个唯一波长的数组
    - mean_flux: 每个波长对应的平均flux值
    """
    df = pd.DataFrame({
        'wavelength': wavelength,
        'flux': flux
    })

    # 对每个唯一的波长进行简单平均
    mean_flux = df.groupby('wavelength', group_keys=False)['flux'].mean()

    unique_wavelength = mean_flux.index.to_numpy()
    return unique_wavelength, mean_flux.to_numpy()


def _convert_to_spectrum(crop_path, axis_fitting_info, band_names=None, band_wavelengths=None):
    """
    从裁剪后的图像中提取曲线并转换为波长（wavelength）和光谱强度（flux）。

    输入：
    - crop_path: 裁剪后的图像路径
    - axis_fitting_info: 包含 x/y 轴拟合系数的字典
    - band_names: 波段名称列表（可选），用于计算并去除波段重叠区域
    - band_wavelengths: 波段波长范围列表（可选），每个元素为 [start, end]

    输出：
    - spectrum_dict: 包含转换后的波长、flux 和平均后的波长与flux的字典
    """
    # Step 1: 提取曲线像素点
    points, gray = _process_and_extract_curve_points(crop_path)

    # Step 2: 转换坐标到物理量
    points = np.array(points)
    xs = points[:, 0]
    ys = points[:, 1]

    # 提取 x 轴和 y 轴的物理量转换系数
    a_y = axis_fitting_info['y']['a']
    b_y = axis_fitting_info['y']['b']
    flux = a_y * ys + b_y

    a_x = axis_fitting_info['x']['a']
    b_x = axis_fitting_info['x']['b']
    wavelength = a_x * xs + b_x

    # Step 2.5: 去除波段重叠区域（若提供了波段信息）
    if band_names is not None and band_wavelengths is not None:
        overlap_regions = find_overlap_regions(band_names, band_wavelengths)
        if overlap_regions:
            print(f"检测到 {len(overlap_regions)} 个重叠区域，将从光谱中移除: {overlap_regions}")
            # 构建原始点的 mask：True 表示保留
            keep_mask = np.ones(len(wavelength), dtype=bool)
            for region_name, (ov_start, ov_end) in overlap_regions.items():
                in_overlap = (wavelength >= ov_start) & (wavelength <= ov_end)
                keep_mask &= ~in_overlap
                print(f"  移除重叠区域 '{region_name}': [{ov_start}, {ov_end}]，共 {in_overlap.sum()} 个点")
            wavelength = wavelength[keep_mask]
            flux = flux[keep_mask]

    # 计算每个波长的平均flux
    unique_wavelength, mean_flux = average_flux_by_wavelength(wavelength, flux)

    max_unresolved_flux = []
    min_unresolved_flux = []
    std_flux = []  # 存储每个波长的标准差

    # 计算每个波长的统计量
    for w in unique_wavelength:
        unresolved_flux = flux[wavelength == w]
        max_unresolved_flux.append(np.max(unresolved_flux))
        min_unresolved_flux.append(np.min(unresolved_flux))

        # 计算标准差
        if len(unresolved_flux) > 1:
            # 使用无偏估计（除以 n-1）计算样本标准差
            std_val = np.std(unresolved_flux, ddof=1)
        else:
            # 如果只有一个数据点，标准差设为 0
            std_val = 0.0
        std_flux.append(std_val)

    # 计算 delta_flux
    delta_flux = np.array(max_unresolved_flux) - np.array(min_unresolved_flux)

    # 计算信噪比 SNR = mean / std
    snr = np.where(
        np.array(std_flux) != 0,
        mean_flux / np.array(std_flux),
        np.inf
    )

    snr_medium = np.median(snr)
    # result = mode(snr)
    # print("SNR中位数:", snr_medium)
    # print("SNR众数:", result.mode)

    spectrum_dict = {
        'flux': flux.tolist(),
        'wavelength': wavelength.tolist(),
        'new_wavelength': unique_wavelength.tolist(),
        'weighted_flux': mean_flux.tolist(),
        'max_unresolved_flux': max_unresolved_flux,
        'min_unresolved_flux': min_unresolved_flux,
        'delta_flux': delta_flux.tolist(),
        'std_flux': std_flux,
        'effective_snr': snr.tolist(),
        'snr_medium': snr_medium
    }

    return spectrum_dict


# ===========================================================
# Step 1.10: Load Spectrum from FITS
# ===========================================================

def _load_spectrum_from_fits(state: SpectroState,
                           arm_names=None,
                           arm_wavelength_ranges=None) -> SpectroState:
    """
    从 FITS 文件中读取光谱数据。
    支持两种格式：
    1. 多波段分波段格式（通过 arm_names 指定波段名，如 ['B','R','Z'] 或 ['U','V','I']）
    2. 单表格格式（包含 WAVELENGTH/FLUX 列的 BinTableHDU）

    Parameters
    ----------
    arm_names : list[str], optional
        波段名称列表，如 ['B','R','Z']（DESI）或 ['U','V','I']（CSST）。
        若为 None 则报错终止，要求用户在 .env 中配置。
    arm_wavelength_ranges : list[list[float]], optional
        各波段波长范围，如 [[3600,5800],[5760,7620],[7520,9824]]。
        若为 None 则从数据中自动计算。用于波段重叠区域检测。
    """
    fits_path = state['file_path']

    with fits.open(fits_path) as hdul:
        # 获取所有 HDU 名称
        hdu_names = [hdu.name.upper() for hdu in hdul]
        logging.info(f"FITS HDU names: {hdu_names}")

        # 检测是否为多波段分波段格式
        # 若 arm_names 由外部配置提供，直接使用；否则自动从 HDU 名推断波段
        if arm_names is not None:
            band_names = arm_names
            is_multi_arm = all(
                f'{band}_WAVELENGTH' in hdu_names and f'{band}_FLUX' in hdu_names
                for band in band_names
            )
        else:
            # 未配置 arm_names，报错提示用户
            raise ValueError(
                "未配置多波段信息。请在 .env 中设置 ARM_NAME 和 ARM_WAVELENGTH_RANGE。\n"
                "例如：\n"
                "  ARM_NAME = B,R,Z\n"
                "  ARM_WAVELENGTH_RANGE = 3600-5800,5760-7620,7520-9824\n"
                "如果你使用 CSST 数据，请替换为 U,V,I 及对应波长范围。"
            )

        if is_multi_arm:
            # === 多波段分波段格式 ===
            logging.info(f"Detected multi-arm FITS format: {band_names}")

            # 读取各波段的数据并记录波长范围
            wavelength_list = []
            flux_list = []
            ivar_list = []
            snr_list = []
            mask_list = []
            has_ivar = False
            has_snr = False
            band_wavelengths = []
            if arm_wavelength_ranges is not None:
                band_wavelengths = arm_wavelength_ranges

            for band in band_names:
                wave_hdu = hdul[f'{band}_WAVELENGTH']
                flux_hdu = hdul[f'{band}_FLUX']

                wave_data = wave_hdu.data
                wavelength_list.append(wave_data)
                flux_list.append(flux_hdu.data)

                # 读取 IVAR / SNR（有则读，二者都有则都读）
                ivar_hdu_name = f'{band}_IVAR'
                snr_hdu_name = f'{band}_SNR'
                band_has_ivar = ivar_hdu_name in hdu_names
                band_has_snr = snr_hdu_name in hdu_names

                if band_has_ivar:
                    ivar_list.append(hdul[ivar_hdu_name].data)
                    has_ivar = True
                else:
                    ivar_list.append(None)

                if band_has_snr:
                    snr_list.append(hdul[snr_hdu_name].data)
                    has_snr = True
                else:
                    snr_list.append(None)

                if not band_has_ivar and not band_has_snr:
                    logging.warning(f"波段 {band} 缺少 IVAR 和 SNR，将使用默认 effective_snr=5.0")

                # 读取 SPECMASK（如果存在）
                mask_hdu_name = f'{band}_MASK'
                if mask_hdu_name in hdu_names:
                    mask_data = hdul[mask_hdu_name].data
                    mask_list.append(mask_data)
                    unique_masks = np.unique(mask_data)
                    if len(unique_masks) > 1 or unique_masks[0] != 0:
                        logging.info(f"  {band}_MASK has non-zero values: {unique_masks}")
                else:
                    mask_list.append(np.zeros(len(wave_data), dtype=np.uint32))
                    logging.info(f"  {band}_MASK not found, assuming all pixels are good")

                # 记录该波段的波长范围（未提供配置值时从数据计算）
                if arm_wavelength_ranges is None:
                    band_wavelengths.append([float(wave_data.min()), float(wave_data.max())])

            # 校验各波段噪声数据一致性（不允许混合 IVAR/SNR）
            all_have_ivar = all(x is not None for x in ivar_list)
            all_have_snr = all(x is not None for x in snr_list)
            if not all_have_ivar and any(x is not None for x in ivar_list):
                raise ValueError(
                    "部分波段有 IVAR 而部分没有，请确保所有波段的 IVAR 数据一致。"
                    f"IVAR 状态: {['有' if x is not None else '无' for x in ivar_list]}"
                )
            if not all_have_snr and any(x is not None for x in snr_list):
                raise ValueError(
                    "部分波段有 SNR 而部分没有，请确保所有波段的 SNR 数据一致。"
                    f"SNR 状态: {['有' if x is not None else '无' for x in snr_list]}"
                )

            # 合并数组
            wavelength = np.concatenate(wavelength_list)
            flux = np.concatenate(flux_list)

            if has_ivar:
                ivar = np.concatenate(ivar_list)  # 全部非 None，直接拼接
            else:
                ivar = None

            if has_snr:
                snr = np.concatenate(snr_list)  # 全部非 None，直接拼接
            else:
                snr = None

            specmask = np.concatenate(mask_list)

            logging.info(f"Combined {len(wavelength)} wavelength points from {len(band_names)} arms")
            logging.info(f"Wavelength range: {wavelength.min():.2f} - {wavelength.max():.2f} Å")

            # 统计整体 mask 情况
            n_masked = np.sum(specmask != 0)
            if n_masked > 0:
                logging.info(f"SPECMASK: {n_masked} pixels ({100*n_masked/len(specmask):.2f}%) have quality issues")
            else:
                logging.info("SPECMASK: All pixels are clean (mask=0)")

            # === 构建 quality mask ===
            if has_ivar and ivar is not None:
                quality_mask = (specmask == 0) & (ivar > 0)
            else:
                # 仅有 SNR 的情况（如 CSST）：mask 为 0 即表示好像素
                quality_mask = (specmask == 0)

            # === 引入 arm overlap mask ===
            overlap_regions = find_overlap_regions(band_names, band_wavelengths)

            keep_mask = quality_mask.copy()

            if overlap_regions:
                logging.info(f"Detected {len(overlap_regions)} overlap regions: {overlap_regions}")
                for region_name, (ov_start, ov_end) in overlap_regions.items():
                    in_overlap = (wavelength >= ov_start) & (wavelength <= ov_end)
                    keep_mask &= ~in_overlap
                    logging.info(f"  Masking overlap '{region_name}': [{ov_start:.2f}, {ov_end:.2f}], "
                                f"{in_overlap.sum()} points removed")

            # 应用 mask
            wavelength_masked = wavelength[keep_mask]
            flux_masked = flux[keep_mask]
            ivar_masked = ivar[keep_mask] if ivar is not None else None
            snr_masked = snr[keep_mask] if snr is not None else None

            # 排序 + 同波长去重（取均值）
            sort_idx = np.argsort(wavelength_masked)
            wavelength_masked = wavelength_masked[sort_idx]
            flux_masked = flux_masked[sort_idx]
            ivar_masked = ivar_masked[sort_idx] if ivar_masked is not None else None
            snr_masked = snr_masked[sort_idx] if snr_masked is not None else None

            # 合并相同波长：取 flux / ivar / snr 的均值
            _, unique_idx, dup_counts = np.unique(
                wavelength_masked, return_index=True, return_counts=True
            )
            if len(unique_idx) < len(wavelength_masked):
                n_dup = len(wavelength_masked) - len(unique_idx)
                logging.warning(f"发现 {n_dup} 个重复波长点，将对 flux/ivar/snr 取均值")
                new_wl = wavelength_masked[unique_idx]
                new_flux = np.zeros(len(unique_idx))
                new_ivar = np.zeros(len(unique_idx)) if ivar_masked is not None else None
                new_snr = np.zeros(len(unique_idx)) if snr_masked is not None else None
                for j, (start, count) in enumerate(
                    zip(unique_idx, dup_counts)
                ):
                    sl = slice(start, start + count)
                    new_flux[j] = np.mean(flux_masked[sl])
                    if new_ivar is not None:
                        new_ivar[j] = np.mean(ivar_masked[sl])
                    if new_snr is not None:
                        new_snr[j] = np.mean(snr_masked[sl])
                wavelength_masked = new_wl
                flux_masked = new_flux
                ivar_masked = new_ivar
                snr_masked = new_snr

            n_removed = len(wavelength) - len(wavelength_masked)
            n_quality_removed = np.sum(~quality_mask)
            logging.info(f"After masking: {len(wavelength_masked)} points remain "
                        f"({n_removed} removed, including {n_quality_removed} from SPECMASK)")

            if has_snr and snr_masked is not None:
                effective_snr = snr_masked.astype(np.float64)
            else:
                effective_snr = None

        else:
            # === 单表格格式 ===
            logging.info("Detected single-table FITS format")
            is_multi_arm = False
            overlap_regions = None

            data = None
            for i, hdu in enumerate(hdul):
                if hdu.data is not None:
                    data = hdu.data
                    logging.info(f"Found data in HDU {i}")
                    break

            if data is None:
                raise ValueError(f"No data found in FITS file: {fits_path}")

            if hasattr(data, 'dtype') and hasattr(data.dtype, 'names') and data.dtype.names is not None:
                col_names = data.dtype.names
                logging.info(f"FITS columns: {col_names}")

                wavelength_col = None
                flux_col = None
                snr_col = None
                ivar_col = None

                for name in col_names:
                    name_upper = name.upper()
                    if name_upper in ['WAVELENGTH', 'WAVE', 'LAMBDA']:
                        wavelength_col = name
                    elif name_upper in ['FLUX', 'F']:
                        flux_col = name
                    elif name_upper in ['SNR', 'SIGNAL_TO_NOISE']:
                        snr_col = name
                    elif name_upper in ['IVAR', 'INVERSE_VARIANCE']:
                        ivar_col = name

                if wavelength_col is None or flux_col is None:
                    raise ValueError(f"Required columns not found. Available: {col_names}")

                wavelength = np.array(data[wavelength_col])
                flux = np.array(data[flux_col])

                if snr_col is not None:
                    effective_snr = np.array(data[snr_col])
                    ivar = None
                    has_ivar = False
                elif ivar_col is not None:
                    ivar = np.array(data[ivar_col])
                    effective_snr = None
                    has_ivar = True
                else:
                    effective_snr = np.full_like(flux, 5.0, dtype=float)
                    ivar = None
                    has_ivar = False
            else:
                # 普通数组
                wavelength = data[:, 0]
                flux = data[:, 1]
                effective_snr = np.full_like(flux, 5.0, dtype=float)
                ivar = None
                has_ivar = False

        # 确保是 numpy 数组
        wavelength = np.array(wavelength, dtype=np.float64)
        flux = np.array(flux, dtype=np.float64)

        if has_ivar and ivar is not None:
            ivar = np.array(ivar, dtype=np.float64)

        # 多波段格式：使用 mask 后的数据
        if is_multi_arm:
            new_wavelength = np.array(wavelength_masked, dtype=np.float64)
            weighted_flux = np.array(flux_masked, dtype=np.float64)
            ivar_final = np.array(ivar_masked, dtype=np.float64) if ivar_masked is not None else None
        else:
            new_wavelength = wavelength
            weighted_flux = flux
            ivar_final = ivar if has_ivar and ivar is not None else None

        # 处理 effective_snr
        if effective_snr is not None:
            effective_snr = np.array(effective_snr, dtype=np.float64)
        elif ivar_final is not None:
            ivar_safe = np.maximum(ivar_final, 0)
            effective_snr = weighted_flux * np.sqrt(ivar_safe)
        else:
            effective_snr = np.full_like(weighted_flux, 5.0, dtype=np.float64)

        # smooth the spectrum
        weighted_flux = gaussian_filter1d(weighted_flux, sigma=2)

        # 构建 spectrum_dict
        spectrum_dict = {
            'new_wavelength': new_wavelength.tolist(),
            'weighted_flux': weighted_flux.tolist(),
            'effective_snr': effective_snr.tolist(),
            'ivar': ivar_final.tolist() if ivar_final is not None else None,
        }

        state['spectrum'] = spectrum_dict

    return state


# ===========================================================
# Step 1.11: Group Features for LLM Line Center Selection
# ===========================================================


# def group_features_for_llm(group: List[Dict[str, Any]], max_candidates: int = 20) -> Dict[str, Any]:
#     """
#     将 peak / trough group 整理为适合 LLM 判断的结构
    
#     Args:
#         group: 特征组列表
#         max_candidates: 最多返回的候选条目数，默认20
#     """

#     candidates = {}

#     # 判断是否包含 trough 信息
#     has_depth = any("depth" in g for g in group)
#     has_ew = any("equivalent_width_pixels" in g for g in group)

#     for item in group:

#         idx = item["index"]

#         if idx not in candidates:
#             candidates[idx] = {
#                 "index": idx,
#                 "wavelength": float(item["wavelength"]),
#                 "flux": float(item["flux"]),
#                 "evidence": {
#                     "global": [],
#                     "local": []
#                 }
#             }

#         source = item.get("source", "global")

#         sigma = item.get("global_sigma") if source == "global" else item.get("local_sigma")

#         ev = {
#             "sigma": sigma,
#             "prominence": float(item["prominence"]),
#             "width": float(item["width_wavelength"]),
#         }

#         if has_depth and "depth" in item:
#             ev['prominence'] = -ev['prominence']
#             ev["depth"] = float(item["depth"])

#         if has_ew and "equivalent_width_pixels" in item:
#             ev["equivalent_width"] = float(item["equivalent_width_pixels"])

#         candidates[idx]["evidence"][source].append(ev)

#     candidates_list = list(candidates.values())

#     # ---------- 智能筛选：如果超过 max_candidates，优先保留重要特征 ----------
#     if len(candidates_list) > max_candidates:
#         # 为每个 candidate 计算优先级分数
#         # 优先级：大 sigma > 高 prominence
#         def get_priority_score(c):
#             all_ev = c["evidence"]["global"] + c["evidence"]["local"]
#             if not all_ev:
#                 return (0, 0)
#             # 最大 sigma（优先保留大 sigma）
#             max_sigma = max(e.get("sigma", 0) or 0 for e in all_ev)
#             # 最大 prominence（在 sigma 相同时，优先保留高 prominence）
#             max_prom = max(abs(e.get("prominence", 0) or 0) for e in all_ev)
#             # 是否有 global 证据（优先保留有 global 的）
#             has_global = len(c["evidence"]["global"]) > 0
#             # 返回排序键：(是否有global, 最大sigma, 最大prominence)
#             return (has_global, max_sigma, max_prom)
        
#         # 按优先级降序排序
#         candidates_list.sort(key=get_priority_score, reverse=True)
#         # 截取前 max_candidates 个
#         candidates_list = candidates_list[:max_candidates]

#     # ---------- group center ----------
#     wavelengths = [c["wavelength"] for c in candidates_list]
#     group_center = float(np.mean(wavelengths))

#     # ---------- summary ----------
#     for c in candidates_list:

#         global_ev = c["evidence"]["global"]
#         local_ev = c["evidence"]["local"]

#         all_ev = global_ev + local_ev

#         prominences = [e["prominence"] for e in all_ev]
#         widths = [e["width"] for e in all_ev]

#         summary = {
#             "n_global": len(global_ev),
#             "n_local": len(local_ev),
#             "max_prominence": float(np.max(prominences)) if prominences else None,
#             "mean_width": float(np.mean(widths)) if widths else None
#         }

#         if has_depth:
#             depths = [e.get("depth") for e in all_ev if "depth" in e]
#             if depths:
#                 summary["max_depth"] = float(np.max(depths))

#         if has_ew:
#             ews = [e.get("equivalent_width") for e in all_ev if "equivalent_width" in e]
#             if ews:
#                 summary["max_equivalent_width"] = float(np.max(ews))

#         c["summary"] = summary

#         c["distance_to_group_center"] = abs(c["wavelength"] - group_center)

#     # 按波长排序
#     candidates_list.sort(key=lambda x: x["wavelength"])

#     return {
#         "group_center": group_center,
#         "candidates": candidates_list
#     }



# ===========================================================
# Step 1.10b: Continuum Fitting (Chebyshev)
# ===========================================================

def run_continuum_fitting(state, chebyshev_degree=None, chebyshev_min_degree=1, chebyshev_max_degree=10, verbose=False):
    """
    使用切比雪夫多项式拟合 continuum，并计算残差光谱。
    
    Parameters
    ----------
    state : SpectroState
        状态字典，需包含 'spectrum' 键
    chebyshev_degree : int or None
        指定切比雪夫多项式阶数；若为 None 则自动选择
    chebyshev_min_degree : int
        自动选择时的最小阶数
    chebyshev_max_degree : int
        自动选择时的最大阶数
    verbose : bool
        是否输出详细信息
    
    Returns
    -------
    state : SpectroState
        更新后的状态，包含 'continuum' 和 'residual_spectrum'
    """
    from specutils.fitting import fit_generic_continuum
    from specutils import Spectrum
    from astropy.modeling import models
    import astropy.units as u
    import warnings
    
    from AstroAgent.agents.multi_agents.utils.feature_finder_precise import select_chebyshev_degree
    
    spec = state['spectrum']
    wavelengths = np.array(spec['new_wavelength'], dtype=np.float64)
    flux = np.array(spec['weighted_flux'], dtype=np.float64)
    
    # 构建 specutils Spectrum 对象
    sp = Spectrum(flux=flux * u.Jy, spectral_axis=wavelengths * u.AA)
    
    # 自动选择或使用指定阶数
    if chebyshev_degree is None:
        chebyshev_degree = select_chebyshev_degree(
            sp, min_degree=chebyshev_min_degree, max_degree=chebyshev_max_degree,
            verbose=verbose
        )
    
    print(f'Continuum: Chebyshev degree={chebyshev_degree}')
    
    # 拟合 continuum
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        cf = fit_generic_continuum(sp, model=models.Chebyshev1D(degree=chebyshev_degree))
    continuum_flux = cf(sp.spectral_axis).value

    # 计算对数导数 dlnF/dλ = (dF/dλ) / |F|
    # 除以 |F| 可放大低信噪比 continuum 的增减性；用 abs 保护避免除零且不引入 sign(F)
    # （若用带符号的 F 作除数，负值区域增减性会被翻转）
    continuum_flux_safe = np.maximum(np.abs(continuum_flux), 1e-10)
    continuum_log_derivative = np.gradient(continuum_flux, wavelengths) / continuum_flux_safe
    # 根据对数导数的符号划分单调区间
    monotonic_regions = np.where(np.diff(np.sign(continuum_log_derivative)))[0]
    # 生成连续谱的自然语言描述
    continuum_description = generate_continuum_description(wavelengths, continuum_flux, monotonic_regions)
    
    print(continuum_description)

    state['continuum'] = {
        'wavelength': wavelengths.tolist(),
        'flux': continuum_flux.tolist(),
        'chebyshev_degree': chebyshev_degree,
        'description': continuum_description
    }
    
    # 计算残差光谱
    residual_flux = flux - continuum_flux
    state['residual_spectrum'] = {
        'wavelength': wavelengths.tolist(),
        'flux': residual_flux.tolist()
    }
    
    return state


def generate_continuum_description(
    wavelengths: np.ndarray,
    continuum_flux: np.ndarray,
    monotonic_regions: np.ndarray
) -> str:
    """
    Generate a natural language description of the continuum based on monotonic intervals.
    
    Parameters
    ----------
    wavelengths : np.ndarray
        Wavelength array
    continuum_flux : np.ndarray
        Continuum flux array
    monotonic_regions : np.ndarray
        Indices where monotonicity changes (from np.where(np.diff(np.sign(...))))
    
    Returns
    -------
    description : str
        Natural language description of the continuum, e.g.:
        "The continuum has a value of 2.027 at 4000.0 Å, monotonically increases
         from 4000.0 Å to 5010.1 Å, reaching 3.499 at 5010.1 Å; ..."
    """
    # monotonic_regions already computed by the caller (run_continuum_fitting);
    # derive per-segment sign directly from flux endpoints to avoid recomputing the derivative.
    # sign > 0: increasing, sign < 0: decreasing, == 0: flat
    boundaries = np.concatenate([[0], monotonic_regions + 1, [len(wavelengths) - 1]])
    segment_signs = np.sign(
        continuum_flux[boundaries[1:]] - continuum_flux[boundaries[:-1]]
    )
    
    # Generate descriptions
    descriptions = []
    
    for i in range(len(boundaries) - 1):
        start_idx = boundaries[i]
        end_idx = boundaries[i + 1]
        
        # Get wavelength and flux at interval endpoints
        lambda_start = wavelengths[start_idx]
        lambda_end = wavelengths[end_idx]
        flux_start = continuum_flux[start_idx]
        flux_end = continuum_flux[end_idx]
        
        # Determine monotonicity from segment sign
        seg_sign = segment_signs[i]
        if seg_sign > 0:
            monotonic_str = 'monotonically increases'
        elif seg_sign < 0:
            monotonic_str = 'monotonically decreases'
        else:
            monotonic_str = 'remains approximately flat'
        
        # Build description sentence
        if start_idx == 0:
            # First interval
            desc = f'The continuum has a value of {flux_start:.3f} at {lambda_start:.1f} Å, '
            desc += f'{monotonic_str} from {lambda_start:.1f} Å to {lambda_end:.1f} Å, '
            desc += f'reaching {flux_end:.3f} at {lambda_end:.1f} Å'
        else:
            # Subsequent intervals
            desc = f'{monotonic_str} from {lambda_start:.1f} Å to {lambda_end:.1f} Å, '
            desc += f'reaching {flux_end:.3f} at {lambda_end:.1f} Å'
        
        descriptions.append(desc)
    
    # Join into full description
    full_description = '; '.join(descriptions) + '.'
    
    return full_description


# ===========================================================
# File I/O Helpers
# ===========================================================

def run_continuum_fitting_masked(
    state,
    peaks=None,
    troughs=None,
    chebyshev_degree=None,
    chebyshev_min_degree=1,
    chebyshev_max_degree=10,
    verbose=False,
):
    """
    在将外检测到的峰/谷区域（中心 ±3σ）mask 掉后，对剩余光谱做切比雪夫拟合。
    拟合完成后用完整波长数组插値，将结果写入 state['continuum'] 和
    state['residual_spectrum']，格式与原 run_continuum_fitting 完全相同。

    Parameters
    ----------
    state : SpectroState
        状态字典，需包含 'spectrum' 键
    peaks : list of dict, optional
        发射线检测结果（records 格式），包含 'wavelength' 和 'FWHM_A' 字段
    troughs : list of dict, optional
        吸收线检测结果（records 格式），包含 'wavelength' 和 'FWHM_A' 字段
    chebyshev_degree : int or None
        指定切比雪夫多项式阶数；若为 None 则自动选择
    chebyshev_min_degree : int
    chebyshev_max_degree : int
    verbose : bool

    Returns
    -------
    state : SpectroState
        更新后的状态
    """
    from specutils.fitting import fit_generic_continuum
    from specutils import Spectrum
    from astropy.modeling import models
    import astropy.units as u
    import warnings

    from AstroAgent.agents.multi_agents.utils.feature_finder_precise import (
        select_chebyshev_degree, SIGMA_TO_FWHM
    )

    spec = state['spectrum']
    wavelengths = np.array(spec['new_wavelength'], dtype=np.float64)
    flux = np.array(spec['weighted_flux'], dtype=np.float64)

    # ── 构建特征区域 mask：中心 ±3σ 的点均被排除 ───────────────
    # 与迭代检测内部的 mask 逻辑保持一致：中心 ±3σ，其中 σ = FWHM_A / SIGMA_TO_FWHM
    fit_mask = np.ones(len(wavelengths), dtype=bool)  # True = 参与拟合

    all_features = []
    if peaks:
        all_features.extend(peaks)
    if troughs:
        all_features.extend(troughs)

    n_masked_pts = 0
    for feat in all_features:
        wl_center = feat.get('wavelength')
        fwhm_a = feat.get('FWHM_A')
        if wl_center is None or fwhm_a is None:
            continue
        sigma = fwhm_a / SIGMA_TO_FWHM
        lo, hi = wl_center - 3 * sigma, wl_center + 3 * sigma
        in_region = (wavelengths >= lo) & (wavelengths <= hi)
        n_masked_pts += int(in_region.sum())
        fit_mask &= ~in_region

    n_fit_pts = int(fit_mask.sum())
    print(f'[Continuum masked] 共 {len(all_features)} 个特征，'
          f'mask掉 {n_masked_pts} 个数据点，'
          f'参与拟合点数: {n_fit_pts}/{len(wavelengths)}')

    # 少于 20 个点时回退到未掉点的拟合
    if n_fit_pts < 20:
        print('[Continuum masked] 可用点数不足 20，回退为全谱拟合')
        fit_mask = np.ones(len(wavelengths), dtype=bool)

    wave_fit = wavelengths[fit_mask]
    flux_fit = flux[fit_mask]

    # ── 在 mask 后的子光谱上自动选阶和拟合 ────────────────────────
    sp_fit = Spectrum(flux=flux_fit * u.Jy, spectral_axis=wave_fit * u.AA)

    if chebyshev_degree is None:
        chebyshev_degree = select_chebyshev_degree(
            sp_fit, min_degree=chebyshev_min_degree, max_degree=chebyshev_max_degree,
            verbose=verbose
        )

    print(f'[Continuum masked] Chebyshev degree={chebyshev_degree}')

    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        cf = fit_generic_continuum(sp_fit, model=models.Chebyshev1D(degree=chebyshev_degree))

    # 在完整波长数组上求値（包括被 mask 的点）
    continuum_flux = cf(wavelengths * u.AA).value

    # ── 计算单调区间和自然语言描述 ───────────────────────────────
    continuum_flux_safe = np.maximum(np.abs(continuum_flux), 1e-10)
    continuum_log_derivative = np.gradient(continuum_flux, wavelengths) / continuum_flux_safe
    monotonic_regions = np.where(np.diff(np.sign(continuum_log_derivative)))[0]
    continuum_description = generate_continuum_description(wavelengths, continuum_flux, monotonic_regions)
    print(continuum_description)

    state['continuum'] = {
        'wavelength': wavelengths.tolist(),
        'flux': continuum_flux.tolist(),
        'chebyshev_degree': chebyshev_degree,
        'description': continuum_description,
        'n_masked_features': len(all_features),
        'n_masked_points': n_masked_pts,
    }

    residual_flux = flux - continuum_flux
    state['residual_spectrum'] = {
        'wavelength': wavelengths.tolist(),
        'flux': residual_flux.tolist()
    }

    return state


def save_features_catalog(
    output_dir: str,
    file_name: str,
    df_features,
    feature_type: str = 'emission'
) -> str:
    """
    将特征目录保存为 CSV 文件。
    
    Parameters
    ----------
    output_dir : str
        输出目录路径
    file_name : str
        文件名前缀
    df_features : DataFrame
        新格式特征 DataFrame
    feature_type : str
        'emission' 或 'absorption'
    
    Returns
    -------
    filepath : str
        保存的文件路径
    """
    if len(df_features) == 0:
        return None
    
    from AstroAgent.agents.multi_agents.utils.feature_finder_precise import save_catalog_csv
    
    suffix = 'emission' if feature_type == 'emission' else 'absorption'
    filepath = os.path.join(output_dir, f"{file_name}_{suffix}.csv")
    return save_catalog_csv(df_features, filepath, feature_type)


# ===========================================================
# Iterative Feature Detection
# ===========================================================

def run_iterative_feature_detection(
    output_dir: str,
    file_name: str,
    wavelength,
    flux,
    ivar=None,
    effective_snr=None,
    n_iterations: int = 3,
    absorption_detection_params: dict = None,
    emission_detection_params: dict = None,
    verbose: bool = True
) -> dict:
    """
    执行迭代特征检测：先检测吸收线，再在吸收线 mask 后的光谱上检测发射线。
    
    Parameters
    ----------
    output_dir : str
        输出目录路径
    file_name : str
        文件名前缀
    wavelength : array-like
        波长数组
    flux : array-like
        流量数组
    ivar : array-like, optional
        逆方差数组（优先于 effective_snr 使用）；由函数内部统一分发，无需在
        absorption_detection_params / emission_detection_params 中重复传入
    effective_snr : float or array-like, optional
        有效信噪比（仅当 ivar 为 None 时使用）；同上，由函数内部统一分发
    n_iterations : int
        迭代次数（默认 3）
    absorption_detection_params : dict, optional
        吸收线检测参数，覆盖 find_absorption_lines 内部默认值。
        可用键（见 iterative_absorption_detection 默认值）：
          chebyshev_degree, chebyshev_min_degree, chebyshev_max_degree,
          window_width, window_overlap,
          delta_chi2_base, dynamic_threshold_factor, global_delta_chi2_threshold,
          fwhm_min, fwhm_max,
          enable_density_presift, density_presift_threshold, density_presift_topk,
          blend_k, snr_depth_threshold,
          local_continuum_degree, local_continuum_n_iter, local_continuum_sigma_clip,
          narrow_sigma_shrink, narrow_sigma_expand,
          broad_sigma_shrink, broad_sigma_expand,
          amp_floor_frac, center_max_shift,
          smooth_sigma_trough, smooth_prominence_frac_trough
    emission_detection_params : dict, optional
        发射线检测参数，覆盖 find_emission_lines 内部默认值。
        可用键（见 iterative_emission_detection 默认值）：
          chebyshev_degree, chebyshev_min_degree, chebyshev_max_degree,
          window_width, window_overlap,
          delta_chi2_base, dynamic_threshold_factor, global_delta_chi2_threshold,
          fwhm_min, fwhm_max,
          enable_density_presift, density_presift_threshold, density_presift_topk,
          blend_k,
          narrow_sigma_shrink, narrow_sigma_expand,
          broad_sigma_shrink, broad_sigma_expand,
          amp_floor_frac, center_max_shift
    verbose : bool
        是否输出详细信息
    
    Returns
    -------
    result : dict
        包含以下键：
        - df_absorption : DataFrame, 吸收线结果（按波长升序排列，含 amplitude_rank）
        - df_emission : DataFrame, 发射线结果（按波长升序排列，含 amplitude_rank）
        - records_absorption : list, 吸收线记录列表
        - records_emission : list, 发射线记录列表
        - wave_remaining : array, mask 后剩余波长
        - flux_remaining : array, mask 后剩余流量
    """
    from AstroAgent.agents.multi_agents.utils.feature_finder_precise import (
        iterative_absorption_detection,
        iterative_emission_detection,
        save_catalog_csv,
    )
    
    wavelength = np.asarray(wavelength, dtype=np.float64)
    flux = np.asarray(flux, dtype=np.float64)
    
    # ── ivar / effective_snr 统一预处理 ──────────────────────────
    # ivar 和 effective_snr 由顶层统一传入，函数内部负责分发，
    # absorption_detection_params / emission_detection_params 中不应包含这两个键
    if ivar is not None:
        ivar = np.asarray(ivar, dtype=np.float64)
        effective_snr = None
    elif effective_snr is None:
        effective_snr = 7.0
    
    if effective_snr is not None:
        if np.isscalar(effective_snr):
            effective_snr = np.full_like(flux, effective_snr, dtype=np.float64)
        else:
            effective_snr = np.asarray(effective_snr, dtype=np.float64)
    
    # ── 构建吸收线检测参数：将 ivar/snr 注入，再合并用户自定义参数 ──
    abs_params = {}
    if ivar is not None:
        abs_params['ivar'] = ivar
    elif effective_snr is not None:
        abs_params['effective_snr'] = effective_snr
    if absorption_detection_params:
        abs_params.update(absorption_detection_params)

    # ── Step 1: 迭代吸收线检测 ───────────────────────────────────
    if verbose:
        print("\n" + "="*70)
        print("开始迭代吸收线检测")
        print("="*70)
    
    df_abs, records_abs, wave_after_abs, flux_after_abs = iterative_absorption_detection(
        wavelength=wavelength,
        flux=flux,
        n_iterations=n_iterations,
        detection_params=abs_params,
        verbose=verbose,
        output_format='new',
    )
    
    if verbose:
        print(f"\n检测到 {len(df_abs)} 条吸收线")
    
    # ── Step 1.5: 将 ivar/snr 同步到吸收线 mask 后的波长网格 ──────
    # iterative_absorption_detection 内部会逐迭代 mask 波长数组，
    # 发射线检测需要与 wave_after_abs 长度一致的 ivar/snr
    if ivar is not None:
        if len(ivar) != len(wave_after_abs):
            keep_mask = np.isin(wavelength, wave_after_abs)
            if keep_mask.sum() == len(wave_after_abs):
                ivar_for_em = ivar[keep_mask]
            else:
                ivar_for_em = np.interp(wave_after_abs, wavelength, ivar)
        else:
            ivar_for_em = ivar
        em_ivar_snr = {'ivar': ivar_for_em}
    else:
        snr = effective_snr
        if snr is not None and not np.isscalar(snr) and len(snr) != len(wave_after_abs):
            keep_mask = np.isin(wavelength, wave_after_abs)
            if keep_mask.sum() == len(wave_after_abs):
                snr_for_em = snr[keep_mask]
            else:
                snr_for_em = np.interp(wave_after_abs, wavelength, snr)
        else:
            snr_for_em = snr
        em_ivar_snr = {'effective_snr': snr_for_em} if snr_for_em is not None else {}
    
    # ── 构建发射线检测参数：注入同步后的 ivar/snr，再合并用户自定义参数 ──
    em_params = {}
    em_params.update(em_ivar_snr)
    if emission_detection_params:
        em_params.update(emission_detection_params)
    
    # ── Step 2: 迭代发射线检测（在吸收线已 mask 的光谱上）──────
    if verbose:
        print("\n" + "="*70)
        print("在吸收线已 mask 的光谱上执行发射线检测")
        print("="*70)
    
    df_em, records_em, wave_remaining, flux_remaining = iterative_emission_detection(
        wavelength=wave_after_abs,
        flux=flux_after_abs,
        n_iterations=n_iterations,
        detection_params=em_params,
        verbose=verbose,
        output_format='new',
        df_troughs=df_abs,  # 用于伪峰标注
    )
    
    if verbose:
        print(f"\n检测到 {len(df_em)} 条发射线")
    
    # ── Step 2.5: 添加近邻信息 ────────────────────────────────────
    records_abs = _add_neighbor_info(records_abs)
    records_em = _add_neighbor_info(records_em)
        
    # 更新 DataFrame 中的邻居信息
    if len(df_abs) > 0:
        df_abs['left_neighbor'] = [r.get('left_neighbor', 'None') for r in records_abs]
        df_abs['right_neighbor'] = [r.get('right_neighbor', 'None') for r in records_abs]
    if len(df_em) > 0:
        df_em['left_neighbor'] = [r.get('left_neighbor', 'None') for r in records_em]
        df_em['right_neighbor'] = [r.get('right_neighbor', 'None') for r in records_em]
    
    # ── Step 2.6: 按波长排序并添加 amplitude_rank ───────────────────────────────
    # amplitude_rank: 发射线按幅度（峰高）从高到低排名；吸收线按深度（幅度绝对值）从深到浅排名
        
    # 吸收线：按波长排序，添加 amplitude_rank
    if len(df_abs) > 0:
        # 先按 amplitude 计算排名（幅度越大，排名越靠前）
        df_abs['amplitude_rank'] = df_abs['amplitude'].abs().rank(ascending=False, method='min').astype(int)
        # 按波长排序
        df_abs = df_abs.sort_values('wavelength', ascending=True).reset_index(drop=True)
        # 同步更新 records_abs
        records_abs = _update_records_with_rank_and_sort(df_abs, records_abs, 'absorption')
        
    # 发射线：按波长排序，添加 amplitude_rank
    if len(df_em) > 0:
        # 按 amplitude 计算排名（幅度越大，排名越靠前）
        df_em['amplitude_rank'] = df_em['amplitude'].rank(ascending=False, method='min').astype(int)
        # 按波长排序
        df_em = df_em.sort_values('wavelength', ascending=True).reset_index(drop=True)
        # 同步更新 records_em
        records_em = _update_records_with_rank_and_sort(df_em, records_em, 'emission')
    
    # ── Step 3: 保存结果 ──────────────────────────────────────────
    if len(df_abs) > 0:
        abs_path = os.path.join(output_dir, f"{file_name}_absorption.csv")
        save_catalog_csv(df_abs, abs_path, 'absorption')
    
    if len(df_em) > 0:
        em_path = os.path.join(output_dir, f"{file_name}_emission.csv")
        save_catalog_csv(df_em, em_path, 'emission')
    
    return {
        'df_absorption': df_abs,
        'df_emission': df_em,
        'records_absorption': records_abs,
        'records_emission': records_em,
        'wave_remaining': wave_remaining,
        'flux_remaining': flux_remaining,
    }


def _add_neighbor_info(records: list) -> list:
    """
    为谱线记录添加近邻信息（同类型谱线内查找）。
    
    Parameters
    ----------
    records : list of dict
        谱线记录列表，每条记录需包含 'wavelength' 字段
    
    Returns
    -------
    records : list of dict
        添加了 'left_neighbor' 和 'right_neighbor' 字段的记录
        格式："Left neighbor: {wavelength:.1f} Å, distance: {distance:.1f} Å"
              "Left neighbor: None, distance: None"
    """
    if not records:
        return records
    
    # 按波长排序
    sorted_records = sorted(records, key=lambda x: x.get('wavelength', 0))
    
    for i, rec in enumerate(sorted_records):
        current_wl = rec.get('wavelength', 0)
        
        # 左邻居（波长更小的最近邻）
        if i > 0:
            left_wl = sorted_records[i - 1].get('wavelength', 0)
            left_dist = current_wl - left_wl
            rec['left_neighbor'] = f"Left neighbor: {left_wl:.1f} Å, distance: {left_dist:.1f} Å"
        else:
            rec['left_neighbor'] = "Left neighbor: None, distance: None"
        
        # 右邻居（波长更大的最近邻）
        if i < len(sorted_records) - 1:
            right_wl = sorted_records[i + 1].get('wavelength', 0)
            right_dist = right_wl - current_wl
            rec['right_neighbor'] = f"Right neighbor: {right_wl:.1f} Å, distance: {right_dist:.1f} Å"
        else:
            rec['right_neighbor'] = "Right neighbor: None, distance: None"
    
    return sorted_records


def _update_records_with_rank_and_sort(df, records, feature_type):
    """
    根据 DataFrame 的排序和排名更新 records 列表。
    
    Parameters
    ----------
    df : DataFrame
        已排序且包含 amplitude_rank 列的 DataFrame
    records : list of dict
        原始记录列表
    feature_type : str
        'emission' 或 'absorption'
    
    Returns
    -------
    updated_records : list of dict
        按波长排序并添加了 amplitude_rank 的记录列表
    """
    if len(df) == 0 or not records:
        return records
    
    # 创建波长到记录的映射
    wavelength_to_record = {rec['wavelength']: rec.copy() for rec in records}
    
    updated_records = []
    for _, row in df.iterrows():
        wl = row['wavelength']
        if wl in wavelength_to_record:
            rec = wavelength_to_record[wl]
            rec['amplitude_rank'] = int(row['amplitude_rank'])
            updated_records.append(rec)
    
    return updated_records


def save_resolved_features(
    output_dir: str,
    file_name: str,
    resolved_peaks,
    resolved_troughs
) -> None:
    """
    将 resolved peaks/troughs 保存到 JSON 文件。
    
    Parameters
    ----------
    output_dir : str
        输出目录路径
    file_name : str
        文件名前缀
    resolved_peaks : list
        已解析的峰列表
    resolved_troughs : list
        已解析的谷列表
    """
    with open(os.path.join(output_dir, f"{file_name}_resolved_peaks.txt"), "w") as f:
        json.dump(resolved_peaks, f, ensure_ascii=False, indent=4)
    with open(os.path.join(output_dir, f"{file_name}_resolved_troughs.txt"), "w") as f:
        json.dump(resolved_troughs, f, ensure_ascii=False, indent=4)


# ===========================================================
# Brute-Force Line Matching
# ============================================================

# 静止系谱线表
EMISSION_LINES = {
    # 高电离 / AGN 特征线
    "Lyα":       1216.0,
    "C IV":      1549.0,
    "He II":     1640.4,
    "C III]":    1909.0,
    "Mg II":     2800.0,
    "Ne [V]":    3426.0,
    "O [II]":    3727.0,
    # Balmer 系列
    "Hε":        3970.1,
    "Hδ":        4102.9,
    "Hγ":        4341.7,
    "Hβ":        4862.7,
    # 窄线区
    "O [III]a":  4960.3,
    "O [III]b":  5008.2,
    "N [II]a":   6549.8,
    "Hα":        6564.6,
    "N [II]b":   6585.3,
    "S [II]a":   6718.3,
    "S [II]b":   6732.7,
}

# 发射线宽窄分类：broad = 宽线区 (BLR) 允许的宽线，narrow = 窄线区 (NLR) 典型窄线
# both = BLR+NLR 均可产生，宽窄皆合理（Balmer 系在 QSO 中有 broad+narrow 叠加，
#        在 galaxy 中仅 narrow），宽度校验对 both 类跳过
# 用于匹配时检查寻峰宽度与物理期望是否一致
EMISSION_LINE_WIDTHS = {
    # BLR 宽线
    "Lyα":       "broad",
    "C IV":      "broad",
    "C III]":    "broad",
    "He II":     "both", # 在 QSO 中可以表现为 BLR 宽线，在低电离 AGN 或 Galaxy 中也可以是较窄的线
    "Mg II":     "broad",
    # Balmer 系列：QSO 中 broad+narrow 叠加，galaxy 中仅 narrow
    "Hε":        "both",
    "Hδ":        "both",
    "Hγ":        "both",
    "Hβ":        "both",
    "Hα":        "both",
    # NLR 窄线
    "Ne [V]":    "narrow",
    "O [II]":    "narrow",
    "O [III]a":  "narrow",
    "O [III]b":  "narrow",
    "N [II]a":   "narrow",
    "N [II]b":   "narrow",
    "S [II]a":   "narrow",
    "S [II]b":   "narrow",
}

ABSORPTION_LINES = {
    "Ca K_abs":      3934.8,
    "Ca H_abs":      3969.6,
    "G-band_abs":    4305.6,
    "Mg_abs":        5176.7,
    "Mg II_abs":     2800.0,
    "Na D_abs":      5895.6,
    "CaT1_abs":      8498.0,
    "CaT2_abs":      8542.0,
    "CaT3_abs":      8662.0,
    # Balmer 吸收
    "Hε_abs":    3970.1,
    "Hδ_abs":    4102.9,
    "Hγ_abs":    4341.7,
    "Hβ_abs":    4862.7,
    "Hα_abs":    6564.6,
}

# 锚定时只用发射线假设
# Mg II 2800 / H 系列既可发射也可吸收，但锚定时统一按发射线处理
ANCHOR_EMISSION_LINES = EMISSION_LINES

# LRG/BGS 专用锚定谱线表：排除 BLR 宽线（broad），保留窄线和 Balmer both 类
# 派生自 EMISSION_LINE_WIDTHS，EMISSION_LINE_WIDTHS 是唯一宽窄分类来源，改一处全部生效
LRG_ANCHOR_EMISSION_LINES = {
    k: v for k, v in EMISSION_LINES.items()
    if EMISSION_LINE_WIDTHS.get(k) != 'broad'
}


def _compute_dn4000(obs_wavelength, obs_flux, z):
    """
    将观测系光谱转换到静止系，计算 D_n(4000) 指标及三段线性斜率。

    Parameters
    ----------
    obs_wavelength : array-like
        观测系波长数组（Å），对应 state['spectrum']['new_wavelength']
    obs_flux : array-like
        对应流量数组，对应 state['spectrum']['weighted_flux']
    z : float
        红移值，用于将观测系转换为静止系

    Returns
    -------
    dict with keys:
        Dn4000           : float | None  — D_n(4000) 值
        strength         : str           — "weak" / "moderate" / "strong" / "insufficient data"
        mean_flux_3850_3950 : float | None
        mean_flux_4000_4100 : float | None
        slope_3850_3950  : float | None  — 线性斜率（flux/Å，静止系）
        slope_3950_4000  : float | None
        slope_4000_4100  : float | None
        n_points_3850_3950 : int
        n_points_4000_4100 : int
    """
    obs_wavelength = np.asarray(obs_wavelength, dtype=np.float64)
    obs_flux = np.asarray(obs_flux, dtype=np.float64)

    # 转换到静止系
    rest_wavelength = obs_wavelength / (1.0 + z)

    def _window_stats(wl_lo, wl_hi):
        """返回窗口内的 (wavelength, flux) 子集"""
        mask = (rest_wavelength >= wl_lo) & (rest_wavelength <= wl_hi)
        return rest_wavelength[mask], obs_flux[mask]

    def _linear_slope(wl_arr, flux_arr):
        """用 numpy.polyfit 拟合一阶多项式，返回斜率；点数 < 2 时返回 None"""
        if len(wl_arr) < 2:
            return None
        coeffs = np.polyfit(wl_arr, flux_arr, 1)
        return float(coeffs[0])  # 斜率，单位 flux/Å

    wl_a, fl_a = _window_stats(3850.0, 3950.0)  # 蓝侧参考窗口
    wl_b, fl_b = _window_stats(3950.0, 4000.0)  # break 过渡段
    wl_c, fl_c = _window_stats(4000.0, 4100.0)  # 红侧 break 窗口

    mean_a = float(np.mean(fl_a)) if len(fl_a) > 0 else None
    mean_c = float(np.mean(fl_c)) if len(fl_c) > 0 else None

    # D_n(4000) = mean_flux(4000-4100) / mean_flux(3850-3950)
    if mean_a is not None and mean_c is not None and mean_a != 0:
        dn4000 = mean_c / mean_a
        if dn4000 < 1.0:
            strength = "unphysical (Dn4000 < 1.0)"
        elif dn4000 < 1.3:
            strength = "weak"
        elif dn4000 > 1.5:
            strength = "strong"
        else:
            strength = "moderate"
    else:
        dn4000 = None
        strength = "insufficient data"

    return {
        "Dn4000":              round(dn4000, 4) if dn4000 is not None else None,
        "strength":            strength,
        "mean_flux_3850_3950": round(mean_a, 6) if mean_a is not None else None,
        "mean_flux_4000_4100": round(mean_c, 6) if mean_c is not None else None,
        "slope_3850_3950":     (lambda s: round(s, 6) if s is not None else None)(_linear_slope(wl_a, fl_a)),
        "slope_3950_4000":     (lambda s: round(s, 6) if s is not None else None)(_linear_slope(wl_b, fl_b)),
        "slope_4000_4100":     (lambda s: round(s, 6) if s is not None else None)(_linear_slope(wl_c, fl_c)),
        "n_points_3850_3950":  int(len(fl_a)),
        "n_points_4000_4100":  int(len(fl_c)),
    }


def brute_force_line_matching(state, tol_wavelength=None,
                              min_qso_redshift=float('-inf'),
                              min_galaxy_redshift=float('-inf'),
                              mode='qso'):
    """
    暴力破解：对每个峰假设其为某条发射线，计算红移，
    再用该红移预测所有其他谱线位置，匹配峰列表和谷列表。
    最后按 (波长, 线名) 对做 Union-Find 去重，合并属于同一物理场景的假设。

    参数
    ----
    state : dict
        必须包含 state['peaks'] 和 state['troughs']，
        每个元素需有 'wavelength' 键。
    tol_wavelength : float, optional
        匹配容差 (Å)。若为 None 则默认为 10.0。
    min_qso_redshift : float, optional
        QSO 最小红移阈值，默认 -inf。
    min_galaxy_redshift : float, optional
        Galaxy 最小红移阈值，默认 -inf。
    mode : str, optional
        匹配模式，控制峰过滤和锚定线表选择：
        - 'qso'      ：全部峰 + 全部发射线（默认）
        - 'elg'      ：窄峰 + 全部发射线
        - 'lrg_bgs'  ：窄峰 + 非broad发射线 + 谷锚定

    返回
    ----
    list[dict] : 去重后的匹配结果，每个元素对应一个物理场景：
        {
            "Hypothesis": "3788.0-Lyα, 4840.0-C IV, ...",
            "z_max": 2.1246,
            "z_min": 2.1031,
            "z_spread": 0.0215,
            "Emission matches": [
                "3717.900 Å (Amp=0.850, W=45.200Å/3200.000 km/s, broad) → Lyα (z=2.063)",
                ...
            ],
            "Absorption matches": [
                "8679.800 Å (Amp=-0.120, W=12.500Å/430.000 km/s, narrow) → Mg II (z=2.100)",
            ],
            "N_emission": 6,
            "N_absorption": 1,
            "Redshift warning": "z too low for QSO" | None,
            "Missing emission lines": [
                "C IV (1549.0 Å rest) → 4760.123–4762.456 Å obs [in range, not matched]",
                "Ne [V] (3426.0 Å rest) → ~10521.234 Å obs [possibly in range]",
            ],
        }
        结果按 N_emission + N_absorption 降序排列。
    """

    if tol_wavelength is None:
        tol_wavelength = 10.0

    # ── mode 分支：控制峰过滤和锚定线表选择 ──
    # qso      ：全部峰 + 全部发射线（BLR 宽线 + NLR 窄线均可）
    # elg      ：窄峰 + 全部发射线（ELG 窄发射线，排除连续谱伪宽峰）
    # lrg_bgs  ：窄峰 + 非broad发射线 + 谷锚定（LRG/BGS 吸收线为主）
    if mode == 'lrg_bgs':
        # active_peaks = [p for p in state['peaks'] if p.get('width_class') != 'broad']
        active_peaks = [p for p in state['peaks'] if p.get('width_class') == 'narrow']
        active_anchor_lines = LRG_ANCHOR_EMISSION_LINES
        active_emission_lines = LRG_ANCHOR_EMISSION_LINES
    elif mode == 'elg':
        active_peaks = [p for p in state['peaks'] if p.get('width_class') == 'narrow']
        active_anchor_lines = ANCHOR_EMISSION_LINES
        active_emission_lines = EMISSION_LINES
    else:  # mode == 'qso'（默认）
        active_peaks = state['peaks']
        active_anchor_lines = ANCHOR_EMISSION_LINES
        active_emission_lines = EMISSION_LINES

    peak_wavelengths = [p['wavelength'] for p in active_peaks]
    trough_wavelengths = [t['wavelength'] for t in state['troughs']]
    trough_wl_set = set(trough_wavelengths)  # 用于区分 emission/absorption

    # 观测波长范围（用于 Missing Emission Lines 判断）
    try:
        _wl_arr = state['spectrum']['new_wavelength']
        obs_wl_min = float(_wl_arr[0])
        obs_wl_max = float(_wl_arr[-1])
    except Exception:
        obs_wl_min = None
        obs_wl_max = None

    # ── 构建波长 → 峰/谷特征信息的查找表 ────────────────────
    peak_info = {}   # wavelength -> {amplitude, FWHM_A, FWHM_km_s, width_class}
    for p in active_peaks:
        peak_info[p['wavelength']] = {
            'amplitude': p.get('amplitude'),
            'FWHM_A': p.get('FWHM_A'),
            'FWHM_km_s': p.get('FWHM_km_s'),
            'width_class': p.get('width_class'),
        }
    trough_info = {}
    for t in state['troughs']:
        trough_info[t['wavelength']] = {
            'amplitude': t.get('amplitude'),
            'FWHM_A': t.get('FWHM_A'),
            'FWHM_km_s': t.get('FWHM_km_s'),
            'width_class': t.get('width_class'),
        }

    def _fmt_feature(wl, info):
        """格式化单条峰/谷的宽高信息片段，数值保留 3 位小数"""
        if info is None:
            return ""
        amp = info.get('amplitude')
        fwhm_a = info.get('FWHM_A')
        fwhm_kms = info.get('FWHM_km_s')
        wc = info.get('width_class')
        parts = []
        if amp is not None:
            parts.append(f"Amp={amp:.3f}")
        if fwhm_a is not None or fwhm_kms is not None:
            w_parts = []
            if fwhm_a is not None:
                w_parts.append(f"{fwhm_a:.3f}Å")
            if fwhm_kms is not None:
                w_parts.append(f"{fwhm_kms:.3f} km/s")
            parts.append(f"W={'/'.join(w_parts)}")
        if wc is not None:
            parts.append(wc)
        if not parts:
            return ""
        return f" ({', '.join(parts)})"

    # ── 第一阶段：逐峰逐线暴力匹配，收集原始结果 ──────────────
    # 每个原始假设产出一组 (wavelength, line_name, z) 三元组
    # raw_results: [{anchor_wl, anchor_line, anchor_z, pairs: [(wl, line, z), ...]}]

    raw_results = []

    for peak_wl in peak_wavelengths:
        for line_name, line_rest in active_anchor_lines.items():
            z = peak_wl / line_rest - 1.0
            if z < 0 or z > 10:
                continue
            z_rounded = round(z, 3)

            pairs = []  # (obs_wavelength, line_name, z)

            # 锚定线自身
            pairs.append((peak_wl, line_name, z_rounded))

            # 发射线匹配
            for ename, erest in active_emission_lines.items():
                if ename == line_name:
                    continue
                lambda_theory = erest * (1.0 + z)
                for pwl in peak_wavelengths:
                    delta = abs(pwl - lambda_theory)
                    if delta <= tol_wavelength:
                        pair_z = round(pwl / erest - 1.0, 3)
                        pairs.append((pwl, ename, pair_z))

            # 吸收线匹配
            for aname, arest in ABSORPTION_LINES.items():
                lambda_theory = arest * (1.0 + z)
                for twl in trough_wavelengths:
                    delta = abs(twl - lambda_theory)
                    if delta <= tol_wavelength:
                        pair_z = round(twl / arest - 1.0, 3)
                        pairs.append((twl, aname, pair_z))

            raw_results.append({
                'anchor_wl': peak_wl,
                'anchor_line': line_name,
                'anchor_z': z_rounded,
                'pairs': pairs,
            })

    # ── 第一阶段补充（lrg_bgs）：逐谷锚定 ──────────────────────
    # LRG/BGS 以吸收线为主要特征，允许用谷做锚点生成假设
    # 对每个谷，假设它是某条吸收线，计算红移，再验证其余谷和窄峰是否吻合
    if mode == 'lrg_bgs':
        for trough_wl in trough_wavelengths:
            for aname, arest in ABSORPTION_LINES.items():
                z = trough_wl / arest - 1.0
                if z < 0 or z > 10:
                    continue
                z_rounded = round(z, 3)

                pairs = []

                # 锚定线自身
                pairs.append((trough_wl, aname, z_rounded))

                # 验证其余吸收谷
                for aname2, arest2 in ABSORPTION_LINES.items():
                    if aname2 == aname:
                        continue
                    lambda_theory = arest2 * (1.0 + z)
                    for twl in trough_wavelengths:
                        delta = abs(twl - lambda_theory)
                        if delta <= tol_wavelength:
                            pair_z = round(twl / arest2 - 1.0, 3)
                            pairs.append((twl, aname2, pair_z))

                # 验证窄峰（active_emission_lines）
                for ename, erest in active_emission_lines.items():
                    lambda_theory = erest * (1.0 + z)
                    for pwl in peak_wavelengths:
                        delta = abs(pwl - lambda_theory)
                        if delta <= tol_wavelength:
                            pair_z = round(pwl / erest - 1.0, 3)
                            pairs.append((pwl, ename, pair_z))

                raw_results.append({
                    'anchor_wl': trough_wl,
                    'anchor_line': aname,
                    'anchor_z': z_rounded,
                    'pairs': pairs,
                })


    # ── 第二阶段：Union-Find 去重 ─────────────────────────────
    # 节点 = (wavelength, line_name) 对
    # 同一假设中的所有节点互连

    parent = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # 路径压缩
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # 收集所有节点并初始化
    for hyp in raw_results:
        for wl, lname, _ in hyp['pairs']:
            node = (wl, lname)
            if node not in parent:
                parent[node] = node

    # 同一假设内所有节点 union
    for hyp in raw_results:
        pair_nodes = [(wl, lname) for wl, lname, _ in hyp['pairs']]
        if len(pair_nodes) < 2:
            continue
        first = pair_nodes[0]
        for node in pair_nodes[1:]:
            union(first, node)

    # 按根节点分组
    groups = {}  # root -> set of (wl, line_name)
    for node in parent:
        root = find(node)
        groups.setdefault(root, set()).add(node)

    # ── 第三阶段：每个连通分量格式化为 LLM 可读输出 ───────────

    results = []

    for root, node_set in groups.items():
        # 收集组内所有 (wl, line_name, z) 信息
        # 需要从 raw_results 中查找每个 (wl, line_name) 对应的 z
        pair_z_map = {}  # (wl, line_name) -> z
        for hyp in raw_results:
            for wl, lname, z in hyp['pairs']:
                node = (wl, lname)
                if node in node_set:
                    if node not in pair_z_map:
                        pair_z_map[node] = z

        # 区分发射线和吸收线：以波长来源（peak/trough）为准，
        # 而非仅凭谱线名——Mg II 等线同时存在于发射线表与吸收线表
        emission_nodes = []
        absorption_nodes = []
        for (wl, lname), z in pair_z_map.items():
            if wl in trough_wl_set:
                absorption_nodes.append((wl, lname, z))
            else:
                emission_nodes.append((wl, lname, z))

        # 按波长升序排列
        emission_nodes.sort(key=lambda x: x[0])
        absorption_nodes.sort(key=lambda x: x[0])

        # z 统计
        all_z = list(pair_z_map.values())
        if not all_z:
            continue
        z_max = max(all_z)
        z_min = min(all_z)
        z_spread = round(z_max - z_min, 3)

        # Hypothesis：列出组内所有锚定对（从 raw_results 中找 anchor）
        anchor_pairs = []
        for hyp in raw_results:
            anchor_node = (hyp['anchor_wl'], hyp['anchor_line'])
            if anchor_node in node_set:
                anchor_pairs.append((hyp['anchor_wl'], hyp['anchor_line']))
        # 去重，按波长排序，同一波长的多条谱线合并为 wl-line1/line2 格式
        anchor_pairs = sorted(set(anchor_pairs), key=lambda x: x[0])
        # 按波长分组，将同一波长的谱线名用 / 连接；谱线名按静止系波长升序排列
        # 静止系波长查找表：合并发射线表与吸收线表
        _all_line_rest = {**EMISSION_LINES, **ABSORPTION_LINES}
        _anchor_wl_lines = defaultdict(list)
        for wl, lname in anchor_pairs:
            if lname not in _anchor_wl_lines[wl]:
                _anchor_wl_lines[wl].append(lname)
        hypothesis_str = ", ".join(
            f"{wl:.3f}-{'/'.join(sorted(lnames, key=lambda n: _all_line_rest.get(n, 0)))}"
            for wl, lnames in sorted(_anchor_wl_lines.items())
        )

        # 格式化 Emission / Absorption matches（嵌入寻峰/寻谷的宽高信息）
        emission_formatted = []
        for wl, lname, z in emission_nodes:
            feat = _fmt_feature(wl, peak_info.get(wl))
            # 宽度不匹配检查：narrow 峰匹配 broad 线 或 broad 峰匹配 narrow 线
            # "both" 类（Balmer 系）宽窄皆合理，跳过校验
            width_warn = ""
            pinfo = peak_info.get(wl)
            if pinfo is not None:
                pwc = pinfo.get('width_class')
                lwc = EMISSION_LINE_WIDTHS.get(lname)
                if (pwc is not None and lwc is not None
                        and lwc != 'both'
                        and pwc != 'intermediate'
                        and pwc != lwc):
                    width_warn = " ⚠ width mismatch"
            emission_formatted.append(f"{wl:.3f} Å{feat} → {lname} (z={z}){width_warn}")

        absorption_formatted = []
        for wl, lname, z in absorption_nodes:
            feat = _fmt_feature(wl, trough_info.get(wl))
            absorption_formatted.append(f"{wl:.3f} Å{feat} → {lname} (z={z})")

        # N_emission / N_absorption: min(独立峰/谷波长数, 独立线名数)
        # 同时处理「一峰配多线」(峰少线多→取峰数) 和「一线配多峰」(线少峰多→取线数)
        # 两种情况都会虚高朴素计数，min 保证有效独立约束数不被高估。
        n_em = min(
            len({wl    for wl, _, _    in emission_nodes}),   # 独立峰值波长数
            len({lname for _, lname, _ in emission_nodes}),   # 独立发射线名数
        )
        n_ab = min(
            len({wl    for wl, _, _    in absorption_nodes}),  # 独立谷值波长数
            len({lname for _, lname, _ in absorption_nodes}),  # 独立吸收线名数
        )

        # Redshift warning（用 z_max 比较）
        low_z_parts = []
        if z_max < min_qso_redshift:
            low_z_parts.append("QSO")
        if z_max < min_galaxy_redshift:
            low_z_parts.append("Galaxy")
        redshift_warning = f"z too low for {' and '.join(low_z_parts)}" if low_z_parts else None

        # ── Missing Emission / Absorption Lines ──────────────────────
        # qso / elg 模式：报缺失发射线（QSO/ELG 诊断）
        # lrg_bgs 模式：报缺失吸收线（LRG/BGS 主要诊断依据为吸收线）
        # 逻辑相同：用 z_min / z_max 计算理论观测波长，判断是否落在观测范围内但未被匹配
        if mode == 'lrg_bgs':
            matched_ab_names = {lname for _, lname, _ in absorption_nodes}
            missing_emission = []   # lrg_bgs 模式不报缺失发射线
            missing_absorption = []
            if obs_wl_min is not None:
                for aname, arest in ABSORPTION_LINES.items():
                    if aname in matched_ab_names:
                        continue
                    wl_at_zmin = arest * (1.0 + z_min)
                    wl_at_zmax = arest * (1.0 + z_max)
                    in_range_zmin = obs_wl_min <= wl_at_zmin <= obs_wl_max
                    in_range_zmax = obs_wl_min <= wl_at_zmax <= obs_wl_max
                    if in_range_zmin and in_range_zmax:
                        missing_absorption.append(
                            f"{aname} ({arest} Å rest) → "
                            f"{wl_at_zmin:.3f}–{wl_at_zmax:.3f} Å obs [in range, not matched]"
                        )
                    elif in_range_zmin or in_range_zmax:
                        wl_in = wl_at_zmin if in_range_zmin else wl_at_zmax
                        missing_absorption.append(
                            f"{aname} ({arest} Å rest) → "
                            f"~{wl_in:.3f} Å obs [possibly in range]"
                        )
        else:
            # qso / elg 模式：报缺失发射线
            matched_em_names = {lname for _, lname, _ in emission_nodes}
            missing_emission = []
            missing_absorption = []   # qso / elg 模式不报缺失吸收线
            if obs_wl_min is not None:
                for ename, erest in active_emission_lines.items():
                    if ename in matched_em_names:
                        continue
                    wl_at_zmin = erest * (1.0 + z_min)
                    wl_at_zmax = erest * (1.0 + z_max)
                    in_range_zmin = obs_wl_min <= wl_at_zmin <= obs_wl_max
                    in_range_zmax = obs_wl_min <= wl_at_zmax <= obs_wl_max
                    if in_range_zmin and in_range_zmax:
                        missing_emission.append(
                            f"{ename} ({erest} Å rest) → "
                            f"{wl_at_zmin:.3f}–{wl_at_zmax:.3f} Å obs [in range, not matched]"
                        )
                    elif in_range_zmin or in_range_zmax:
                        wl_in = wl_at_zmin if in_range_zmin else wl_at_zmax
                        missing_emission.append(
                            f"{ename} ({erest} Å rest) → "
                            f"~{wl_in:.3f} Å obs [possibly in range]"
                        )

        # ── Observable Lines (all lines within obs range with matching status) ──
        # QSO/ELG: all emission lines; LRG/BGS: all absorption lines
        # 供 LLM 在 Step F-a / F-b 中结构化核验关键诊断谱线，避免遗漏
        observable_emission = []
        observable_absorption = []

        if obs_wl_min is not None:
            if mode != 'lrg_bgs':
                # QSO / ELG：列出所有落在观测范围内的发射线及其匹配状态
                matched_em_map = defaultdict(list)
                for wl, ln, _ in emission_nodes:
                    matched_em_map[ln].append(wl)
                for lname, lrest in active_emission_lines.items():
                    wl_zmin = lrest * (1.0 + z_min)
                    wl_zmax = lrest * (1.0 + z_max)
                    in_range_zmin = obs_wl_min <= wl_zmin <= obs_wl_max
                    in_range_zmax = obs_wl_min <= wl_zmax <= obs_wl_max
                    if not (in_range_zmin or in_range_zmax):
                        continue
                    if in_range_zmin and in_range_zmax:
                        obs_range = f"{wl_zmin:.3f}–{wl_zmax:.3f}"
                    else:
                        wl_in = wl_zmin if in_range_zmin else wl_zmax
                        obs_range = f"~{wl_in:.3f}"
                    if lname in matched_em_map:
                        wl_str = ", ".join(f"{w:.3f}" for w in sorted(matched_em_map[lname]))
                        status = f"matched at {wl_str} Å"
                    else:
                        status = "NOT matched"
                    observable_emission.append(
                        f"{lname} ({lrest:.1f} Å rest) → {obs_range} Å obs [{status}]"
                    )
            else:
                # LRG/BGS：列出所有落在观测范围内的吸收线及其匹配状态
                matched_ab_map = defaultdict(list)
                for wl, ln, _ in absorption_nodes:
                    matched_ab_map[ln].append(wl)
                for aname, arest in ABSORPTION_LINES.items():
                    wl_zmin = arest * (1.0 + z_min)
                    wl_zmax = arest * (1.0 + z_max)
                    in_range_zmin = obs_wl_min <= wl_zmin <= obs_wl_max
                    in_range_zmax = obs_wl_min <= wl_zmax <= obs_wl_max
                    if not (in_range_zmin or in_range_zmax):
                        continue
                    if in_range_zmin and in_range_zmax:
                        obs_range = f"{wl_zmin:.3f}–{wl_zmax:.3f}"
                    else:
                        wl_in = wl_zmin if in_range_zmin else wl_zmax
                        obs_range = f"~{wl_in:.3f}"
                    if aname in matched_ab_map:
                        wl_str = ", ".join(f"{w:.3f}" for w in sorted(matched_ab_map[aname]))
                        status = f"matched at {wl_str} Å"
                    else:
                        status = "NOT matched"
                    observable_absorption.append(
                        f"{aname} ({arest:.1f} Å rest) → {obs_range} Å obs [{status}]"
                    )

        # ── D_n(4000) 4000Å Break 指标 ────────────────────────────
        # 分别用 z_max 和 z_min 将观测系光谱转换到静止系计算
        _obs_wl   = state['spectrum']['new_wavelength']
        _obs_flux = state['spectrum']['weighted_flux']
        dn4000_at_zmax = _compute_dn4000(_obs_wl, _obs_flux, z_max)
        dn4000_at_zmin = _compute_dn4000(_obs_wl, _obs_flux, z_min)

        results.append({
            "Hypothesis": hypothesis_str,
            "z_max": z_max,
            "z_min": z_min,
            "z_spread": z_spread,
            "Emission matches": emission_formatted,
            "Absorption matches": absorption_formatted,
            "N_emission": n_em,
            "N_absorption": n_ab,
            "Redshift warning": redshift_warning,
            "Missing emission lines": missing_emission,
            "Missing absorption lines": missing_absorption,
            "Observable emission lines": observable_emission,
            "Observable absorption lines": observable_absorption,
            "Dn4000": {
                "z_max_as_true_redshift": dn4000_at_zmax,
                "z_min_as_true_redshift": dn4000_at_zmin,
            },
        })

    # 按 N_emission + N_absorption 降序排列
    results.sort(key=lambda r: r['N_emission'] + r['N_absorption'], reverse=True)


    # 过滤掉有效独立约束数 < 2 的候选
    # N_emission / N_absorption 已经是 min(独立波长数, 独立线名数)，
    # 因此此处过滤同时排除「单峰多线冲突」和「单线多峰」两种无效情况。
    results = [r for r in results if r['N_emission'] + r['N_absorption'] >= 3]

    return results
