"""寄存器快照 / 对比 / 回滚工具（防止厂商实验静默改写标定值）。

背景：2026-11-04 的 DSL「真机 D1」流程在 q2ld3 上跑了一串厂商实验
（TimingXYZ / SpectroscopyAuto / PiAmpFine / AlphaFine），它们会直接改写
registry 里的 f10 / timing.xy / PiGate.amp / ReadIn.power 等标定量，
把一个本来 contrast=0.82 的比特静默毁成 0.036。

D2 改造（2026-11-06）：**不再自己连 LabRAD**，改走 quantum_service 的只读
端点 `POST /qubit/registry`（含 timing.* 等 /qubit/params 拿不到的键）和写端点
`POST /qubit/set_params`。原因：直连会绕开服务的 LabRAD 串行锁、忙/卡死判断、
写闸门与 P3 审计链 —— 实验正在跑时可能读到半途值，restore 还能绕过写闸门直接
推垃圾值。

用法：
  python reg_snapshot.py snap  q2ld3 [q4ld3 ...]      # 存快照
  python reg_snapshot.py show  q2ld3                   # 看当前值
  python reg_snapshot.py diff  q2ld3 snap_before.json  # 对比
  python reg_snapshot.py restore q2ld3 snap_before.json --keys f10,timing.xy,PiGate.amp
                                                        # 回滚（默认全部非 discriminator 键）
  python reg_snapshot.py set   q2ld3 f10=3.6217 PiGate.amp=1.23

快照文件写到 D:/qmclaw/reg_snapshots/<qubit>_<时间戳>.json
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("QMCLAW_QUANTUM_URL", "http://127.0.0.1:3003")
SNAP_DIR = os.environ.get("QMCLAW_REG_SNAP_DIR", "D:/qmclaw/reg_snapshots")

# 这些键每次 iqraw/实验都会自动重写，回滚时默认跳过
VOLATILE_PREFIX = ("discriminator",)


def _post(path, payload, base=None, timeout=30):
    url = (base or BASE).rstrip("/") + path
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(path, base=None, timeout=30):
    url = (base or BASE).rstrip("/") + path
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def dump(qubit, base=None):
    """通过 :3003 读取某个 qubit 的 registry（摊平成 {key: value}，嵌套保留子字典）。"""
    res = _post("/qubit/registry", {"name": qubit}, base=base)
    if res.get("error"):
        raise RuntimeError(res["error"])
    return res.get("registry") or {}


def flat(d):
    """{'PiGate': {'amp':..}} -> {'PiGate.amp': ..}"""
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            for sk, sv in v.items():
                out[f"{k}.{sk}"] = sv
        else:
            out[k] = v
    return out


def is_volatile(key):
    return any(key.startswith(p) for p in VOLATILE_PREFIX)


def set_keys(qubit, kv, base=None):
    """写寄存器（走 /qubit/set_params，受服务端写闸门保护）。"""
    res = _post("/qubit/set_params", {"name": qubit, "params": kv}, base=base)
    return res


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        return
    base = BASE
    if "--base" in args:
        i = args.index("--base")
        base = args[i + 1]
        del args[i:i + 2]
    cmd = args[0]

    if cmd == "snap":
        os.makedirs(SNAP_DIR, exist_ok=True)
        for qubit in args[1:]:
            d = dump(qubit, base)
            p = f"{SNAP_DIR}/{qubit}_{time.strftime('%Y%m%d_%H%M%S')}.json"
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(d, fh, indent=2, ensure_ascii=False)
            print(f"{qubit}: {len(flat(d))} keys -> {p}")
    elif cmd == "show":
        for qubit in args[1:]:
            print(f"--- {qubit} ---")
            for k, v in sorted(flat(dump(qubit, base)).items()):
                print(f"  {k} = {v}")
    elif cmd == "diff":
        qubit = args[1]
        old = json.load(open(args[2], encoding="utf-8"))
        new = flat(dump(qubit, base))
        oldf = flat(old)
        keys = sorted(set(oldf) | set(new))
        diffs = [(k, oldf.get(k), new.get(k)) for k in keys
                 if oldf.get(k) != new.get(k)]
        if not diffs:
            print("no differences")
        for k, o, n in diffs:
            flag = "  (volatile)" if is_volatile(k) else ""
            print(f"  {k}: {o!r} -> {n!r}{flag}")
    elif cmd == "restore":
        qubit = args[1]
        old = json.load(open(args[2], encoding="utf-8"))
        only = None
        if "--keys" in args:
            only = set(args[args.index("--keys") + 1].split(","))
        oldf = flat(old)
        kv = {}
        for k, v in oldf.items():
            if only is not None and k not in only:
                continue
            if only is None and is_volatile(k):
                continue
            if not isinstance(v, (int, float, str, bool)):
                continue
            kv[k] = v
        if not kv:
            print("nothing to restore")
            return
        res = set_keys(qubit, kv, base)
        if not res.get("success"):
            print(f"restore failed: {res.get('status') or ''} {res.get('error')}")
            print(f"  hint: {res.get('hint') or ''}")
            return
        print(f"restored {len(res.get('updated') or [])} keys on {qubit}")
        for e in res.get("errors") or []:
            print(f"  ! {e}")
    elif cmd == "set":
        # python reg_snapshot.py set q2ld3 f10=3.6217 PiGate.amp=1.23
        qubit = args[1]
        kv = {}
        for item in args[2:]:
            k, v = item.split("=", 1)
            try:
                val = float(v)
            except ValueError:
                val = v
            kv[k] = val
        res = set_keys(qubit, kv, base)
        if not res.get("success"):
            print(f"set failed: {res.get('status') or ''} {res.get('error')}")
            print(f"  hint: {res.get('hint') or ''}")
            return
        for k in res.get("updated") or []:
            print(f"  {qubit}.{k} = {kv[k]}")
    else:
        print(__doc__)


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as e:
        print(f"cannot reach quantum_service at {BASE}: {e}")
        print("hint: 服务没起，或用 --base http://127.0.0.1:3003 指定地址")
    except Exception as e:
        print(f"error: {type(e).__name__}: {e}")
