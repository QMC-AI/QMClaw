#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_phase8_check.py — 阶段 8：会话路径的唯一实现。

对应 docs/ARCHITECTURE_ROADMAP.md §3 #2/#3（同一能力多处实现）：

  - `quantum_service/server.py` / `analysis_service/server.py` 的会话接口
    各自裸 `json.loads(session_path)`，坏输入直接 500；
  - analysis 的 `_handle_connect` 忽略请求里的 session_path；
  - "读 config/session.json → ['', user] + path → 缺文件回默认" 有两份逐字重复。

离线自检，不连 LabRAD：
  1. parse_session_path 的各种输入形态与错误回报（永不抛异常）
  2. session_key / same_session
  3. load_session_config（真配置 / 临时配置 / 缺失 / 损坏 / 形状不对）
  4. 四个会话接口与两份 _load_session_config 的接线（源码级，防回退）

用法：D:/anaconda3/envs/lqcs/python.exe _phase8_check.py
"""
from __future__ import annotations

import os
import re
import shutil
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
print("_phase8_check — 阶段 8：会话路径唯一实现")
print("=" * 72)

from services.common import session as S  # noqa: E402

# ── 1. parse_session_path：合法输入 ───────────────────────────────────────
check("parse: 标准列表", S.parse_session_path(["", "LQHL", "test", "x"]) == (["", "LQHL", "test", "x"], ""))
check("parse: 元组也接受", S.parse_session_path(("", "LQHL", "test", "x")) == (["", "LQHL", "test", "x"], ""))
check(
    "parse: JSON 字符串",
    S.parse_session_path('["", "LQHL", "test", "x"]') == (["", "LQHL", "test", "x"], ""),
)
check(
    "parse: '/a/b/c' 斜杠写法",
    S.parse_session_path("/LQHL/test/x") == (["", "LQHL", "test", "x"], ""),
)
check("parse: 宽松去空白", S.parse_session_path(["", " LQHL ", "test"]) == (["", "LQHL", "test"], ""))
check("parse: None → 未指定", S.parse_session_path(None) == (None, ""))
check("parse: 空串 → 未指定", S.parse_session_path("   ") == (None, ""))

# ── 2. parse_session_path：非法输入只回原因 ───────────────────────────────
for label, value in [
    ("坏 JSON", "[not json"),
    ("空数组", "[]"),
    ("中间空段", ["", "LQHL", ""]),
    ("非字符串段", ["", 1]),
    ("只有空段", ["", ""]),
    ("不支持的类型", 123),
]:
    path, error = S.parse_session_path(value)
    check(f"parse: {label} 回错误不抛", path is None and bool(error), f"got={(path, error)!r}")

# ── 3. session_key / same_session ─────────────────────────────────────────
check("key: 带前导空段", S.session_key(["", "LQHL", "test", "x"]) == "/LQHL/test/x")
check("key: 同会话不同写法一致",
      S.session_key("/LQHL/test/x") == S.session_key('["", "LQHL", "test", "x"]'))
check("same: 跨写法为 True", S.same_session("/LQHL/test/x", ["", "LQHL", "test", "x"]) is True)
check("same: 不同会话为 False", S.same_session("/LQHL/test/x", "/LQHL/test/y") is False)
check("same: 坏输入判不同（不猜）", S.same_session("[bad", "[bad") is False)
check("normalize: 坏输入回默认会话", S.normalize_session_path("[bad") == list(S.DEFAULT_SESSION))

# ── 4. load_session_config ────────────────────────────────────────────────
path, source = S.load_session_config()
check("config: 读到真配置", source == "config" and len(path) >= 3 and path[0] == "", f"got={(path, source)!r}")
check("config: 形如 ['', user, ...]", len(path) > 1 and isinstance(path[1], str) and path[1] != "")

tmp_dir = os.path.join(ROOT, "_tmp_session_cfg")
try:
    shutil.rmtree(tmp_dir, ignore_errors=True)
    os.makedirs(tmp_dir, exist_ok=True)
    good = os.path.join(tmp_dir, "good.json")
    with open(good, "w", encoding="utf-8") as fh:
        fh.write('{"session": {"user": "ZZ", "path": ["a", "b"]}}')
    got, src = S.load_session_config(good)
    check("config: 临时配置文件生效", got == ["", "ZZ", "a", "b"] and src == "config", f"got={(got, src)!r}")

    broken = os.path.join(tmp_dir, "broken.json")
    with open(broken, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    got, src = S.load_session_config(broken)
    check("config: 损坏文件回默认", got == list(S.DEFAULT_SESSION) and src == "default")

    weird = os.path.join(tmp_dir, "weird.json")
    with open(weird, "w", encoding="utf-8") as fh:
        fh.write('{"session": {"user": "ZZ", "path": []}}')
    got, src = S.load_session_config(weird)
    check("config: path 为空回默认", got == list(S.DEFAULT_SESSION) and src == "default")

    got, src = S.load_session_config(os.path.join(tmp_dir, "missing.json"))
    check("config: 缺失文件回默认", got == list(S.DEFAULT_SESSION) and src == "default")
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)

# ── 5. 接线（源码级，防回退到重复实现）────────────────────────────────────
qs = read("services/quantum_service/server.py")
asrv = read("services/analysis_service/server.py")
lc = read("services/quantum_service/labrad_client.py")

check("quantum: connect 走 parse_session_path", "parse_session_path(data.get(\"session_path\"))" in qs)
check("quantum: switch 走 parse_session_path", qs.count("from ..common.session import parse_session_path") >= 2)
check("quantum: 非法会话回 validation_error", 'error_envelope("validation_error", session_error' in qs)
check("quantum: 不再裸 json.loads(session_path)", "json.loads(session_path)" not in qs)
check("quantum: switch 失败也是信封", 'error_envelope("internal_error", "Session switch failed"' in qs)

check("analysis: connect 接受请求里的会话", "requested_session or self._load_session_config()" in asrv)
check("analysis: switch 走 parse_session_path", "parse_session_path(data.get(\"session_path\"))" in asrv)
check("analysis: 非法会话回 validation_error", 'error_envelope("validation_error", session_error' in asrv)
check("analysis: 不再裸 json.loads(session_path)", "json.loads(session_path)" not in asrv)

check("analysis: _load_session_config 委托公共实现", "from ..common.session import load_session_config" in asrv)
check("labrad_client: _load_session_config 委托公共实现", "from ..common.session import load_session_config" in lc)
check("labrad_client: 不再自己拼 ['', user] + path", "[\"\", user] + path" not in lc)
check("analysis: 不再自己拼 ['', user] + path", "[\"\", user] + path" not in asrv)
hard = re.compile(r"return\s+\[\"\",\s*\"LQHL\",\s*\"test\",\s*\"20260324\"\]")
check("没有第三份默认会话字面量", not hard.search(lc) and not hard.search(asrv))

print("=" * 72)
print(f"PASS={PASS} FAIL={FAIL} WARN={WARN}")
if FAILED:
    print("失败项：")
    for name in FAILED:
        print("  - " + name)
print("=" * 72)
sys.exit(0 if FAIL == 0 else 1)
