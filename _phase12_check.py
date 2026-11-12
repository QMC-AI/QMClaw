#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_phase12_check.py — 阶段 12：`dataset_index` 透传（Express → analysis）。

对应 docs/ARCHITECTURE_ROADMAP.md §3 #2 的最后一项：
> `:3004/plot` vs Express `/sessions/plot` —— 后者只转发 `{command}`，
> 不透传 `dataset_index`，于是"画指定数据集"只能绕过 Express 直连微服务。

两套入口两套行为是真实事故来源：会话里刚跑完 `sq.iqraw` → 数据集 2669，
但 `POST :3002/sessions/plot` 永远画"最新"，没法画"刚才那个 2668"；反过来直连
`:3004` 又会绕过 Express 的鉴权/日志。本阶段让一个参数就够：

  - Express 接受 `dataset_index`（驼峰 `datasetIndex` 也认），非法值 **在 Express 层就 400**，
    合法值原样透传给 analysis `/plot`；`dataset_index` 不传 = 画最新（行为不变）。
  - analysis `/plot` 回包补 `dataset_index` / `dataset_name`，让"画的是哪个数据集"能对账。

离线自检（纯静态 + Python 侧契约）：
  1. Express：两个别名都读、非法值 400、两个分支都转发、不再有只发 command 的旧写法
  2. analysis：`dataset_index` 默认 -1、回包带实际编号与名字、异常分支没被改坏
  3. 契约：Express 透传的键名与 analysis 读的键名一致；文档写了这件事

用法：D:/anaconda3/envs/lqcs/python.exe _phase12_check.py
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(ROOT, "Agentic Workflow", "qmclaw-server")

PASS = 0
FAIL = 0
FAILED: list[str] = []

try:
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


def read(*parts: str) -> str:
    with open(os.path.join(SERVER, *parts), "r", encoding="utf-8") as fh:
        return fh.read()


print("=" * 72)
print("_phase12_check — 阶段 12：dataset_index 透传")
print("=" * 72)

ts = read("src", "index.ts")
py = read("services", "analysis_service", "server.py")

# ── 1. Express ────────────────────────────────────────────────────────────
start = ts.index('app.post("/sessions/plot"')
end = ts.index('app.post("/sessions/config"')
block = ts[start:end]

check("express: 读取 snake_case", "body.dataset_index" in block)
check("express: 读取 camelCase 别名", "body.datasetIndex" in block)
check("express: 有统一取值函数", "function coerceDatasetIndex" in ts)
check("express: 非整数值被判非法", "Number.isInteger(num)" in block or "Number.isInteger(num)" in ts)
check("express: 负值被判非法", "num < 0" in ts[start:ts.index('app.post("/sessions/config"')] or "num < 0" in ts)
check("express: 非法值回 400 且带 validation_error",
      'res.status(400).json({ error: picked.error' in block and 'reasonCode: "validation_error"' in block)
check("express: 微服务分支转发 dataset_index", "payload.dataset_index = picked.index" in block)
check("express: 微服务分支发的是 payload", "proxyToService('analysis', '/plot', 'POST', payload)" in block)
check("express: legacy 分支也发 payload", 'sendbackendRequest("plot_dataset", payload)' in block)
check("express: 旧的只发 command 的写法已消失",
      'proxyToService(\'analysis\', \'/plot\', \'POST\', { command: command || "" })' not in ts)
check("express: 不传 dataset_index 时不塞该键（画最新语义不变）",
      "if (picked.index !== undefined) payload.dataset_index = picked.index;" in block)
check("express: 空字符串视为未提供", 'value === undefined || value === null || value === ""' in ts)

# ── 2. analysis ───────────────────────────────────────────────────────────
check("analysis: /plot 读 dataset_index 且默认 -1", 'dataset_index = data.get("dataset_index", -1)' in py)
check("analysis: 按编号加载", "self._data.loadDataset(dataset_index)" in py)
check("analysis: 回包带实际编号", '"dataset_index": exp_num' in py)
check("analysis: 回包带数据集名", '"dataset_name": getattr(self._data, "dataset_name", None)' in py)
check("analysis: 异常分支仍在",
      '_log(f"Plot error: {e}\\n{traceback.format_exc()}")' in py
      and 'return {"error": str(e), "plotPath": None}' in py)
check("analysis: 仍是串行化绘图（不会被并发画图踩坏）",
      "@serialized_plot" in py and py.count("@serialized_plot") >= 3)

# 结构完整性：改完的 return 块必须仍在 try 内、后面紧跟 except
m = re.search(r'return \{\s*"success": True,\s*"plotPath": str\(plot_path\),.*?\n            \}\n\n        except', py, re.S)
check("analysis: return 块与 except 的缩进结构完好", bool(m))

# ── 3. 契约一致 + 文档 ────────────────────────────────────────────────────
check("契约: Express 发出的键名与 analysis 读的键名一致",
      "payload.dataset_index" in ts and 'data.get("dataset_index"' in py)
doc_ops = os.path.join(ROOT, "docs", "OPERATIONS.md")
with open(doc_ops, "r", encoding="utf-8") as fh:
    ops = fh.read()
check("文档: OPERATIONS 记录了 dataset_index 透传", "dataset_index" in ops and "/sessions/plot" in ops)

print("=" * 72)
print(f"PASS={PASS} FAIL={FAIL}")
if FAILED:
    print("失败项：")
    for name in FAILED:
        print("  - " + name)
print("=" * 72)
sys.exit(0 if FAIL == 0 else 1)
