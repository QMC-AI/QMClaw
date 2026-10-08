"""
services/common/offline_data_lab.py - 离线数据实验室

模拟 lqms.data_process.dataAnalysisCore.DataLab 的接口，
使离线绘图命令（如 qter.fitData）能够正常工作。
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np


class OfflineDataLab:
    """
    模拟 DataLab 接口的离线数据加载器

    主要功能:
    - 加载 HDF5 数据集
    - 提供 data 属性（numpy 数组）
    - 支持通过实验编号或 dataset_id 加载

    使用方式与在线 DataLab 兼容:
        lab = OfflineDataLab(provider)
        lab.loadDataset(123)  # 通过实验编号
        lab.loadDatasetById("20251005_00001")  # 通过 dataset_id
        print(lab.data)  # numpy array
    """

    def __init__(self, offline_provider: 'OfflineDataProvider'):
        """
        初始化离线数据实验室

        Args:
            offline_provider: OfflineDataProvider 实例
        """
        self._provider = offline_provider
        self._current_dataset: Optional[Any] = None
        self._data: Optional[np.ndarray] = None
        self._dataset_name: str = ""
        self._session_path: List[str] = []

        # 当前过滤条件
        self._current_qubit: Optional[str] = None
        self._current_exp_type: Optional[str] = None
        self._current_date: Optional[str] = None

    @property
    def data(self) -> Optional[np.ndarray]:
        """获取当前加载的数据数组"""
        return self._data

    @property
    def dataset_name(self) -> str:
        """获取当前数据集名称"""
        return self._dataset_name

    @property
    def dataset(self) -> Optional[Any]:
        """获取当前数据集信息"""
        return self._current_dataset

    @property
    def session_path(self) -> List[str]:
        """获取当前会话路径"""
        return self._session_path

    def switch_session(self, path: Union[str, List[str]]):
        """
        切换会话（离线模式下设置过滤条件）

        Args:
            path: 会话路径，如 "LQHL/test/20251005" 或 ["LQHL", "test", "20251005"]
        """
        if isinstance(path, str):
            # 解析字符串路径
            parts = [p for p in path.strip('/').split('/') if p]
        else:
            parts = list(path)

        self._session_path = parts

        # 从路径中提取过滤条件
        if len(parts) >= 3:
            # 假设格式: [user, subpath, date]
            self._current_date = parts[-1]
        elif len(parts) >= 1:
            # 可能是 qubit 名称或其他
            pass

    def loadDataset(self, exp_num: int):
        """
        通过实验编号加载数据集

        Args:
            exp_num: 实验编号（如 123）

        注意: 离线模式下，实验编号对应 datasets.json 中的索引顺序
        """
        datasets = self._provider.get_datasets(
            qubit=self._current_qubit,
            experiment_type=self._current_exp_type,
            date=self._current_date
        )

        if exp_num <= 0 or exp_num > len(datasets):
            raise ValueError(f"Invalid experiment number: {exp_num}, available: 1-{len(datasets)}")

        dataset = datasets[exp_num - 1]  # 实验编号从 1 开始
        self._load_from_dataset(dataset)

    def loadDatasetById(self, dataset_id: str):
        """
        通过 dataset_id 加载数据集

        Args:
            dataset_id: 数据集 ID，如 "20251005_00001"
        """
        dataset = self._provider.get_dataset(dataset_id)
        if not dataset:
            raise ValueError(f"Dataset not found: {dataset_id}")
        self._load_from_dataset(dataset)

    def loadDatasetByName(self, name: str):
        """
        通过数据集名称加载

        Args:
            name: 数据集名称，如 "00001 - q11ld4%c S21.hdf5"
        """
        datasets = self._provider.get_datasets()
        for ds in datasets:
            if ds.name == name:
                self._load_from_dataset(ds)
                return
        raise ValueError(f"Dataset not found: {name}")

    def _load_from_dataset(self, dataset: Any):
        """
        从 DatasetInfo 加载数据

        Args:
            dataset: DatasetInfo 实例
        """
        self._current_dataset = dataset
        self._dataset_name = dataset.name

        # 加载 HDF5 数据
        hdf5_data = self._provider.load_hdf5_data(dataset.file_path)
        if not hdf5_data:
            raise RuntimeError(f"Failed to load data from {dataset.file_path}")

        # 转换为 numpy 数组
        data_dict = hdf5_data.get("data", {})
        if "x" in data_dict and "y" in data_dict:
            x = np.array(data_dict["x"])
            y = np.array(data_dict["y"])
            self._data = np.column_stack([x, y])
        elif "data" in data_dict:
            raw = np.array(data_dict["data"])
            if raw.ndim == 1:
                self._data = raw.reshape(-1, 1)
            else:
                self._data = raw
        elif "values" in data_dict:
            self._data = np.array(data_dict["values"])
        else:
            # 尝试使用第一个可用数据
            for key in ["x", "y", "data"]:
                if key in data_dict:
                    arr = np.array(data_dict[key])
                    if arr.ndim == 1:
                        self._data = arr.reshape(-1, 1)
                    else:
                        self._data = arr
                    break
            else:
                raise RuntimeError(f"No data found in HDF5 file: {dataset.file_path}")

    def set_filter(self, qubit: Optional[str] = None,
                   experiment_type: Optional[str] = None,
                   date: Optional[str] = None):
        """
        设置过滤条件

        Args:
            qubit: Qubit 名称
            experiment_type: 实验类型
            date: 日期
        """
        self._current_qubit = qubit
        self._current_exp_type = experiment_type
        self._current_date = date

    def get_dir_contents(self, path: str = "") -> Tuple[List[str], List[str]]:
        """
        获取目录内容（模拟 DataLab 接口）

        Args:
            path: 目录路径

        Returns:
            (子目录列表, 数据集名称列表)
        """
        # 在离线模式下，返回按日期组织的目录结构
        datasets = self._provider.get_datasets(
            qubit=self._current_qubit,
            experiment_type=self._current_exp_type,
            date=self._current_date
        )

        # 收集所有日期
        dates = set()
        for ds in datasets:
            dates.add(ds.date)

        subdirs = sorted(dates)
        ds_names = [ds.name for ds in datasets]

        return (subdirs, ds_names)

    def find_ds_num(self, name_pattern: str) -> List[int]:
        """
        查找匹配的数据集编号

        Args:
            name_pattern: 名称模式（支持部分匹配）

        Returns:
            匹配的实验编号列表
        """
        datasets = self._provider.get_datasets(
            qubit=self._current_qubit,
            experiment_type=self._current_exp_type,
            date=self._current_date
        )

        results = []
        for i, ds in enumerate(datasets):
            if name_pattern.lower() in ds.name.lower():
                results.append(i + 1)  # 实验编号从 1 开始

        return results

    def get_current_info(self) -> Dict[str, Any]:
        """获取当前数据集信息"""
        if not self._current_dataset:
            return {}

        return {
            "name": self._dataset_name,
            "qubit": self._current_dataset.qubit,
            "experiment_type": self._current_dataset.experiment_type,
            "date": self._current_dataset.date,
            "file_path": self._current_dataset.file_path,
            "data_shape": self._data.shape if self._data is not None else None,
        }


class OfflineQubitUpdater:
    """
    模拟 QubitUpdater 的离线版本

    用于执行 qter.fitData() 等绘图命令
    """

    def __init__(self, data_lab: OfflineDataLab, info: Optional[Any] = None):
        """
        初始化离线 QubitUpdater

        Args:
            data_lab: OfflineDataLab 实例
            info: InfoBase 实例（可选，离线模式下为 None）
        """
        self._data = data_lab
        self._info = info

    def fitData(self, exp_num: int = None, collect: bool = False, do_plot: bool = True):
        """
        拟合数据：对 t1 做 A*exp(-t/T1)+c 最小二乘统计拟合
        """
        import matplotlib.pyplot as plt
        from scipy.optimize import curve_fit

        if exp_num is not None:
            self._data.loadDataset(exp_num)

        data = self._data.data
        if data is None:
            print("Warning: No data loaded")
            return {"success": False, "error": "No data loaded"}

        info = self._data.get_current_info()
        exp_type = info.get("experiment_type", "")

        if data.ndim == 2 and data.shape[1] >= 2:
            x = data[:, 0]
            y = data[:, 1]
        else:
            x = np.arange(len(data.flatten()))
            y = data.flatten()

        # ---- 统计拟合：t1 指数衰减 A*exp(-t/T1)+c ----
        fit_metrics = {}
        fit_params = None
        if exp_type == "t1" and len(x) > 3:
            try:
                def _exp_decay(t, A, tau, c):
                    return A * np.exp(-t / tau) + c
                A0 = float(np.max(y) - np.min(y))
                c0 = float(np.min(y))
                tau0 = float((np.max(x) - np.min(x)) / 3.0)
                popt, _ = curve_fit(_exp_decay, x, y, p0=[A0, tau0, c0], maxfev=20000)
                A_fit, tau_fit, c_fit = [float(v) for v in popt]
                if tau_fit > 0:
                    fit_params = (A_fit, tau_fit, c_fit)
                    if tau_fit > 1000:
                        fit_metrics["T1_ns"] = tau_fit
                        fit_metrics["T1_us"] = tau_fit / 1000.0
                    elif tau_fit > 1:
                        fit_metrics["T1_us"] = tau_fit
                    else:
                        fit_metrics["T1_s"] = tau_fit
                    y_pred = _exp_decay(x, *popt)
                    ss_res = float(np.sum((y - y_pred) ** 2))
                    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
                    if ss_tot > 0:
                        fit_metrics["R_squared"] = 1.0 - ss_res / ss_tot
            except Exception as e:
                print(f"[fitData] exponential fit failed: {e}")

        if do_plot:
            fig, ax = plt.subplots(1, 1, figsize=(10, 6))
            ax.plot(x, y, 'b.-', markersize=3, label='Data')
            if fit_params is not None:
                A_fit, tau_fit, c_fit = fit_params
                x_fit = np.linspace(float(np.min(x)), float(np.max(x)), 300)
                y_fit = A_fit * np.exp(-x_fit / tau_fit) + c_fit
                t1_us = fit_metrics.get("T1_us", tau_fit)
                ax.plot(x_fit, y_fit, 'r--', linewidth=2, label=f'Fit: T1 = {t1_us:.2f} us')
                ax.legend(fontsize=10)

            if exp_type in ["s21", "spectroscopy", "s21_dis"]:
                ax.set_xlabel('Frequency (Hz)')
                ax.set_ylabel('S21 (dB)')
            elif exp_type in ["t1"]:
                ax.set_xlabel('Delay (ns)')
                ax.set_ylabel('Amplitude')
            elif exp_type in ["ramsey", "piamp"]:
                ax.set_xlabel('Time (s)')
                ax.set_ylabel('Amplitude')
            elif exp_type in ["iqraw"]:
                ax.set_xlabel('I')
                ax.set_ylabel('Q')

            ax.set_title(f"{info.get('qubit', 'Unknown')}: {exp_type} ({info.get('date', '')})")
            ax.grid(True, alpha=0.3)
            plt.tight_layout()

        result = {"success": True, "data_shape": data.shape}
        if fit_metrics:
            result["metrics"] = fit_metrics
        return result

    def get_metrics(self) -> Dict[str, float]:
        """
        从当前数据提取指标

        Returns:
            指标字典
        """
        data = self._data.data
        if data is None:
            return {}

        metrics = {}
        if data.ndim == 2 and data.shape[1] >= 2:
            y = data[:, 1]
            metrics["max"] = float(np.max(y))
            metrics["min"] = float(np.min(y))
            metrics["mean"] = float(np.mean(y))
            metrics["std"] = float(np.std(y))

        return metrics
