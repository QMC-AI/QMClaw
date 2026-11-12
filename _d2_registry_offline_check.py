"""D2 服务边界收口 —— 离线 + HTTP 级自检（**不碰真机 :3003，不连 LabRAD**）。

覆盖：
  A. 处理器级：QuantumService._handle_qubit_registry 用假 registry 验证
     （摊平嵌套 / timing.* 可见 / 未连接时返回 error / 读操作在 _labrad_lock 内）
  B. HTTP 级：起一个桩 HTTP 服务（/qubit/registry + /qubit/set_params），
     用 subprocess 跑 reg_snapshot.py 的 snap / show / diff / restore，
     验证它**只走 HTTP**、restore 跳过 discriminator.*、写回旧值
  C. 静态：reg_snapshot.py 不再 import labrad / registry_wrapper2

用法：PYTHONIOENCODING=utf-8 python _d2_registry_offline_check.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(r"D:/qmclaw")
SERVER = ROOT / "Agentic Workflow" / "qmclaw-server"
sys.path.insert(0, str(SERVER))
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name} {extra}")
    return bool(cond)


# ---------------------------------------------------------------- 假 registry
FAKE_REGISTRY = {
    "f10": 3.621227332,
    "f21": 3.421227332,
    "fread": 6.725531,
    "fc": 5.9,
    "PiGate": {"amp": 1.206984, "alpha": -0.756558, "length": 30},
    "ReadIn": {"power": -30, "length": 1500},
    "ReadOut": {"amp": 1.0, "length": 1500},
    "timing": {"xy": 72.0, "z": 30.0},
    "discriminator": {"center0": [-0.1, 0.2], "center1": [0.3, -0.1]},
}


class _FakeReg:
    """模拟 registry_wrapper2 暴露的寄存器对象。"""

    def __init__(self, data):
        self._data = data

    def keys(self):
        return list(self._data.keys())

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value


def part_a():
    print("[A] 处理器级：_handle_qubit_registry")
    import lqms.pyle as lqms_pyle

    class FakeRegistryWrapper:
        def __init__(self, cxn, session):
            self.cxn = cxn
            self.session = session

        def __getitem__(self, qubit):
            return _FakeReg(FAKE_REGISTRY)

    class FakeModule:
        RegistryWrapper = FakeRegistryWrapper

    orig = getattr(lqms_pyle, "registry_wrapper2", None)
    lqms_pyle.registry_wrapper2 = FakeModule
    try:
        from services.quantum_service.server import QuantumService

        svc = QuantumService(port=3999)          # __init__ 不连 LabRAD
        svc._labrad = type("FakeLabrad", (), {
            "_cxn": object(),
            "connected": True,
            "session_path": ["", "LQHL", "test", "20251103_LD_SQ"],
        })()

        res = svc._handle_qubit_registry({"name": "q2ld3"})
        check("返回 ok 且无 error", not res.get("error"), res.get("error"))
        reg = res.get("registry") or {}
        check("含顶层标量 f10", "f10" in reg, list(reg)[:6])
        check("含 timing.xy（/qubit/params 拿不到的键）", "timing" in reg and "xy" in reg.get("timing", {}))
        check("含 PiGate 子字典", "amp" in reg.get("PiGate", {}))
        check("numpy/复杂值可 JSON 序列化", isinstance(json.dumps(res), str))
        check("返回 source=labrad", res.get("source") == "labrad", res.get("source"))

        # 未连接 LabRAD 时必须报错而不是崩
        # 注意：不能把 _labrad 置 None 后直接调用，否则 _ensure_connected() 会真的去连 LabRAD
        svc._ensure_connected = lambda: False
        res2 = svc._handle_qubit_registry({"name": "q2ld3"})
        check("未连接时返回 error", bool(res2.get("error")), res2)

        # 静态检查：读操作必须在 _labrad_lock 内
        src = (SERVER / "services" / "quantum_service" / "server.py").read_text(encoding="utf-8")
        m = re.search(r"def _handle_qubit_registry\(self.*?\n(?=    def )", src, re.S)
        body = m.group(0) if m else ""
        check("registry 读取在 _labrad_lock 内", "_labrad_lock" in body)
    finally:
        if orig is not None:
            lqms_pyle.registry_wrapper2 = orig
        else:
            try:
                delattr(lqms_pyle, "registry_wrapper2")
            except AttributeError:
                pass


# ------------------------------------------------------------ 桩 HTTP 服务
class _StubHandler(BaseHTTPRequestHandler):
    received = []

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        _StubHandler.received.append((self.path, payload))
        if self.path == "/qubit/registry":
            self._json({"name": payload.get("name"), "registry": FAKE_REGISTRY,
                        "session_path": ["", "LQHL", "test", "20251103_LD_SQ"],
                        "source": "labrad"})
        elif self.path == "/qubit/set_params":
            self._json({"success": True, "updated": list((payload.get("params") or {}).keys()),
                        "errors": [], "status": "ok"})
        else:
            self._json({"error": "Unknown path: " + self.path}, 404)


def part_b():
    print("[B] HTTP 级：桩服务 + reg_snapshot.py（进程内调用，沙箱不允许 subprocess）")
    import importlib.util

    srv = HTTPServer(("127.0.0.1", 0), _StubHandler)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{port}"
    snap_dir = tempfile.mkdtemp(prefix="d2-snap-")

    spec = importlib.util.spec_from_file_location("reg_snapshot", ROOT / "reg_snapshot.py")
    rs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rs)
    os.environ["QMCLAW_REG_SNAP_DIR"] = snap_dir
    rs.SNAP_DIR = snap_dir

    orig_argv = sys.argv
    try:
        # 1) dump
        d = rs.dump("q2ld3", base=base)
        check("dump 拿到 registry", isinstance(d, dict) and "f10" in d, list(d)[:5])
        check("dump 含 timing.xy（摊平前是子字典）", "xy" in d.get("timing", {}))
        check("flat 摊平 PiGate.amp", "PiGate.amp" in rs.flat(d))

        # 2) CLI: snap
        sys.argv = ["reg_snapshot.py", "snap", "q2ld3", "--base", base]
        rs.main()
        snaps = list(Path(snap_dir).glob("q2ld3_*.json"))
        check("snap 落盘快照文件", len(snaps) == 1, [s.name for s in snaps])
        if snaps:
            data = json.loads(snaps[0].read_text(encoding="utf-8"))
            check("快照含 timing.xy", "xy" in data.get("timing", {}))

        # 3) CLI: show
        sys.argv = ["reg_snapshot.py", "show", "q2ld3", "--base", base]
        rs.main()

        # 4) CLI: diff（无变化）
        if snaps:
            sys.argv = ["reg_snapshot.py", "diff", "q2ld3", str(snaps[0]), "--base", base]
            rs.main()

        # 5) CLI: restore → 应跳过 discriminator.*，写回真实键
        if snaps:
            _StubHandler.received.clear()
            sys.argv = ["reg_snapshot.py", "restore", "q2ld3", str(snaps[0]), "--base", base]
            rs.main()
            writes = [p for p in _StubHandler.received if p[0] == "/qubit/set_params"]
            check("restore 走了 /qubit/set_params", len(writes) == 1, writes)
            if writes:
                keys = set(writes[0][1].get("params") or {})
                check("restore 跳过 discriminator.*", not any(k.startswith("discriminator") for k in keys), keys)
                check("restore 含真实键 f10", "f10" in keys, keys)
                check("restore 含 timing.xy", "timing.xy" in keys, keys)
                check("restore 含 PiGate.amp", "PiGate.amp" in keys, keys)
            reads = [p for p in _StubHandler.received if p[0] == "/qubit/registry"]
            check("restore 只写不回读（按快照文件回滚）", len(reads) == 0, len(reads))
    finally:
        sys.argv = orig_argv
        srv.shutdown()

    src = (ROOT / "reg_snapshot.py").read_text(encoding="utf-8")
    check("reg_snapshot 不再 import labrad_connect", "labrad_connect" not in src)
    check("reg_snapshot 不再 import registry_wrapper2", "registry_wrapper2" not in src)
    check("reg_snapshot 用 /qubit/registry", "/qubit/registry" in src)
    check("reg_snapshot 用 /qubit/set_params", "/qubit/set_params" in src)


if __name__ == "__main__":
    part_a()
    part_b()
    print(f"\nPASS={PASS}  FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
