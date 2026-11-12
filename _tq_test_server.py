"""临时：起一个 task_queue 实例（端口 3019），验证 P3 的 `/task*` HTTP 端点。

不改动业务代码；正式服务仍由人重启 :3009。
用法：QMCLAW_TASK_DATA_DIR=... python _tq_test_server.py
"""
import sys
from pathlib import Path

SERVER = Path(r"D:/qmclaw/Agentic Workflow/qmclaw-server")
sys.path.insert(0, str(SERVER))

from services.base import run_service  # noqa: E402
from services.task_queue.server import TaskQueueService  # noqa: E402

if __name__ == "__main__":
    run_service(TaskQueueService(port=3019))
