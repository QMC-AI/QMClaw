"""
只读联调回归脚本 — 对运行中的 quantum_service / analysis_service 做只读验收

前提：服务已由人工启动（quantum:3003, analysis:3004）。
本脚本只发只读请求 + 会被闸门拒绝的写请求，不真正下发实验。

Usage:
    python scripts/check_readonly_regression.py
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

_SERVER = Path(__file__).resolve().parent.parent
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

QUANTUM = "http://localhost:3003"
ANALYSIS = "http://localhost:3004"


def _call(url: str, data: dict = None, method: str = "GET") -> dict:
    try:
        if method == "GET":
            req = urllib.request.Request(url)
        else:
            payload = json.dumps(data or {}).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method=method,
            )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:400]
        return {"error": f"HTTP {e.code}: {e.reason} | {body}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def main() -> int:
    failures = []

    def check(name: str, ok: bool, detail: str = ""):
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    print("== quantum_service /health ==")
    health = _call(f"{QUANTUM}/health")
    if health.get("error"):
        print(f"  [FAIL] health unreachable: {health['error']}")
        return 1
    check("data_source present", "data_source" in health, health.get("data_source", "?"))
    check("actuation_allowed present", "actuation_allowed" in health)
    check("labrad_host present", "labrad_host" in health, health.get("labrad_host", "?"))
    check("labrad_port present", "labrad_port" in health, str(health.get("labrad_port", "?")))
    check("stuck present", "stuck" in health)

    print("== quantum_service /status ==")
    status = _call(f"{QUANTUM}/status")
    check("status data_source present", "data_source" in status, status.get("data_source", "?"))
    check("status actuation_allowed present", "actuation_allowed" in status)

    print("== 写闸门（默认关闭）==")
    flags_ok = health.get("actuation_allowed") is False
    check("actuation_allowed is False by default", flags_ok)

    print("== /execute 应被拒为 read_only ==")
    ex = _call(f"{QUANTUM}/execute", {"code": "print('x')"}, "POST")
    offline_or_ro = ex.get("status") in ("read_only", "offline_simulated", "offline")
    check(
        "execute rejected or offline sim",
        offline_or_ro,
        f"status={ex.get('status')} error={str(ex.get('error'))[:80]}",
    )
    if ex.get("status") == "read_only":
        check("read_only has hint", bool(ex.get("hint")))
        check("read_only did not busy-lock", health.get("busy") is False)

    print("== /qubit/set_params 应被拒为 read_only ==")
    sp = _call(
        f"{QUANTUM}/qubit/set_params",
        {"name": "q1", "params": {"f10": 5.0}},
        "POST",
    )
    check(
        "set_params rejected",
        sp.get("status") == "read_only" or sp.get("success") is False,
        f"status={sp.get('status')} success={sp.get('success')}",
    )
    if sp.get("status") == "read_only":
        check("set_params has hint", bool(sp.get("hint")))

    print("== analysis_service /health ==")
    ah = _call(f"{ANALYSIS}/health")
    check("analysis health ok", "error" not in ah, str(ah.get("error", ""))[:80])
    check("analysis data_source present", "data_source" in ah, ah.get("data_source", "?"))
    check("analysis actuation_allowed present", "actuation_allowed" in ah)

    print("== analysis /execute 应被拒 ==")
    ae = _call(f"{ANALYSIS}/execute", {"code": "print('x')"}, "POST")
    check(
        "analysis execute rejected or offline",
        ae.get("status") in ("read_only", "offline", "degraded", "offline_simulated"),
        f"status={ae.get('status')}",
    )

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {failures}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
