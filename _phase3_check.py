"""_phase3_check.py — 阶段 3 第一批（traceId 贯穿 + 设备画像）离线自检

不连 LabRAD、不下发实验，只验证：
1. services/common/trace.py —— traceId 生成 / 透传优先级 / 清洗 / 头
2. services/common/profile.py —— 设备画像加载、校验、registry 合并、报告
3. config/device_profile.json —— 12 比特画像可解析且无致命问题
4. services/quantum_service/server.py —— 静态接线（/qubit/profile 路由、trace 注入、RunRecord.trace_id）

用法：python _phase3_check.py
"""

from __future__ import annotations

import ast
import json
import sys
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


# ------------------------------------------------------------------ 1. trace
def check_trace() -> None:
    from services.common.trace import (
        TRACE_FIELD,
        TRACE_HEADER,
        attach_trace,
        ensure_trace,
        new_trace_id,
        normalize_trace_id,
        trace_headers,
    )

    check("trace/头名", TRACE_HEADER == "X-Trace-Id", TRACE_HEADER)
    check("trace/字段名", TRACE_FIELD == "trace_id", TRACE_FIELD)

    tid = new_trace_id()
    check("trace/生成带前缀", tid.startswith("t-"), tid)
    check("trace/生成唯一", tid != new_trace_id())
    check("trace/生成长度合理", 10 <= len(tid) <= 24, tid)

    check("trace/清洗保留合法字符", normalize_trace_id("abc-123_x.y:z") == "abc-123_x.y:z")
    check("trace/清洗剔除空白", normalize_trace_id("a b") == "ab", normalize_trace_id("a b"))
    check("trace/清洗截断 64", len(normalize_trace_id("x" * 200)) == 64)

    tid2, is_new = ensure_trace(None)
    check("trace/无输入生成新 id", is_new and tid2.startswith("t-"), f"{tid2} {is_new}")

    tid3, is_new3 = ensure_trace({"trace_id": "caller-1"})
    check("trace/透传 body trace_id", tid3 == "caller-1" and not is_new3, f"{tid3} {is_new3}")

    tid4, is_new4 = ensure_trace({"traceId": "caller-2"})
    check("trace/透传 body traceId", tid4 == "caller-2" and not is_new4, f"{tid4} {is_new4}")

    tid5, is_new5 = ensure_trace({"trace_id": "body-1"}, {"X-Trace-Id": "hdr-1"})
    check("trace/header 优先于 body", tid5 == "hdr-1" and not is_new5, f"{tid5} {is_new5}")

    tid6, is_new6 = ensure_trace(None, {"X-Trace-Id": "hdr-2"})
    check("trace/仅 header 也可透传", tid6 == "hdr-2" and not is_new6, f"{tid6} {is_new6}")

    hdrs = trace_headers("abc")
    check("trace/响应头", hdrs.get(TRACE_HEADER) == "abc", str(hdrs))

    payload = attach_trace({"ok": True}, "t-xyz")
    check("trace/附加到 payload", payload.get("trace_id") == "t-xyz", str(payload))
    check("trace/保留原字段", payload.get("ok") is True)


# ---------------------------------------------------------------- 2. profile
def check_profile() -> None:
    from services.common.profile import (
        EXPECTED_FIELDS,
        default_profile_path,
        load_profile,
        merge_registry,
        profile_report,
        qubit_entry,
        save_profile,
        validate_profile,
    )

    check("profile/期望字段完整", set(EXPECTED_FIELDS) >= {
        "xy_dac", "z_dac", "readout_adc", "f10", "f21", "fread", "fc_read", "bias_z",
    }, str(EXPECTED_FIELDS))

    empty = load_profile(str(ROOT / "_no_such_profile.json"))
    check("profile/缺失返回空骨架", empty.get("version") == "0.0.0" and not empty.get("qubits"))

    p = {"version": "0.0.1", "session": [], "qubits": {}}
    issues = validate_profile(p)
    check("profile/空画像有问题清单", isinstance(issues, list))

    reg = {
        "channels": {
            "xy": ["DAC_LQ 761", "AB"],
            "z": ["DAC_LQ 762", "A"],
            "readout": ["ADC_LQ 760"],
            "readin": ["DAC_LQ 759", "AB"],
        },
        "f10": 3.65,
        "f21": 3.45,
        "fread": 6.63,
        "fc_read": 6.843,
        "fc": 4.08,
        "bias_z": 0.0,
        "PiGate": {"amp": 0.965, "alpha": -0.507, "length": 30},
        "ReadIn": {"power": -38},
        "ReadOut": {"amp": 1.0},
        "timing": {"xy": 1.85},
    }
    merge_registry(p, "q7ld3", reg)
    e = qubit_entry(p, "q7ld3")
    check("profile/合并出 xy_dac", e.get("xy_dac") == "DAC_LQ 761 AB", str(e.get("xy_dac")))
    check("profile/合并出 z_dac", e.get("z_dac") == "DAC_LQ 762 A", str(e.get("z_dac")))
    check("profile/合并出 readout_adc", e.get("readout_adc") == "ADC_LQ 760", str(e.get("readout_adc")))
    check("profile/合并出 f10", abs(float(e.get("f10")) - 3.65) < 1e-9)
    check("profile/合并出 pi_gate_amp", abs(float(e.get("pi_gate_amp")) - 0.965) < 1e-9)
    check("profile/合并出 timing_xy", abs(float(e.get("timing_xy")) - 1.85) < 1e-9)
    check("profile/不臆造字段", "xy_uwave" not in e, str(sorted(e.keys())))

    bad = {"version": "1", "session": [], "qubits": {"qX": {"f10": 1.0}}}
    check("profile/缺字段被报出", len(validate_profile(bad)) > 0)

    rep = profile_report(p)
    check("profile/报告含 count", rep.get("qubit_count") == 1, str(rep.get("qubit_count")))
    check("profile/报告含 path", bool(rep.get("path")))
    check("profile/报告 complete 键存在", "complete" in rep)

    # 落盘读回
    tmp = ROOT / "_phase3_profile_tmp.json"
    try:
        save_profile(p, str(tmp))
        back = load_profile(str(tmp))
        check("profile/存读一致", back.get("qubits", {}).get("q7ld3", {}).get("f10") == 3.65)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass

    check("profile/默认路径在 config 下", default_profile_path().name == "device_profile.json",
          str(default_profile_path()))


# ------------------------------------------------- 3. 真实 device_profile.json
def check_device_profile() -> None:
    path = SERVER / "config" / "device_profile.json"
    check("画像文件存在", path.exists(), str(path))
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover
        check("画像文件可解析", False, str(exc))
        return
    check("画像文件可解析", True)
    qubits = data.get("qubits") or {}
    check("画像含 12 比特", len(qubits) == 12, str(len(qubits)))
    check("画像含 session 路径", isinstance(data.get("session"), list) and len(data.get("session")) >= 2,
          str(data.get("session")))
    missing = [q for q, v in qubits.items() if "f10" not in v or "fread" not in v]
    check("画像每比特有 f10/fread", not missing, str(missing))
    q7 = qubits.get("q7ld3") or {}
    check("画像 q7ld3 f10 正确", abs(float(q7.get("f10", 0)) - 3.650111089742225) < 1e-9,
          str(q7.get("f10")))
    check("画像 q7ld3 xy_dac 正确", q7.get("xy_dac") == "DAC_LQ 761 AB", str(q7.get("xy_dac")))

    from services.common.profile import validate_profile

    issues = validate_profile(data)
    fatal = [i for i in issues if isinstance(i, str) and ("缺失" in i or "非数值" in i)]
    check("画像无致命问题", not fatal, str(fatal[:3]))


# ------------------------------------------------------------ 4. server 接线
def check_server_wiring() -> None:
    src_path = SERVER / "services" / "quantum_service" / "server.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    check("server/存在 _handle_qubit_profile", "_handle_qubit_profile" in funcs)
    check("server/存在 _handle_execute", "_handle_execute" in funcs)

    check("server/注册 /qubit/profile 路由", '"/qubit/profile"' in src)
    check("server/引用 ensure_trace", "ensure_trace" in src)
    check("server/响应带 trace_id", '"trace_id"' in src)
    check("server/RunRecord 传 trace_id", "trace_id=trace_id" in src)

    rr_path = SERVER / "services" / "common" / "runrecord.py"
    rr_src = rr_path.read_text(encoding="utf-8")
    check("runrecord/含 trace_id 字段", "trace_id" in rr_src)
    check("runrecord/from_execute 支持 trace_id", "trace_id" in rr_src)

    # execute 包装必须仍然不硬拦截（不打断用户实验）
    check("server/execute 包装不硬拦截 task_required", "task_required" not in src.split("def _handle_execute(")[1].split("def ")[0])


def main() -> int:
    for fn in (check_trace, check_profile, check_device_profile, check_server_wiring):
        try:
            fn()
        except Exception as exc:  # pragma: no cover
            global FAIL
            FAIL += 1
            print(f"[FAIL] {fn.__name__} 抛出异常 :: {exc}")
    print(f"_phase3_check: PASS={PASS} FAIL={FAIL} WARN={WARN}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
