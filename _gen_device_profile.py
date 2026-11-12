#!/usr/bin/env python
"""_gen_device_profile.py — 生成设备画像 `config/device_profile.json`。

只读：逐个比特调用 `POST :3003/qubit/registry`（读寄存器），**不下发任何实验**，
因此不会占用硬件通道、不会影响用户正在跑的测控。

用法：
    D:/anaconda3/envs/lqcs/python.exe _gen_device_profile.py --qubits q1ld3,q2ld3,... --out "Agentic Workflow/qmclaw-server/config/device_profile.json"
    # 只看报告不落盘：--dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "Agentic Workflow" / "qmclaw-server"
sys.path.insert(0, str(SERVER))

from services.common.profile import (  # noqa: E402
    default_profile_path,
    load_profile,
    merge_registry,
    save_profile,
    validate_profile,
)

QUANTUM_URL = "http://127.0.0.1:3003"


def post_json(url: str, payload: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def get_json(url: str, timeout: int = 30) -> Dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def fetch_registry(name: str) -> Dict[str, Any]:
    """取一个比特的 registry（失败抛异常由调用方处理）。"""
    resp = post_json(f"{QUANTUM_URL}/qubit/registry", {"name": name})
    if not resp.get("success", True):
        raise RuntimeError(str(resp.get("error") or resp)[:200])
    reg = resp.get("registry")
    if not isinstance(reg, dict):
        reg = resp
    return reg


def default_qubits() -> List[str]:
    return [f"q{i}ld3" for i in range(1, 13)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qubits", default="", help="逗号分隔的比特名；默认 q1ld3..q12ld3")
    ap.add_argument("--out", default="", help="输出路径；默认 config/device_profile.json")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不落盘")
    args = ap.parse_args()

    names = [n.strip() for n in args.qubits.split(",") if n.strip()] or default_qubits()

    session: List[str] = []
    try:
        status = get_json(f"{QUANTUM_URL}/status")
        session = status.get("session") or status.get("session_path") or []
    except Exception as exc:  # 状态取不到不致命
        print(f"[warn] 读 /status 失败：{exc}")

    profile = load_profile()
    profile["version"] = "1.0.0"
    if session:
        profile["session"] = session

    ok, failed = [], []
    for name in names:
        try:
            reg = fetch_registry(name)
        except Exception as exc:
            failed.append((name, str(exc)[:120]))
            continue
        merge_registry(profile, name, reg)
        ok.append(name)

    issues = validate_profile(profile)
    print(f"[ok]   {len(ok)} 个比特：{', '.join(ok)}")
    for name, err in failed:
        print(f"[fail] {name}: {err}")
    print(f"[info] 校验问题 {len(issues)} 条" + (f"：{issues[:8]}" if issues else ""))

    if args.dry_run:
        print("[dry-run] 未落盘")
        return 0

    out = Path(args.out) if args.out else default_profile_path()
    try:
        save_profile(profile, str(out))
    except PermissionError:
        fallback = ROOT / "_device_profile.json"
        save_profile(profile, str(fallback))
        print(f"[warn] {out} 不可写，已改写到 {fallback}（请手动拷进 config/）")
        return 0
    print(f"[done] 写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
