"""Try to import each QMClaw microservice module in the target env.

Run with the env's python, from the server root:

    cd "Agentic Workflow\\qmclaw-server"
    <env>\\python.exe -B ..\\..\\docs\\deploy_diff_tools\\check_services.py

or let the script locate the server root itself:
    <env>\\python.exe -B check_services.py [server_root]

Importing a server module executes its module-level imports but NOT main()
(guarded by __name__ == "__main__"), so this reveals missing runtime deps
without binding any ports.
"""
import importlib
import os
import sys
import traceback

DEFAULT_ROOT = r"D:\semester4\QMClaw\Agentic Workflow\qmclaw-server"

SERVICES = [
    ("llm",         "services.llm_service.server",         "3006", "LLM 推理"),
    ("quantum",     "services.quantum_service.server",     "3003", "测控执行 / LabRAD"),
    ("analysis",    "services.analysis_service.server",    "3004", "数据分析 / 绘图"),
    ("agent",       "services.agent_service.server",       "3005", "Agent ReAct"),
    ("image",       "services.image_service.server",       "3007", "图像推理"),
    ("workflow",    "services.workflow_service.server",    "3008", "工作流调度"),
    ("task_queue",  "services.task_queue.server",          "3009", "任务队列"),
    ("qca",         "services.qca_service.server",         "3011", "QCA 智能体"),
    ("hermes",      "services.hermes_service.server",      "3012", "Hermes Agent"),
    ("qubitclient", "services.qubitclient_service.server", "—",    "QubitClient"),
]


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ROOT
    if not os.path.isdir(root):
        raise SystemExit(f"server root not found: {root}")
    os.chdir(root)
    sys.path.insert(0, root)

    print(f"server root : {root}")
    print(f"interpreter : {sys.executable}")
    print(f"python      : {sys.version.split()[0]}")
    print("=" * 74)

    ok = bad = 0
    for name, mod, port, desc in SERVICES:
        try:
            importlib.import_module(mod)
            print(f"  OK    {name:12} :{port:5} {desc}")
            ok += 1
        except BaseException as e:  # noqa: BLE001 - report everything
            first = traceback.format_exception_only(type(e), e)[-1].strip()
            cause = ""
            if e.__cause__ is not None:
                cause = f"  <- {type(e.__cause__).__name__}: {e.__cause__}"
            print(f"  FAIL  {name:12} :{port:5} {desc}")
            print(f"          {first}{cause}")
            bad += 1

    print("=" * 74)
    print(f"可导入 {ok} / {len(SERVICES)}，失败 {bad}")


if __name__ == "__main__":
    main()
