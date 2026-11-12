"""_phase18_check.py - 阶段 18 自检：审计查询（/qubit/runs 过滤）

验证目标：
1. `services/common/runrecord.py` 的纯函数：`record_canonical_status`、`_as_epoch`、
   `filter_records`（qubit / 规范状态 / source / dataset_id / trace_id / since-until）。
2. 过滤**不改变顺序、不修改输入**，非法/认不出的条件按"宁缺毋错"处理。
3. `:3003 /qubit/runs` 接线：过滤在任何截断之前生效（老 trace 不会被 limit 截掉）、
   回包带 `filters`、`count` 是过滤后的条数、仍然按"新→旧"。
4. 文档在位：OPERATIONS §3.21 + §4 表行 + 路线图 §2.3 阶段 18 注记。

全程离线：用桩 writer 喂假记录，不连设备、不读真实台账。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "Agentic Workflow" / "qmclaw-server"
for path in (str(SERVER),):
    if path not in sys.path:
        sys.path.insert(0, path)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PASS = 0
FAIL = 0
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILED.append(f"{name} {detail}".strip())
        print(f"[FAIL] {name} {detail}".rstrip())


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


from services.common import runrecord as rr  # noqa: E402

RECORDS = [
    {
        "run_id": "run-a", "status": "success", "qubits": ["q4ld3"], "source": "execute",
        "dataset_ids": [2670], "trace_id": "t-a", "started_at": 1000.0, "finished_at": 1005.0,
    },
    {
        "run_id": "run-b", "status": "error", "error_code": "quota_exceeded", "qubits": ["q2ld3"],
        "source": "execute", "dataset_ids": [], "trace_id": "t-b",
        "started_at": 2000.0, "finished_at": 2003.0,
    },
    {
        "run_id": "run-c", "status": "timeout", "qubits": ["q4ld3", "q7ld3"], "source": "workflow",
        "dataset_ids": [2671, 2672], "trace_id": "t-c", "started_at": 3000.0,
    },
    {
        "run_id": "run-d", "status": "success", "qubits": [], "source": "execute",
        "dataset_ids": [], "trace_id": "t-d", "started_at": 4000.0,
    },
]

# ── 1 规范状态映射 ───────────────────────────────────────────────────────────
check("success → ok", rr.record_canonical_status(RECORDS[0]) == "ok")
check("error + quota_exceeded → blocked", rr.record_canonical_status(RECORDS[1]) == "blocked")
check("timeout → failed", rr.record_canonical_status(RECORDS[2]) == "failed")
check("未知 status → None", rr.record_canonical_status({"status": "weird"}) is None)
check("非 dict → None", rr.record_canonical_status(None) is None)
check("未登记错误码 → failed（不假装成功）",
      rr.record_canonical_status({"status": "x", "error_code": "made_up_code"}) == "failed")
check("blocked 直传保留", rr.record_canonical_status({"status": "blocked"}) == "blocked")

# ── 2 时间解析 ───────────────────────────────────────────────────────────────
check("epoch 数字", rr._as_epoch(1234.5) == 1234.5)
check("数字字符串", rr._as_epoch("1234.5") == 1234.5)
check("ISO 串", rr._as_epoch("1970-01-01T01:00:00") == 3600.0)
check("ISO 带 Z", rr._as_epoch("1970-01-01T01:00:00Z") == 3600.0)
check("空串 → None", rr._as_epoch("") is None)
check("None → None", rr._as_epoch(None) is None)
check("bool 不算时间", rr._as_epoch(True) is None)
check("垃圾串 → None", rr._as_epoch("yesterday") is None)
check("_record_time 优先 finished_at", rr._record_time({"finished_at": 2, "started_at": 1}) == 2.0)
check("_record_time 退回 started_at", rr._record_time({"started_at": 3}) == 3.0)

# ── 3 filter_records ─────────────────────────────────────────────────────────
check("无过滤：顺序不变", [r["run_id"] for r in rr.filter_records(RECORDS)] ==
      ["run-a", "run-b", "run-c", "run-d"])
check("无过滤：不修改输入", len(RECORDS) == 4 and RECORDS[0]["run_id"] == "run-a")
check("非 list → []", rr.filter_records(None) == [])
check("非 dict 元素跳过", rr.filter_records([None, RECORDS[0]]) == [RECORDS[0]])

check("qubit 单值", [r["run_id"] for r in rr.filter_records(RECORDS, qubit="q2ld3")] == ["run-b"])
check("qubit 多值命中", [r["run_id"] for r in rr.filter_records(RECORDS, qubit="q4ld3")] ==
      ["run-a", "run-c"])
check("qubit 列表", [r["run_id"] for r in rr.filter_records(RECORDS, qubit=["q7ld3"])] == ["run-c"])
check("qubit 逗号分隔", [r["run_id"] for r in rr.filter_records(RECORDS, qubit="q2ld3,q7ld3")] ==
      ["run-b", "run-c"])
check("qubit 未命中 → 空", rr.filter_records(RECORDS, qubit="q9ld3") == [])
check("全局实验（qubits 空）不被 qubit 过滤放行",
      "run-d" not in [r["run_id"] for r in rr.filter_records(RECORDS, qubit="q4ld3")])

check("status=ok 命中成功记录", [r["run_id"] for r in rr.filter_records(RECORDS, status="ok")] ==
      ["run-a", "run-d"])
check("status=blocked 命中配额拒绝", [r["run_id"] for r in rr.filter_records(RECORDS, status="blocked")] ==
      ["run-b"])
check("status=failed 命中 timeout（配额拒绝算 blocked 不算 failed）",
      [r["run_id"] for r in rr.filter_records(RECORDS, status="failed")] == ["run-c"])
check("status 也认原始生命周期值",
      [r["run_id"] for r in rr.filter_records(RECORDS, status="timeout")] == ["run-c"])
check("status 大小写不敏感", [r["run_id"] for r in rr.filter_records(RECORDS, status="OK")] ==
      ["run-a", "run-d"])

check("source=workflow", [r["run_id"] for r in rr.filter_records(RECORDS, source="workflow")] == ["run-c"])
check("source 未命中 → 空", rr.filter_records(RECORDS, source="nope") == [])

check("dataset_id 命中", [r["run_id"] for r in rr.filter_records(RECORDS, dataset_id=2671)] == ["run-c"])
check("dataset_id 字符串也认", [r["run_id"] for r in rr.filter_records(RECORDS, dataset_id="2670")] ==
      ["run-a"])
check("dataset_id 非数字必然空", rr.filter_records(RECORDS, dataset_id="abc") == [])
check("dataset_id 为空列表不命中", "run-b" not in [r["run_id"] for r in rr.filter_records(RECORDS, dataset_id=1)])

check("trace_id 精确", [r["run_id"] for r in rr.filter_records(RECORDS, trace_id="t-b")] == ["run-b"])
check("trace_id 空白忽略", len(rr.filter_records(RECORDS, trace_id="  ")) == 4)

check("since 含边界", [r["run_id"] for r in rr.filter_records(RECORDS, since=2000)] ==
      ["run-b", "run-c", "run-d"])
check("until 含边界", [r["run_id"] for r in rr.filter_records(RECORDS, until=2005)] ==
      ["run-a", "run-b"])
check("since+until 窗口", [r["run_id"] for r in rr.filter_records(RECORDS, since=1500, until=3500)] ==
      ["run-b", "run-c"])
check("时间不可判定时排除（run-c 无 finished_at 用 started_at）",
      "run-c" in [r["run_id"] for r in rr.filter_records(RECORDS, since=3000)])
check("无时间字段的记录不进入时间窗",
      rr.filter_records([{"run_id": "x", "status": "success"}], since=0) == [])

check("组合过滤（qubit + status）",
      [r["run_id"] for r in rr.filter_records(RECORDS, qubit="q4ld3", status="ok")] == ["run-a"])
check("组合过滤互斥 → 空",
      rr.filter_records(RECORDS, qubit="q2ld3", status="ok") == [])

# ── 4 服务接线（桩 writer，不读真实台账）────────────────────────────────────
import importlib  # noqa: E402

qs = importlib.import_module("services.quantum_service.server")


class _FakeWriter:
    def __init__(self, records):
        self._records = records  # 新→旧
        self.directory = "<stub>"

    def list_records(self, limit: int = 50):
        return self._records[:limit]


def _dispatch(records, data):
    original = rr.RunRecordWriter
    rr.RunRecordWriter = lambda: _FakeWriter(records)
    try:
        service = object.__new__(qs.QuantumService)
        return qs.QuantumService._handle_qubit_runs(service, data)
    finally:
        rr.RunRecordWriter = original


# 新→旧：d, c, b, a
NEW_TO_OLD = list(reversed(RECORDS))
out = _dispatch(NEW_TO_OLD, {"limit": 2})
check("默认只回 limit 条", out["count"] == 2)
check("默认顺序新→旧", [r["run_id"] for r in out["records"]] == ["run-d", "run-c"])
check("回包带 directory 与 governance", out["directory"] == "<stub>" and "governance" in out)
check("无过滤时 filters 为空", out["filters"] == {})

out = _dispatch(NEW_TO_OLD, {"limit": 1, "qubit": "q2ld3"})
check("过滤生效（limit 不挡老记录）", [r["run_id"] for r in out["records"]] == ["run-b"])
check("回包回显 filters", out["filters"].get("qubit") == "q2ld3")

out = _dispatch(NEW_TO_OLD, {"limit": 50, "trace_id": "t-a"})
check("trace 过滤命中最早记录", [r["run_id"] for r in out["records"]] == ["run-a"])
check("trace_id 回显", out["trace_id"] == "t-a")

out = _dispatch(NEW_TO_OLD, {"limit": 50, "status": "blocked"})
check("规范状态过滤接线", [r["run_id"] for r in out["records"]] == ["run-b"])

out = _dispatch(NEW_TO_OLD, {"limit": 50, "dataset_id": 2672})
check("数据集过滤接线", [r["run_id"] for r in out["records"]] == ["run-c"])

out = _dispatch(NEW_TO_OLD, {"limit": 50, "since": 3500})
check("时间过滤接线", [r["run_id"] for r in out["records"]] == ["run-d"])

# queued + 完成两条同 run_id：过滤后再去重，只留完成态
DUP = [
    {"run_id": "run-z", "status": "success", "qubits": ["q4ld3"], "dataset_ids": [10]},
    {"run_id": "run-z", "status": "queued", "qubits": ["q4ld3"], "dataset_ids": []},
]
out = _dispatch(DUP, {"limit": 10})
check("同 run_id 只回一条（最后写入）", out["count"] == 1 and out["records"][0]["status"] == "success")

# ── 5 源码契约 ───────────────────────────────────────────────────────────────
server_src = read(SERVER / "services" / "quantum_service" / "server.py")
check("handler 使用 filter_records", "filter_records(" in server_src)
check("有过滤时扩大扫描（避免 limit 截掉老记录）", "limit * 10 if active" in server_src)
check("回包带 filters 字段", '"filters": active' in server_src)
check("旧的截断后 trace 过滤已移除",
      'if str(r.get("trace_id") or "") == str(trace_filter)' not in server_src)

runrecord_src = read(SERVER / "services" / "common" / "runrecord.py")
check("filter_records 定义在 runrecord", "def filter_records(" in runrecord_src)
check("规范状态映射是唯一实现", "def record_canonical_status(" in runrecord_src)

# ── 6 文档在位 ───────────────────────────────────────────────────────────────
ops = read(ROOT / "docs" / "OPERATIONS.md")
check("OPERATIONS 有 §3.21", "### 3.21" in ops)
check("OPERATIONS §4 表含 _phase18_check.py", "_phase18_check.py" in ops)
roadmap = read(ROOT / "docs" / "ARCHITECTURE_ROADMAP.md")
check("路线图有阶段 18 注记", "阶段 18" in roadmap and "filter_records" in roadmap)

print(f"\n_phase18_check: PASS={PASS} FAIL={FAIL}")
if FAILED:
    print("失败项：")
    for item in FAILED:
        print("  -", item)
sys.exit(0 if FAIL == 0 else 1)
