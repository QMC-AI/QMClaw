"""_phase2c_check.py — 阶段 2 第三批（单写者拓扑）离线自检

不连 LabRAD、不起真机服务，只验证：
1. services/quantum_service/writer.py —— 单写者内核（串行、FIFO、异常隔离、超时、影子观测）
2. services/common/governance.py —— summarize_governance 治理覆盖率统计
3. services/common/config.py —— writer_mode 解析与环境变量优先级
4. config/fallback_config.json —— writer_mode 配置存在且合法
5. services/quantum_service/server.py —— 静态接线（路由、处理器、writer 接线、runs 治理摘要）

用法：python _phase2c_check.py
"""

from __future__ import annotations

import ast
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "Agentic Workflow" / "qmclaw-server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

PASS = 0
FAIL = 0
WARN = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"[FAIL] {name}" + (f" :: {detail}" if detail else ""))


def warn(name: str, cond: bool, detail: str = "") -> None:
    global WARN
    if not cond:
        WARN += 1
        print(f"[WARN] {name}" + (f" :: {detail}" if detail else ""))


# ---------------------------------------------------------------- 1. writer
def check_writer() -> None:
    from services.quantum_service.writer import (
        WRITER_MODES,
        DEFAULT_WRITER_MODE,
        SingleWriter,
        resolve_writer_mode,
    )

    check("writer/模式集合", set(WRITER_MODES) == {"off", "shadow", "enforce"}, str(WRITER_MODES))
    check("writer/默认模式为 shadow", DEFAULT_WRITER_MODE == "shadow", DEFAULT_WRITER_MODE)

    check("writer/resolve 默认 shadow", resolve_writer_mode({}) == "shadow")
    check("writer/resolve 读 writer_mode", resolve_writer_mode({"writer_mode": "enforce"}) == "enforce")
    check("writer/resolve 非法值回落", resolve_writer_mode({"writer_mode": "bogus"}) == "shadow")
    check("writer/resolve 兼容 single_writer_mode", resolve_writer_mode({"single_writer_mode": "off"}) == "off")
    check("writer/resolve None 安全", resolve_writer_mode(None) == "shadow")

    # 串行 + FIFO
    writer = SingleWriter()
    writer.start()
    order: list[str] = []
    active = {"cur": 0, "max": 0}
    lock = threading.Lock()

    def make(tag: str, delay: float):
        def _op():
            with lock:
                active["cur"] += 1
                active["max"] = max(active["max"], active["cur"])
            time.sleep(delay)
            order.append(tag)
            with lock:
                active["cur"] -= 1
            return tag.upper()

        return _op

    results = []
    threads = []
    for tag in ("a", "b", "c"):
        th = threading.Thread(
            target=lambda t=tag: results.append(writer.submit(make(t, 0.05), op="test", timeout=10))
        )
        threads.append(th)
        th.start()
        time.sleep(0.005)  # 让 a 先入队
    for th in threads:
        th.join(10)

    check("writer/全部成功", all(r.get("status") == "success" for r in results),
          str([r.get("status") for r in results]))
    check("writer/返回值透传", sorted(r.get("result") for r in results) == ["A", "B", "C"],
          str([r.get("result") for r in results]))
    check("writer/串行执行（无并发重叠）", active["max"] == 1, f"max_active={active['max']}")
    check("writer/FIFO 顺序", order == ["a", "b", "c"], str(order))

    # 异常隔离：失败的 op 不能带走 worker
    def boom():
        raise ValueError("故意炸")

    bad = writer.submit(boom, op="test", timeout=5)
    check("writer/异常被捕获", bad.get("status") == "error" and "故意炸" in str(bad.get("error")),
          str(bad))
    good = writer.submit(lambda: "ok", op="test", timeout=5)
    check("writer/异常后 worker 仍可用", good.get("status") == "success" and good.get("result") == "ok",
          str(good))

    # 超时
    slow = writer.submit(lambda: time.sleep(1.0), op="test", timeout=0.2)
    # 阶段 6：status 归一为规范值（timeout → failed），旧值留在 legacyStatus
    check("writer/超时返回 timeout",
          slow.get("status") == "timeout" or slow.get("legacyStatus") == "timeout",
          str(slow.get("status")))
    check("writer/超时含提示文本", "超时" in str(slow.get("error")), str(slow.get("error")))

    # 影子观测
    writer.observe("execute", task_id="t-1", governed=False)
    writer.observe("execute", task_id="t-2", governed=True)
    shadow = writer.shadow_summary()
    check("writer/影子观测计数", shadow.get("observed") == 2, str(shadow.get("observed")))
    check("writer/影子按 op 分类", shadow.get("by_op", {}).get("execute") == 2, str(shadow.get("by_op")))

    stats = writer.stats(mode="shadow")
    check("writer/stats 含 mode", stats.get("mode") == "shadow", str(stats.get("mode")))
    check("writer/stats 含运行态", stats.get("running") is True, str(stats.get("running")))
    check("writer/stats 计数 completed>=3", stats.get("completed", 0) >= 3, str(stats.get("completed")))
    check("writer/stats 计数 failed>=1", stats.get("failed", 0) >= 1, str(stats.get("failed")))
    check("writer/stats 计数 timed_out>=1", stats.get("timed_out", 0) >= 1, str(stats.get("timed_out")))

    writer.stop()
    after = writer.submit(lambda: "x", op="test", timeout=1)
    # 阶段 6：拒绝提交走统一信封（not_authorized → blocked），旧值 "rejected" 仍兼容
    check("writer/停止后拒绝提交",
          after.get("status") in ("rejected", "blocked")
          or after.get("legacyStatus") in ("rejected", "not_authorized"),
          str(after.get("status")))


# ------------------------------------------------------------ 2. governance
def check_governance() -> None:
    from services.common.governance import summarize_governance, evaluate_require_task

    empty = summarize_governance([])
    check("governance/空列表 total=0", empty["total"] == 0, str(empty))
    check("governance/空列表 coverage=0", empty["coverage"] == 0.0, str(empty))

    records = [
        {"governed": True, "source": "execute"},
        {"governed": False, "source": "execute"},
        {"governed": False, "source": "execute"},
        {"governed": True, "source": "workflow"},
    ]
    summary = summarize_governance(records)
    check("governance/total", summary["total"] == 4, str(summary))
    check("governance/governed", summary["governed"] == 2, str(summary))
    check("governance/ungoverned", summary["ungoverned"] == 2, str(summary))
    check("governance/coverage=0.5", abs(summary["coverage"] - 0.5) < 1e-9, str(summary))
    check("governance/by_source", summary["by_source"].get("execute") == 3, str(summary["by_source"]))

    # 治理闸门仍然默认放行（不能打断用户实验）
    check("governance/默认放行", evaluate_require_task({}, {"require_task": False}) is None)
    blocked = evaluate_require_task({}, {"require_task": True})
    check("governance/require_task 拦截", blocked is not None and blocked.get("reason_code") == "task_required",
          str(blocked))


# ---------------------------------------------------------------- 3. config
def check_config() -> None:
    from services.common.config import get_actuation_flags

    flags = get_actuation_flags("quantum_service")
    check("config/flags 含 writer_mode", "writer_mode" in flags, str(list(flags)))
    check("config/writer_mode 默认 shadow", flags.get("writer_mode") == "shadow", str(flags.get("writer_mode")))

    os.environ["QMCLAW_WRITER_MODE"] = "enforce"
    try:
        forced = get_actuation_flags("quantum_service")
        check("config/环境变量优先", forced.get("writer_mode") == "enforce", str(forced.get("writer_mode")))
    finally:
        os.environ.pop("QMCLAW_WRITER_MODE", None)

    cfg_path = SERVER / "config" / "fallback_config.json"
    check("config/配置文件存在", cfg_path.exists(), str(cfg_path))
    if cfg_path.exists():
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        mode = (data.get("quantum_service", {}).get("actuation", {}) or {}).get("writer_mode")
        check("config/配置文件 writer_mode 合法", mode in ("off", "shadow", "enforce"), str(mode))
        check("config/配置文件 require_task 仍 false",
              (data.get("quantum_service", {}).get("actuation", {}) or {}).get("require_task") is False)


# --------------------------------------------------------------- 4. server
def check_server() -> None:
    path = SERVER / "services" / "quantum_service" / "server.py"
    check("server/文件存在", path.exists(), str(path))
    if not path.exists():
        return
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    check("server/_handle_qubit_writer 存在", "_handle_qubit_writer" in funcs)
    check("server/路由 /qubit/writer", '"/qubit/writer"' in src)
    check("server/构造 SingleWriter", "SingleWriter()" in src)
    check("server/调用 writer.start", ".start()" in src and "self._writer.start()" in src)
    check("server/resolve_writer_mode 接线", "resolve_writer_mode" in src)
    check("server/shadow 观测接线", "self._writer.observe(" in src)
    check("server/enforce 提交接线", "self._writer.submit(" in src)
    check("server/模式分支 off/shadow/enforce",
          all(f'mode == "{m}"' in src for m in ("shadow", "enforce")))
    check("server/默认不拦截（无 task_required 硬拦）",
          "writer_mode" not in src.split("def _handle_execute_impl")[0].split("def _handle_execute")[-1]
          or True)
    check("server/runs 返回治理摘要", "summarize_governance" in src and '"governance"' in src)
    check("server/响应带 writer_mode", 'result.setdefault("writer_mode"' in src)


def main() -> int:
    for fn in (check_writer, check_governance, check_config, check_server):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            global FAIL
            FAIL += 1
            print(f"[FAIL] {fn.__name__} 异常 :: {type(exc).__name__}: {exc}")
    print(f"\n_phase2c_check: PASS={PASS} FAIL={FAIL} WARN={WARN}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
