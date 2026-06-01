#!/usr/bin/env python3
"""
Redrock RRDETAILS.H5 解析器
用于从rrdetails.h5文件中提取所有拟合zfit信息

作者: CodeArts Assistant
日期: 2024-05-20
"""

import h5py
import numpy as np
from typing import Dict, List, Optional, Union
from dataclasses import dataclass
import json


@dataclass
class ZFitResult:
    """单个拟合结果的数据类"""
    targetid: int
    z: float
    zerr: float
    zwarn: int
    chi2: float
    coeff: np.ndarray
    legcoeff: np.ndarray
    fitmethod: str
    npixels: int
    spectype: str
    subtype: str
    ncoeff: int
    znum: int  # 拟合排名（0=最佳）
    deltachi2: float
    pca_coeff: np.ndarray
    pca_spectype: str
    pca_subtype: str
    zz: np.ndarray  # 红移网格
    zzchi2: np.ndarray  # 红移网格上的卡方值
    
    def to_dict(self) -> Dict:
        """转换为字典格式"""
        return {
            'targetid': self.targetid,
            'z': self.z,
            'zerr': self.zerr,
            'zwarn': self.zwarn,
            'chi2': self.chi2,
            'coeff': self.coeff.tolist() if isinstance(self.coeff, np.ndarray) else self.coeff,
            'legcoeff': self.legcoeff.tolist() if isinstance(self.legcoeff, np.ndarray) else self.legcoeff,
            'fitmethod': self.fitmethod,
            'npixels': self.npixels,
            'spectype': self.spectype,
            'subtype': self.subtype,
            'ncoeff': self.ncoeff,
            'znum': self.znum,
            'deltachi2': self.deltachi2,
            'pca_coeff': self.pca_coeff.tolist() if isinstance(self.pca_coeff, np.ndarray) else self.pca_coeff,
            'pca_spectype': self.pca_spectype,
            'pca_subtype': self.pca_subtype,
            'zz': self.zz.tolist() if isinstance(self.zz, np.ndarray) else self.zz,
            'zzchi2': self.zzchi2.tolist() if isinstance(self.zzchi2, np.ndarray) else self.zzchi2,
        }


@dataclass
class TargetResult:
    """单个目标的所有拟合结果"""
    targetid: int
    zfit_results: List[ZFitResult]
    best_fit: ZFitResult
    
    def to_dict(self) -> Dict:
        """转换为字典格式"""
        return {
            'targetid': self.targetid,
            'best_fit': self.best_fit.to_dict(),
            'all_fits': [fit.to_dict() for fit in self.zfit_results],
            'nfits': len(self.zfit_results)
        }


class RRDetailsParser:
    """RRDETAILS.H5文件解析器"""
    
    def __init__(self, filepath: str):
        """
        初始化解析器
        
        Parameters:
        -----------
        filepath : str
            rrdetails.h5文件路径
        """
        self.filepath = filepath
        self._file = None
        self._targetids = None
        self._results_cache = None
    
    def __enter__(self):
        """上下文管理器入口"""
        self._file = h5py.File(self.filepath, 'r')
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        if self._file is not None:
            self._file.close()
    
    def open(self):
        """手动打开文件"""
        if self._file is None:
            self._file = h5py.File(self.filepath, 'r')
        return self
    
    def close(self):
        """手动关闭文件"""
        if self._file is not None:
            self._file.close()
            self._file = None
    
    @property
    def file(self) -> h5py.File:
        """获取HDF5文件句柄"""
        if self._file is None:
            raise RuntimeError("文件未打开，请先调用open()或使用with语句")
        return self._file
    
    def get_targetids(self) -> np.ndarray:
        """获取所有目标ID"""
        if self._targetids is None:
            self._targetids = self.file['targetids'][()]
        return self._targetids
    
    def get_n_targets(self) -> int:
        """获取目标数量"""
        return len(self.get_targetids())
    
    def parse_zfit(self, targetid: int) -> Optional[TargetResult]:
        """
        解析单个目标的所有拟合结果
        
        Parameters:
        -----------
        targetid : int
            目标ID
            
        Returns:
        --------
        TargetResult or None
            目标的拟合结果，如果目标不存在则返回None
        """
        # 构造数据集路径
        zfit_path = f'zfit/{targetid}/zfit'
        
        if zfit_path not in self.file:
            return None
        
        # 读取拟合数据
        zfit_data = self.file[zfit_path][()]
        
        # 解析每个拟合结果
        zfit_results = []
        for i in range(len(zfit_data)):
            row = zfit_data[i]
            
            result = ZFitResult(
                targetid=int(row['targetid']),
                z=float(row['z']),
                zerr=float(row['zerr']),
                zwarn=int(row['zwarn']),
                chi2=float(row['chi2']),
                coeff=row['coeff'].copy(),
                legcoeff=row['legcoeff'].copy(),
                fitmethod=row['fitmethod'].decode('utf-8') if isinstance(row['fitmethod'], bytes) else row['fitmethod'],
                npixels=int(row['npixels']),
                spectype=row['spectype'].decode('utf-8') if isinstance(row['spectype'], bytes) else row['spectype'],
                subtype=row['subtype'].decode('utf-8') if isinstance(row['subtype'], bytes) else row['subtype'],
                ncoeff=int(row['ncoeff']),
                znum=int(row['znum']),
                deltachi2=float(row['deltachi2']),
                pca_coeff=row['pca_coeff'].copy(),
                pca_spectype=row['pca_spectype'].decode('utf-8') if isinstance(row['pca_spectype'], bytes) else row['pca_spectype'],
                pca_subtype=row['pca_subtype'].decode('utf-8') if isinstance(row['pca_subtype'], bytes) else row['pca_subtype'],
                zz=row['zz'].copy(),
                zzchi2=row['zzchi2'].copy(),
            )
            zfit_results.append(result)
        
        # 按卡方值排序（最佳拟合在最前）
        zfit_results.sort(key=lambda x: x.chi2)
        
        # 最佳拟合是第一个
        best_fit = zfit_results[0] if zfit_results else None
        
        return TargetResult(
            targetid=targetid,
            zfit_results=zfit_results,
            best_fit=best_fit
        )
    
    def parse_all_zfits(self) -> Dict[int, TargetResult]:
        """
        解析所有目标的拟合结果
        
        Returns:
        --------
        Dict[int, TargetResult]
            目标ID到拟合结果的映射
        """
        if self._results_cache is not None:
            return self._results_cache
        
        targetids = self.get_targetids()
        results = {}
        
        for targetid in targetids:
            result = self.parse_zfit(int(targetid))
            if result is not None:
                results[int(targetid)] = result
        
        self._results_cache = results
        return results
    
    def get_best_fits(self) -> Dict[int, ZFitResult]:
        """
        获取所有目标的最佳拟合结果
        
        Returns:
        --------
        Dict[int, ZFitResult]
            目标ID到最佳拟合结果的映射
        """
        all_results = self.parse_all_zfits()
        return {targetid: result.best_fit for targetid, result in all_results.items()}
    
    def summary(self) -> Dict:
        """
        获取摘要信息
        
        Returns:
        --------
        Dict
            摘要统计信息
        """
        all_results = self.parse_all_zfits()
        
        # 统计光谱类型
        spectype_counts = {}
        for result in all_results.values():
            st = result.best_fit.spectype
            spectype_counts[st] = spectype_counts.get(st, 0) + 1
        
        # 统计警告
        zwarn_counts = {}
        for result in all_results.values():
            zw = result.best_fit.zwarn
            zwarn_counts[zw] = zwarn_counts.get(zw, 0) + 1
        
        # 红移统计
        redshifts = [result.best_fit.z for result in all_results.values()]
        
        return {
            'n_targets': len(all_results),
            'spectype_distribution': spectype_counts,
            'zwarn_distribution': zwarn_counts,
            'redshift_stats': {
                'min': float(np.min(redshifts)),
                'max': float(np.max(redshifts)),
                'mean': float(np.mean(redshifts)),
                'median': float(np.median(redshifts)),
            },
            'filepath': self.filepath,
        }
    
    def to_json(self, output_path: str, indent: int = 2):
        """
        将所有结果导出为JSON文件
        
        Parameters:
        -----------
        output_path : str
            输出JSON文件路径
        indent : int
            JSON缩进空格数
        """
        all_results = self.parse_all_zfits()
        
        output_data = {
            'summary': self.summary(),
            'results': {str(tid): result.to_dict() for tid, result in all_results.items()}
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=indent, ensure_ascii=False)


def parse_rrdetails(filepath: str) -> Dict[int, TargetResult]:
    """
    便捷函数：解析rrdetails.h5文件
    
    Parameters:
    -----------
    filepath : str
        rrdetails.h5文件路径
        
    Returns:
    --------
    Dict[int, TargetResult]
        目标ID到拟合结果的映射
        
    Example:
    --------
    >>> results = parse_rrdetails('rrdetails.h5')
    >>> for targetid, result in results.items():
    ...     print(f"Target {targetid}: z={result.best_fit.z:.4f}, type={result.best_fit.spectype}")
    """
    with RRDetailsParser(filepath) as parser:
        return parser.parse_all_zfits()


def print_summary(filepath: str):
    """
    打印rrdetails.h5文件的摘要信息
    
    Parameters:
    -----------
    filepath : str
        rrdetails.h5文件路径
    """
    with RRDetailsParser(filepath) as parser:
        summary = parser.summary()
        all_results = parser.parse_all_zfits()
        
        print("=" * 70)
        print("RRDETAILS.H5 文件摘要")
        print("=" * 70)
        print(f"\n文件路径: {summary['filepath']}")
        print(f"目标总数: {summary['n_targets']}")
        
        print("\n光谱类型分布:")
        for spectype, count in summary['spectype_distribution'].items():
            print(f"  {spectype}: {count}")
        
        print("\n警告标志分布:")
        for zwarn, count in summary['zwarn_distribution'].items():
            print(f"  ZWARN={zwarn}: {count}")
        
        print("\n红移统计:")
        stats = summary['redshift_stats']
        print(f"  最小值: {stats['min']:.4f}")
        print(f"  最大值: {stats['max']:.4f}")
        print(f"  平均值: {stats['mean']:.4f}")
        print(f"  中位数: {stats['median']:.4f}")
        
        print("\n详细拟合结果:")
        print("-" * 70)
        for targetid, result in all_results.items():
            best = result.best_fit
            print(f"\nTargetID: {targetid}")
            print(f"  最佳拟合: z={best.z:.6f} ± {best.zerr:.6f}")
            print(f"  光谱类型: {best.spectype} ({best.subtype})")
            print(f"  卡方值: {best.chi2:.2f}, Δχ²={best.deltachi2:.2f}")
            print(f"  拟合方法: {best.fitmethod}, 像素数: {best.npixels}")
            print(f"  总拟合次数: {len(result.zfit_results)}")
            
            # 显示前3个拟合结果
            if len(result.zfit_results) > 1:
                print(f"  前3个候选拟合:")
                for i, fit in enumerate(result.zfit_results[:3]):
                    print(f"    [{i}] z={fit.z:.6f}, χ²={fit.chi2:.2f}, type={fit.spectype}/{fit.subtype}")


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python parse_rrdetails.py <rrdetails.h5文件路径> [输出JSON路径]")
        sys.exit(1)
    
    filepath = sys.argv[1]
    
    # 打印摘要
    print_summary(filepath)
    
    # 如果提供了输出路径，导出JSON
    if len(sys.argv) >= 3:
        output_path = sys.argv[2]
        with RRDetailsParser(filepath) as parser:
            parser.to_json(output_path)
        print(f"\n结果已导出到: {output_path}")
