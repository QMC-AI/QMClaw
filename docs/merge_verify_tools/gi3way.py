#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""定论 .gitignore 的三方合并到底是 CLEAN 还是 CONFLICT。

三个输入全部从 git 对象库取，避免工作树/换行/编码干扰：
  ours   = 用户 HEAD 的 .gitignore（603B，已含 config/memory/ 与删除 CLAUDE.md 行）
  base   = f2d83b5（fork 点）
  theirs = 上游 main
再分别用 git merge-file 与 git merge-tree 两条路径判定。
"""
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

USER = r"D:\semester4\QMClaw"
MIR = os.path.join(USER, ".upstream-mirror")
TMP = os.path.join(USER, ".merge-scratch", "gi3")
REL = ".gitignore"


def blob(repo, rev):
    p = subprocess.run(["git", "-C", repo, "cat-file", "blob", "%s:%s" % (rev, REL)], capture_output=True)
    assert p.returncode == 0, (repo, rev, p.stderr)
    return p.stdout


os.makedirs(TMP, exist_ok=True)
ours, base, theirs = blob(USER, "HEAD"), blob(USER, "f2d83b5"), blob(MIR, "main")
mine_wt = open(os.path.join(USER, REL), "rb").read()   # 当前工作树（含未提交的 3 条）

print("输入尺寸: ours(HEAD)=%d  base=%d  theirs=%d  mine(工作树)=%d"
      % (len(ours), len(base), len(theirs), len(mine_wt)))
print("含 CRLF? ours=%s theirs=%s" % (b"\r\n" in ours, b"\r\n" in theirs))

paths = {}
for name, data in (("ours", ours), ("base", base), ("theirs", theirs), ("mine", mine_wt)):
    p = os.path.join(TMP, name)
    with open(p, "wb") as fh:
        fh.write(data)
    paths[name] = p


def merge3(a, b, c, label):
    p = subprocess.run(["git", "merge-file", "-p", a, b, c], capture_output=True)
    out = p.stdout.decode("utf-8", "replace")
    n = out.count("<<<<<<<")
    print("  %-42s exit=%d 冲突块=%d" % (label, p.returncode, n))
    return p.returncode, n, out


print("\n=== 场景 A：第 0 步把 HEAD 版提交后合并（ours=HEAD, base=fork点, theirs=main）===")
rc_a, n_a, out_a = merge3(paths["ours"], paths["base"], paths["theirs"], "merge-file(HEAD, base, main)")

print("\n=== 场景 B：第 0 步把我加过 3 条的工作树版提交后合并 ===")
rc_b, n_b, out_b = merge3(paths["mine"], paths["base"], paths["theirs"], "merge-file(worktree, base, main)")

for tag, out in (("A", out_a), ("B", out_b)):
    if "<<" in out:
        print("\n--- 场景 %s 的冲突块 ---" % tag)
        lines = out.split("\n")
        i = 0
        while i < len(lines):
            if lines[i].startswith("<<<<<<<"):
                end = i
                while end < len(lines) and not lines[end].startswith(">>>>>>>"):
                    end += 1
                for k in range(i, min(end + 1, i + 30)):
                    print("    " + lines[k])
                break
            i += 1

print("\n=== 结论 ===")
print("场景 A: %s" % ("CLEAN 自动合并" if rc_a == 0 else "CONFLICT %d 处" % n_a))
print("场景 B: %s" % ("CLEAN 自动合并" if rc_b == 0 else "CONFLICT %d 处" % n_b))

# 与真实 merge 对照：探针仓库里的冲突清单有没有 .gitignore
repo = os.path.join(USER, ".merge-scratch", "syncprobe", "repo")
if os.path.exists(repo):
    p = subprocess.run(["git", "-C", repo, "diff", "--name-only", "--diff-filter=U"], capture_output=True)
    print("\n探针真实 merge 的冲突清单: %s" % (p.stdout.decode().strip().split("\n")))
