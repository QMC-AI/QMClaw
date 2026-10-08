#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对 7 个"自动合并"的重叠文件做内容级完整性验证：
双方各自特有的里程碑字符串，是否都出现在合并结果里。

这比只看 git 报 CLEAN 更硬 —— CLEAN 只说明文本能合，不说明语义都在。
"""
import os
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

USER = r"D:\semester4\QMClaw"
MIR = os.path.join(USER, ".upstream-mirror")
FORK = "f2d83b5"
TMP = os.path.join(tempfile.gettempdir(), "qmclaw-3way")

# 每个文件：{ "文件": (我方里程碑列表, 导师方里程碑列表) }
CASES = {
    "Agentic Workflow/qmclaw-server/services/hermes_service/server.py": (
        ["model_configs", "_resolve_model_config"],
        ["from . import memory as memory_module", "from . import skills as skills_module",
         "from . import cron as cron_module", "from .approval_ws import"],
    ),
    "Agentic Workflow/qmclaw-server/services/base/base_service.py": (
        ["ThreadingHTTPServer", "_timed_request"],
        ["handle_streaming_request", "text/event-stream"],
    ),
    "Agentic Workflow/qmclaw-server/services/analysis_service/server.py": (
        # 注意：我方符号是 @serialized_plot（装饰器）与 services/common/plotting.py 里的 PLOT_LOCK，
        # 本文件里不含小写的 plot_lock —— 上一版探针写成 plot_lock 导致误报 LOST。
        ["serialized_plot", "from ..common.plotting import"],
        ["iqraw"],
    ),
    "Agentic Workflow/qmclaw-server/src/index.ts": (
        ["/memory/", "memory"],
        ["hermes"],
    ),
    "Agentic Workflow/qmclaw-server/scripts/start_all_services.py": (
        ["utf-8", "UTF-8"],
        ["hermes"],
    ),
    "Agentic Workflow/qmclaw-server/scripts/hermes_runner.py": (
        # 注意：BASE/OURS 里都不含 HERMES_HOME（你是在 .env 里修的，没改本脚本），
        # 导师是纯新增；上一版探针用 'cron' 导致误报 LOST。
        ["_SERVER_DIR"],
        ["HERMES_HOME", "skills"],
    ),
    ".gitignore": (
        ["config/memory/"],
        ["cache/", "*.db*"],
    ),
}


def blob(repo, rev, rel):
    rc = subprocess.run(["git", "-C", repo, "cat-file", "-s", "%s:%s" % (rev, rel)], capture_output=True)
    if rc.returncode != 0:
        return None
    return subprocess.run(["git", "-C", repo, "cat-file", "blob", "%s:%s" % (rev, rel)],
                          capture_output=True).stdout.decode("utf-8", "replace")


allok = True
for rel, (mine, theirs) in CASES.items():
    o = blob(USER, "HEAD", rel)
    b = blob(USER, FORK, rel)
    t = blob(MIR, "main", rel)
    d = os.path.join(TMP, "clean", rel.replace("/", "__").replace(" ", "_"))
    os.makedirs(os.path.dirname(d), exist_ok=True)
    for s, txt in ((".o", o), (".b", b), (".t", t)):
        with open(d + s, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(txt)
    rc = subprocess.run(["git", "merge-file", "-p", d + ".o", d + ".b", d + ".t"], capture_output=True)
    merged = rc.stdout.decode("utf-8", "replace")
    print("=" * 92)
    print("%s   (merge rc=%d, 冲突块=%d)" % (rel, rc.returncode, merged.count("<<<<<<<")))
    for label, needles in (("我方", mine), ("导师方", theirs)):
        for n in needles:
            present = n in merged
            if not present:
                allok = False
            print("   [%s] %-52s %s" % ("OK  " if present else "LOST", "%s: %r" % (label, n),
                                        "在合并结果中" if present else "!! 丢失"))

print("=" * 92)
print("内容级完整性: %s" % ("PASS —— 自动合并的 7 个文件双方功能都在" if allok else "FAIL —— 有里程碑丢失"))
sys.exit(0 if allok else 1)
