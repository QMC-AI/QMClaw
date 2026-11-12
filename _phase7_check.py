#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_phase7_check.py — 阶段 7：可复现性补齐（数据集归属 + 直发侧 manifest）。

对应 docs/ARCHITECTURE_ROADMAP.md §2.3 / §3 #8 里"仍未解"的两条：
  - stdout 不打印数据集号的实验，RunRecord 的 dataset_ids 恒为空；
  - 直发 :3003/execute 没有 manifest。

离线自检，不连 LabRAD、不执行真机实验：
  1. dataset_ids.py 的 stdout 解析 / 目录解析 / 差集 / 合并 / 探测 / 来源标注
  2. program_manifest.py 的清单结构与厂商语义补全（含确定性）
  3. RunRecord 新字段（dataset_source / manifest）的落盘形状
  4. :3003/execute 包装层的接线（源码级核对）

用法：D:/anaconda3/envs/lqcs/python.exe _phase7_check.py
"""
from __future__ import annotations

import json
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

try:  # 控制台是 GBK 时别因为 ✓/中文括号炸掉
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


def read(rel: str) -> str:
    with open(os.path.join(SERVER, rel), "r", encoding="utf-8") as fh:
        return fh.read()


print("=" * 72)
print("_phase7_check — 阶段 7：数据集归属 + 直发侧 manifest")
print("=" * 72)

# ── 1. dataset_ids：stdout 解析 ────────────────────────────────────────────
from services.common import dataset_ids as D  # noqa: E402

check("parse: 'dataset: 536'", D.parse_dataset_ids("dataset: 536") == [536])
check("parse: 'Dataset 45'（大写无冒号）", D.parse_dataset_ids("Dataset 45") == [45])
check("parse: 'dataset#12'", D.parse_dataset_ids("dataset#12") == [12])
check("parse: 去重保序", D.parse_dataset_ids("dataset: 5\ndataset: 7\ndataset: 5") == [5, 7])
check("parse: 无编号不编造", D.parse_dataset_ids("Qubit frequency adjusted by 0.001") == [])
check("parse: None/非字符串不炸", D.parse_dataset_ids(None) == [] and D.parse_dataset_ids(123) == [])

# ── 2. dataset_ids：DataVault 目录解析 ─────────────────────────────────────
payload = (
    ["q2ld3"],
    ["00536 - q2ld3: iqraw", "00537 - q2ld3: pidf", "readme.txt"],
)
check("dir: 标准 (dirs, files) 形状", D.iter_dataset_numbers(payload) == [536, 537])
check("dir: 扁平列表", D.iter_dataset_numbers(["12 - a", "9 - b"]) == [9, 12])
check("dir: 无编号名忽略", D.iter_dataset_numbers(([], ["notes.txt", "draft"])) == [])
check("dir: None → 空", D.iter_dataset_numbers(None) == [])
check("dir: 形状怪异不炸", D.iter_dataset_numbers(42) == [] and D.iter_dataset_numbers("00500 - x") == [500])

# ── 3. dataset_ids：差集 / 合并 / 来源 ─────────────────────────────────────
check("diff: 只取新增", D.new_dataset_numbers([536, 537], [536, 537, 538]) == [538])
check("diff: 无新增 → 空", D.new_dataset_numbers([536], [536]) == [])
check("diff: before 未知 → 空（宁缺毋错）", D.new_dataset_numbers(None, [536]) == [])
check("diff: after 未知 → 空", D.new_dataset_numbers([536], None) == [])
check("merge: 升序去重", D.merge_dataset_ids([538, 536], [536, 540]) == [536, 538, 540])
check("merge: 忽略 bool / 垃圾字符串", D.merge_dataset_ids([True, "x", None], ["541"]) == [541])
check("merge: 空输入", D.merge_dataset_ids(None, []) == [])
check("merge: 上限 50", len(D.merge_dataset_ids(range(1, 80))) == 50)
check("source: stdout only", D.dataset_source_label([1], []) == "stdout")
check("source: datavault only", D.dataset_source_label([], [2]) == "datavault")
check("source: both", D.dataset_source_label([1], [2]) == "stdout+datavault")
check("source: neither", D.dataset_source_label([], []) == "unknown")


# ── 4. dataset_ids：探测失败绝不外抛 ───────────────────────────────────────
def _boom(_session):
    raise RuntimeError("LabRAD 断了")


ids, err = D.probe_dataset_numbers(_boom, ["", "LQHL"])
check("probe: 异常 → 空 + 诊断", ids == [] and "RuntimeError" in err, err)
ids, err = D.probe_dataset_numbers(lambda s: None, ["", "LQHL"])
check("probe: None payload → 诊断", ids == [] and "None" in err, err)
ids, err = D.probe_dataset_numbers(lambda s: ([], ["00536 - q"]), ["", "LQHL"])
check("probe: 正常 → 编号且无错", ids == [536] and err == "", f"{ids} {err}")
check("probe: 空目录是合法结果", D.probe_dataset_numbers(lambda s: ([], []), []) == ([], ""))


# ── 5. program_manifest：清单结构与厂商语义 ───────────────────────────────
from services.common import program_manifest as PM  # noqa: E402
from services.common.runrecord import RunRecord, program_fingerprint  # noqa: E402

CODE_T1 = "sq.iqraw(q2ld3, do_plot=False)\nsq.t1(q2ld3, do_plot=False)"
m = PM.build_program_manifest(CODE_T1, ["q2ld3"])
check("manifest: 函数序列保序", m["functions"] == ["iqraw", "t1"], str(m["functions"]))
check("manifest: 程序指纹与 runrecord 一致", m["program_hash"] == program_fingerprint(CODE_T1))
check("manifest: qubits 透传", m["qubits"] == ["q2ld3"])
check("manifest: t1 缺 zpa 被预判", m["calls"][1]["missing_required"] == ["zpa"],
      str(m["calls"][1]["missing_required"]))
check("manifest: iqraw 的静默回写被列出",
      "discriminator.center0" in m["calls"][0]["auto_writes"])
check("manifest: auto_writes 汇总去重", len(m["auto_writes"]) == len(set(m["auto_writes"])))
check("manifest: 缺 zpa 有 warning", any("zpa" in w for w in m["warnings"]), str(m["warnings"]))

m2 = PM.build_program_manifest("sq.spectroscopy(q, freq=np.arange(3.6, 3.7, 0.002))", ["q"])
check("manifest: spectroscopy 带 freq → 不判定缺参", m2["calls"][0]["missing_required"] == [])
check("manifest: spectroscopy 回写 f10/f21",
      m2["calls"][0]["auto_writes"] == ["f10", "f21"], str(m2["calls"][0]["auto_writes"]))

m3 = PM.build_program_manifest("sq.my_custom_fn(q)", ["q"])
check("manifest: 未知函数标 known=False", m3["calls"][0]["known"] is False)
check("manifest: 未知函数给 warning", any("语义表未覆盖" in w for w in m3["warnings"]))

check("manifest: 同码同输出（确定性）",
      json.dumps(PM.build_program_manifest(CODE_T1, ["q2ld3"]), sort_keys=True)
      == json.dumps(PM.build_program_manifest(CODE_T1, ["q2ld3"]), sort_keys=True))
check("manifest: 空代码 → 空 calls", PM.build_program_manifest("")["calls"] == [])
check("manifest: 非字符串输入不炸", PM.build_program_manifest(None)["functions"] == [])
check("summarize: 含函数名与回写提示",
      "iqraw" in PM.summarize_manifest(m) and "回写" in PM.summarize_manifest(m))
check("summarize: 空清单 → 空串", PM.summarize_manifest(None) == "" and PM.summarize_manifest({}) == "")


# ── 6. RunRecord 新字段的落盘形状 ─────────────────────────────────────────
rec = RunRecord.from_execute(CODE_T1, qubits=["q2ld3"])
rec.dataset_ids = [536]
rec.dataset_source = "datavault"
rec.manifest = m
data = rec.to_dict()
check("record: dataset_source 落盘", data.get("dataset_source") == "datavault")
check("record: manifest 落盘", isinstance(data.get("manifest"), dict) and data["manifest"]["version"])
rec2 = RunRecord.from_execute("sq.iqraw(q)")
check("record: 未设置 manifest 时不写该键", "manifest" not in rec2.to_dict())
check("record: 默认 dataset_source=unknown", rec2.to_dict().get("dataset_source") == "unknown")

# ── 7. :3003/execute 包装层接线（源码级）──────────────────────────────────
src = read("services/quantum_service/server.py")
check("server: 有 _probe_dataset_numbers 方法", "def _probe_dataset_numbers" in src)
check("server: 执行前取目录快照", re.search(r"datasets_before\s*=\s*self\._probe_dataset_numbers\(\)", src) is not None)
check("server: 差集 + 合并", "new_dataset_numbers(" in src and "merge_dataset_ids(" in src)
check("server: 标注编号来源", "dataset_source_label(stdout_ids, probed_ids)" in src)
check("server: 已不再只靠 stdout 取号",
      not re.search(r"record\.dataset_ids\s*=\s*self\._extract_dataset_ids\(result\)", src))
check("server: 构造程序清单", "build_program_manifest(code, record.qubits)" in src)
check("server: 响应回填 dataset_ids", 'result["dataset_ids"]' in src)
check("server: 可用环境变量关掉探测", "QMCLAW_DATASET_PROBE" in src)
check("server: 探测异常不影响执行",
      re.search(r"def _probe_dataset_numbers[\s\S]{0,2500}?except Exception:\s*\n\s*return None", src) is not None)

# ── 8. cd + dir 的真实语义（2026-11-10 真机发现：dir 不吃 session 参数）──────
check("server: 探测走 read_session_dataset_numbers", "read_session_dataset_numbers(" in src)
check("server: 不再把 session 传给 dir()", "data_vault.dir(session)" not in src and "dir(s)" not in src)


class _FakeDV:
    """模拟 LabRAD DataVault：dir() 无参、只列当前目录。"""

    def __init__(self, tree):
        self.tree = tree
        self.cwd = [""]
        self.calls = []

    def cd(self, path):
        self.calls.append(("cd", path))
        parts = list(path) if isinstance(path, (list, tuple)) else [path]
        if parts == [""]:
            self.cwd = [""]
            return
        self.cwd = [""] + [str(p) for p in parts if p != ""]

    def dir(self):
        self.calls.append(("dir", None))
        return self.tree.get(tuple(self.cwd), ([], []))


_session = ["", "LQHL", "test", "2025"]
dv = _FakeDV({("", "LQHL", "test", "2025"): (["sub"], ["00536 - q4ld3: iqraw", "00537 - q4ld3: pidf"])})
ids, err = D.read_session_dataset_numbers(dv.cd, dv.dir, _session)
check("cd+dir: 进到 session 再列", ids == [536, 537] and err == "", f"got={(ids, err)!r}")
check("cd+dir: 先回根再进 session", dv.calls[0] == ("cd", "") and dv.calls[1][0] == "cd")

dv2 = _FakeDV({})
ids, err = D.read_session_dataset_numbers(dv2.cd, dv2.dir, ["", "LQHL"])
check("cd+dir: 空目录是合法结果", ids == [] and err == "")


def _boom_cd(_path):
    raise RuntimeError("连接断了")


ids, err = D.read_session_dataset_numbers(_boom_cd, lambda: ([], []), _session)
check("cd+dir: cd 失败回原因不外抛", ids == [] and "连接断了" in err)


def _boom_dir():
    raise RuntimeError("timeout")


ids, err = D.read_session_dataset_numbers(lambda _p: None, _boom_dir, _session)
check("cd+dir: dir 失败回原因不外抛", ids == [] and "timeout" in err)

ids, err = D.read_session_dataset_numbers(lambda _p: None, lambda: None, _session)
check("cd+dir: dir 返回 None 视为失败", ids == [] and "None" in err)

print("=" * 72)
print(f"PASS={PASS} FAIL={FAIL} WARN={WARN}")
if FAILED:
    print("失败项：")
    for name in FAILED:
        print("  - " + name)
print("=" * 72)
sys.exit(0 if FAIL == 0 else 1)
