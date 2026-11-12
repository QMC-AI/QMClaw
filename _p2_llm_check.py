"""_p2_llm_check.py - P2 自检：LLM 客户端 + 字符串比较（decide 驱动分支的基础）。

覆盖：
  1) ast.parse：dsl/llm.py、dsl/executor.py、workflow_service/server.py
  2) load_llm_config("llm_decision") / ("llm_decision_minimax")：key 解析
  3) chat() 两种协议各真打一次（Ising openai 兼容 + MiniMax anthropic 兼容）
  4) executor._compare：数值比较 + 字符串比较（== / != / in / contains）

运行：
  PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 D:/anaconda3/envs/lqcs/python.exe _p2_llm_check.py
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(r"D:/qmclaw")
SERVER = ROOT / "Agentic Workflow" / "qmclaw-server"
sys.path.insert(0, str(SERVER))

PASS, FAIL, SKIP = 0, 0, 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def skip(name: str, reason: str = "") -> None:
    """外部依赖（供应商 key / 网络 / 模型上下线）不判失败，记 SKIP。

    离线套件必须能断网跑：`chat()` 真的打外网，供应商抽风不该让整套自检变红。
    """
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {name} {reason}")


def load_dotenv_for_test() -> None:
    env_path = SERVER / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    print("== 1) ast.parse ==")
    for rel in ("services/workflow_service/dsl/llm.py",
                "services/workflow_service/dsl/executor.py",
                "services/workflow_service/server.py"):
        path = SERVER / rel
        try:
            ast.parse(path.read_text(encoding="utf-8"))
            check(f"ast.parse {rel}", True)
        except Exception as exc:
            check(f"ast.parse {rel}", False, repr(exc))

    load_dotenv_for_test()

    print("== 2) 配置解析 ==")
    from services.workflow_service.dsl.llm import load_llm_config, chat, extract_json

    cfg_dec = load_llm_config("llm_decision")
    check("llm_decision provider", cfg_dec.get("provider") == "openai", str(cfg_dec.get("provider")))
    check("llm_decision 有 api_key", bool(cfg_dec.get("api_key")))
    check("llm_decision 有 model", bool(cfg_dec.get("model")))

    cfg_mm = load_llm_config("llm_decision_minimax")
    check("llm_decision_minimax provider", cfg_mm.get("provider") == "anthropic",
          str(cfg_mm.get("provider")))
    check("llm_decision_minimax key 来自 .env", bool(cfg_mm.get("api_key")),
          "MINIMAX_API_KEY 未载入")
    check("llm_decision_minimax model 非空", bool(cfg_mm.get("model")),
          str(cfg_mm.get("model")))

    print("== 3) 真打 LLM（外部依赖：无 key / 供应商不可用 → SKIP，不判失败）==")
    msgs = [{"role": "user", "content": "只回答一个词：pong"}]
    if not cfg_mm.get("api_key"):
        skip("MiniMax(anthropic) 调用", "MINIMAX_API_KEY 未配置")
    else:
        content, err = chat(cfg_mm, msgs, max_tokens=32)
        if content and content.strip():
            check("MiniMax(anthropic) 调用成功", True)
            print(f"       minimax 回：{content[:80]!r}")
        else:
            skip("MiniMax(anthropic) 调用",
                 f"供应商未返回内容（外部依赖）：{err or 'empty'}")

    if not cfg_dec.get("api_key"):
        skip("Ising(openai) 调用", "OPENAI_API_KEY 未配置")
    else:
        content2, err2 = chat(cfg_dec, msgs, max_tokens=32)
        if content2 and content2.strip():
            check("Ising(openai) 调用成功", True)
            print(f"       ising 回：{content2[:80]!r}")
        else:
            skip("Ising(openai) 调用",
                 f"供应商未返回内容（外部依赖）：{err2 or 'empty'}")

    check("extract_json 容忍围栏",
          extract_json('```json\n{"a": 1}\n```').get("a") == 1)

    print("== 4) executor._compare（数值 + 字符串）==")
    from services.workflow_service.dsl.executor import IrExecutor

    ex = IrExecutor(None, {"ir_version": 1, "id": "t", "pipeline": []}, {}, dry_run=True)
    ex.bindings["d1"] = {"value": "increase_power"}
    ex.bindings["n1"] = {"value": 0.72}
    cases = [
        ("数值 >= 成立", {"metric": "$n1", "operator": ">=", "threshold": 0.6}, True),
        ("数值 >= 不成立", {"metric": "$n1", "operator": ">=", "threshold": 0.9}, False),
        ("字符串 == 成立", {"metric": "$d1", "operator": "==", "value": "increase_power"}, True),
        ("字符串 == 不成立", {"metric": "$d1", "operator": "==", "value": "decrease"}, False),
        ("字符串 != 成立", {"metric": "$d1", "operator": "!=", "value": "decrease"}, True),
        ("left/operator/value 写法",
         {"left": "$d1", "operator": "==", "value": "increase_power"}, True),
    ]
    for name, spec, want in cases:
        try:
            got = ex._compare(spec)
            check(name, got == want, f"got={got} want={want}")
        except Exception as exc:
            check(name, False, repr(exc))

    print(f"\n合计：PASS={PASS} FAIL={FAIL} SKIP={SKIP}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
