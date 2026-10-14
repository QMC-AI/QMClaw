#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""可信的三方合并预演 + 冲突解析验证。

不经过 cmd 重定向：用 git cat-file 取 blob（stdout=内容, stderr=元数据），
逐文件校验 git 报告的 blob 大小与取到的字节数一致，再跑 git merge-file。
"""
import ast
import json
import os
import subprocess
import sys
import tempfile

USER_REPO = r"D:\semester4\QMClaw"
MIRROR = os.path.join(USER_REPO, ".upstream-mirror")
BASE_REV = "f2d83b5"      # 用户仓库里的 fork 点
THEIRS_REV = "main"       # 上游镜像的 main
OURS_REV = "HEAD"

FILES = [
    ".claude/progress.md",
    ".gitignore",
    "Agentic Workflow/qmclaw-server/config/model_configs.json",
    "Agentic Workflow/qmclaw-server/scripts/hermes_runner.py",
    "Agentic Workflow/qmclaw-server/scripts/start_all_services.py",
    "Agentic Workflow/qmclaw-server/services/analysis_service/server.py",
    "Agentic Workflow/qmclaw-server/services/base/base_service.py",
    "Agentic Workflow/qmclaw-server/services/hermes_service/server.py",
    "Agentic Workflow/qmclaw-server/src/index.ts",
    "Agentic Workflow/qmclaw-web/src/components/HermesChatPanel.tsx",
    "CLAUDE.md",
]

GIT = ["git"]
TMP = os.path.join(tempfile.gettempdir(), "qmclaw-3way")


def blob_size(repo, rev, rel):
    p = subprocess.run(GIT + ["-C", repo, "cat-file", "-s", "%s:%s" % (rev, rel)],
                       capture_output=True)
    if p.returncode != 0:
        return None
    return int(p.stdout.decode("utf-8", "replace").strip())


def blob(repo, rev, rel):
    """返回 blob 原始字节；校验与 cat-file -s 报的大小一致。"""
    expected = blob_size(repo, rev, rel)
    if expected is None:
        return None
    p = subprocess.run(GIT + ["-C", repo, "cat-file", "blob", "%s:%s" % (rev, rel)],
                       capture_output=True)
    if p.returncode != 0:
        return None
    data = p.stdout
    if len(data) != expected:
        raise RuntimeError("size mismatch %s@%s: git=%d got=%d" % (rel, rev, expected, len(data)))
    return data


def main():
    os.makedirs(TMP, exist_ok=True)
    results = []
    for rel in FILES:
        o = blob(USER_REPO, OURS_REV, rel)
        b = blob(USER_REPO, BASE_REV, rel)
        t = blob(MIRROR, THEIRS_REV, rel)
        if o is None or b is None or t is None:
            results.append({"path": rel, "verdict": "MISSING",
                            "ours": o is not None, "base": b is not None, "theirs": t is not None})
            continue

        if o == t:
            results.append({"path": rel, "verdict": "IDENTICAL", "conflicts": 0,
                            "ours_bytes": len(o), "base_bytes": len(b), "theirs_bytes": len(t)})
            continue
        if b == t:
            results.append({"path": rel, "verdict": "OURS_ONLY", "conflicts": 0,
                            "ours_bytes": len(o), "base_bytes": len(b), "theirs_bytes": len(t)})
            continue
        if b == o:
            results.append({"path": rel, "verdict": "THEIRS_ONLY", "conflicts": 0,
                            "ours_bytes": len(o), "base_bytes": len(b), "theirs_bytes": len(t)})
            continue

        d = os.path.join(TMP, rel.replace("/", "__").replace(" ", "_"))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        fo, fb, ft = d + ".o", d + ".b", d + ".t"
        for path, data in ((fo, o), (fb, b), (ft, t)):
            with open(path, "wb") as fh:
                fh.write(data)

        p = subprocess.run(GIT + ["merge-file", "-p", "--diff3", fo, fb, ft],
                           capture_output=True)
        merged = p.stdout
        conflict_count = merged.count(b"<<<<<<<")
        verdict = "CLEAN" if p.returncode == 0 else ("CONFLICT" if conflict_count else "RC_%d" % p.returncode)
        results.append({"path": rel, "verdict": verdict, "conflicts": conflict_count,
                        "rc": p.returncode,
                        "ours_bytes": len(o), "base_bytes": len(b), "theirs_bytes": len(t),
                        "merged_bytes": len(merged)})

    print("%-66s %-12s %5s  %8s %8s %8s" % ("path", "verdict", "conf", "ours", "base", "theirs"))
    print("-" * 116)
    for r in sorted(results, key=lambda x: (x["verdict"], x["path"])):
        if r["verdict"] == "MISSING":
            print("%-66s %-12s   ours=%s base=%s theirs=%s" % (r["path"], r["verdict"], r["ours"], r["base"], r["theirs"]))
        else:
            print("%-66s %-12s %5s  %8s %8s %8s" % (r["path"], r["verdict"], r.get("conflicts", 0),
                                                    r["ours_bytes"], r["base_bytes"], r["theirs_bytes"]))
    bad = [r for r in results if r["verdict"] == "MISSING"]
    print("\nMISSING inputs: %d" % len(bad))
    print("temp dir: %s" % TMP)
    return 0


if __name__ == "__main__":
    sys.exit(main())
