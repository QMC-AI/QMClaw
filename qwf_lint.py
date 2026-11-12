#!/usr/bin/env python
"""qwf 离线体检 CLI —— 不起服务、不碰硬件，纯静态检查。

用法：
    python D:/qmclaw/qwf_lint.py a.qwf b.qwf
    python D:/qmclaw/qwf_lint.py a.qwf --json
    python D:/qmclaw/qwf_lint.py a.qwf --strict      # 有 warning 也返回非 0

退出码：0 = 没有 error；1 = 有 error（--strict 时 warning 也算）。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "Agentic Workflow" / "qmclaw-server"
sys.path.insert(0, str(SERVER))

from services.workflow_service.dsl.lint import lint_text  # noqa: E402


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="qwf 离线体检")
    ap.add_argument("files", nargs="+", help=".qwf 文件路径，- 表示标准输入")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--strict", action="store_true", help="warning 也算失败")
    args = ap.parse_args()

    results = {}
    worst = 0
    for f in args.files:
        try:
            text = _read(f)
        except Exception as exc:
            results[f] = {"ok": False, "errors": [{"code": "E_READ", "message": str(exc)}],
                          "warnings": [], "infos": [], "summary": {}}
            worst = 1
            continue
        rep = lint_text(text)
        results[f] = rep
        if rep["errors"] or (args.strict and rep["warnings"]):
            worst = 1

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return worst

    for f, rep in results.items():
        c = rep.get("counts") or {}
        print(f"\n=== {f} ===  error={c.get('error', 0)} "
              f"warning={c.get('warning', 0)} info={c.get('info', 0)}")
        s = rep.get("summary") or {}
        if s:
            print(f"  摘要: 实验 {s.get('experiments')} 次, 会写的键 {s.get('writes')}, "
                  f"qubit={s.get('qubit')}")
        for lvl, key in (("ERROR", "errors"), ("WARN ", "warnings"), ("INFO ", "infos")):
            for item in rep.get(key) or []:
                line = item.get("line")
                loc = f" (行 {line})" if line else ""
                print(f"  {lvl} {item.get('code')}{loc}: {item.get('message')}")
                if item.get("hint"):
                    print(f"         提示: {item['hint']}")
        if not rep.get("errors"):
            print("  ✓ 没有 error")
    return worst


if __name__ == "__main__":
    # Windows 控制台常是 GBK，打印 ✓ 之类的字符会 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
