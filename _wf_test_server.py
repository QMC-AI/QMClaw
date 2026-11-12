"""临时：起一个带新代码的 workflow_service 实例（端口 3018），用于验证 P2 改动。

不改动任何业务代码；正式服务仍由人按 AGENTS.md 第 3 条重启 :3008。
用法：python _wf_test_server.py
"""
import sys
from pathlib import Path

SERVER = Path(r"D:/qmclaw/Agentic Workflow/qmclaw-server")
sys.path.insert(0, str(SERVER))

from services.base import run_service  # noqa: E402
from services.workflow_service.server import WorkflowService  # noqa: E402

if __name__ == "__main__":
    run_service(WorkflowService(port=3018))
