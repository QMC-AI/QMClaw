"""Test offline data provider"""
import re
from pathlib import Path
from dataclasses import dataclass

@dataclass
class DatasetInfo:
    id: str
    name: str
    qubit: str
    experiment_type: str
    date: str
    file_path: str
    file_size: int

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

def parse_hdf5_filename(filename: str) -> dict:
    """Parse HDF5 filename"""
    match = re.match(r"(\d+)\s*-\s*(\S+)\s+(.+)\.hdf5$", filename)
    if not match:
        return None

    exp_num_str = match.group(1)
    qubit_raw = match.group(2)
    exp_type_raw = match.group(3)

    qubit = qubit_raw.replace('%c', '').strip()
    exp_type_key = exp_type_raw.lower().strip()
    exp_type = EXPERIMENT_TYPE_MAP.get(exp_type_key, exp_type_key)

    return {
        "id": f"test_{exp_num_str}",
        "name": filename,
        "qubit": qubit,
        "experiment_type": exp_type,
    }

# Test parsing
test_files = [
    "00001 - q1ld4%c S21.hdf5",
    "00002 - q1ld4%c IQraw.hdf5",
    "00003 - q11ld4%c S21.hdf5",
    "00005 - q11ld4%c PiPulse.hdf5",
    "00087 - q11ld4%c XEB reference.hdf5",
]

print("Testing filename parsing:")
for f in test_files:
    result = parse_hdf5_filename(f)
    print(f"  {f}")
    print(f"    -> {result}")
    print()

# Scan actual files
# 与运行时保持一致：data_path 相对 qmclaw-server 根解析（见 config/fallback_config.json）
data_path = Path(__file__).resolve().parent / "data" / "offline_data"
if not data_path.exists():
    raise SystemExit(
        f"offline data not found: {data_path}\n"
        r"copy deploy\offline_data there first, then run:" "\n"
        "  python -m services.common.offline_data_provider --generate-index"
    )
datasets = []

for date_dir in sorted(data_path.iterdir()):
    if not date_dir.is_dir() or not date_dir.name.endswith('.dir'):
        continue

    date = date_dir.name.replace('.dir', '')
    for hdf5_file in sorted(date_dir.glob("*.hdf5")):
        result = parse_hdf5_filename(hdf5_file.name)
        if result:
            result["date"] = date
            result["file_path"] = str(hdf5_file)
            datasets.append(result)

print(f"\nTotal datasets found: {len(datasets)}")

# Group by qubit
qubits = {}
for ds in datasets:
    q = ds["qubit"]
    if q not in qubits:
        qubits[q] = {"name": q, "dates": set(), "experiments": set()}
    qubits[q]["dates"].add(ds["date"])
    qubits[q]["experiments"].add(ds["experiment_type"])

print(f"\nQubits found: {len(qubits)}")
for name, info in sorted(qubits.items()):
    print(f"  {name}:")
    print(f"    dates: {sorted(info['dates'])}")
    print(f"    experiments: {sorted(info['experiments'])}")
