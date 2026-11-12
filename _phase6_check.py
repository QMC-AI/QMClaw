#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_phase6_check.py — 阶段 6：统一状态语义（canonical status + reasonCode）。

离线自检，不连 LabRAD、不执行真机实验：
  1. errors.py 的规范状态集合、错误码表、信封、归一函数
  2. 各服务失败路径已改用统一信封（源码级核对）
  3. 服务里不再残留"裸"错误 status 字面量
  4. 消费方（agent adapter / quantum_tools / qca runner）能认新旧两种 status
  5. workflow / goal outcome 额外给出 canonicalStatus + reasonCode

用法：D:/anaconda3/envs/lqcs/python.exe _phase6_check.py
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(ROOT, "Agentic Workflow", "qmclaw-server")
if SERVER not in sys.path:
    sys.path.insert(0, SERVER)

PASS = 0
FAIL = 0
WARN = 0
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


def warn(name: str, detail: str = "") -> None:
    global WARN
    WARN += 1
    print(f"  [WARN] {name} {detail}")


def read(rel: str) -> str:
    path = os.path.join(SERVER, rel)
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


print("=" * 72)
print("_phase6_check — 阶段 6：统一状态语义")
print("=" * 72)

# ── 1. errors.py：规范状态与错误码表 ───────────────────────────────────────
from services.common import errors as E  # noqa: E402

check("规范状态集合是 5 个固定值",
      tuple(E.CANONICAL_STATUSES) == ("ok", "degraded", "queued", "blocked", "failed"),
      str(E.CANONICAL_STATUSES))
check("每个错误码的 status 都是规范值",
      all(spec["status"] in E.CANONICAL_STATUSES for spec in E.ERROR_CODES.values()),
      str([(k, v["status"]) for k, v in E.ERROR_CODES.items() if v["status"] not in E.CANONICAL_STATUSES]))
check("每个错误码都记了 legacy_status",
      all(spec.get("legacy_status") for spec in E.ERROR_CODES.values()))

env = E.error_envelope("quota_exceeded", "额度过啦")
check("quota_exceeded → status=blocked", env["status"] == "blocked", env["status"])
check("quota_exceeded → reasonCode 保留错误码", env["reasonCode"] == "quota_exceeded", env["reasonCode"])
check("quota_exceeded → error_code 兼容旧消费者", env["error_code"] == "quota_exceeded", env["error_code"])
check("quota_exceeded → http 429", env["http_status"] == 429, str(env["http_status"]))
check("quota_exceeded → legacyStatus 留旧值", env.get("legacyStatus") == "quota_exceeded", str(env.get("legacyStatus")))

env2 = E.error_envelope("stuck", "卡住了")
check("stuck → status=degraded", env2["status"] == "degraded", env2["status"])
env3 = E.error_envelope("timeout")
check("timeout → status=failed", env3["status"] == "failed", env3["status"])
check("timeout → retryable=True", env3["retryable"] is True)
env4 = E.error_envelope("not_authorized")
check("not_authorized → status=blocked", env4["status"] == "blocked", env4["status"])
env5 = E.error_envelope("whatever_unknown_code")
check("未知错误码退化为 internal_error",
      env5["reasonCode"] == "internal_error" and env5["status"] == "failed",
      f"{env5['reasonCode']}/{env5['status']}")
env6 = E.error_envelope("task_required", "缺 task", task_id="t-1")
check("信封可以追加额外字段", env6["task_id"] == "t-1")

n1 = E.normalize_error({"status": "stuck", "error": "上一次超时"})
check("normalize_error: stuck → degraded", n1["status"] == "degraded", n1["status"])
check("normalize_error: reasonCode=stuck", n1["reasonCode"] == "stuck", n1["reasonCode"])
check("normalize_error: 保留 legacyStatus", n1.get("legacyStatus") == "stuck", str(n1.get("legacyStatus")))
n2 = E.normalize_error(dict(n1))
check("normalize_error 幂等", n2["status"] == n1["status"] and n2["reasonCode"] == n1["reasonCode"])
n3 = E.normalize_error({"status": "success", "stdout": "hi"})
check("normalize_error 不动成功响应", n3 == {"status": "success", "stdout": "hi"}, str(n3))
n4 = E.normalize_error({"status": "busy", "error": "占用中"})
check("normalize_error 不改写执行生命周期状态", n4["status"] == "busy", n4["status"])
n5 = E.normalize_error({"error": "坏请求"})
check("normalize_error 裸 error → failed", n5["status"] == "failed", n5["status"])

check("canonical_status(stuck)=degraded", E.canonical_status("stuck") == "degraded")
check("canonical_status(success)=None（生命周期不改写）", E.canonical_status("success") is None)
check("canonical_status(blocked)=blocked", E.canonical_status("blocked") == "blocked")
check("is_canonical_status(failed)=True", E.is_canonical_status("failed") is True)
check("is_canonical_status(busy)=False", E.is_canonical_status("busy") is False)

desc = E.describe_codes()
check("describe_codes 版本 1.1.0", desc["version"] == "1.1.0", desc["version"])
check("describe_codes 列出规范状态",
      list(desc.get("canonical_statuses", [])) == list(E.CANONICAL_STATUSES))

# ── 2. 各服务失败路径已改用统一信封 ────────────────────────────────────────
SVC = {
    "quantum": "services/quantum_service/server.py",
    "writer": "services/quantum_service/writer.py",
    "analysis": "services/analysis_service/server.py",
    "taskq_server": "services/task_queue/server.py",
    "taskq_ledger": "services/task_queue/ledger.py",
    "governance": "services/common/governance.py",
    "executor": "services/workflow_service/dsl/executor.py",
    "adapter": "services/agent_service/adapter.py",
    "qtools": "services/common/quantum_tools/execution.py",
    "qca": "services/qca_service/core/runner.py",
}
SRC = {k: read(v) for k, v in SVC.items()}

check("quantum_service 用 error_envelope", "error_envelope(" in SRC["quantum"])
check("quantum_service 用 normalize_error", "normalize_error(" in SRC["quantum"])
check("writer 用 error_envelope", "error_envelope(" in SRC["writer"])
check("analysis_service 用 error_envelope", "error_envelope(" in SRC["analysis"])
check("task_queue/server 用 normalize_error 收口", "normalize_error(" in SRC["taskq_server"])
check("task_queue/ledger 用 error_envelope", "error_envelope(" in SRC["taskq_ledger"])
check("governance 用 blocked 语义", '"blocked"' in SRC["governance"])
check("executor 给出 canonicalStatus", "canonicalStatus" in SRC["executor"])
check("executor 给出 reasonCode", "reasonCode" in SRC["executor"])
check("agent adapter 认规范状态 blocked/degraded",
      '"blocked"' in SRC["adapter"] and '"degraded"' in SRC["adapter"])
check("quantum_tools 认规范状态与 legacyStatus",
      '"blocked"' in SRC["qtools"] and "legacyStatus" in SRC["qtools"])
check("qca runner 兼容 failed", '"failed"' in SRC["qca"])

# ── 3. 不再残留裸错误 status 字面量 ────────────────────────────────────────
BARE = [
    '"status": "read_only"',
    '"status": "stuck"',
    '"status": "quota_exceeded"',
    '"status": "needs_human"',
    '"status": "not_authorized"',
    '"status": "degraded_permanent"',
    '"status": "not_connected"',
    '"status": "budget_exhausted"',
]
for key in ("quantum", "writer", "analysis", "taskq_server", "taskq_ledger"):
    hits = [p for p in BARE if p in SRC[key]]
    check(f"{key} 无裸错误 status 字面量", not hits, str(hits))

# ── 4. workflow / goal outcome 的规范映射 ──────────────────────────────────
m = re.search(r"_STATUS_CANONICAL\s*=\s*\{(.*?)\}", SRC["executor"], re.S)
check("executor 有 _STATUS_CANONICAL 映射表", m is not None)
if m:
    pairs = re.findall(r'"([a-z_]+)"\s*:\s*"([a-z]+)"', m.group(1))
    check("映射表的值都是规范状态",
          all(v in E.CANONICAL_STATUSES for _, v in pairs),
          str([p for p in pairs if p[1] not in E.CANONICAL_STATUSES]))
    mapping = dict(pairs)
    check("needs_human → blocked", mapping.get("needs_human") == "blocked")
    check("quota_exceeded → blocked", mapping.get("quota_exceeded") == "blocked")
    check("not_achieved → failed", mapping.get("not_achieved") == "failed")
    check("completed/achieved → ok", mapping.get("completed") == "ok" and mapping.get("achieved") == "ok")

# ── 5. 消费方兼容 ───────────────────────────────────────────────────────────
check("adapter 兼容旧 legacyStatus",
      "legacyStatus" in SRC["adapter"] and "read_only" in SRC["adapter"])
check("qtools 兼容旧 legacyStatus",
      re.search(r'legacyStatus[^\n]*read_only', SRC["qtools"]) is not None)
check("qca runner 同时认 error 与 failed",
      re.search(r'"error",\s*"failed"', SRC["qca"]) is not None)

print("=" * 72)
print(f"PASS={PASS} FAIL={FAIL} WARN={WARN}")
if FAILED:
    print("失败项：")
    for name in FAILED:
        print("  - " + name)
print("=" * 72)

sys.exit(1 if FAIL else 0)
