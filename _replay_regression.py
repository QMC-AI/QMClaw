#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""回放回归 CLI：把真机 fixture 批量回放，检出厂商语义漂移。

用法（离线，不连设备）：
    python _replay_regression.py                      # 读默认 fixture 目录
    python _replay_regression.py --dir D:\\tmp\\fx     # 指定目录
    python _replay_regression.py --limit 20 --json    # 限制条数 / 输出 JSON
    python _replay_regression.py --max-drift 2        # 允许 2 条漂移（默认 0）

退出码：
    0 = 通过（或目录为空 -> 明确打印 empty，不算通过）
    1 = 检出漂移 / fixture 损坏
    2 = 参数或运行错误
"""
from __future__ import annotations

import argparse
import json
import os
import sys

SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Agentic Workflow", "qmclaw-server")
if SERVER not in sys.path:
    sys.path.insert(0, SERVER)

from services.common.regression import (  # noqa: E402
    format_report,
    regression_from_directory,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="QMClaw 回放回归（fixture → 仿真 vs 真机）")
    ap.add_argument("--dir", dest="directory", default=None, help="fixture 目录（默认 services/common/replay.py 的 ReplayStore 目录）")
    ap.add_argument("--limit", type=int, default=50, help="最多回放多少条 fixture（默认 50）")
    ap.add_argument("--max-drift", type=int, default=0, help="允许的漂移条数上限（默认 0）")
    ap.add_argument("--json", dest="as_json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    try:
        report = regression_from_directory(
            directory=args.directory, limit=args.limit, max_drift=args.max_drift
        )
    except Exception as exc:  # noqa: BLE001
        print("回放回归执行失败：%r" % (exc,), file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))

    verdict = report.get("verdict")
    if verdict == "empty":
        if not args.as_json:  # JSON 输出保持可解析，提示走 stderr
            print("\n提示：当前没有任何真机 fixture —— 回归链路未被真实数据覆盖。")
            print("      需要用户授权跑一次真机小实验才会产生 fixture。")
        else:
            print("提示：当前没有任何真机 fixture —— 回归链路未被真实数据覆盖。",
                  file=sys.stderr)
    if verdict in ("drift", "broken"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
