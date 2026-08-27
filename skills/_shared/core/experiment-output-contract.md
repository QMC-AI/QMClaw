# 实验输出契约 — 共享参考

`_execute_workflow()` 的返回值格式规范。

适配自己的测控系统时，请严格遵循此契约，以确保 `get_data()` / 数据分析 / 绘图等下游功能正常工作。

---

## 返回值结构

```python
return {
    "data": { ... },   # 必选，实验原始数据
    "meta": { ... },   # 可选，扫描轴/shots 等元信息
}
```

---

## `data` 字段规范

| 实验类型 (workflow.name) | data 键名 | 数据类型 | 单位 | 说明 |
|--------------------------|-----------|----------|------|------|
| `S21` | `s21` | `list[complex]` | - | 传输参数 S21（复数） |
| `Rabi` | `population` | `list[float]` | - | 占据率 (0~1) |
| `T1` | `population` | `list[float]` | - | 占据率 (0~1) |
| `Ramsey` | `population` | `list[float]` | - | 占据率 (0~1) |
| `SingleShot` | `iq` | `list[list[complex]]` | - | 单发 IQ 散点 |
| `Spectrum` | `population` | `list[float]` | - | 占据率 (0~1) |
| `Spectrum2D` | `population` | `list[list[float]]` | - | 2D 占据率矩阵 |
| `PowerShift` | `iq_avg` | `list[list[complex]]` | - | IQ 平均值 |
| `S21vsFlux` | `iq_avg` | `list[list[complex]]` | - | IQ 平均值 |
| `DRAG` | `population` | `list[float]` | - | 占据率 (0~1) |
| `OptPiPulse` | `population` | `list[float]` | - | 占据率 (0~1) |
| `Delta` | `population` | `list[float]` | - | 占据率 (0~1) |
| `RB` | `population` | `list[float]` | - | 保真度 (0~1) |

### 数据类型说明

- `list[complex]`: Python list，每个元素为 `complex`（`a + bj`）
- `list[float]`: Python list，每个元素为 `float`
- `list[list[complex]]`: 二维 list（N 比特 × M 点）
- `list[list[float]]`: 二维 list（频率轴 × 偏置轴）

---

## `meta` 字段规范

```python
meta = {
    "freq": {                    # 扫描频率轴
        "def": [6.495e9, ..., 6.505e9],  # 实际频率值（list[float]）
        "param": "$gate.Measure.Q0.params.frequency",  # 参数路径
    },
    "delay": {                   # 延迟轴（T1/Ramsey）
        "def": [0, 1e-6, ..., 20e-6],     # 实际延迟值（list[float]）
    },
    "qubits": ["Q0", "Q1"],      # 比特列表
    "shots": 1024,               # 采样次数
    "signal": "population",      # 信号类型
}
```

### meta 常见字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `freq` / `delay` / `amp` / `bias` | `dict` with `def` key | 扫描轴，`def` 为实际值 list |
| `qubits` | `list[str]` | 参与实验的比特列表 |
| `shots` | `int` | 采样次数 |
| `signal` | `str` | 信号类型 (`population` / `iq_avg` / `iq`) |
| `stage` | `int` | 测量阶段 |

---

## 适配示例

### S21 示例

```python
if wf_name == 'S21':
    result = run_s21(qubits=['Q0'], freq=[6.495e9, ..., 6.505e9])
    return {
        "data": {"s21": result['s21']},     # list[complex]
        "meta": {"freq": {"def": result['freq']}},
    }
```

### T1 示例

```python
if wf_name == 'T1':
    result = run_t1(qubits=['Q0'], delay=[0, 1e-6, ..., 20e-6])
    return {
        "data": {"population": result['population']},  # list[float]
        "meta": {"delay": {"def": result['delay']}},
    }
```

---

## 注意事项

1. `data` 中的值必须为可 JSON 序列化的类型（list / dict / str / int / float / bool）
2. 复数需转换为 list（`[real, imag]`）或用 `convert_ndarray()` 处理
3. 如果实验失败，返回 `{"error": "错误信息"}`，不要抛出异常
4. `meta` 中的扫描轴 `def` 应与 `data` 中的数据维度对齐
