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


def _log(msg: str):
    """安全日志输出"""
    print(f"[OfflineDataLab] {msg}")


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
    支持与在线模式相同的 fitData 接口。
    """

    # 实验类型到绘图函数的映射
    FIT_FUNCTIONS = {}

    def __init__(self, data_lab: OfflineDataLab, info: Optional[Any] = None):
        """
        初始化离线 QubitUpdater

        Args:
            data_lab: OfflineDataLab 实例
            info: InfoBase 实例（可选，离线模式下为 None）
        """
        self._data = data_lab
        self._info = info

    def fitData(
        self,
        datasets=None,
        fit_func=None,
        qname=None,
        update=False,
        collect=False,
        config_name=None,
        des=None,
        **kwargs
    ):
        """
        拟合数据（模拟 qter.fitData）

        参数与在线 QubitUpdater.fitData 兼容:
            datasets: 实验编号（可选，默认使用当前加载的数据集）
            fit_func: 拟合函数（可选，自动检测）
            qname: qubit 名称（可选）
            update: 是否更新 qubit 参数（离线模式下忽略）
            collect: 是否收集结果并返回
            config_name: 配置名称（可选）
            des: 描述（可选）

        Returns:
            拟合结果（collect=True 时返回）
        """
        import matplotlib.pyplot as plt
        import numpy as np

        # 加载数据集（如果指定了编号）
        if datasets is not None:
            self._data.loadDataset(datasets)

        data = self._data.data
        if data is None:
            print("Warning: No data loaded")
            return None if collect else None

        # 获取数据集信息
        info = self._data.get_current_info()
        exp_type = info.get("experiment_type", "").lower()
        dataset_name = info.get("name", "")

        # 根据实验类型选择绘图/分析方式
        do_plot = kwargs.get("do_plot", True)
        if do_plot:
            plt.close('all')  # 确保干净的 matplotlib 状态

        # IQraw 实验类型专用分析
        if exp_type in ["iqraw", "iq raw"] or 'iqraw' in dataset_name.lower():
            _log(f"Fitting Function: IQ_raw")
            result = self._fit_iqraw(data, info, do_plot=do_plot, collect=collect)
            if collect:
                return result
            return None

        # 其他实验类型：检查是否有对应的拟合函数
        if fit_func is None:
            fit_func = self._auto_detect_fit_func(exp_type, dataset_name)

        if fit_func is None:
            # 没有找到匹配的拟合函数，使用默认绘图
            _log(f"No fit function found for exp_type={exp_type}, using default plot")
            if do_plot:
                self._default_plot(data, info, exp_type)
            return {"success": True, "data_shape": data.shape} if collect else None

        fit_func_name = fit_func.__name__
        _log(f"Fitting Function: {fit_func_name}")

        # 准备数据（转换为 DataLab 兼容格式）
        # 创建模拟的 DataLab 对象供拟合函数使用
        class MockDataLab:
            """模拟 DataLab 用于离线拟合"""
            def __init__(self, data_array, dataset_name):
                self.data = data_array
                self.dataset_name = dataset_name
                self.inds = np.arange(len(data_array))
                self.deps = np.arange(len(data_array))

        mock_data = MockDataLab(data, dataset_name)

        # 调用拟合函数
        try:
            result = fit_func(data=mock_data, info=self._info, collect=True, **kwargs)

            # 绘制结果
            if do_plot:
                self._plot_fit_result(result, mock_data, info, fit_func_name)

            if collect:
                return result
            return None

        except Exception as e:
            _log(f"Fit function error: {e}")
            # 回退到默认绘图
            if do_plot:
                self._default_plot(data, info, exp_type)
            return None if not collect else {"error": str(e)}

    def _fit_iqraw(self, data: np.ndarray, info: Dict[str, Any], do_plot: bool = True, collect: bool = False):
        """
        IQ Raw 数据分析

        对 IQ 数据进行双高斯拟合，提取 SNR、分离度等指标。

        Args:
            data: IQ 数据，形状为 (N, 5)，包含 I, Q, 权重等
            info: 数据集信息
            do_plot: 是否绘图
            collect: 是否收集结果

        Returns:
            拟合结果字典（collect=True 时返回）
        """
        import matplotlib.pyplot as plt
        from scipy import stats
        from sklearn.mixture import GaussianMixture

        n = len(data)
        if data.shape[1] >= 2:
            i_data = data[:, 0]
            q_data = data[:, 1]
        else:
            i_data = data.flatten()
            q_data = None

        # 计算幅度
        if q_data is not None:
            amplitude = np.sqrt(i_data**2 + q_data**2)
        else:
            amplitude = i_data

        # 使用双高斯混合模型进行拟合
        try:
            # 准备数据：使用幅度或 I 通道
            X = amplitude.reshape(-1, 1)

            # 双高斯混合模型
            gmm = GaussianMixture(n_components=2, random_state=42, max_iter=200)
            gmm.fit(X)

            # 获取参数
            means = gmm.means_.flatten()
            stds = np.sqrt(gmm.covariances_.flatten())
            weights = gmm.weights_

            # 排序使 mean[0] < mean[1]
            if means[0] > means[1]:
                means = means[::-1]
                stds = stds[::-1]
                weights = weights[::-1]

            # 计算分离度和 SNR
            separation = abs(means[1] - means[0])
            snr = separation / np.sqrt(stds[0]**2 + stds[1]**2)

            # 计算误判概率
            from scipy.stats import norm
            boundary = (means[0] * stds[1]**2 + means[1] * stds[0]**2) / (stds[0]**2 + stds[1]**2)
            p0_error = norm.cdf(boundary, means[0], stds[0])
            p1_error = 1 - norm.cdf(boundary, means[1], stds[1])

            result = {
                "separation": float(separation),
                "SNR": float(snr),
                "sigma0": float(stds[0]),
                "sigma1": float(stds[1]),
                "mean0": float(means[0]),
                "mean1": float(means[1]),
                "stateError0": float(p0_error),
                "stateError1": float(p1_error),
                "visibilityMax": float((means[1] - means[0]) / (means[1] + means[0]) if (means[1] + means[0]) > 0 else 0),
            }

            _log(f"IQraw fit: SNR={snr:.2f}, separation={separation:.2f}")

            # 绘图
            if do_plot:
                fig, axes = plt.subplots(1, 2, figsize=(14, 5))

                # 左图：直方图 + 拟合曲线
                ax1 = axes[0]
                counts, bins, _ = ax1.hist(amplitude, bins=50, density=True, alpha=0.6, color='blue', label='Data')

                # 绘制高斯拟合曲线
                x_range = np.linspace(amplitude.min(), amplitude.max(), 200)
                for k in range(2):
                    y = weights[k] * norm.pdf(x_range, means[k], stds[k])
                    ax1.plot(x_range, y, 'r-', linewidth=2, label=f'State {k}')

                ax1.axvline(x=boundary, color='green', linestyle='--', label='Boundary')
                ax1.set_xlabel('Amplitude')
                ax1.set_ylabel('Probability Density')
                ax1.set_title(f"IQ Raw Analysis: {info.get('qubit', 'Unknown')}")
                ax1.legend()
                ax1.grid(True, alpha=0.3)

                # 添加结果文本
                textstr = '\n'.join([
                    f"SNR: {snr:.2f}",
                    f"Separation: {separation:.4f}",
                    f"σ₀: {stds[0]:.4f}",
                    f"σ₁: {stds[1]:.4f}",
                ])
                ax1.text(0.98, 0.98, textstr, transform=ax1.transAxes, fontsize=10,
                        verticalalignment='top', horizontalalignment='right',
                        bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

                # 右图：IQ 散点图（如果有 Q 数据）
                ax2 = axes[1]
                if q_data is not None:
                    ax2.scatter(i_data[::5], q_data[::5], alpha=0.3, s=1, c='blue')
                    ax2.set_xlabel('I')
                    ax2.set_ylabel('Q')
                else:
                    ax2.hist(amplitude, bins=50, alpha=0.6, color='blue')
                    ax2.set_xlabel('Amplitude')
                    ax2.set_ylabel('Count')
                ax2.set_title(f"{info.get('date', '')}: {info.get('experiment_type', '')}")
                ax2.grid(True, alpha=0.3)

                plt.tight_layout()

            if collect:
                return result
            return None

        except Exception as e:
            _log(f"IQraw fit error: {e}")
            # 回退到默认绘图
            if do_plot:
                plt.figure(figsize=(10, 6))
                plt.hist(amplitude, bins=50, density=True, alpha=0.6, color='blue')
                plt.xlabel('Amplitude')
                plt.ylabel('Probability Density')
                plt.title(f"IQ Raw: {info.get('qubit', 'Unknown')} ({info.get('date', '')})")
                plt.grid(True, alpha=0.3)
                plt.tight_layout()

            if collect:
                return {"error": str(e), "data_shape": data.shape}
            return None

    def _auto_detect_fit_func(self, exp_type: str, dataset_name: str):
        """根据实验类型或数据集名称自动检测拟合函数"""
        # 先尝试从 lqms 加载
        if not OfflineQubitUpdater.FIT_FUNCTIONS:
            self._init_fit_functions()

        # 检查实验类型
        if exp_type in self.FIT_FUNCTIONS:
            return self.FIT_FUNCTIONS[exp_type]

        # 检查数据集名称（不区分大小写）
        name_lower = dataset_name.lower()
        for pattern, func in self.FIT_FUNCTIONS.items():
            if pattern.lower() in name_lower:
                return func

        return None

    def _init_fit_functions(self):
        """初始化拟合函数映射（从 lqms 加载）"""
        if OfflineQubitUpdater.FIT_FUNCTIONS:
            return  # 已初始化

        try:
            from lqms.data_process.analysis_util import readout_analysis as ra
            # IQraw 相关拟合函数
            OfflineQubitUpdater.FIT_FUNCTIONS['iqraw'] = ra.IQ_raw
            OfflineQubitUpdater.FIT_FUNCTIONS['iq raw'] = ra.IQ_raw
            OfflineQubitUpdater.FIT_FUNCTIONS['IQraw'] = ra.IQ_raw
            OfflineQubitUpdater.FIT_FUNCTIONS['IQ raw'] = ra.IQ_raw
            _log("Loaded IQ_raw fit function from lqms")
        except ImportError as e:
            _log(f"Warning: Could not load lqms fit functions: {e}")

    def _default_plot(self, data, info, exp_type):
        """默认绘图函数"""
        import matplotlib.pyplot as plt
        import numpy as np

        fig, ax = plt.subplots(1, 1, figsize=(10, 6))

        if data.ndim == 2 and data.shape[1] >= 2:
            x = data[:, 0]
            y = data[:, 1]
        else:
            x = np.arange(len(data.flatten()))
            y = data.flatten()

        ax.plot(x, y, 'b.-', markersize=3)

        # 根据实验类型设置标签
        if exp_type in ["s21", "spectroscopy", "s21_dis"]:
            ax.set_xlabel('Frequency (Hz)')
            ax.set_ylabel('S21 (dB)')
        elif exp_type in ["t1"]:
            ax.set_xlabel('Delay (s)')
            ax.set_ylabel('Amplitude')
        elif exp_type in ["ramsey", "piamp"]:
            ax.set_xlabel('Time (s)')
            ax.set_ylabel('Amplitude')
        elif exp_type in ["iqraw", "iq raw"]:
            ax.set_xlabel('I')
            ax.set_ylabel('Q')

        ax.set_title(f"{info.get('qubit', 'Unknown')}: {exp_type} ({info.get('date', '')})")
        ax.grid(True, alpha=0.3)
        plt.tight_layout()

    def _plot_fit_result(self, result, data, info, fit_func_name):
        """绘制拟合结果"""
        import matplotlib.pyplot as plt
        import numpy as np

        fig, ax = plt.subplots(1, 1, figsize=(10, 6))

        if data.data.ndim == 2 and data.data.shape[1] >= 2:
            x = data.data[:, 0]
            y = data.data[:, 1]
        else:
            x = np.arange(len(data.data.flatten()))
            y = data.data.flatten()

        ax.plot(x, y, 'b.-', markersize=3, alpha=0.6, label='Data')

        # 根据拟合函数类型添加特定可视化
        if 'IQ' in fit_func_name and hasattr(result, '__iter__') and not isinstance(result, str):
            # IQraw 拟合结果可视化
            try:
                # result 可能是 (params, fit_data) 或直接是 params
                if isinstance(result, tuple) and len(result) >= 2:
                    params, fit_data = result[0], result[1]
                else:
                    params = result if isinstance(result, dict) else {}

                # 尝试绘制高斯拟合曲线
                if isinstance(params, dict) and 'sigma0' in params and 'sigma1' in params:
                    sigma0 = params.get('sigma0', 1)
                    sigma1 = params.get('sigma1', 1)
                    separation = params.get('separation', 0)

                    # 绘制状态分布
                    if x.ndim == 1 and len(x) > 100:
                        # 假设 x 是 I 通道数据
                        i_data = x
                        # 简化可视化：直方图
                        ax.hist(i_data, bins=50, alpha=0.5, density=True, label='I distribution')
                        ax.set_xlabel('I')
                        ax.set_ylabel('Probability')
            except Exception as e:
                _log(f"IQraw plot error: {e}")

        exp_type = info.get("experiment_type", "").lower()
        if exp_type in ["iqraw", "iq raw"]:
            ax.set_xlabel('I')
            ax.set_ylabel('Q')

        ax.set_title(f"{info.get('qubit', 'Unknown')}: {fit_func_name} ({info.get('date', '')})")
        ax.grid(True, alpha=0.3)
        ax.legend()
        plt.tight_layout()

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
