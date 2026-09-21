"""
services/common/offline_data_provider.py - 离线数据提供器

从 offline_data 目录读取历史测控数据，提供：
- Qubit 列表查询
- 实验类型列表
- 数据集索引和 HDF5 数据读取
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime


@dataclass
class DatasetInfo:
    """数据集信息"""
    id: str
    name: str
    qubit: str
    experiment_type: str
    date: str
    file_path: str
    file_size: int


@dataclass
class QubitInfo:
    """量子比特信息"""
    name: str
    dates: List[str]  # 出现过的日期
    experiments: List[str]  # 相关的实验类型


class OfflineDataProvider:
    """离线数据提供器

    从 offline_data 目录读取历史 HDF5 数据，建立索引。
    """

    # 实验类型名称映射（标准化为小写）
    EXPERIMENT_TYPE_MAP = {
        "s21": "s21",
        "iqraw": "iqraw",
        "pipulse": "pipulse",
        "pipulse df": "pipulse_df",
        "spectroscopy": "spectroscopy",
        "xeb reference": "xeb",
        "s21power2d": "s21power2d",
        "s21zpa2d": "s21zpa2d",
    }

    def __init__(self, data_path: str):
        """初始化离线数据提供器

        Args:
            data_path: 离线数据目录路径，如 "data/offline_data"
        """
        self._data_path = Path(data_path)
        self._qubits: Dict[str, QubitInfo] = {}
        self._datasets: List[DatasetInfo] = []
        self._loaded = False

    @property
    def data_path(self) -> Path:
        """获取数据路径"""
        return self._data_path

    @property
    def is_available(self) -> bool:
        """检查离线数据是否可用"""
        return self._data_path.exists() and len(self._datasets) > 0

    def load(self) -> bool:
        """加载离线数据索引

        优先从预生成的索引文件加载，如果不存在则扫描 HDF5 目录。
        """
        # 优先尝试加载预生成的索引文件
        qubits_file = self._data_path / "qubits.json"
        datasets_file = self._data_path / "datasets.json"
        experiments_file = self._data_path / "experiments.json"

        if qubits_file.exists() and datasets_file.exists():
            try:
                # 加载 qubits
                with open(qubits_file, "r", encoding="utf-8") as f:
                    qubits_data = json.load(f)
                for q in qubits_data:
                    self._qubits[q["name"]] = QubitInfo(
                        name=q["name"],
                        dates=q.get("dates", []),
                        experiments=q.get("experiments", [])
                    )

                # 加载 datasets
                with open(datasets_file, "r", encoding="utf-8") as f:
                    datasets_data = json.load(f)
                for ds in datasets_data:
                    self._datasets.append(DatasetInfo(
                        id=ds["id"],
                        name=ds["name"],
                        qubit=ds["qubit"],
                        experiment_type=ds["experiment_type"],
                        date=ds["date"],
                        file_path=ds.get("file_path", ""),
                        file_size=ds.get("file_size", 0)
                    ))

                self._loaded = True
                print(f"[OfflineDataProvider] Loaded {len(self._datasets)} datasets, {len(self._qubits)} qubits from index files")
                return True
            except Exception as e:
                print(f"[OfflineDataProvider] Failed to load index files: {e}")

        # 如果没有索引文件，扫描 HDF5 目录
        if not self._data_path.exists():
            print(f"[OfflineDataProvider] Data path not found: {self._data_path}")
            return False

        self._qubits = {}
        self._datasets = []

        # 遍历日期目录
        for date_dir in sorted(self._data_path.iterdir()):
            if not date_dir.is_dir() or not date_dir.name.endswith('.dir'):
                continue

            date = date_dir.name.replace('.dir', '')
            self._scan_date_dir(date_dir, date)

        self._loaded = True
        print(f"[OfflineDataProvider] Loaded {len(self._datasets)} datasets, {len(self._qubits)} qubits from HDF5 scan")
        return True

    def _scan_date_dir(self, date_dir: Path, date: str):
        """扫描日期目录"""
        for hdf5_file in sorted(date_dir.glob("*.hdf5")):
            try:
                dataset_info = self._parse_hdf5_filename(hdf5_file, date)
                if dataset_info:
                    self._datasets.append(dataset_info)

                    # 更新 qubit 信息
                    qubit_name = dataset_info.qubit
                    if qubit_name not in self._qubits:
                        self._qubits[qubit_name] = QubitInfo(
                            name=qubit_name,
                            dates=[],
                            experiments=[]
                        )

                    qinfo = self._qubits[qubit_name]
                    if date not in qinfo.dates:
                        qinfo.dates.append(date)
                    if dataset_info.experiment_type not in qinfo.experiments:
                        qinfo.experiments.append(dataset_info.experiment_type)

            except Exception as e:
                print(f"[OfflineDataProvider] Error parsing {hdf5_file}: {e}")

    def _parse_hdf5_filename(self, file_path: Path, date: str) -> Optional[DatasetInfo]:
        """解析 HDF5 文件名

        文件名格式: "00001 - q11ld4%c S21.hdf5"
        返回: DatasetInfo 或 None
        """
        filename = file_path.name
        # 匹配格式: "NNNNN - qubit 实验类型.hdf5"
        match = re.match(r"(\d+)\s*-\s*(\S+)\s+(.+)\.hdf5$", filename)
        if not match:
            return None

        exp_num_str = match.group(1)
        qubit_raw = match.group(2)
        exp_type_raw = match.group(3)

        # 清理 qubit 名称（去掉 %c 等后缀）
        qubit = qubit_raw.replace('%c', '').strip()

        # 标准化实验类型
        exp_type_key = exp_type_raw.lower().strip()
        exp_type = self.EXPERIMENT_TYPE_MAP.get(exp_type_key, exp_type_key)

        return DatasetInfo(
            id=f"{date}_{exp_num_str}",
            name=filename,
            qubit=qubit,
            experiment_type=exp_type,
            date=date,
            file_path=str(file_path),
            file_size=file_path.stat().st_size
        )

    def get_qubits(self) -> List[Dict[str, Any]]:
        """获取所有量子比特列表

        Returns:
            Qubit 列表
        """
        return [
            {
                "name": q.name,
                "dates": sorted(q.dates),
                "experiments": sorted(q.experiments),
            }
            for q in sorted(self._qubits.values(), key=lambda x: x.name)
        ]

    def get_qubit(self, name: str) -> Optional[QubitInfo]:
        """获取指定量子比特信息

        Args:
            name: Qubit 名称

        Returns:
            QubitInfo 或 None
        """
        return self._qubits.get(name)

    def get_experiments(self) -> List[Dict[str, str]]:
        """获取实验类型列表

        Returns:
            实验列表
        """
        # 优先从预生成的 experiments.json 加载
        experiments_file = self._data_path / "experiments.json"
        if experiments_file.exists():
            try:
                with open(experiments_file, "r", encoding="utf-8") as f:
                    exp_list = json.load(f)
                if exp_list:
                    return exp_list
            except Exception as e:
                print(f"[OfflineDataProvider] Failed to load experiments.json: {e}")

        # 回退：从 experiment_configs.json 加载实验配置
        exp_list = []
        config_path = Path(__file__).parent.parent.parent / "config" / "experiment_configs.json"
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                for exp_key, exp_info in config.get("experiments", {}).items():
                    exp_list.append({
                        "name": exp_key,
                        "fullName": f"sq.{exp_key}",
                        "doc": exp_info.get("description", ""),
                    })
            except Exception as e:
                print(f"[OfflineDataProvider] Error loading experiments config: {e}")

        # 如果配置文件不存在，返回默认列表
        if not exp_list:
            exp_list = [
                {"name": "s21", "fullName": "sq.s21", "doc": "Cavity S21 measurement"},
                {"name": "iqraw", "fullName": "sq.iqraw", "doc": "IQ Raw data acquisition"},
                {"name": "spectroscopy", "fullName": "sq.spectroscopy", "doc": "Qubit spectroscopy"},
                {"name": "t1", "fullName": "sq.t1", "doc": "T1 relaxation measurement"},
                {"name": "ramsey", "fullName": "sq.ramsey", "doc": "Ramsey interference"},
                {"name": "pipulse", "fullName": "sq.piamp", "doc": "Pi pulse amplitude calibration"},
                {"name": "xeb", "fullName": "sq.xeb", "doc": "Cross-entropy benchmarking"},
            ]

        return exp_list

    def get_datasets(
        self,
        qubit: Optional[str] = None,
        experiment_type: Optional[str] = None,
        date: Optional[str] = None
    ) -> List[DatasetInfo]:
        """获取数据集列表（可选过滤）

        Args:
            qubit: Qubit 名称过滤
            experiment_type: 实验类型过滤
            date: 日期过滤

        Returns:
            匹配的数据集列表
        """
        result = self._datasets

        if qubit:
            result = [ds for ds in result if ds.qubit == qubit]
        if experiment_type:
            result = [ds for ds in result if ds.experiment_type == experiment_type]
        if date:
            result = [ds for ds in result if ds.date == date]

        return sorted(result, key=lambda x: (x.date, x.name))

    def get_dataset(self, dataset_id: str) -> Optional[DatasetInfo]:
        """获取指定数据集

        Args:
            dataset_id: 数据集 ID (格式: "date_expnum")

        Returns:
            DatasetInfo 或 None
        """
        for ds in self._datasets:
            if ds.id == dataset_id:
                return ds
        # Debug: show available IDs if not found
        available_ids = [ds.id for ds in self._datasets[:5]]
        print(f"[OfflineDataProvider] Dataset not found: {dataset_id}, available (first 5): {available_ids}")
        return None

    def load_hdf5_data(self, file_path: str) -> Optional[Dict[str, Any]]:
        """加载 HDF5 文件数据

        Args:
            file_path: HDF5 文件路径

        Returns:
            包含 'x', 'y' 数据的字典，或 None
        """
        try:
            import h5py
            import numpy as np

            with h5py.File(file_path, 'r') as f:
                data = {}

                # 尝试读取常见的数据集名称
                for key in ['data', 'x', 'y', 'values']:
                    if key in f:
                        data[key] = np.array(f[key]).tolist()

                # 如果没有命名的数据集，尝试获取根目录的数据
                if not data and len(f.keys()) > 0:
                    # 获取第一个数值数据集
                    for key in f.keys():
                        try:
                            arr = np.array(f[key])
                            if arr.ndim == 1 and len(arr) > 0:
                                if 'x' not in data:
                                    data['x'] = arr.tolist()
                                else:
                                    data['y'] = arr.tolist()
                            elif arr.ndim == 2 and arr.shape[1] >= 2:
                                data['x'] = arr[:, 0].tolist()
                                data['y'] = arr[:, 1].tolist()
                                break
                        except:
                            continue

                # 读取元数据
                metadata = {}
                for key in f.attrs:
                    try:
                        metadata[key] = str(f.attrs[key])
                    except:
                        pass

                return {
                    "data": data,
                    "metadata": metadata,
                    "file_size": Path(file_path).stat().st_size if Path(file_path).exists() else 0
                }

        except ImportError:
            print("[OfflineDataProvider] h5py not installed, cannot load HDF5 data")
            return None
        except Exception as e:
            print(f"[OfflineDataProvider] Error loading HDF5 {file_path}: {e}")
            return None

    def get_summary(self) -> Dict[str, Any]:
        """获取数据摘要

        Returns:
            包含统计信息的字典
        """
        # 按日期统计
        dates = {}
        for ds in self._datasets:
            if ds.date not in dates:
                dates[ds.date] = {"datasets": 0, "qubits": set(), "experiments": set()}
            dates[ds.date]["datasets"] += 1
            dates[ds.date]["qubits"].add(ds.qubit)
            dates[ds.date]["experiments"].add(ds.experiment_type)

        # 转换为可序列化格式
        for date in dates:
            dates[date]["qubits"] = list(dates[date]["qubits"])
            dates[date]["experiments"] = list(dates[date]["experiments"])

        return {
            "data_path": str(self._data_path),
            "is_available": self.is_available,
            "total_datasets": len(self._datasets),
            "total_qubits": len(self._qubits),
            "qubit_names": sorted(self._qubits.keys()),
            "by_date": dates,
        }


def generate_offline_index(data_path: str, output_path: Optional[str] = None) -> Dict[str, Any]:
    """生成离线数据索引

    Args:
        data_path: 离线数据目录路径
        output_path: 输出目录路径（默认与 data_path 相同）

    Returns:
        索引摘要
    """
    provider = OfflineDataProvider(data_path)
    success = provider.load()

    if not success:
        return {"success": False, "error": "Failed to load offline data"}

    # 生成输出目录
    # 读取端 OfflineDataProvider.load() 从 <data_path>/qubits.json 读取索引，
    # 所以默认输出目录必须就是 data_path 本身；写成 data_path/../offline
    # 会导致生成的索引永远不会被读到。
    if output_path is None:
        output_path = Path(data_path)
    else:
        output_path = Path(output_path)

    output_path.mkdir(parents=True, exist_ok=True)

    # 保存 qubits.json
    qubits = provider.get_qubits()
    with open(output_path / "qubits.json", "w", encoding="utf-8") as f:
        json.dump(qubits, f, indent=2, ensure_ascii=False)

    # 保存 experiments.json
    experiments = provider.get_experiments()
    with open(output_path / "experiments.json", "w", encoding="utf-8") as f:
        json.dump(experiments, f, indent=2, ensure_ascii=False)

    # 保存 datasets.json
    datasets = [
        {
            "id": ds.id,
            "name": ds.name,
            "qubit": ds.qubit,
            "experiment_type": ds.experiment_type,
            "date": ds.date,
            "file_path": ds.file_path,
            "file_size": ds.file_size,
        }
        for ds in provider.get_datasets()
    ]
    with open(output_path / "datasets.json", "w", encoding="utf-8") as f:
        json.dump(datasets, f, indent=2, ensure_ascii=False)

    summary = provider.get_summary()
    summary["output_path"] = str(output_path)
    summary["success"] = True

    return summary


# CLI 入口
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Offline Data Index Generator")
    parser.add_argument("--data-path", default="data/offline_data", help="Offline data directory")
    parser.add_argument("--output-path", default=None, help="Output directory for index files")
    parser.add_argument("--generate-index", action="store_true", help="Generate index files")

    args = parser.parse_args()

    if args.generate_index:
        print(f"Generating index from {args.data_path}...")
        result = generate_offline_index(args.data_path, args.output_path)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        # 加载并显示摘要
        provider = OfflineDataProvider(args.data_path)
        if provider.load():
            print(json.dumps(provider.get_summary(), indent=2, ensure_ascii=False))
        else:
            print("Failed to load offline data")
