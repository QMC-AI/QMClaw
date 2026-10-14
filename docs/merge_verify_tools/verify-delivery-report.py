#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""核验交付报告里的事实主张，对着 git 数据逐条检查。

用法:
    python verify-delivery-report.py [仓库路径]     # 默认当前目录
"""
import io
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.getcwd()
print("被核验仓库: %s" % ROOT)


def git(*a):
    p = subprocess.run(["git", "-C", ROOT] + list(a), capture_output=True)
    return p.returncode, p.stdout.decode("utf-8", "replace").strip()


results = []


def check(label, measured, expect, ok=None):
    if ok is None:
        ok = str(measured) == str(expect)
    results.append((label, ok))
    print("[%s] %-46s 实测=%s  期望=%s" % ("PASS" if ok else "FAIL", label, measured, expect))


def report_says(snippet):
    """断言交付报告里确实写了这句话（而不是把状态写死在脚本里）。"""
    try:
        txt = io.open(os.path.join(ROOT, "docs/UPSTREAM_MERGE_DELIVERY.md"), encoding="utf-8").read()
    except OSError:
        return False
    return snippet in txt


print("=" * 100)
print("核验 docs/UPSTREAM_MERGE_DELIVERY.md 的事实主张")
print("=" * 100)
print("注：HEAD 后续会继续增长，所以 HEAD/提交数不写死 —— 改为核验【报告里写的那个 HEAD】")
print("    在当前仓库里真实存在（用 -1 参数可核验到某个历史快照）。")

# 报告里声称的 HEAD 必须真实存在（而不是断言它等于当前 HEAD）
head_in_report = os.environ.get("REPORT_HEAD", "c1bfd44")
rc, _ = git("cat-file", "-e", head_in_report + "^{commit}")
check("报告所述 HEAD 在仓库中存在", "是" if rc == 0 else "否", "是")

# 归档脚本数：用「至少」而不是「等于」，因为后续会继续加脚本
n_scripts_now = len([f for f in os.listdir(os.path.join(ROOT, "docs/merge_verify_tools"))
                     if f.endswith((".py", ".ps1"))])
check("归档脚本数 >= 报告所述 13", n_scripts_now, ">=13",
      ok=n_scripts_now >= 13)

# 锚点
check("fork 点", git("rev-parse", "--short", "f2d83b5")[1], "f2d83b5")
check("上游 HEAD", git("rev-parse", "--short", "03d838d")[1], "03d838d")
check("合并提交", git("rev-parse", "--short", "bf725e9")[1], "bf725e9")
check("合并前 HEAD", git("rev-parse", "--short", "3d646c9")[1], "3d646c9")
check("当前 HEAD 是报告所述 HEAD 的后代", "是" if git("merge-base", "--is-ancestor", head_in_report, "HEAD")[0] == 0 else "否", "是")

# 合并提交的两个父
parents = git("log", "-1", "--pretty=%P", "bf725e9")[1].split()
check("合并第一父", parents[0][:7], "3d646c9")
check("合并第二父", parents[1][:7], "03d838d")

# 我方提交零改写
rc, _ = git("merge-base", "--is-ancestor", "3d646c9", "HEAD")
check("3d646c9 仍是 HEAD 祖先（历史未改写）", "是" if rc == 0 else "否", "是")

# 规模
_, s1 = git("diff", "--shortstat", "f2d83b5", "3d646c9")
check("我方 62 文件 +7726 -347", s1, "62 files changed, 7726 insertions(+), 347 deletions(-)")

_, s2 = git("diff", "--shortstat", "3d646c9", "bf725e9")
check("合并 41 文件 +12400 -357", s2, "41 files changed, 12400 insertions(+), 357 deletions(-)")

# 合并文件分类
_, ns = git("diff", "--name-status", "3d646c9", "bf725e9")
cnt = {"A": 0, "M": 0, "R": 0}
for line in ns.split("\n"):
    if line.strip():
        cnt[line[0]] = cnt.get(line[0], 0) + 1
check("合并分类 A/M/R", "A=%d M=%d R=%d" % (cnt["A"], cnt["M"], cnt["R"]), "A=18 M=19 R=4")

# 4 个冲突文件
_, u = git("diff", "--name-only", "3d646c9", "bf725e9", "--",
           "Agentic Workflow/qmclaw-server/config/model_configs.json",
           "Agentic Workflow/qmclaw-server/services/base/base_service.py",
           "Agentic Workflow/qmclaw-web/src/components/HermesChatPanel.tsx", "CLAUDE.md")
check("4 个冲突文件确在合并 diff 中", len([x for x in u.split("\n") if x.strip()]), 4)

# 本会话提交数（3d646c9 之后）：报告写的是 8 个，但后续提交会继续增加 —— 用「至少」
rc, n = git("rev-list", "--count", "bf725e9..HEAD")
check("合并之后的提交数 >= 报告所述 8", n, ">=8", ok=int(n) >= 8)

# 关键文件实测
root = ROOT

# 模型配置
d = json.loads(io.open(os.path.join(root, "Agentic Workflow/qmclaw-server/config/model_configs.json"),
                       encoding="utf-8").read())
ms = d["models"]
srt = sorted(ms, key=lambda m: m.get("updatedAt", ""), reverse=True)
et = [m for m in srt if m.get("enabled") and "text" in (m.get("capabilities") or [])]
check("模型条数", len(ms), 10)
check("按 updatedAt 倒序首条", et[0]["id"], "deepseek-flash")
mini = [m for m in ms if m.get("modelId") == "MiniMax-M2.7"][0]
check("MiniMax-M2.7 baseUrl 保留导师改动", mini.get("baseUrl"), "https://api.minimaxi.com/anthropic")

# 装饰器
import ast
bs = io.open(os.path.join(root, "Agentic Workflow/qmclaw-server/services/base/base_service.py"),
             encoding="utf-8").read()
binds = {}
for node in ast.walk(ast.parse(bs)):
    if isinstance(node, ast.FunctionDef):
        for dec in node.decorator_list:
            s = ast.unparse(dec)
            if "_timed_request" in s:
                binds.setdefault(s, []).append(node.name)
check("装饰器 DELETE 绑定", binds.get("_timed_request('DELETE')"), ["do_DELETE"])
check("导师 SSE 方法在位", "handle_streaming_request" in bs, True)

# 前端面板
hp = io.open(os.path.join(root, "Agentic Workflow/qmclaw-web/src/components/HermesChatPanel.tsx"),
             encoding="utf-8").read()
check("HermesChatPanel 花括号平衡", hp.count("{") == hp.count("}"), True)
check("DEFAULT_HERMES_MODEL 已无残留", "DEFAULT_HERMES_MODEL" in hp, False)
check("useModelStore 保留", "useModelStore" in hp, True)

# CLAUDE.md
cd = io.open(os.path.join(root, "CLAUDE.md"), encoding="utf-8").read()
check("CLAUDE.md 含导师 Compaction 段", "Compaction" in cd, True)
check("CLAUDE.md 含我方本机 python 路径", r"D:\semester4\deploy\qmclaw\qmclaw\python.exe" in cd, True)

# 技能联接
import pathlib
sk = pathlib.Path(root) / "Agentic Workflow/qmclaw-server/data/skills"
n_skill = len(list(sk.rglob("SKILL.md"))) if sk.exists() else 0
check("技能目录联接可扫到 SKILL.md 数", n_skill, 7)

# 归档说明文档数：同样用「至少」
tools = os.path.join(root, "docs/merge_verify_tools")
n_md = len([f for f in os.listdir(tools) if f.endswith(".md")])
check("归档说明文档数 >= 报告所述 3", n_md, ">=3", ok=n_md >= 3)

# 回退分支存在
_, br = git("branch", "--list", "backup/pre-upstream-merge-20261008-202936")
check("回退分支存在", "backup/pre-upstream-merge-20261008-202936" in br, True)

print("=" * 100)
fails = [l for l, ok in results if not ok]
print("核验 %d 条，失败 %d 条" % (len(results), len(fails)))
if fails:
    print("失败项:")
    for f in fails:
        print("   -", f)
sys.exit(1 if fails else 0)
