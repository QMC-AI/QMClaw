"""校验 + 干跑 `single_qubit_tuneup_gated.qwf`（18 步 + 阈值门禁模板）。

用法：
    D:/anaconda3/python.exe docs/deploy_diff_tools/check_gated_template.py

只做两件事：
  1. 解析 / 规范化 / 语义校验，打印 diags（期望 0 error）
  2. dry-run（不碰真机），确认 27 条语句都能跑通、门禁节点判定结果符合预期
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "Agentic Workflow" / "qmclaw-server"
sys.path.insert(0, str(SERVER))

from services.workflow_service import dsl  # noqa: E402
from services.workflow_service.server import WorkflowService  # noqa: E402

TEMPLATE = SERVER / "data" / "workflows" / "examples" / "single_qubit_tuneup_gated.qwf"


def main() -> int:
    text = TEMPLATE.read_text(encoding="utf-8")
    ir = dsl.normalize(dsl.parse_qwf(text))
    ir, diags = dsl.validate(ir)
    errors = [d for d in diags if d.get("level") == "error"]
    warnings = [d for d in diags if d.get("level") != "error"]
    stmts = list(dsl.iter_all_statements(ir["pipeline"]))
    print(f"file      : {TEMPLATE.name}")
    print(f"statements: {len(stmts)}")
    print(f"budget    : {json.dumps(ir.get('budget'), ensure_ascii=False)}")
    print(f"diags     : {len(errors)} error / {len(warnings)} warning")
    for d in diags:
        print(f"  [{d.get('level')}] {d.get('code')} {d.get('path') or ''} {d.get('message')}")
    if errors:
        print("FAIL: 有 error，干跑不执行")
        return 1

    svc = WorkflowService(port=0)
    report = dsl.run_ir(svc, ir, context={"qubit": ir["env"]["qubit"]}, dry_run=True)
    print(f"status    : {report.get('status')}")
    print(f"error     : {report.get('error')}")
    print(f"usage     : {json.dumps(report.get('usage'), ensure_ascii=False)}")
    rows = report.get("statements") or []
    print(f"executed  : {len(rows)} statements")
    for r in rows:
        print(f"  {r.get('as'):<10} {r.get('op'):<8} {r.get('status'):<10} {str(r.get('error') or '')[:60]}")
    ok = report.get("status") == "completed" and len(rows) == len(stmts)
    print("RESULT    :", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
