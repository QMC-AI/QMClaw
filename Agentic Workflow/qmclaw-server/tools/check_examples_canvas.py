"""校验 examples 下所有 `.qwf` 都能在画布上显示，并且画布往返语义等价。

对每个示例：
  1) parse_qwf → ir_to_canvas（画布图）
  2) canvas_to_qwf（画布 → IR → `.qwf` 文本），带 base_ir 做等价判定
  3) 打印节点数 / 灰节点（画布不支持的 op）/ 等价结果 / 差异

在 qmclaw-server 目录下运行：
  python tools/check_examples_canvas.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.workflow_service.dsl import parse_qwf  # noqa: E402
from services.workflow_service.dsl.canvas_ir import (  # noqa: E402
    canvas_to_qwf,
    ir_to_canvas,
)

EXAMPLES = ROOT / "data" / "workflows" / "examples"


def main() -> int:
    files = sorted(EXAMPLES.glob("*.qwf"))
    if not files:
        print(f"!! {EXAMPLES} 下没有 .qwf")
        return 1
    bad = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        try:
            ir = parse_qwf(text)
        except Exception as exc:  # 语法错误也要报出来
            print(f"✗ {path.name}: 解析失败 {exc}")
            bad += 1
            continue
        canvas = ir_to_canvas(ir)
        unsupported = canvas.get("unsupported") or []
        result = canvas_to_qwf(canvas, base_ir=ir)
        ok = result.get("ok")
        stable = result.get("stable")
        changed = result.get("changed")
        diff = result.get("diff") or []
        status = "✓" if ok and stable else "✗"
        if not (ok and stable):
            bad += 1
        print(
            f"{status} {path.name}: nodes={len(canvas.get('nodes') or [])} "
            f"edges={len(canvas.get('edges') or [])} "
            f"unsupported={unsupported} ok={ok} stable={stable} changed={changed}"
        )
        for d in diff[:5]:
            print(f"    · {d}")
        if not ok:
            for e in (result.get("errors") or [])[:5]:
                print(f"    ! {e}")
    print(f"\n共 {len(files)} 个示例，失败 {bad} 个")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
