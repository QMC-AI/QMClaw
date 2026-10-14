#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""上游融合 —— 6 处冲突的修正版解法（可直接在真实 merge 后的仓库上运行）。

用法:
    python resolve-conflicts.py <仓库路径> [--write]

不带 --write 时只做**试算与校验**，不改任何文件（默认安全）。
带 --write 时才写回文件并 git add。

对应方案 docs/UPSTREAM_MERGE_PLAN.md 第五节（修正版规则）：
  1. config/model_configs.json
     按 (provider, modelId) 语义去重 —— **不是**按 id。
     导师把 8 个模型 id 整套重编号，按 id 并集会得到 18 条里 16 条重复，
     并把两个面板的默认模型从 deepseek-flash 翻成 MiniMax M2.7。
     规则：采纳导师字段、保留我方 id、deepseek-flash 置首、不整文件重排。
  2. services/base/base_service.py
     **顺序敏感**：导师的 handle_streaming_request 必须插到我方
     @_timed_request("DELETE") 之上，否则装饰器会粘到 SSE 方法上，
     do_DELETE 裸奔（实测 AST 确认）。
  3. qmclaw-web/src/components/HermesChatPanel.tsx
     冲突1 并集；冲突2 保留 interface ApprovalRequest、丢弃 DEFAULT_HERMES_MODEL 那行；
     冲突3 取我方（两侧是同一个 useEffect 的互斥实现，并集语法坏）。
  4. CLAUDE.md
     并集：保留我方约定 10/11 + 追加导师的 Compaction 压缩规则段，
     只丢弃导师那侧重复的 python 路径行。**不是**"取你的"（会丢整段规则）。
"""
import ast
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

TARGETS = [
    ("Agentic Workflow/qmclaw-server/config/model_configs.json", "模型配置"),
    ("Agentic Workflow/qmclaw-server/services/base/base_service.py", "服务基类"),
    ("Agentic Workflow/qmclaw-web/src/components/HermesChatPanel.tsx", "Hermes 面板"),
    ("CLAUDE.md", "项目约定"),
]


# ───────────────────────── 通用工具 ─────────────────────────

def split_markers(text):
    """把带冲突标记的文本切成 [('text', 行列表) | ('conflict', ours, theirs), ...]"""
    lines = text.split("\n")
    chunks, i = [], 0
    while i < len(lines):
        if lines[i].startswith("<<<<<<<"):
            i += 1
            ours, theirs = [], []
            while i < len(lines) and not lines[i].startswith("======="):
                if lines[i].startswith("|||||||"):      # diff3 风格的 base 段
                    while i < len(lines) and not lines[i].startswith("======="):
                        i += 1
                    continue
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


def git(repo, *args):
    p = subprocess.run(["git", "-C", repo] + list(args), capture_output=True)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def show(repo, ref, rel):
    rc, out, err = git(repo, "show", "%s:%s" % (ref, rel))
    if rc != 0:
        raise RuntimeError("git show %s:%s 失败: %s" % (ref, rel, err.strip()))
    return out


# ───────────────────────── 四条规则 ─────────────────────────

def rule_model_configs(repo, rel):
    ours = json.loads(show(repo, "HEAD", rel))
    theirs = json.loads(show(repo, "MERGE_HEAD", rel))
    ours_by_key = {(m.get("provider"), m.get("modelId")): m for m in ours["models"]}

    out, seen = [], set()
    for m in theirs["models"]:
        k = (m.get("provider"), m.get("modelId"))
        seen.add(k)
        if k in ours_by_key:
            mm = dict(m)
            mm["id"] = ours_by_key[k]["id"]      # 保留我方 id（前端与 DB 引用的是旧 id）
            out.append(mm)
        else:
            out.append(m)
    for m in ours["models"]:
        k = (m.get("provider"), m.get("modelId"))
        if k not in seen:
            out.append(m)

    flash = [m for m in out if m.get("id") == "deepseek-flash"]
    rest = [m for m in out if m.get("id") != "deepseek-flash"]
    obj = dict(theirs)
    obj["models"] = flash + rest
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def rule_base_service(repo, rel):
    text = show(repo, "CHECKOUT", rel) if False else _read_worktree(repo, rel)
    out = []
    for c in split_markers(text):
        if c[0] == "text":
            out.append(c[1])
        else:
            out.extend(c[2]); out.extend(c[1])   # theirs 在前、ours 在后
    return "\n".join(out)


def rule_hermes_panel(repo, rel):
    text = _read_worktree(repo, rel)
    out, ci = [], 0
    for c in split_markers(text):
        if c[0] == "text":
            out.append(c[1]); continue
        ci += 1
        ours, theirs = c[1], c[2]
        if ci == 3:
            out.extend(ours)                                    # 取我方
        else:
            out.extend(ours + [l for l in theirs if "DEFAULT_HERMES_MODEL" not in l])
    return "\n".join(out)


def rule_claude(repo, rel):
    text = _read_worktree(repo, rel)
    out = []
    for c in split_markers(text):
        if c[0] == "text":
            out.append(c[1]); continue
        ours, theirs = c[1], c[2]
        out.extend(ours + [l for l in theirs if "python.exe" not in l])
    return "\n".join(out)


RULES = {
    TARGETS[0][0]: rule_model_configs,
    TARGETS[1][0]: rule_base_service,
    TARGETS[2][0]: rule_hermes_panel,
    TARGETS[3][0]: rule_claude,
}


def _read_worktree(repo, rel):
    with open(os.path.join(repo, rel), "rb") as fh:
        return fh.read().decode("utf-8")


# ───────────────────────── 语义校验 ─────────────────────────

def check_model_configs(text):
    notes, ok = [], True
    data = json.loads(text)
    ms = data["models"]
    keys = [(m.get("provider"), m.get("modelId")) for m in ms]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    ids = [m["id"] for m in ms]
    idd = sorted({i for i in ids if ids.count(i) > 1})
    et = [m for m in ms if m.get("enabled") and "text" in (m.get("capabilities") or [])]
    first = et[0]["id"] if et else None
    notes.append("条数=%d（期望 10）" % len(ms))
    if len(ms) != 10: ok = False; notes.append("条数不对")
    notes.append("(provider,modelId) 重复=%d" % len(dup))
    if dup: ok = False; notes.append("语义重复: %s" % dup)
    notes.append("id 重复=%d" % len(idd))
    if idd: ok = False; notes.append("id 重复: %s" % idd)
    notes.append("第一条 enabled+text = %s" % first)
    if first != "deepseek-flash": ok = False; notes.append("默认模型被翻转")
    if "deepseek-flash" not in ids: ok = False; notes.append("deepseek-flash 丢失")
    return ok, notes


def check_base_service(text):
    notes, ok = [], True
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return False, ["Python 语法 line %s: %s" % (e.lineno, e.msg)]
    notes.append("ast.parse OK")
    binds = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for d in node.decorator_list:
                s = ast.unparse(d)
                if "_timed_request" in s:
                    binds.setdefault(s, []).append(node.name)
    notes.append("装饰器绑定: %s" % binds)
    if binds.get("_timed_request('DELETE')") != ["do_DELETE"]:
        ok = False; notes.append("@_timed_request('DELETE') 没绑到 do_DELETE")
    else:
        notes.append("@_timed_request('DELETE') -> do_DELETE OK")
    if "handle_streaming_request" in text:
        notes.append("导师 handle_streaming_request 在")
    else:
        ok = False; notes.append("导师 handle_streaming_request 丢失")
    for must in ("ThreadingHTTPServer", "_timed_request"):
        if must not in text: ok = False; notes.append("%s 丢失" % must)
    return ok, notes


def check_hermes_panel(text):
    notes, ok = [], True
    if "DEFAULT_HERMES_MODEL" in text:
        ok = False; notes.append("DEFAULT_HERMES_MODEL 残留（H3 要求已删除）")
    else:
        notes.append("DEFAULT_HERMES_MODEL 不存在 OK")
    for must in ("useModelStore", "MarkdownContent", "ReasoningBlock", "ToolCallsList", "ApprovalRequest"):
        if must not in text: ok = False; notes.append("%s 丢失" % must)
    if text.count("{") != text.count("}"):
        ok = False; notes.append("花括号不平衡 {=%d }=%d" % (text.count("{"), text.count("}")))
    else:
        notes.append("花括号平衡 {=%d OK" % text.count("{"))
    notes.append("5 个关键标识符齐")
    return ok, notes


def check_claude(text):
    notes, ok = [], True
    if "Compaction" not in text:
        ok = False; notes.append("导师 Compaction 段丢失")
    else:
        notes.append("Compaction 段在 OK")
    for must in ("D:\\semester4\\deploy\\qmclaw\\qmclaw\\python.exe", "按节奏提交"):
        if must not in text: ok = False; notes.append("我方 %r 丢失" % must[:26])
    notes.append("我方约定 10/11 在")
    if "<<<<<<<" in text or ">>>>>>>" in text:
        ok = False; notes.append("残留冲突标记")
    return ok, notes


CHECKS = {
    TARGETS[0][0]: check_model_configs,
    TARGETS[1][0]: check_base_service,
    TARGETS[2][0]: check_hermes_panel,
    TARGETS[3][0]: check_claude,
}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    repo = os.path.abspath(sys.argv[1])
    write = "--write" in sys.argv

    if not os.path.isdir(os.path.join(repo, ".git")):
        print("!! 不是 git 仓库: %s" % repo)
        return 2

    rc, out, _ = git(repo, "ls-files", "-u")
    unresolved = sorted({l.split("\t")[1] for l in out.split("\n") if l.strip()})
    print("=" * 92)
    print("冲突文件（git ls-files -u）: %d 个" % len(unresolved))
    for f in unresolved:
        print("   - %s" % f)
    print("=" * 92)

    expected = {p for p, _ in TARGETS}
    unexpected = [f for f in unresolved if f not in expected]
    if unexpected:
        print("!! 出现预期之外的冲突文件，请人工判断: %s" % unexpected)
    missing = [p for p in expected if p not in unresolved]
    if missing:
        print("!! 预期冲突文件缺失（可能已被手工解过）: %s" % missing)

    allok = True
    for rel, label in TARGETS:
        if rel not in unresolved:
            print("\n[SKIP] %s（不在冲突清单里）" % rel)
            continue
        resolved = RULES[rel](repo, rel)
        ok, notes = CHECKS[rel](resolved)
        allok = allok and ok
        print("\n%s  —— %s" % (rel, label))
        print("   试算结果: %s   %d 字节" % ("PASS" if ok else "FAIL", len(resolved.encode())))
        for n in notes:
            print("     - %s" % n)
        if write and ok:
            with open(os.path.join(repo, rel), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(resolved)
            rc, _, err = git(repo, "add", rel)
            print("   → 已写入并 git add (exit=%d)" % rc)
        elif write and not ok:
            print("   → 校验未过，**不写入**，请人工处理")

    print("=" * 92)
    print("汇总: %s%s" % ("全部 PASS" if allok else "存在 FAIL",
                          "（已写回）" if write else "（试算模式，未改动文件）"))
    if not allok:
        print("有 FAIL 项 → 不要提交，先按上面提示人工处理。")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
