#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""在探针仓库的真实 merge 结果上，应用第五节【修正版】规则解冲突，并落地暂存。

与 resolve-fixed.py 的区别：输入是**真实 git merge** 产出的标准冲突标记
（<<<<<<< HEAD / ======= / >>>>>>> theirs），不是 git merge-file --diff3 输出。
这验证的是"规则能不能作用在真实冲突上"，而不只是作用在我的预判产物上。
"""
import ast
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

REPO = r"D:\semester4\QMClaw\.merge-scratch\syncprobe\repo"

CONFLICTED = [
    "Agentic Workflow/qmclaw-server/config/model_configs.json",
    "Agentic Workflow/qmclaw-server/services/base/base_service.py",
    "Agentic Workflow/qmclaw-web/src/components/HermesChatPanel.tsx",
    "CLAUDE.md",
]


def read(rel):
    with open(os.path.join(REPO, rel), "rb") as fh:
        return fh.read().decode("utf-8")


def write(rel, text):
    with open(os.path.join(REPO, rel), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def split(text):
    lines = text.split("\n")
    chunks, i = [], 0
    while i < len(lines):
        if lines[i].startswith("<<<<<<<"):
            i += 1
            ours, theirs = [], []
            while i < len(lines) and not lines[i].startswith("======="):
                ours.append(lines[i]); i += 1
            if i < len(lines) and lines[i].startswith("======="):
                i += 1
            while i < len(lines) and not lines[i].startswith(">>>>>>>"):
                theirs.append(lines[i]); i += 1
            if i < len(lines) and lines[i].startswith(">>>>>>>"):
                i += 1
            chunks.append(("conflict", ours, theirs))
        else:
            chunks.append(("text", lines[i])); i += 1
    return chunks


def n_conflicts(text):
    return text.count("<<<<<<<")


# ---------------- 规则实现 ----------------

def rule_model_configs(rel):
    """按 (provider, modelId) 语义去重；保留我方 id；deepseek-flash 置首。"""
    text = read(rel)
    ch = [c for c in split(text) if c[0] == "conflict"]
    assert len(ch) == 1, "期望 1 处冲突，实得 %d" % len(ch)
    ours_txt = "\n".join(ch[0][1])
    theirs_txt = "\n".join(ch[0][2])
    # 冲突块两侧都是"从数组开头到某条为止"的片段；直接解析两侧完整文件更稳
    ours = json.loads(read_after_merge_side(rel, "ours"))
    theirs = json.loads(read_after_merge_side(rel, "theirs"))
    ours_by_key = {(m.get("provider"), m.get("modelId")): m for m in ours["models"]}
    out_models, seen = [], set()
    for m in theirs["models"]:
        k = (m.get("provider"), m.get("modelId"))
        seen.add(k)
        if k in ours_by_key:
            mm = dict(m)
            mm["id"] = ours_by_key[k]["id"]   # 保留我方 id
            out_models.append(mm)
        else:
            out_models.append(m)
    for m in ours["models"]:
        k = (m.get("provider"), m.get("modelId"))
        if k not in seen:
            out_models.append(m)
    flash = [m for m in out_models if m.get("id") == "deepseek-flash"]
    rest = [m for m in out_models if m.get("id") != "deepseek-flash"]
    obj = dict(theirs)
    obj["models"] = flash + rest
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def read_after_merge_side(rel, side):
    """取该文件在 ours/theirs 两个提交里的原始内容（用于结构化处理）。"""
    ref = "ours" if side == "ours" else "theirs"
    p = subprocess.run(["git", "-C", REPO, "show", "%s:%s" % (ref, rel)], capture_output=True)
    return p.stdout.decode("utf-8")


def rule_base_service(rel):
    """theirs 在前、ours 在后：导师方法插到 @_timed_request("DELETE") 之上。"""
    text = read(rel)
    out = []
    for c in split(text):
        if c[0] == "text":
            out.append(c[1])
        else:
            out.extend(c[2]); out.extend(c[1])
    return "\n".join(out)


def rule_hermes_panel(rel):
    """冲突1 并集；冲突2 留 interface ApprovalRequest、丢 DEFAULT_HERMES_MODEL；冲突3 取 ours。"""
    text = read(rel)
    out, ci = [], 0
    for c in split(text):
        if c[0] == "text":
            out.append(c[1]); continue
        ci += 1
        ours, theirs = c[1], c[2]
        if ci == 3:
            out.extend(ours)
        else:
            out.extend(ours + [l for l in theirs if "DEFAULT_HERMES_MODEL" not in l])
    return "\n".join(out)


def rule_claude(rel):
    """并集：保留我方约定 10/11 + 追加导师 Compaction 段，只丢重复的 python 路径行。"""
    text = read(rel)
    out = []
    for c in split(text):
        if c[0] == "text":
            out.append(c[1]); continue
        out.extend(c[1] + [l for l in c[2] if "python.exe" not in l])
    return "\n".join(out)


RULES = {
    "Agentic Workflow/qmclaw-server/config/model_configs.json": rule_model_configs,
    "Agentic Workflow/qmclaw-server/services/base/base_service.py": rule_base_service,
    "Agentic Workflow/qmclaw-web/src/components/HermesChatPanel.tsx": rule_hermes_panel,
    "CLAUDE.md": rule_claude,
}


def main():
    print("=" * 92)
    print("在真实 merge 结果上应用修正版规则")
    print("=" * 92)
    ok_all = True
    for rel in CONFLICTED:
        before = read(rel)
        n_before = n_conflicts(before)
        resolved = RULES[rel](rel)
        n_after = n_conflicts(resolved)
        write(rel, resolved)
        # 无残留标记是硬要求
        good = n_after == 0
        ok_all = ok_all and good
        print("%-70s 冲突块 %d -> %d  %s" % (rel.split("/")[-1], n_before, n_after,
                                             "OK" if good else "!! 残留标记"))
    # 暂存
    p = subprocess.run(["git", "-C", REPO, "add"] + CONFLICTED, capture_output=True)
    print("\ngit add exit=%d" % p.returncode)
    p = subprocess.run(["git", "-C", REPO, "status", "--short"], capture_output=True)
    print("--- git status ---")
    print(p.stdout.decode("utf-8", "replace"))
    print("解冲突: %s" % ("PASS" if ok_all else "FAIL"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
