#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把所有 DSL 自检脚本的输出 + 相关运行产物 **汇总到同一个地方**：`D:\\qmclaw\\_checks\\`。

目录结构（每次运行一个时间戳子目录）：
    _checks/<ts>/
        summary.json          每个自检脚本的 {name, exit_code, passed, failed, seconds}
        manifest.json         总索引：自检结果 + 归档产物清单(sha256) + 外部引用(DataVault/图/快照)
        checks/<name>.log     每个自检脚本的原始 stdout+stderr
        artifacts/
            workflow-runs/*.json   每次 workflow run 的落档（含 outcome / gateNotes / sideEffects）
            ledger/*.json          task_queue 账本（预算/额度/票据/事件）
            e2e-tasks/*.json       e2e 自检产生的 task 数据
            lane-tasks/*.json      实验 lane 任务（含下发给量子端的 payload）

DataVault 原始 hdf5、绘图 png、寄存器快照体积大且是活的源目录，**只记路径不复制**，在 manifest 里列为 external。

沙箱禁止 subprocess（CreatePipe 被拒），所以检查脚本用 runpy 在当前进程内跑并重定向 stdout。

用法：
    D:/anaconda3/envs/lqcs/python.exe run_all_checks.py             # 12 套离线自检 + 归档
    D:/anaconda3/envs/lqcs/python.exe run_all_checks.py --e2e      # 再加 e2e / registry 两套
    D:/anaconda3/envs/lqcs/python.exe run_all_checks.py --only _p3_check
    D:/anaconda3/envs/lqcs/python.exe run_all_checks.py --archive-only --window-min 600
"""
import hashlib
import io
import json
import os
import re
import runpy
import shutil
import sys
import time
import traceback
from contextlib import redirect_stdout, redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(HERE, "_checks")

# 离线套件（不连真机 / 不需要服务）
OFFLINE = [
    "_p1_check.py",
    "_p1_goal_dsl_check.py",
    "_p1_manifest_check.py",
    "_p2_ask_choice_check.py",
    "_p2_flow_check.py",
    "_p2_llm_check.py",
    "_p2_outcome_check.py",
    "_p3_check.py",
    "_qwf_gen_goal_check.py",
    "_derived_key_check.py",
    "_autorevert_check.py",
    "_autorevert_exec_check.py",
    "_d2_registry_offline_check.py",
    "_phase2_check.py",
    "_phase2b_check.py",
    "_phase2c_check.py",
    "_phase3_check.py",
    "_phase3b_check.py",
    "_phase3c_check.py",
    "_phase3d_check.py",
    "_phase3e_check.py",
    "_phase4_check.py",
    "_phase5_check.py",
    "_phase6_check.py",
    "_phase7_check.py",
    "_phase8_check.py",
    "_phase9_check.py",
    "_phase10_check.py",
    "_phase11_check.py",
    "_phase12_check.py",
    "_phase13_check.py",
    "_phase14_check.py",
    "_phase15_check.py",
    "_phase16_check.py",
    "_phase17_check.py",
    "_phase18_check.py",
    "_phase19_check.py",
    "_docs_paths_check.py",
    "_syntax_check.py",
]
# 需要在跑的服务的套件
ONLINE = [
    "_p3_e2e.py",
    "_registry_ep_check.py",
    "_apply_dataset_ep_check.py",
]

# 产物来源：类别 -> (源目录, 只收 mtime 在窗口内的 .json)
ARTIFACT_SOURCES = {
    "workflow-runs": os.path.join(HERE, "Agentic Workflow", "qmclaw-server", "data", "workflow-runs"),
    "ledger": os.path.join(HERE, "Agentic Workflow", "qmclaw-server", "services", "task_queue", "data", "tasks"),
    "e2e-tasks": os.path.join(HERE, "_p3_e2e_tasks"),
    "lane-tasks": os.path.join(HERE, "Agentic Workflow", "config", "tasks"),
}
# 体积大 / 活的源目录：只在 manifest 里记路径
EXTERNAL_SOURCES = {
    "datavault": "D:/DataVault/LQHL.dir/test.dir/20251103_LD_SQ.dir",
    "plots": os.path.join(HERE, "Agentic Workflow", "qmclaw-web", "public", "plots"),
    "reg_snapshots": os.path.join(HERE, "reg_snapshots"),
}

PASS_RE = re.compile(r"PASS[=:\s]+(\d+)")
FAIL_RE = re.compile(r"FAIL[=:\s]+(\d+)")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run_one(name: str, outdir: str) -> dict:
    path = os.path.join(HERE, name)
    buf = io.StringIO()
    err = io.StringIO()
    started = time.time()
    exit_code = 0
    error = ""
    try:
        with redirect_stdout(buf), redirect_stderr(err):
            try:
                runpy.run_path(path, run_name="__main__")
            except SystemExit as e:
                exit_code = int(e.code) if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException:
        exit_code = 99
        error = traceback.format_exc()
    text = buf.getvalue()
    if err.getvalue():
        text += "\n--- stderr ---\n" + err.getvalue()
    if error:
        text += "\n--- runner exception ---\n" + error

    log_name = name.replace(".py", "") + ".log"
    with open(os.path.join(outdir, log_name), "w", encoding="utf-8") as f:
        f.write(text)

    m_pass = PASS_RE.search(text)
    m_fail = FAIL_RE.search(text)
    return {
        "name": name,
        "exit_code": exit_code,
        "passed": int(m_pass.group(1)) if m_pass else None,
        "failed": int(m_fail.group(1)) if m_fail else None,
        "seconds": round(time.time() - started, 2),
        "log_file": "checks/" + log_name,
        "error": error[-800:] if error else "",
    }


def collect_artifacts(outdir: str, since_ts: float) -> list:
    """把窗口内变动的 json 产物复制进 outdir/artifacts/<类别>/，返回清单。"""
    items = []
    for category, src in ARTIFACT_SOURCES.items():
        if not os.path.isdir(src):
            continue
        dest_dir = os.path.join(outdir, "artifacts", category)
        for fn in sorted(os.listdir(src)):
            if not fn.endswith(".json"):
                continue
            sp = os.path.join(src, fn)
            try:
                mt = os.path.getmtime(sp)
            except OSError:
                continue
            if mt < since_ts:
                continue
            os.makedirs(dest_dir, exist_ok=True)
            dp = os.path.join(dest_dir, fn)
            try:
                shutil.copy2(sp, dp)
            except OSError as e:
                items.append({"category": category, "file": fn, "source": sp,
                              "copied": False, "error": str(e)})
                continue
            items.append({
                "category": category,
                "file": fn,
                "source": sp,
                "archived": "artifacts/%s/%s" % (category, fn),
                "bytes": os.path.getsize(dp),
                "sha256": sha256(dp),
            })
    return items


def external_index() -> dict:
    out = {}
    for key, path in EXTERNAL_SOURCES.items():
        entry = {"path": path, "exists": os.path.isdir(path)}
        if entry["exists"]:
            try:
                names = os.listdir(path)
            except OSError:
                names = []
            entry["file_count"] = len(names)
            entry["latest"] = sorted(names)[-5:]
        out[key] = entry
    return out


def write_manifest(outdir: str, results: list, items: list, ts: str, note: str = "") -> str:
    manifest = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": ts,
        "note": note,
        "workspace": HERE,
        "checks": results,
        "checks_total": len(results),
        "checks_failed": sum(1 for r in results if r["exit_code"] != 0),
        "artifacts": items,
        "artifacts_total": len(items),
        "external": external_index(),
    }
    path = os.path.join(outdir, "manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return path


def main() -> int:
    args = sys.argv[1:]
    only = None
    if "--only" in args:
        only = args[args.index("--only") + 1]
    window_min = 180
    if "--window-min" in args:
        window_min = int(args[args.index("--window-min") + 1])
    archive_only = "--archive-only" in args

    ts = time.strftime("%Y%m%d_%H%M%S")
    outdir = os.path.join(OUT_ROOT, ts)
    os.makedirs(os.path.join(outdir, "checks"), exist_ok=True)

    results = []
    if not archive_only:
        names = list(OFFLINE)
        if "--e2e" in args:
            names += ONLINE
        if only:
            cand = list(OFFLINE) + list(ONLINE)
            names = [n for n in cand if only in n]
        if not names:
            print("没有匹配到要跑的脚本:", only)
            return 2
        for n in names:
            print(f"[run] {n} ...", flush=True)
            r = run_one(n, os.path.join(outdir, "checks"))
            results.append(r)
            print(f"      exit={r['exit_code']} pass={r['passed']} fail={r['failed']} "
                  f"({r['seconds']}s) -> {r['log_file']}", flush=True)
        with open(os.path.join(outdir, "summary.json"), "w", encoding="utf-8") as f:
            json.dump({"timestamp": ts, "results": results}, f, ensure_ascii=False, indent=2)

    since = time.time() - window_min * 60
    print(f"\n[archive] 收集最近 {window_min} 分钟内的运行产物 ...", flush=True)
    items = collect_artifacts(outdir, since)
    mpath = write_manifest(outdir, results, items, ts,
                           note="archive-only" if archive_only else "checks+archive")
    cats = {}
    for it in items:
        cats[it["category"]] = cats.get(it["category"], 0) + 1

    print("\n==== 汇总 ====")
    for r in results:
        status = "OK " if r["exit_code"] == 0 else "ERR"
        print(f"{status} {r['name']:<34} exit={r['exit_code']:<3} pass={r['passed']} fail={r['failed']}")
    print("归档产物:", ", ".join(f"{k}={v}" for k, v in sorted(cats.items())) or "(无)")
    print(f"\n落盘目录: {outdir}")
    print(f"总索引:   {mpath}")
    return 0 if all(r["exit_code"] == 0 for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
