#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修正版冲突解析器 —— 落实独立复核的 6 条 P0 修改。

与原 resolve.py 的差别（每条都对应一个被证实的语义错误）：
  model_configs.json  -> 按 (provider, modelId) 语义去重；保留我方 id；deepseek-flash 置首；不整文件重排
  base_service.py     -> 导师方法插到 @_timed_request("DELETE") 之上（原 union 把装饰器挂到了 SSE 方法）
  HermesChatPanel #2  -> 保留 interface ApprovalRequest，丢弃导师那行 DEFAULT_HERMES_MODEL
  CLAUDE.md           -> 保留我方约定 10/11 + 追加导师 Compaction 段，只丢重复的 python 路径行
校验器升级为语义断言：
  - 装饰器的 AST 目标必须是 do_DELETE
  - 无 (provider, modelId) 重复；默认模型第一条 == deepseek-flash
  - DEFAULT_HERMES_MODEL 必须不存在
  - Compaction 段必须存在
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
OUT = os.path.join(TMP, "resolved2")

FILES = {
    "Agentic Workflow/qmclaw-server/config/model_configs.json": "json_semantic",
    "Agentic Workflow/qmclaw-server/services/base/base_service.py": "bs_before",
    "Agentic Workflow/qmclaw-web/src/components/HermesChatPanel.tsx": "drop_default_model",
    "CLAUDE.md": "claude_union",
}


def key(rel):
    return os.path.join(TMP, rel.replace("/", "__").replace(" ", "_"))


def read(p):
    with open(p, "rb") as fh:
        return fh.read().decode("utf-8")


def split_conflicts(text):
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


def merged_with_markers(rel):
    p = subprocess.run(["git", "merge-file", "-p", "--diff3",
                        key(rel) + ".o", key(rel) + ".b", key(rel) + ".t"], capture_output=True)
    return p.stdout.decode("utf-8")


# ---------- 1) model_configs.json：按 (provider, modelId) 语义去重 ----------
def fix_model_configs(rel):
    ours = json.loads(read(key(rel) + ".o"))
    theirs = json.loads(read(key(rel) + ".t"))
    ours_by_key = {(m.get("provider"), m.get("modelId")): m for m in ours["models"]}

    out_models = []
    seen = set()
    for m in theirs["models"]:
        k = (m.get("provider"), m.get("modelId"))
        seen.add(k)
        if k in ours_by_key:
            # 同一逻辑模型两边都有：采纳导师字段，保留我方 id（前端/DB 引用的是旧 id）
            merged = dict(m)
            merged["id"] = ours_by_key[k]["id"]
            out_models.append(merged)
        else:
            out_models.append(m)
    # 我方独有（含 deepseek-flash）
    for m in ours["models"]:
        k = (m.get("provider"), m.get("modelId"))
        if k not in seen:
            out_models.append(m)

    # deepseek-flash 置首，维持我方验收行为（两个面板默认取第一条 enabled+text）
    flash = [m for m in out_models if m.get("id") == "deepseek-flash"]
    rest = [m for m in out_models if m.get("id") != "deepseek-flash"]
    out_models = flash + rest

    obj = dict(theirs)
    obj["models"] = out_models
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


# ---------- 2) base_service.py：导师方法插到装饰器之上 ----------
def fix_base_service(rel):
    text = merged_with_markers(rel)
    chunks = split_conflicts(text)
    out = []
    for c in chunks:
        if c[0] == "text":
            out.append(c[1])
            continue
        ours, theirs = c[1], c[2]
        # theirs 在前（新增方法），ours 的装饰器紧随其后 -> 装饰器重新贴到 do_DELETE 上
        out.extend(theirs + ours)
    return "\n".join(out)


# ---------- 3) HermesChatPanel：冲突1/2 union 但丢弃 DEFAULT_HERMES_MODEL 声明行 ----------
def fix_hermes_panel(rel):
    text = merged_with_markers(rel)
    chunks = split_conflicts(text)
    out, ci = [], 0
    for c in chunks:
        if c[0] == "text":
            out.append(c[1])
            continue
        ours, theirs = c[1], c[2]
        ci += 1
        if ci == 3:
            out.extend(ours)          # 同一 useEffect 的二选一：取我方
        else:
            # 去掉导师侧的 DEFAULT_HERMES_MODEL 声明行（用户在 H3 特意删除的错误字面量）
            t = [l for l in theirs if "DEFAULT_HERMES_MODEL" not in l]
            out.extend(ours + t)
    return "\n".join(out)


# ---------- 4) CLAUDE.md：保留我方约定 10/11 + 追加导师 Compaction 段 ----------
def fix_claude(rel):
    text = merged_with_markers(rel)
    chunks = split_conflicts(text)
    out = []
    for c in chunks:
        if c[0] == "text":
            out.append(c[1])
            continue
        ours, theirs = c[1], c[2]
        # 导师侧若含重复的 python 路径行则丢弃；其余（Compaction 段）全部保留
        t = [l for l in theirs if "python.exe" not in l]
        out.extend(ours + t)
    return "\n".join(out)


# ---------- 校验：语义断言，不是子串存在 ----------
def check_model_cfg(text):
    notes, ok = [], True
    data = json.loads(text)
    ms = data["models"]
    keys = [(m.get("provider"), m.get("modelId")) for m in ms]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    notes.append("models=%d" % len(ms))
    if dupes:
        ok = False; notes.append("(provider,modelId) 重复: %s" % dupes)
    else:
        notes.append("无 (provider,modelId) 重复 OK")
    ids = [m["id"] for m in ms]
    idd = sorted({i for i in ids if ids.count(i) > 1})
    if idd:
        ok = False; notes.append("id 重复: %s" % idd)
    enabled_text = [m for m in ms if m.get("enabled") and "text" in (m.get("capabilities") or [])]
    first = enabled_text[0]["id"] if enabled_text else None
    notes.append("第一条 enabled+text = %s" % first)
    if first != "deepseek-flash":
        ok = False; notes.append("默认模型不是 deepseek-flash（推翻 H3 验收）")
    for must in ("deepseek-flash",):
        if must not in ids:
            ok = False; notes.append("%s 丢失" % must)
    return ok, notes


def check_base_service(text):
    notes, ok = [], True
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return False, ["Python 语法 line %s: %s" % (e.lineno, e.msg)]
    notes.append("ast.parse OK")
    targets = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for d in node.decorator_list:
                s = ast.unparse(d)
                if "_timed_request" in s:
                    targets.setdefault(s, []).append(node.name)
    notes.append("装饰器绑定: %s" % targets)
    if "do_DELETE" not in targets.get("_timed_request('DELETE')", []):
        ok = False; notes.append("@_timed_request('DELETE') 没有绑定到 do_DELETE")
    else:
        notes.append("@_timed_request('DELETE') -> do_DELETE OK")
    if "handle_streaming_request" in text:
        notes.append("导师的 handle_streaming_request 在")
    else:
        ok = False; notes.append("导师的 handle_streaming_request 丢失")
    return ok, notes


def check_panel(text):
    notes, ok = [], True
    if "DEFAULT_HERMES_MODEL" in text:
        ok = False; notes.append("DEFAULT_HERMES_MODEL 残留（H3 要求删除）")
    else:
        notes.append("DEFAULT_HERMES_MODEL 已不存在 OK")
    for must in ("useModelStore", "MarkdownContent", "ReasoningBlock", "ToolCallsList", "ApprovalRequest"):
        if must not in text:
            ok = False; notes.append("%s 丢失" % must)
    if text.count("{") != text.count("}"):
        ok = False; notes.append("花括号不平衡 {=%d }=%d" % (text.count("{"), text.count("}")))
    else:
        notes.append("花括号平衡 {=%d OK" % text.count("{"))
    notes.append("关键标识符均存在")
    return ok, notes


def check_claude(text):
    notes, ok = [], True
    if "Compaction" not in text:
        ok = False; notes.append("导师的 Compaction 段丢失")
    else:
        notes.append("Compaction 段在 OK")
    for must in ("D:\\semester4\\deploy\\qmclaw\\qmclaw\\python.exe", "按节奏提交"):
        if must not in text:
            ok = False; notes.append("我方 %s 丢失" % must[:30])
    notes.append("我方约定 10/11 在")
    if "<<<<<<<" in text or ">>>>>>>" in text:
        ok = False; notes.append("残留冲突标记")
    return ok, notes


HANDLERS = {
    "json_semantic": (fix_model_configs, check_model_cfg),
    "bs_before": (fix_base_service, check_base_service),
    "drop_default_model": (fix_hermes_panel, check_panel),
    "claude_union": (fix_claude, check_claude),
}


def main():
    os.makedirs(OUT, exist_ok=True)
    allok = True
    for rel, rule in FILES.items():
        fix, chk = HANDLERS[rule]
        resolved = fix(rel)
        dst = os.path.join(OUT, rel.replace("/", "__").replace(" ", "_"))
        with open(dst, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(resolved)
        ok, notes = chk(resolved)
        allok = allok and ok
        print("=" * 92)
        print("%s\n  规则=%s   结果=%s   %d 字节" % (rel, rule, "PASS" if ok else "FAIL", len(resolved.encode())))
        for n in notes:
            print("    - %s" % n)
    print("=" * 92)
    print("修正版规则: %s" % ("全部 PASS" if allok else "仍有 FAIL"))
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
