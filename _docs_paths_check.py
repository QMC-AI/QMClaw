#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_docs_paths_check.py — 文档漂移自检（roadmap §3 #9/#10）。

两条断言，全部离线、不连设备：

1. **路径必须存在**：`docs/*.md` 与仓库根 `*.md`（AGENTS.md / CLAUDE.md / README.md…）里写的
   `D:\\qmclaw\\...` 路径必须真实存在。已知的历史/临时路径登记在 `EXCEPTIONS` 里。
   解释器路径（`...python.exe`）不在本机时只记 WARN —— 文档里本来就写着"他人机器路径"。

2. **自检清单不许漂移**：`docs/OPERATIONS.md` §4 的表格必须与 `run_all_checks.py` 的
   `OFFLINE` 列表一一对应（两个方向都查）。漏一个脚本 = 有人加了自检却没进 runner，
   或者进了 runner 却没写文档。

用法：python _docs_paths_check.py [--json]
退出码：0 全通过（可能有 WARN）；1 有 FAIL。
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOC_FILES = sorted((ROOT / "docs").glob("*.md")) + [
    p for p in sorted(ROOT.glob("*.md")) if p.is_file()
]

# 文档里合法但本机不存在的路径（历史记录 / 临时产物 / 他人机器）
EXCEPTIONS = {
    "D:\\QMClaw\\test_qubit_info.json",   # docs/TODO_RUNTIME.md 的历史示例
    "D:\\qmclaw\\_device_profile.json",   # OPERATIONS §3.4 的**降级输出路径**，正常不存在
}

PATH_RE = re.compile(
    r"[A-Za-z]:[\\/][^\s`\"'()\[\]{}<>|，。；：]+\.(?:py|md|json|qwf|ts|tsx|ps1)"
)

PASS = 0
FAIL = 0
WARN = 0
FAILED = []
WARNED = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


def warn(name: str, detail: str = "") -> None:
    global WARN
    WARN += 1
    WARNED.append(name)
    print(f"  [WARN] {name} {detail}")


def _normalize(raw: str) -> str:
    return raw.replace("/", "\\").rstrip(".,;:")


def _resolve(path: str) -> Path:
    """把文档里的 D:\\qmclaw\\... 换成相对仓库根的本地路径。"""
    rel = path[len("D:\\qmclaw"):].lstrip("\\") if path.lower().startswith("d:\\qmclaw") else ""
    return ROOT / rel if rel else Path(path)


def collect_paths() -> dict:
    found = {}
    for f in DOC_FILES:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in PATH_RE.finditer(text):
            p = _normalize(m.group(0))
            found.setdefault(p, set()).add(f.name)
    return found


def check_paths() -> None:
    print("[1] 文档里的路径必须存在")
    paths = collect_paths()
    check("扫到文档路径（说明正则在干活）", len(paths) > 20, str(len(paths)))

    in_repo_missing = []
    interpreter_missing = []
    for p, sources in sorted(paths.items()):
        if p in EXCEPTIONS:
            continue
        target = _resolve(p)
        exists = target.exists()
        if p.lower().startswith("d:\\qmclaw"):
            if not exists:
                in_repo_missing.append((p, sorted(sources)))
        elif p.lower().endswith("python.exe"):
            if not exists:
                interpreter_missing.append((p, sorted(sources)))

    check("仓库内文档路径全部存在", not in_repo_missing,
          "; ".join(f"{p}（{','.join(s)}）" for p, s in in_repo_missing[:5]))
    if interpreter_missing:
        warn("解释器路径不在本机（文档里已注明他人机器）",
             "; ".join(p for p, _ in interpreter_missing[:3]))


def load_runner_list(name: str) -> list:
    src = (ROOT / "run_all_checks.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return list(ast.literal_eval(node.value))
    return []


def load_runner_offline() -> list:
    return load_runner_list("OFFLINE")


def load_doc_scripts() -> list:
    text = (ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
    return re.findall(r"^\|\s*`(_[A-Za-z0-9_]+\.py)`\s*\|", text, flags=re.MULTILINE)


def check_manifest() -> None:
    print("[2] 自检清单与 runner 不许漂移")
    offline = load_runner_offline()
    online = load_runner_list("ONLINE")
    doc = load_doc_scripts()
    check("runner OFFLINE 非空", len(offline) > 15, str(len(offline)))
    check("OPERATIONS §4 表格非空", len(doc) > 15, str(len(doc)))

    # 本脚本自己：进了 runner 才需要文档行，避免自引用死循环时也不误报
    missing_doc = [s for s in offline if s not in doc and s != "_docs_paths_check.py"]
    check("runner 里的自检都写进了文档", not missing_doc, str(missing_doc))

    known = set(offline) | set(online) | {"_docs_paths_check.py"}
    missing_runner = [s for s in doc if s not in known]
    check("文档里的自检都在 runner 里（离线或在线）", not missing_runner, str(missing_runner))


def main(argv: list) -> int:
    as_json = "--json" in argv
    buf = io.StringIO()
    with redirect_stdout(buf):
        print("=" * 72)
        print("_docs_paths_check — 文档漂移自检（路径存在性 + 自检清单一致性）")
        print("=" * 72)
        check_paths()
        check_manifest()
        print("=" * 72)
        print(f"_docs_paths_check: PASS={PASS} FAIL={FAIL} WARN={WARN}")
        if FAILED:
            print("FAILED: " + ", ".join(FAILED))
        if WARNED:
            print("WARNED: " + ", ".join(WARNED))
        print("=" * 72)
    out = buf.getvalue()
    if as_json:
        print(json.dumps({"pass": PASS, "fail": FAIL, "warn": WARN,
                          "failed": FAILED, "warned": WARNED}, ensure_ascii=False))
        sys.stderr.write(out)
    else:
        sys.stdout.write(out)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
