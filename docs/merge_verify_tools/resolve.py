#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按「逐冲突块规则」解掉 6 处冲突，并对结果做结构性校验。

规则表（每条都基于已核实的冲突内容）：
  model_configs.json        -> JSON 结构化并集（导师条目在前，用户独有条目随后）
  base_service.py   #1      -> union（你加 @_timed_request("DELETE")，导师加 handle_streaming_request，互不相干）
  HermesChatPanel   #1      -> union（你加 useModelStore import，导师加 3 个 lib import + 两个工具函数）
  HermesChatPanel   #2      -> union（你删掉硬编码默认模型，导师加 ApprovalRequest 接口）
  HermesChatPanel   #3      -> OURS（同一处模型加载逻辑的二选一，你已改对，取你的）
  CLAUDE.md         #1      -> OURS（导师那行 python 路径是他人机器路径，已核实本机不存在）
"""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

TMP = os.path.join(tempfile.gettempdir(), "qmclaw-3way")
OUT = os.path.join(TMP, "resolved")

RULES = {
    "Agentic Workflow/qmclaw-server/config/model_configs.json": "json_union",
    "Agentic Workflow/qmclaw-server/services/base/base_service.py": ["union"],
    "Agentic Workflow/qmclaw-web/src/components/HermesChatPanel.tsx": ["union", "union", "ours"],
    "CLAUDE.md": ["ours"],
}

TARGETS = list(RULES.keys())


def key(rel):
    return os.path.join(TMP, rel.replace("/", "__").replace(" ", "_"))


def read(p):
    with open(p, "rb") as fh:
        return fh.read().decode("utf-8")


def split_conflicts(text):
    """返回 (chunks) —— chunk 为 ('text', str) 或 ('conflict', ours_lines, theirs_lines)"""
    lines = text.split("\n")
    chunks, i = [], 0
    while i < len(lines):
        if lines[i].startswith("<<<<<<<"):
            i += 1
            ours, theirs = [], []
            while i < len(lines) and not lines[i].startswith("|||||||") and not lines[i].startswith("======="):
                ours.append(lines[i]); i += 1
            if i < len(lines) and lines[i].startswith("|||||||"):
                while i < len(lines) and not lines[i].startswith("======="):
                    i += 1
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


def resolve_by_rules(merged, rules):
    chunks = split_conflicts(merged)
    nconf = sum(1 for c in chunks if c[0] == "conflict")
    chosen = []
    out = []
    ci = 0
    for c in chunks:
        if c[0] == "text":
            out.append(c[1])
        else:
            rule = rules[ci] if ci < len(rules) else "union"
            ci += 1
            if rule == "ours":
                out.extend(c[1])
            elif rule == "theirs":
                out.extend(c[2])
            else:
                out.extend(c[1]); out.extend(c[2])
            chosen.append(rule)
    return "\n".join(out), nconf, chosen


def check(rel, text):
    notes, ok = [], True
    if rel.endswith(".json"):
        try:
            data = json.loads(text)
        except Exception as e:
            return False, ["JSON 解析失败: %s" % e]
        notes.append("JSON 可解析 OK")
        ids = [m.get("id") for m in data.get("models", [])]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        notes.append("models=%d" % len(ids))
        if dupes:
            ok = False; notes.append("id 重复: %s" % dupes)
        for must in ("deepseek-flash", "model_default_006"):
            present = must in ids
            notes.append("%s=%s" % (must, "在" if present else "缺失!"))
            if not present:
                ok = False
    elif rel.endswith(".py"):
        try:
            ast.parse(text)
            notes.append("Python ast.parse OK")
        except SyntaxError as e:
            return False, ["Python 语法 line %s: %s" % (e.lineno, e.msg)]
        for must in ("ThreadingHTTPServer", "handle_streaming_request", "_timed_request"):
            present = must in text
            notes.append("%s=%s" % (must, "在" if present else "缺失!"))
            if not present:
                ok = False
    elif rel.endswith(".tsx"):
        if text.count("{") != text.count("}"):
            ok = False
            notes.append("花括号不平衡 {=%d }=%d" % (text.count("{"), text.count("}")))
        else:
            notes.append("花括号平衡 {=%d" % text.count("{"))
        for must in ("useModelStore", "MarkdownContent", "ReasoningBlock", "ToolCallsList", "ApprovalRequest"):
            present = must in text
            notes.append("%s=%s" % (must, "在" if present else "缺失!"))
            if not present:
                ok = False
    else:
        if "<<<<<<<" in text or ">>>>>>>" in text:
            return False, ["残留冲突标记"]
        notes.append("无冲突标记 OK")
        # 约定 10 必须是本机路径，且不得再出现他人机器路径
        if "D:\\semester4\\deploy\\qmclaw\\qmclaw\\python.exe" in text:
            notes.append("约定10=本机路径 OK")
        else:
            ok = False; notes.append("约定10 本机路径缺失!")
        # 他人机器路径只允许出现在「解释为何不用它」的那一行；不得出现在规则正文
        foreign = [l for l in text.split("\n") if "C:\\Users\\HUAWEI" in l]
        rule_lines = [l for l in foreign if not l.strip().startswith("-")]
        notes.append("他人机器路径出现 %d 次（其中规则正文 %d 次）" % (len(foreign), len(rule_lines)))
        if rule_lines:
            ok = False; notes.append("规则正文残留他人机器路径: %s" % rule_lines[0].strip()[:60])
    return ok, notes


def main():
    os.makedirs(OUT, exist_ok=True)
    allok = True
    for rel in TARGETS:
        rules = RULES[rel]
        merged = read(key(rel) + ".merged") if os.path.exists(key(rel) + ".merged") else None
        if merged is None:
            p = subprocess.run(["git", "merge-file", "-p", "--diff3",
                                key(rel) + ".o", key(rel) + ".b", key(rel) + ".t"],
                               capture_output=True)
            merged = p.stdout.decode("utf-8")
            with open(key(rel) + ".merged", "w", encoding="utf-8", newline="\n") as fh:
                fh.write(merged)

        if rules == "json_union":
            theirs = json.loads(read(key(rel) + ".t"))
            ours = json.loads(read(key(rel) + ".o"))
            tids = {m["id"] for m in theirs["models"]}
            out_obj = dict(theirs)
            out_obj["models"] = list(theirs["models"]) + [m for m in ours["models"] if m["id"] not in tids]
            resolved = json.dumps(out_obj, ensure_ascii=False, indent=2) + "\n"
            desc = "JSON 结构化并集"
            nconf, chosen = 1, ["json_union"]
        else:
            resolved, nconf, chosen = resolve_by_rules(merged, rules)
            desc = "逐块规则 " + str(chosen)

        dst = os.path.join(OUT, rel.replace("/", "__").replace(" ", "_"))
        with open(dst, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(resolved)
        ok, notes = check(rel, resolved)
        allok = allok and ok
        print("=" * 92)
        print(rel)
        print("  冲突块=%d   %s   结果=%s   %d 字节" % (nconf, desc, "PASS" if ok else "FAIL", len(resolved.encode())))
        for n in notes:
            print("    - %s" % n)
    print("=" * 92)
    print("ALL RESOLVED: %s" % ("PASS" if allok else "FAIL"))
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
