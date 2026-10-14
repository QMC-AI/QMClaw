#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""在探针仓库的真实 merge 结果上，跑第七节验证清单里**沙箱内可自动化**的项。

不可自动化的项（npm install / 起服务 / 前端 build / 人工看图）明确标 SKIP 并写原因，
不假装通过。
"""
import ast
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\semester4\QMClaw"
# 允许指定被验证的仓库路径（默认用最早那个探针仓库）
if len(sys.argv) > 1:
    REPO = os.path.abspath(sys.argv[1])
else:
    REPO = os.path.join(ROOT, ".merge-scratch", "syncprobe", "repo")
PY = r"D:\semester4\deploy\qmclaw\qmclaw\python.exe"

print("被验证仓库: %s" % REPO)

RESULTS = []


def git(*args):
    p = subprocess.run(["git", "-C", REPO] + list(args), capture_output=True)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def read(rel):
    with open(os.path.join(REPO, rel), "rb") as fh:
        return fh.read()


def readt(rel):
    return read(rel).decode("utf-8", "replace")


def rec(num, name, status, detail):
    RESULTS.append((num, name, status, detail))
    print("[%-4s] #%-2s %-34s %s" % (status, num, name, detail))


print("=" * 100)
print("验证清单实测 —— 输入是真实 git merge + 修正版规则解冲突后的工作树")
print("=" * 100)

# ---- 1 无残留冲突标记（用复核修正后的正确判据） ----
rc, out, _ = git("grep", "-nE", r"^(<{7}|={7}$|>{7}|\|{7})", "--", ".")
lines = [l for l in out.split("\n") if l.strip()]
rec(1, "无残留冲突标记", "PASS" if len(lines) == 0 else "FAIL",
    "命中 %d 行（期望 0；若为 18 则说明解冲突没生效）" % len(lines))

# ---- 2 模型配置语义 ----
try:
    cfg = json.loads(readt("Agentic Workflow/qmclaw-server/config/model_configs.json"))
    ms = cfg["models"]
    keys = [(m.get("provider"), m.get("modelId")) for m in ms]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    ids = [m["id"] for m in ms]
    idd = sorted({i for i in ids if ids.count(i) > 1})
    # 关键：handler 的默认模型取的是【按 updatedAt 倒序】后的第一条 enabled+text，
    # 不是文件里的第一条（src/services/modelService.ts 的 listModels() 就这么排）。
    # 2026-10-08 的回归正栽在这里：合并采纳了导师的 updatedAt(2026-09-23)，
    # 把 deepseek-flash(2026-09-21) 从首位挤到第 9 位 → 默认模型变成 GPT-4o Mini。
    ordered = sorted(ms, key=lambda m: m.get("updatedAt", ""), reverse=True)
    enabled_text = [m for m in ordered if m.get("enabled") and "text" in (m.get("capabilities") or [])]
    first = enabled_text[0]["id"] if enabled_text else None
    first_in_file = next((m["id"] for m in ms if m.get("enabled") and "text" in (m.get("capabilities") or [])), None)
    ok = (len(ms) == 10) and not dup and not idd and first == "deepseek-flash"
    rec(2, "模型配置语义", "PASS" if ok else "FAIL",
        "条数=%d(期望10) (provider,modelId)重复=%d id重复=%d | 文件首条=%s | 按updatedAt倒序首条=%s（这才是面板默认）" %
        (len(ms), len(dup), len(idd), first_in_file, first))
except Exception as e:
    rec(2, "模型配置语义", "FAIL", "JSON 解析失败: %s" % e)

# ---- 3 Python 语法（用约定 10 的解释器） ----
dirs = ["Agentic Workflow/qmclaw-server/services", "Agentic Workflow/qmclaw-server/scripts"]
bad = []
for d in dirs:
    p = subprocess.run([PY, "-m", "compileall", "-q", os.path.join(REPO, d)], capture_output=True)
    if p.returncode != 0:
        bad.append(d + ": " + p.stdout.decode("utf-8", "replace")[-300:])
rec(3, "Python 语法 (3.11.16)", "PASS" if not bad else "FAIL",
    "compileall %s -> %s" % ("/".join(x.split("/")[-1] for x in dirs), "OK" if not bad else bad[0][:120]))

# ---- 4 装饰器 AST 绑定 ----
bs = readt("Agentic Workflow/qmclaw-server/services/base/base_service.py")
try:
    tree = ast.parse(bs)
    binds = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for d in node.decorator_list:
                s = ast.unparse(d)
                if "_timed_request" in s:
                    binds.setdefault(s, []).append(node.name)
    ok4 = binds.get("_timed_request('DELETE')") == ["do_DELETE"] and "handle_streaming_request" in bs
    rec(4, "装饰器语义绑定", "PASS" if ok4 else "FAIL",
        "%s | handle_streaming_request=%s" % (binds, "在" if "handle_streaming_request" in bs else "缺"))
except SyntaxError as e:
    rec(4, "装饰器语义绑定", "FAIL", "语法错误 line %s: %s" % (e.lineno, e.msg))

# ---- 5 离线链路标志 ----
flags = {
    "X_UNIT_SCALE": "Agentic Workflow/qmclaw-server/services/quantum_service/offline_simulator.py",
}
miss5 = []
for name, path in flags.items():
    if name not in readt(path):
        miss5.append(name)
sim = readt("Agentic Workflow/qmclaw-server/services/common/plotting.py") if os.path.exists(
    os.path.join(REPO, "Agentic Workflow/qmclaw-server/services/common/plotting.py")) else ""
if "PLOT_LOCK" not in sim:
    miss5.append("PLOT_LOCK")
ms_path = "Agentic Workflow/qmclaw-server/services/common/memory_store.py"
if "_INDEX_LOCK" not in readt(ms_path):
    miss5.append("_INDEX_LOCK")
if "ThreadingHTTPServer" not in bs:
    miss5.append("ThreadingHTTPServer")
rec(5, "稳定性/离线改造标志", "PASS" if not miss5 else "FAIL",
    "缺失: %s" % miss5 if miss5 else "X_UNIT_SCALE / PLOT_LOCK / _INDEX_LOCK / ThreadingHTTPServer 全在")

# ---- 6 新模块 import 冒烟（AST 静态导入解析，不执行） ----
newmods = ["memory", "skills", "cron", "approval_ws", "mcp_toolsets"]
hs = readt("Agentic Workflow/qmclaw-server/services/hermes_service/server.py")
refd = [m for m in newmods if ("import %s as" % m) in hs or ("import %s" % m) in hs or ("%s import" % m) in hs]
missing_files = [m for m in newmods
                 if not os.path.exists(os.path.join(
                     REPO, "Agentic Workflow/qmclaw-server/services/hermes_service", m + ".py"))]
rec(6, "新模块存在+被引用", "PASS" if len(refd) == len(newmods) and not missing_files else "FAIL",
    "server.py 引用 %d/%d, 缺文件: %s" % (len(refd), len(newmods), missing_files or "无"))

# ---- 7 前端依赖（本项必然 SKIP：npm install 是写操作+联网） ----
pkg = json.loads(readt("Agentic Workflow/qmclaw-web/package.json"))
deps = {}
deps.update(pkg.get("dependencies") or {})
deps.update(pkg.get("devDependencies") or {})
need = ["highlight.js", "react-markdown", "rehype-highlight", "remark-gfm"]
indep = [d for d in need if d in deps]
rec(7, "前端构建", "SKIP",
    "package.json 已声明 %d/%d 个新依赖；npm install + build 需人工执行（联网写操作）" % (len(indep), len(need)))

# ---- 8 接口默认模型（需起服务） ----
rec(8, "接口默认模型", "SKIP", "需人工启停服务（约定 3）；离线已由第 2 项断言首条=deepseek-flash")

# ---- 9 端到端 + 看图 ----
rec(9, "端到端 + 人工看图", "SKIP", "需人工起服务并肉眼比对出图（offline_data_lab.py 被整份重写）")

# ---- 10 hermes 对话 + 新功能 ----
rec(10, "hermes 对话 + approval/cron", "SKIP", "需人工启停服务")

# ---- 11 HERMES_HOME / skills 可见性 ----
envp = os.path.join(REPO, "Agentic Workflow/qmclaw-server/.env")
hh = None
if os.path.exists(envp):
    for l in open(envp, encoding="utf-8", errors="replace"):
        if l.strip().startswith("HERMES_HOME"):
            hh = l.strip()
runner = readt("Agentic Workflow/qmclaw-server/scripts/hermes_runner.py")
rec(11, "HERMES_HOME 语义", "WARN",
    "树内 hermes_runner.py 已含导师的 HERMES_HOME 逻辑=%s；本机 .env 值需人工决策（不在合并范围）"
    % ("是" if "HERMES_HOME" in runner else "否"))

# ---- 12 DEFAULT_HERMES_MODEL 必须不存在 ----
found12 = []
for dp, _, fns in os.walk(os.path.join(REPO, "Agentic Workflow/qmclaw-web/src")):
    for fn in fns:
        if fn.endswith((".ts", ".tsx")):
            p = os.path.join(dp, fn)
            if "DEFAULT_HERMES_MODEL" in open(p, encoding="utf-8", errors="replace").read():
                found12.append(os.path.relpath(p, REPO))
rec(12, "DEFAULT_HERMES_MODEL 不存在", "PASS" if not found12 else "FAIL",
    "命中: %s" % found12 if found12 else "全 src 零命中（H3 验收守住）")

# ---- 13 记忆路径盘点（记录，不判失败） ----
rec(13, "记忆路径盘点", "INFO",
    "三处：config/memory/（你的）+ 导师 python 侧 data/memories/ + 网关侧 <repo>/memory/（导师自带 memory/MEMORY.md 已进树）")

# ---- 14 .gitignore 判定 ----
gi = readt(".gitignore")
need14 = ["config/memory/", ".upstream-mirror/", ".merge-scratch/", ".analysis/"]
miss14 = [x for x in need14 if x not in gi]
official = ["cache/", "*.db*"]
miss14b = [x for x in official if x not in gi]
rec(14, ".gitignore 规则齐备", "PASS" if not miss14 and not miss14b else "FAIL",
    "我方缺 %s / 导师缺 %s" % (miss14 or "无", miss14b or "无"))

# ---- 15 新模块依赖可导入 ----
p = subprocess.run([PY, "-c",
                    "import importlib.util as u;"
                    "mods=['apscheduler','tzlocal','portalocker','websockets'];"
                    "missing=[m for m in mods if not u.find_spec(m)];"
                    "print('MISSING:'+','.join(missing) if missing else 'ALL_OK')"],
                   capture_output=True)
out15 = p.stdout.decode("utf-8", "replace").strip()
rec(15, "新模块依赖可导入", "PASS" if out15 == "ALL_OK" else "FAIL",
    "%s（注意：requirements 里仍未声明，迁移环境会缺）" % out15)

# ---- 16 vendor 不动 ----
rc, out, _ = git("diff", "--name-only", "ours", "theirs", "--", "vendor", ".gitmodules")
rec(16, "vendor/.gitmodules 无差异", "PASS" if not out.strip() else "FAIL",
    "两侧差异: %s" % (out.strip() or "无"))

# ---- 17 qca_service 起服务前提 ----
vendor_ok = os.path.isdir(os.path.join(REPO, "vendor", "Quantum-Calibration-Agent-Blueprint"))
qc = readt("Agentic Workflow/qmclaw-server/services/qca_service/server.py")
rec(17, "qca_service 前提", "INFO",
    "vendor 目录在=%s；server.py 缺 vendor 会 raise RuntimeError=%s" %
    (vendor_ok, "是" if "raise RuntimeError" in qc else "否"))

# ---- 18 重命名测试文件的旧路径引用 ----
old = ["test_load_qubits.py", "sse_verify.py", "sse_verify_standalone.py", "test_labrad.py"]
hits18 = []
for dp, _, fns in os.walk(REPO):
    if ".git" in dp or "node_modules" in dp or ".merge-scratch" in dp:
        continue
    for fn in fns:
        if fn in ("README.md", "install.md") or dp.endswith("docs") or dp.endswith("scripts"):
            p = os.path.join(dp, fn)
            try:
                t = open(p, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            for o in old:
                if o in t and "../tests/" not in t and "tests/" + o not in t:
                    hits18.append("%s -> %s" % (os.path.relpath(p, REPO), o))
rec(18, "重命名旧引用扫描", "PASS" if not hits18 else "WARN",
    "疑似旧引用 %d 处: %s" % (len(hits18), hits18[:3]) if hits18 else "未发现指向根路径的旧引用")

# ---- 19 tsbuildinfo 出库 ----
rc, out, _ = git("ls-files", "--error-unmatch", "Agentic Workflow/qmclaw-web/tsconfig.tsbuildinfo")
tracked = (rc == 0)
rec(19, "tsbuildinfo 出库", "FAIL" if tracked else "PASS",
    "仍被跟踪（需移出索引）" if tracked else "已不在索引")

# ---- 20 改动面核对 ----
rc, out, _ = git("diff", "--name-only", "ours", "theirs")
up_files = {l for l in out.split("\n") if l.strip()}
p = subprocess.run(["git", "-C", ROOT, "diff", "--name-only", "f2d83b5", "HEAD"], capture_output=True)
my_files = {l for l in p.stdout.decode("utf-8", "replace").split("\n") if l.strip()}
rc, out, _ = git("diff", "--name-only", "--cached", "ours")
staged = {l for l in out.split("\n") if l.strip()}
expected = up_files | my_files
unexpected = sorted(staged - expected)
rec(20, "改动面核对", "PASS" if not unexpected else "FAIL",
    "暂存 %d 个文件；不在'导师41∪我方62'里的: %s" % (len(staged), unexpected[:5] or "无"))

# ---- 21 运行时状态文件 ----
mem_ex = os.path.exists(os.path.join(REPO, "memory/MEMORY.md"))
lock_ex = os.path.exists(os.path.join(REPO, "auth.lock"))
rec(21, "运行时状态文件入库", "WARN",
    "memory/MEMORY.md 在树=%s（网关会改写）, auth.lock 在树=%s（全上游零引用）→ 需人工决定" %
    (mem_ex, lock_ex))

print("=" * 100)
cnt = {}
for _, _, s, _ in RESULTS:
    cnt[s] = cnt.get(s, 0) + 1
print("汇总: " + "  ".join("%s=%d" % (k, v) for k, v in sorted(cnt.items())))
fails = [r for r in RESULTS if r[2] == "FAIL"]
print("FAIL 项: %s" % ([r[0] for r in fails] or "无"))
sys.exit(1 if fails else 0)
