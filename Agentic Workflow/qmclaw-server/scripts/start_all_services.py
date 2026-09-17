"""
scripts/start_all_services.py - 启动所有服务

Usage:
    python start_all_services.py [service1, service2, ...]
    python start_all_services.py --all  # 启动所有服务
    python start_all_services.py llm quantum  # 只启动指定服务
"""

import subprocess
import sys
import os
import signal
import time
import json
import threading
import queue
from pathlib import Path

# 服务配置
SERVICES = {
    "llm": {
        "port": 3006,
        "script": "services/llm_service/server.py",
        "description": "LLM 推理服务",
    },
    "quantum": {
        "port": 3003,
        "script": "services/quantum_service/server.py",
        "description": "测控执行服务",
    },
    "analysis": {
        "port": 3004,
        "script": "services/analysis_service/server.py",
        "description": "数据分析服务",
    },
    "agent": {
        "port": 3005,
        "script": "services/agent_service/server.py",
        "description": "Agent 服务",
    },
    "image": {
        "port": 3007,
        "script": "services/image_service/server.py",
        "description": "图像服务",
    },
    "workflow": {
        "port": 3008,
        "script": "services/workflow_service/server.py",
        "description": "工作流服务",
    },
    "task_queue": {
        "port": 3009,
        "script": "services/task_queue/server.py",
        "description": "任务队列服务",
    },
}

# 全局进程列表
processes = []
process_info = {}  # 存储进程详细信息
to_start = []      # 要启动的服务列表


def load_config():
    """从配置文件加载服务列表"""
    config_path = Path(__file__).parent.parent / "config" / "services.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f).get("services", {})
    return {}


def read_output(name: str, pipe, output_queue: queue.Queue, is_error: bool = False):
    """读取子进程输出的线程函数"""
    try:
        for line in iter(pipe.readline, ""):
            if line:
                output_queue.put((name, is_error, line.rstrip("\n\r")))
    except Exception:
        pass  # 进程退出时会触发


def print_output_loop(name: str, output_queue: queue.Queue):
    """定期从队列中取出输出并打印"""
    prefix = f"[{name:12}]"
    while True:
        try:
            msg_name, is_error, line = output_queue.get(timeout=0.1)
            if msg_name == name:  # 只打印本服务的消息
                if is_error:
                    print(f"{prefix} [STDERR] {line}")
                else:
                    print(f"{prefix} {line}")
        except queue.Empty:
            continue


def start_service(name: str, config: dict, output_queue: queue.Queue) -> subprocess.Popen:
    """启动单个服务"""
    script_path = config["script"]  # 如 "services/llm_service/server.py"

    # 转换为模块路径: services.llm_service.server
    module_path = script_path.replace("/", ".").replace("\\", ".").replace(".py", "")

    script_full_path = Path(__file__).parent.parent / script_path
    if not script_full_path.exists():
        print(f"[start_all] {name}: Script not found: {script_full_path}")
        return None

    # 设置环境变量
    env = os.environ.copy()

    print(f"[start_all] Starting {name} ({config['description']})...")
    print(f"[start_all]   Module: {module_path}")
    print(f"[start_all]   Port: {config['port']}")

    # 使用模块方式启动 (python -m services.llm_service.server)
    proc = subprocess.Popen(
        [sys.executable, "-m", module_path],
        cwd=Path(__file__).parent.parent,  # 设置工作目录为 server 目录
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,  # 使用文本模式
        bufsize=1,  # 行缓冲
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if sys.platform == 'win32' else 0,
    )

    # 启动输出读取线程
    stdout_thread = threading.Thread(target=read_output, args=(name, proc.stdout, output_queue, False), daemon=True)
    stderr_thread = threading.Thread(target=read_output, args=(name, proc.stderr, output_queue, True), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    # 存储进程信息
    process_info[name] = {
        "proc": proc,
        "config": config,
        "start_time": time.time(),
        "stdout_thread": stdout_thread,
        "stderr_thread": stderr_thread,
    }

    print(f"[start_all] {name}: Started (PID: {proc.pid})")
    return proc


def stop_all():
    """停止所有服务"""
    print("\n[start_all] Stopping all services...")

    for name, info in process_info.items():
        proc = info["proc"]
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
                print(f"[start_all] {name} (PID: {proc.pid}) terminated")
            except subprocess.TimeoutExpired:
                proc.kill()
                print(f"[start_all] {name} (PID: {proc.pid}) killed")

    # 等待一下让输出线程退出
    time.sleep(0.5)


def signal_handler(signum, frame):
    """处理信号"""
    print(f"\n[start_all] Received signal {signum}")
    stop_all()
    sys.exit(0)


def wait_for_services(timeout: int = 60):
    """等待所有服务就绪"""
    import urllib.request
    import urllib.error

    print(f"\n[start_all] Waiting for services to be ready (timeout={timeout}s)...")

    start_time = time.time()
    config = load_config()

    # 跟踪每个服务的状态
    service_status = {name: "starting" for name in to_start}

    while time.time() - start_time < timeout:
        all_ready = True
        any_running = False

        for name in to_start:
            svc_config = config.get(name, SERVICES.get(name, {}))
            port = svc_config.get("port", SERVICES[name]["port"] if name in SERVICES else 0)
            url = f"http://localhost:{port}/health"

            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=2) as response:
                    if response.status == 200:
                        if service_status[name] != "ready":
                            service_status[name] = "ready"
                            print(f"[start_all] ✅ {name}: Ready (port {port})")
                    else:
                        all_ready = False
                        any_running = True
            except urllib.error.URLError as e:
                all_ready = False
                if service_status[name] != "error":
                    # 只打印一次错误
                    print(f"[start_all] ⏳ {name}: Waiting... ({e.reason})")
                    service_status[name] = "waiting"
            except Exception as e:
                all_ready = False
                if service_status[name] != "error":
                    print(f"[start_all] ⏳ {name}: Waiting... ({type(e).__name__})")
                    service_status[name] = "waiting"

        if all_ready and any_running:
            elapsed = time.time() - start_time
            print(f"[start_all] All services ready! (took {elapsed:.1f}s)")
            return True

        if not any_running:
            # 至少有一个服务在运行
            pass

        time.sleep(1)

    # 超时时显示哪些服务未就绪
    print("[start_all] ⚠️  Timeout waiting for services")
    print("[start_all] Service status:")
    for name, status in service_status.items():
        print(f"  - {name}: {status}")
    return False


def main():
    global processes, to_start, process_info

    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 创建输出队列
    output_queue = queue.Queue()

    # 确定要启动哪些服务
    args = sys.argv[1:] if len(sys.argv) > 1 else ["--all"]

    if "--all" in args:
        # 启动所有服务
        config = load_config()
        to_start = [name for name in SERVICES.keys()]
    else:
        # 只启动指定的服务
        to_start = [arg for arg in args if arg in SERVICES]

    if not to_start:
        print("No services to start. Use --all or specify service names.")
        print(f"Available services: {', '.join(SERVICES.keys())}")
        return

    print("=" * 60)
    print("QMClaw Services Starter")
    print("=" * 60)
    print(f"Services to start: {', '.join(to_start)}")
    print()

    # 启动服务
    config = load_config()
    for name in to_start:
        svc_config = config.get(name, SERVICES.get(name, {}))
        if not svc_config.get("enabled", True):
            print(f"[start_all] {name}: Disabled in config")
            continue

        proc = start_service(name, svc_config, output_queue)
        if proc:
            processes.append(proc)

    if not processes:
        print("[start_all] No services started")
        return

    # 等待服务就绪
    wait_for_services()

    print()
    print("=" * 60)
    print("All services started. Press Ctrl+C to stop.")
    print("=" * 60)

    # 启动输出打印线程
    def output_printer():
        """定期从队列中取出输出并打印"""
        prefix_chars = 14
        while True:
            try:
                msg_name, is_error, line = output_queue.get(timeout=0.1)
                prefix = f"[{msg_name[:prefix_chars]:<{prefix_chars}}]"
                if is_error:
                    print(f"{prefix} [ERR] {line}")
                else:
                    print(f"{prefix} {line}")
            except queue.Empty:
                continue

    printer_thread = threading.Thread(target=output_printer, daemon=True)
    printer_thread.start()

    # 保持运行，监控进程状态
    try:
        # 等待所有进程
        while True:
            for proc in processes:
                if proc and proc.poll() is not None:
                    # 进程已退出，检查是否有错误输出
                    returncode = proc.returncode
                    print(f"\n[start_all] ⚠️  Process {proc.pid} exited with code {returncode}")
                    print(f"[start_all] Please check the output above for errors")

            # 检查是否所有进程都退出了
            if all(p.poll() is not None for p in processes if p):
                print("[start_all] All processes have exited")
                break

            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[start_all] Interrupted")
    finally:
        stop_all()


if __name__ == "__main__":
    main()
