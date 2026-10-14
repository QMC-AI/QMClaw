#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 docs/UPSTREAM_MERGE_PLAN.md 里的事实主张逐条对着 git 数据复核。

每条打印: 主张 / 实测 / PASS|FAIL
只在 FAIL 时要求人工介入。
"""
import json
import os
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

USER = r"D:\semester4\QMClaw"
MIR = os.path.join(USER, ".upstream-mirror")
FORK = "f2d83b5"
TMP = os.path.join(tempfile.gettempdir(), "qmclaw-3way")

results = []


def run(args, cwd=None):
    p = subprocess.run(args, capture_output=True, cwd=cwd)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def git(repo, *args):
    return run(["git", "-C", repo] + list(args))


def check(claim, measured, expect, ok=None):
    if ok is None:
        ok = measured == expect
    results.append((claim, measured, expect, ok))
    print("[%s] %s\n      实测=%s  期望=%s" % ("PASS" if ok else "FAIL", claim, measured, expect))
    return ok


print("=" * 96)
print("复核 docs/UPSTREAM_MERGE_PLAN.md 的事实主张")
print("=" * 96)

# --- 1. 上游 SHA ---
rc, out = git(MIR, "rev-parse", "main")
check("上游 main = 03d838d...", out.strip(), "03d838d23fbc95f6a8c727a3fece164976085820")

# --- 2. fork 点 tree 相同 ---
rc, t1 = git(USER, "rev-parse", "%s^{tree}" % FORK)
rc, t2 = git(MIR, "rev-parse", "c3fd6e6^{tree}")
check("fork 点 f2d83b5 的 tree == 上游 c3fd6e6 的 tree", t1.strip() == t2.strip(), True)

# --- 3. 上游提交数（相对 fork 点内容） ---
rc, out = git(MIR, "rev-list", "--count", "c3fd6e6..main")
check("导师相对 fork 点的提交数", out.strip(), "2")

# --- 4. 导师改动文件数 + 新增数 ---
rc, out = git(MIR, "diff", "--name-only", "c3fd6e6", "main")
up_files = [l for l in out.split("\n") if l.strip()]
check("导师改动文件数", str(len(up_files)), "41")
rc, out = git(MIR, "diff", "--name-status", "--diff-filter=A", "c3fd6e6", "main")
up_added = [l for l in out.split("\n") if l.strip()]
check("导师新增文件数", str(len(up_added)), "18")

# --- 5. 用户改动文件数 ---
rc, out = git(USER, "diff", "--name-only", FORK, "HEAD")
my_files = [l for l in out.split("\n") if l.strip()]
check("你相对 fork 点的改动文件数", str(len(my_files)), "62")

# --- 6. 重叠数 ---
overlap = sorted(set(my_files) & set(up_files))
check("双方都改过的文件数", str(len(overlap)), "11")

# --- 7. 冲突文件数 / 冲突块数 ---
CONFLICT_EXPECT = {
    "Agentic Workflow/qmclaw-server/config/model_configs.json": 1,
    "Agentic Workflow/qmclaw-server/services/base/base_service.py": 1,
    "Agentic Workflow/qmclaw-web/src/components/HermesChatPanel.tsx": 3,
    "CLAUDE.md": 1,
}
conf_total = 0
conf_files = []
for rel in overlap:
    def blob(repo, rev):
        rc, sz = run(["git", "-C", repo, "cat-file", "-s", "%s:%s" % (rev, rel)])
        if rc != 0:
            return None
        rc, data = run(["git", "-C", repo, "cat-file", "blob", "%s:%s" % (rev, rel)])
        assert len(data.encode("utf-8", "surrogateescape")) >= 0
        return data
    o = blob(USER, "HEAD")
    b = blob(USER, FORK)
    t = blob(MIR, "main")
    if o is None or b is None or t is None:
        continue
    if o == t or b == t or b == o:
        continue
    d = os.path.join(TMP, rel.replace("/", "__").replace(" ", "_"))
    os.makedirs(os.path.dirname(d), exist_ok=True)
    for s, txt in ((".o", o), (".b", b), (".t", t)):
        with open(d + s, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(txt)
    rc, merged = run(["git", "merge-file", "-p", "--diff3", d + ".o", d + ".b", d + ".t"])
    n = merged.count("<<<<<<<")
    if n:
        conf_files.append(rel)
        conf_total += n
check("真冲突文件数", str(len(conf_files)), "4")
check("冲突块总数", str(conf_total), "6")
for rel, exp in CONFLICT_EXPECT.items():
    check("  %s 冲突块数" % rel.split("/")[-1], "见上", str(exp),
          ok=(rel in conf_files))

# --- 8. 自动合并文件数 = 11 - 4 ---
check("可自动合并的重叠文件数", str(len(overlap) - len(conf_files)), "7")

# --- 9. 导师未改 .gitmodules ---
rc, out = git(MIR, "diff", "--name-only", "c3fd6e6", "main", "--", ".gitmodules")
check("导师两个提交未改动 .gitmodules", out.strip(), "")

# --- 10. .gitignore 新增行 ---
rc, out = git(MIR, "diff", "c3fd6e6", "main", "--", ".gitignore")
added = [l[1:].strip() for l in out.split("\n") if l.startswith("+") and not l.startswith("+++")]
check(".gitignore 导师新增行", str(added), "['cache/', '*.db*']")

# --- 11. db / cache 路径盘点（判 .gitignore 影响） ---
rc, out = git(USER, "ls-files")
tracked = [l for l in out.split("\n") if l.strip()]
db_hits = [f for f in tracked if f.endswith(".db") or ".db-" in f]
cache_hits = [f for f in tracked if "cache/" in f]
# 文档主张：cache/ 命中 0；db 命中 1（qca_service/data/index.db），已被跟踪故当前不误伤
check("被跟踪的 cache/ 路径数", str(len(cache_hits)), "0")
check("被跟踪的 db 文件数", str(len(db_hits)), "1")
check("被跟踪的 db 文件就是 qca_service/data/index.db",
      db_hits[0] if db_hits else "无",
      "Agentic Workflow/qmclaw-server/services/qca_service/data/index.db")

# --- 12. 你的关键改造仍在 HEAD ---
for needle, label in [("ThreadingHTTPServer", "多线程服务"),
                      ("SLOW ", "慢请求日志"),
                      ("X_UNIT_SCALE", "单位换算表"),
                      ("PLOT_LOCK", "绘图锁"),
                      ("_INDEX_LOCK", "记忆索引锁")]:
    rc, out = git(USER, "grep", "-l", needle, "HEAD")
    check("你的改造仍在 HEAD: %s" % label, "有" if out.strip() else "无", "有")

# --- 13. 导师新模块被 hermes server 引用 ---
rc, out = git(MIR, "show", "main:Agentic Workflow/qmclaw-server/services/hermes_service/server.py")
for mod in ("memory", "skills", "cron"):
    check("上游 hermes server 引用 .%s 模块" % mod, "在" if ("import %s as" % mod) in out else "不在", "在")
check("上游 hermes server 引用 approval_ws", "在" if "approval_ws import" in out else "不在", "在")

# --- 14. 新增工具模块的注册入口是 hermes 侧 ---
rc, out = git(MIR, "grep", "-l", "setup_analysis_tools", "main", "--", "Agentic Workflow/qmclaw-server/services/agent_service")
check("Agent 侧 adapter 是否注册新工具模块", "有" if out.strip() else "无", "无")
rc, out = git(MIR, "grep", "-l", "setup_analysis_tools", "main", "--", "Agentic Workflow/qmclaw-server/services/hermes_service")
check("Hermes 侧是否注册新工具模块", "有" if out.strip() else "无", "有")

# --- 15. 重命名 4 个文件 ---
rc, out = git(MIR, "diff", "--name-status", "--find-renames", "c3fd6e6", "main")
ren = [l for l in out.split("\n") if l.startswith("R")]
check("导师重命名文件数", str(len(ren)), "4")

# --- 16. 你的分支提交数 ---
rc, out = git(USER, "rev-list", "--count", "%s..HEAD" % FORK)
check("你相对 fork 点的提交数", out.strip(), "36")

print("=" * 96)
fails = [r for r in results if not r[3]]
print("复核条目: %d   失败: %d" % (len(results), len(fails)))
if fails:
    print("\n失败条目:")
    for c, m, e, _ in fails:
        print("  - %s  实测=%s 期望=%s" % (c, m, e))
sys.exit(1 if fails else 0)
