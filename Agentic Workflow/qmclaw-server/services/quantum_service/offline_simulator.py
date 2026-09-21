"""
services/quantum_service/offline_simulator.py - 离线实验模拟器

在离线模式下，基于历史数据模拟实验执行：
- 解析实验代码
- 查找匹配的离线数据
- 生成模拟输出
"""

import json
import random
import time
import re
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np


class ExperimentSimulator:
    """
    实验模拟器

    根据实验类型和历史数据生成合理的模拟输出。
    支持的实验类型:
    - spectroscopy: 频率扫描
    - s21: S21 测量
    - iqraw: IQ 原始数据
    - t1: T1 弛豫
    - ramsey: Ramsey 干涉
    - piamp: π脉冲幅度
    - xeb: 交叉熵基准测试
    - allxy: AllXY 表征
    - single_shot: 单次读取
    """

    # 实验类型 -> 模拟策略
    SIMULATION_STRATEGIES = {
        "spectroscopy": "frequency_sweep",
        "s21": "frequency_sweep",
        "iqraw": "iq_clusters",
        "t1": "exponential_decay",
        "ramsey": "damped_oscillation",
        "piamp": "rabi_oscillation",
        "ramsey_df": "damped_oscillation",
        "xeb": "xeb_simulation",
        "allxy": "allxy_simulation",
        "single_shot": "iq_clusters",
        "s21_dis": "frequency_sweep",
        "pulsed_spec": "frequency_sweep",
        "swap": "swap_simulation",
        "drag_calibrate": "drag_simulation",
    }

    # 实验函数名映射
    EXP_FUNC_MAP = {
        "spectroscopy": "spectroscopy",
        "s21": "s21",
        "iqraw": "iqraw",
        "t1": "t1",
        "ramsey": "ramsey_df",
        "ramsey_df": "ramsey_df",
        "piamp": "piamp",
        "xeb": "xeb",
        "allxy": "allxy",
        "single_shot": "single_shot",
        "s21_dis": "s21_dis",
        "pulsed_spec": "pulsed_spec",
        "swap": "swap",
        "drag_calibrate": "drag_calibrate",
    }

    def __init__(self, offline_provider: 'OfflineDataProvider'):
        """
        初始化实验模拟器

        Args:
            offline_provider: OfflineDataProvider 实例
        """
        self._provider = offline_provider
        self._last_simulation_result: Optional[Dict[str, Any]] = None

    def parse_experiment_code(self, code: str) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
        """
        解析实验代码

        Args:
            code: 实验代码，如 "sq.spectroscopy(q1, do_plot=True)"

        Returns:
            (实验类型, qubit名称, 参数字典) 或 (None, None, None)
        """
        # 匹配 sq.<exp_name>(<qubit>, ...)
        match = re.search(r'sq\.(\w+)\s*\(\s*([^,\s)]+)', code)
        if not match:
            return None, None, None

        exp_name = match.group(1)
        qubit_name = match.group(2).strip()

        # 解析参数
        params = {}
        param_match = re.search(r'sq\.\w+\s*\([^)]+\)', code)
        if param_match:
            param_str = param_match.group()
            # 提取 do_plot 等参数
            for param in re.findall(r'(\w+)\s*=\s*(\S+)', param_str):
                key, value = param
                if value.lower() == 'true':
                    params[key] = True
                elif value.lower() == 'false':
                    params[key] = False
                else:
                    try:
                        params[key] = int(value)
                    except ValueError:
                        try:
                            params[key] = float(value)
                        except ValueError:
                            params[key] = value.strip()

        # 标准化实验名称
        exp_type = self.EXP_FUNC_MAP.get(exp_name, exp_name)

        return exp_type, qubit_name, params

    def find_matching_datasets(
        self,
        qubit: Optional[str] = None,
        experiment_type: Optional[str] = None
    ) -> List[Any]:
        """
        查找匹配的离线数据集

        Args:
            qubit: Qubit 名称
            experiment_type: 实验类型

        Returns:
            匹配的数据集列表
        """
        datasets = self._provider.get_datasets(
            qubit=qubit,
            experiment_type=experiment_type
        )
        return datasets

    def simulate(
        self,
        code: str,
        qubit: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        模拟实验执行

        Args:
            code: 实验代码
            qubit: Qubit 名称（从代码解析或显式提供）
            params: 实验参数

        Returns:
            模拟结果字典
        """
        # 解析实验代码
        exp_type, parsed_qubit, parsed_params = self.parse_experiment_code(code)

        if not exp_type:
            return {
                "status": "offline_simulated",
                "stdout": "[OFFLINE SIMULATION] Unknown experiment type",
                "stderr": "",
                "error": None,
                "simulated": True,
                "data": None,
            }

        # 使用解析的 qubit 和参数
        qubit = qubit or parsed_qubit
        params = params or parsed_params or {}

        # 查找匹配的离线数据
        datasets = self.find_matching_datasets(qubit=qubit, experiment_type=exp_type)

        if not datasets:
            # 没有找到匹配数据，生成合成数据
            return self._generate_synthetic_data(exp_type, qubit, params)

        # 使用找到的数据集
        dataset = datasets[0]

        # 根据实验类型生成模拟
        strategy = self.SIMULATION_STRATEGIES.get(exp_type, "generic")
        simulator_method = getattr(self, f"_simulate_{strategy}", self._simulate_generic)

        result = simulator_method(exp_type, qubit, dataset, params)
        result["dataset_id"] = dataset.id
        result["source_data"] = dataset.name

        self._last_simulation_result = result
        return result

    # 历史数据里扫描轴的原生单位 -> SI 的换算系数。
    # 依据是对本仓库离线数据的实测抽样：
    #   t1 0..84000 / ramsey 0..9900 / spinechocpmg 0..29800   -> 纳秒
    #   s21 6.45..6.49 / spectroscopy 2.6..3.6（GHz 量级）      -> GHz
    #   iqraw 0..2999、xeb 0..29（样本序号）、piamp*（无量纲）  -> 无需换算
    # 模拟器的合成数据路径用的就是 SI，若不归一化，真实数据与合成数据
    # 会相差 1e9 量级（例如 T1 会报成 8000 µs 这种荒谬值）。
    X_UNIT_SCALE = {
        "t1": 1e-9,
        "ramsey": 1e-9,
        "ramsey_df": 1e-9,
        "spinechocpmg": 1e-9,
        "s21": 1e9,
        "s21_dis": 1e9,
        "s21power2d": 1e9,
        "s21zpa2d": 1e9,
        "spectroscopy": 1e9,
        "spectroscopyauto": 1e9,
    }

    def _load_data(self, exp_type: str, dataset: Any) -> Optional[Dict[str, Any]]:
        """加载数据集，并把扫描轴从历史数据的原生单位换算到 SI。

        必须在**计算 metrics / 生成 stdout 之前**完成换算 —— 否则 stdout 里
        会留下未换算的原始值（例如 T1 报成 8000），而 metrics 却是换算后的值，
        两者自相矛盾。
        """
        h = self._provider.load_hdf5_data(dataset.file_path)
        if not h:
            return h
        scale = self.X_UNIT_SCALE.get(exp_type)
        if not scale or scale == 1.0:
            return h
        d = h.get("data")
        if isinstance(d, dict) and d.get("x") is not None:
            try:
                arr = np.asarray(d["x"], dtype=float).ravel()
                d["x"] = (arr * scale).tolist()
            except (TypeError, ValueError):
                pass
        return h

    def _simulate_frequency_sweep(
        self,
        exp_type: str,
        qubit: Optional[str],
        dataset: Any,
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """模拟频率扫描实验（如 S21、spectroscopy）"""
        # 加载原始数据
        hdf5_data = self._load_data(exp_type, dataset)
        if not hdf5_data:
            return self._generate_synthetic_data(exp_type, qubit, params)

        data_dict = hdf5_data.get("data", {})

        # 提取 x (频率) 和 y (信号)
        if "x" in data_dict and "y" in data_dict:
            x = np.array(data_dict["x"])
            y = np.array(data_dict["y"])
        elif "data" in data_dict:
            raw = np.array(data_dict["data"])
            if raw.ndim == 2 and raw.shape[1] >= 2:
                x = raw[:, 0]
                y = raw[:, 1]
            else:
                x = np.arange(len(raw))
                y = raw.flatten()
        else:
            return self._generate_synthetic_data(exp_type, qubit, params)

        # 添加少量噪声变化模拟
        noise_scale = random.uniform(0.9, 1.1)
        noise = np.random.normal(0, np.std(y) * 0.02, len(y))
        simulated_y = y * noise_scale + noise

        # 生成图表数据
        plot_data = self._generate_plot_data(x, simulated_y, exp_type, qubit)

        # 生成标准输出
        peak_idx = np.argmax(np.abs(simulated_y))
        peak_freq = x[peak_idx] if len(x) > 0 else 0
        peak_val = simulated_y[peak_idx]

        stdout = f"""[OFFLINE SIMULATION] {exp_type} on {qubit or 'unknown'}
Dataset: {dataset.name}
Data points: {len(x)}
Noise scale: {noise_scale:.3f}
Peak frequency: {peak_freq:.3e} Hz
Peak value: {peak_val:.6f}
{'QMCLAW_ANALYSIS:' + json.dumps({'peak_freq': float(peak_freq), 'peak_value': float(peak_val)})}
"""

        return {
            "status": "offline_simulated",
            "stdout": stdout,
            "stderr": "",
            "error": None,
            "simulated": True,
            "exp_type": exp_type,
            "qubit": qubit,
            "data": plot_data,
            "metrics": {
                "peak_freq": float(peak_freq),
                "peak_value": float(peak_val),
            }
        }

    def _simulate_exponential_decay(
        self,
        exp_type: str,
        qubit: Optional[str],
        dataset: Any,
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """模拟指数衰减实验（如 T1）"""
        # 加载原始数据
        hdf5_data = self._load_data(exp_type, dataset)
        if not hdf5_data:
            return self._generate_synthetic_data(exp_type, qubit, params)

        data_dict = hdf5_data.get("data", {})

        if "x" in data_dict and "y" in data_dict:
            x = np.array(data_dict["x"])
            y = np.array(data_dict["y"])
        elif "data" in data_dict:
            raw = np.array(data_dict["data"])
            if raw.ndim == 2 and raw.shape[1] >= 2:
                x = raw[:, 0]
                y = raw[:, 1]
            else:
                x = np.arange(len(raw))
                y = raw.flatten()
        else:
            return self._generate_synthetic_data(exp_type, qubit, params)

        # 添加噪声
        noise_scale = random.uniform(0.95, 1.05)
        noise = np.random.normal(0, np.std(y) * 0.01, len(y))
        simulated_y = y * noise_scale + noise

        # 拟合 T1 (简化估算)
        try:
            t1_idx = len(y) // 3
            if t1_idx > 0 and t1_idx < len(x):
                t1_estimate = float(x[t1_idx])
            else:
                t1_estimate = float(np.mean(x))
        except:
            t1_estimate = 100e-6  # 默认 100 us

        plot_data = self._generate_plot_data(x, simulated_y, exp_type, qubit)

        stdout = f"""[OFFLINE SIMULATION] {exp_type} on {qubit or 'unknown'}
Dataset: {dataset.name}
Data points: {len(x)}
T1 estimate: {t1_estimate * 1e6:.2f} us
{'QMCLAW_ANALYSIS:' + json.dumps({'t1': t1_estimate})}
"""

        return {
            "status": "offline_simulated",
            "stdout": stdout,
            "stderr": "",
            "error": None,
            "simulated": True,
            "exp_type": exp_type,
            "qubit": qubit,
            "data": plot_data,
            "metrics": {"t1": t1_estimate}
        }

    def _simulate_damped_oscillation(
        self,
        exp_type: str,
        qubit: Optional[str],
        dataset: Any,
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """模拟阻尼振荡实验（如 Ramsey）"""
        hdf5_data = self._load_data(exp_type, dataset)
        if not hdf5_data:
            return self._generate_synthetic_data(exp_type, qubit, params)

        data_dict = hdf5_data.get("data", {})

        if "x" in data_dict and "y" in data_dict:
            x = np.array(data_dict["x"])
            y = np.array(data_dict["y"])
        elif "data" in data_dict:
            raw = np.array(data_dict["data"])
            if raw.ndim == 2 and raw.shape[1] >= 2:
                x = raw[:, 0]
                y = raw[:, 1]
            else:
                x = np.arange(len(raw))
                y = raw.flatten()
        else:
            return self._generate_synthetic_data(exp_type, qubit, params)

        # 添加噪声
        noise = np.random.normal(0, np.std(y) * 0.02, len(y))
        simulated_y = y + noise

        # 估算 T2*
        t2_estimate = float(np.mean(x)) * 2

        plot_data = self._generate_plot_data(x, simulated_y, exp_type, qubit)

        stdout = f"""[OFFLINE SIMULATION] {exp_type} on {qubit or 'unknown'}
Dataset: {dataset.name}
Data points: {len(x)}
T2* estimate: {t2_estimate * 1e6:.2f} us
{'QMCLAW_ANALYSIS:' + json.dumps({'t2': t2_estimate})}
"""

        return {
            "status": "offline_simulated",
            "stdout": stdout,
            "stderr": "",
            "error": None,
            "simulated": True,
            "exp_type": exp_type,
            "qubit": qubit,
            "data": plot_data,
            "metrics": {"t2": t2_estimate}
        }

    def _simulate_iq_clusters(
        self,
        exp_type: str,
        qubit: Optional[str],
        dataset: Any,
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """模拟 IQ 聚类实验（如 iqraw、single_shot）"""
        hdf5_data = self._load_data(exp_type, dataset)
        if not hdf5_data:
            return self._generate_synthetic_data(exp_type, qubit, params)

        data_dict = hdf5_data.get("data", {})

        # 尝试提取 I, Q 数据
        i_data = None
        q_data = None

        if "I" in data_dict and "Q" in data_dict:
            i_data = np.array(data_dict["I"])
            q_data = np.array(data_dict["Q"])
        elif "x" in data_dict and "y" in data_dict:
            i_data = np.array(data_dict["x"])
            q_data = np.array(data_dict["y"])

        if i_data is None or q_data is None:
            return self._generate_synthetic_data(exp_type, qubit, params)

        # 添加噪声
        noise_scale = random.uniform(0.95, 1.05)
        i_sim = i_data * noise_scale + np.random.normal(0, np.std(i_data) * 0.02, len(i_data))
        q_sim = q_data * noise_scale + np.random.normal(0, np.std(q_data) * 0.02, len(q_data))

        # 计算 SNR (简化)
        i_center = np.mean(i_sim)
        q_center = np.mean(q_sim)
        separation = np.sqrt((np.std(i_sim) ** 2 + np.std(q_sim) ** 2))

        plot_data = {
            "type": "scatter",
            "x": i_sim.tolist(),
            "y": q_sim.tolist(),
            "xlabel": "I",
            "ylabel": "Q",
            "title": f"{exp_type} on {qubit or 'unknown'} (Offline Sim)"
        }

        stdout = f"""[OFFLINE SIMULATION] {exp_type} on {qubit or 'unknown'}
Dataset: {dataset.name}
Data points: {len(i_sim)}
I center: {i_center:.6f}
Q center: {q_center:.6f}
Separation: {separation:.6f}
{'QMCLAW_ANALYSIS:' + json.dumps({'separation': float(separation), 'snr': float(separation / np.std(i_sim)) if np.std(i_sim) > 0 else 0})}
"""

        return {
            "status": "offline_simulated",
            "stdout": stdout,
            "stderr": "",
            "error": None,
            "simulated": True,
            "exp_type": exp_type,
            "qubit": qubit,
            "data": plot_data,
            "metrics": {"separation": float(separation)}
        }

    def _simulate_rabi_oscillation(
        self,
        exp_type: str,
        qubit: Optional[str],
        dataset: Any,
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """模拟 Rabi 振荡实验（如 piamp）"""
        hdf5_data = self._load_data(exp_type, dataset)
        if not hdf5_data:
            return self._generate_synthetic_data(exp_type, qubit, params)

        data_dict = hdf5_data.get("data", {})

        if "x" in data_dict and "y" in data_dict:
            x = np.array(data_dict["x"])
            y = np.array(data_dict["y"])
        elif "data" in data_dict:
            raw = np.array(data_dict["data"])
            if raw.ndim == 2 and raw.shape[1] >= 2:
                x = raw[:, 0]
                y = raw[:, 1]
            else:
                x = np.arange(len(raw))
                y = raw.flatten()
        else:
            return self._generate_synthetic_data(exp_type, qubit, params)

        # 添加噪声
        noise = np.random.normal(0, np.std(y) * 0.02, len(y))
        simulated_y = y + noise

        # 估算 π 脉冲幅度
        try:
            pi_amp_idx = len(y) // 4
            if pi_amp_idx > 0 and pi_amp_idx < len(x):
                pi_amp = float(x[pi_amp_idx])
            else:
                pi_amp = float(np.mean(x))
        except:
            pi_amp = 0.5

        plot_data = self._generate_plot_data(x, simulated_y, exp_type, qubit)

        stdout = f"""[OFFLINE SIMULATION] {exp_type} on {qubit or 'unknown'}
Dataset: {dataset.name}
Data points: {len(x)}
Pi amplitude estimate: {pi_amp:.4f}
{'QMCLAW_ANALYSIS:' + json.dumps({'pi_amplitude': pi_amp})}
"""

        return {
            "status": "offline_simulated",
            "stdout": stdout,
            "stderr": "",
            "error": None,
            "simulated": True,
            "exp_type": exp_type,
            "qubit": qubit,
            "data": plot_data,
            "metrics": {"pi_amplitude": pi_amp}
        }

    def _simulate_xeb_simulation(
        self,
        exp_type: str,
        qubit: Optional[str],
        dataset: Any,
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """模拟 XEB 实验"""
        # XEB 通常需要模拟多个门的保真度
        hdf5_data = self._load_data(exp_type, dataset)
        if not hdf5_data:
            return self._generate_synthetic_data(exp_type, qubit, params)

        # 生成模拟保真度
        gate_fidelity = random.uniform(0.985, 0.998)
        error_per_cycle = 1 - gate_fidelity

        stdout = f"""[OFFLINE SIMULATION] {exp_type} on {qubit or 'unknown'}
Dataset: {dataset.name}
Gate fidelity: {gate_fidelity * 100:.2f}%
Error per cycle: {error_per_cycle * 100:.4f}%
{'QMCLAW_ANALYSIS:' + json.dumps({'gate_fidelity': gate_fidelity, 'error_per_cycle': error_per_cycle})}
"""

        return {
            "status": "offline_simulated",
            "stdout": stdout,
            "stderr": "",
            "error": None,
            "simulated": True,
            "exp_type": exp_type,
            "qubit": qubit,
            "data": None,
            "metrics": {"gate_fidelity": gate_fidelity, "error_per_cycle": error_per_cycle}
        }

    def _simulate_allxy_simulation(
        self,
        exp_type: str,
        qubit: Optional[str],
        dataset: Any,
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """模拟 AllXY 实验"""
        # 模拟 21 种组合的平均保真度
        average_fidelity = random.uniform(0.97, 0.995)

        stdout = f"""[OFFLINE SIMULATION] {exp_type} on {qubit or 'unknown'}
Dataset: {dataset.name}
Average fidelity: {average_fidelity * 100:.2f}%
{'QMCLAW_ANALYSIS:' + json.dumps({'average_fidelity': average_fidelity})}
"""

        return {
            "status": "offline_simulated",
            "stdout": stdout,
            "stderr": "",
            "error": None,
            "simulated": True,
            "exp_type": exp_type,
            "qubit": qubit,
            "data": None,
            "metrics": {"average_fidelity": average_fidelity}
        }

    def _simulate_swap_simulation(
        self,
        exp_type: str,
        qubit: Optional[str],
        dataset: Any,
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """模拟 SWAP 实验"""
        swap_fidelity = random.uniform(0.95, 0.99)

        stdout = f"""[OFFLINE SIMULATION] {exp_type} on {qubit or 'unknown'}
Dataset: {dataset.name}
SWAP fidelity: {swap_fidelity * 100:.2f}%
{'QMCLAW_ANALYSIS:' + json.dumps({'swap_fidelity': swap_fidelity})}
"""

        return {
            "status": "offline_simulated",
            "stdout": stdout,
            "stderr": "",
            "error": None,
            "simulated": True,
            "exp_type": exp_type,
            "qubit": qubit,
            "data": None,
            "metrics": {"swap_fidelity": swap_fidelity}
        }

    def _simulate_drag_simulation(
        self,
        exp_type: str,
        qubit: Optional[str],
        dataset: Any,
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """模拟 DRAG 校准实验"""
        optimal_drag = random.uniform(-0.5, 0.5)

        stdout = f"""[OFFLINE SIMULATION] {exp_type} on {qubit or 'unknown'}
Dataset: {dataset.name}
Optimal DRAG coefficient: {optimal_drag:.4f}
{'QMCLAW_ANALYSIS:' + json.dumps({'optimal_drag': optimal_drag})}
"""

        return {
            "status": "offline_simulated",
            "stdout": stdout,
            "stderr": "",
            "error": None,
            "simulated": True,
            "exp_type": exp_type,
            "qubit": qubit,
            "data": None,
            "metrics": {"optimal_drag": optimal_drag}
        }

    def _simulate_generic(
        self,
        exp_type: str,
        qubit: Optional[str],
        dataset: Any,
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """通用模拟方法"""
        hdf5_data = self._load_data(exp_type, dataset)
        if not hdf5_data:
            return self._generate_synthetic_data(exp_type, qubit, params)

        stdout = f"""[OFFLINE SIMULATION] {exp_type} on {qubit or 'unknown'}
Dataset: {dataset.name}
(Generic simulation)
"""

        return {
            "status": "offline_simulated",
            "stdout": stdout,
            "stderr": "",
            "error": None,
            "simulated": True,
            "exp_type": exp_type,
            "qubit": qubit,
            "data": None,
            "metrics": {}
        }

    def _generate_synthetic_data(
        self,
        exp_type: str,
        qubit: Optional[str],
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """生成合成数据（当没有匹配的历史数据时）"""
        n_points = 100

        if exp_type in ["spectroscopy", "s21", "s21_dis", "pulsed_spec"]:
            # 频率扫描
            freq = np.linspace(4e9, 5e9, n_points)
            signal = np.exp(-((freq - 4.5e9) ** 2) / (2 * 100e6 ** 2)) + 0.1 * np.random.randn(n_points)
            x = freq.tolist()
            y = signal.tolist()
        elif exp_type in ["t1"]:
            # T1 衰减
            time_pts = np.linspace(0, 500e-6, n_points)
            signal = np.exp(-time_pts / 100e-6) + 0.1 * np.random.randn(n_points)
            x = time_pts.tolist()
            y = signal.tolist()
        elif exp_type in ["ramsey", "ramsey_df"]:
            # Ramsey 振荡
            time_pts = np.linspace(0, 10e-6, n_points)
            signal = np.cos(2 * np.pi * 5e6 * time_pts) * np.exp(-time_pts / 5e-6) + 0.1 * np.random.randn(n_points)
            x = time_pts.tolist()
            y = signal.tolist()
        elif exp_type in ["iqraw", "single_shot"]:
            # IQ 聚类
            n_samples = 50
            i_data = np.concatenate([
                np.random.randn(n_samples) - 0.5,
                np.random.randn(n_samples) + 0.5
            ])
            q_data = np.concatenate([
                np.random.randn(n_samples),
                np.random.randn(n_samples)
            ])
            x = i_data.tolist()
            y = q_data.tolist()
        else:
            # 默认
            x = list(range(n_points))
            y = (np.sin(np.linspace(0, 4 * np.pi, n_points)) + 0.1 * np.random.randn(n_points)).tolist()

        plot_data = self._generate_plot_data(x, y, exp_type, qubit)

        stdout = f"""[OFFLINE SIMULATION] {exp_type} on {qubit or 'unknown'}
(No matching historical data found - synthetic data generated)
Data points: {n_points}
Experiment: {exp_type}
"""

        return {
            "status": "offline_simulated",
            "stdout": stdout,
            "stderr": "",
            "error": None,
            "simulated": True,
            "synthetic": True,
            "exp_type": exp_type,
            "qubit": qubit,
            "data": plot_data,
            "metrics": {}
        }

    def _generate_plot_data(
        self,
        x: List[float],
        y: List[float],
        exp_type: str,
        qubit: Optional[str]
    ) -> Dict[str, Any]:
        """生成绘图数据"""
        # 根据实验类型确定轴标签
        xlabel = "X"
        ylabel = "Y"

        if exp_type in ["spectroscopy", "s21", "s21_dis", "pulsed_spec"]:
            xlabel = "Frequency (Hz)"
            ylabel = "S21 (dB)"
        elif exp_type in ["t1"]:
            xlabel = "Time (s)"
            ylabel = "Amplitude"
        elif exp_type in ["ramsey", "ramsey_df", "piamp"]:
            xlabel = "Time (s)"
            ylabel = "Amplitude"

        # 必须转成纯 Python list：真实数据路径传进来的是 numpy 数组，
        # 直接返回会导致服务端 JSON 序列化失败（"Object of type ndarray is not
        # JSON serializable"），响应体退化成 500。合成路径本来就已 tolist()。
        try:
            x_out = np.asarray(x, dtype=float).ravel().tolist()
        except (TypeError, ValueError):
            x_out = list(x) if x is not None else []
        try:
            y_out = np.asarray(y, dtype=float).ravel().tolist()
        except (TypeError, ValueError):
            y_out = list(y) if y is not None else []

        return {
            "type": "line",
            "x": x_out,
            "y": y_out,
            "xlabel": xlabel,
            "ylabel": ylabel,
            "title": f"{exp_type} on {qubit or 'unknown'} (Offline Sim)",
            "grid": True
        }

    def get_last_result(self) -> Optional[Dict[str, Any]]:
        """获取上次模拟结果"""
        return self._last_simulation_result
