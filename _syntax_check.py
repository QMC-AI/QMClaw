#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_syntax_check.py — 全仓 Python 语法自检（只 ast.parse，不执行、不导入）。

为什么需要它：`python/workflow/entry.py` 曾经有几处少了右括号，**从来没能 import**，
却因为没人跑过它而一直躺在仓库里。compileall 只覆盖 services，覆盖不到这类角落。

范围：仓库根下所有 `.py`，跳过构建/依赖目录与 `vendor/`（第三方快照）。
退出码：0 全通过；1 有文件语法错误。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

SKIP_DIRS = {
    ".git", "node_modules", ".next", "_checks", "__pycache__",
    ".venv", "venv", "dist", "build", ".mypy_cache", ".pytest_cache",
    "_task_data", "vendor",
}

PASS = 0
FAIL = 0
FAILED = []


def main() -> int:
    global PASS, FAIL
    files = []
    for p in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts):
            continue
        files.append(p)

    print("=" * 72)
    print(f"_syntax_check — ast.parse {len(files)} 个 .py（跳过 vendor/构建目录）")
    print("=" * 72)

    for p in sorted(files):
        rel = p.relative_to(ROOT).as_posix()
        try:
            ast.parse(p.read_text(encoding="utf-8"))
            PASS += 1
        except SyntaxError as exc:
            FAIL += 1
            FAILED.append(rel)
            print(f"  [FAIL] {rel}:{exc.lineno}: {exc.msg}")
            if exc.text:
                print(f"         {exc.text.rstrip()}")
        except UnicodeDecodeError as exc:
            FAIL += 1
            FAILED.append(f"{rel} (encoding)")
            print(f"  [FAIL] {rel}: 不是 UTF-8 文本（{exc.reason}）")
        except OSError as exc:
            FAIL += 1
            FAILED.append(f"{rel} (io)")
            print(f"  [FAIL] {rel}: 读取失败 {exc}")

    print("=" * 72)
    print(f"_syntax_check: PASS={PASS} FAIL={FAIL}")
    if FAILED:
        print("FAILED: " + ", ".join(FAILED))
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
