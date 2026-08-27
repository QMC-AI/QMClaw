"""
QmClaw Service Start Script
启动测控服务

Usage:
    python start_services.py
"""

import sys
import json
import subprocess
import time
import os

def check_port(port: int) -> bool:
    """检查端口是否被占用"""
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if sys.platform == 'win32' else 0
        )
        for line in result.stdout.split("\n"):
            if f":{port}" in line and "LISTENING" in line:
                return True
        return False
    except:
        return False

def start_services():
    """启动所有服务"""
    results = {
        "started": [],
        "skipped": [],
        "failed": [],
        "errors": [],
    }

    print("QmClaw Service Starter", flush=True)
    print("=" * 50, flush=True)

    # 1. 检查/启动 LabRAD
    print("\n[1/5] Checking LabRAD Server...", flush=True)
    if check_port(7682):
        print("  LabRAD already running on port 7682", flush=True)
        results["skipped"].append("labrad")
    else:
        print("  LabRAD not running!", flush=True)
        print("  Please start LabRAD manually:", flush=True)
        print('  python -c "from lqcs.servers_control import run_server_control; run_server_control()"', flush=True)
        results["failed"].append("labrad")
        results["errors"].append("LabRAD server not running on port 7682")

    # 2. 检查 Ray
    print("\n[2/5] Checking Ray...", flush=True)
    try:
        import ray
        if ray.is_initialized():
            print("  Ray already initialized", flush=True)
            results["skipped"].append("ray")
        else:
            print("  Initializing Ray...", flush=True)
            from lqcs import system_config
            head_ip = system_config.get_ray_head()
            node_ip = system_config.get_config()['ip']
            port = system_config.get_ray_port()

            ray.init(
                address=f"{head_ip}:{port}",
                namespace='main',
                _node_ip_address=node_ip,
                log_to_driver=False,
            )
            print("  Ray initialized successfully", flush=True)
            results["started"].append("ray")
    except Exception as e:
        print(f"  Ray initialization failed: {e}", flush=True)
        results["failed"].append("ray")
        results["errors"].append(f"Ray: {e}")

    # 3. 启动设备管理器
    print("\n[3/5] Checking Device Manager...", flush=True)
    try:
        from lqcs.servers_control.start_server import start_managers
        from lqcs import system_config

        node_ip = system_config.get_config()['ip']

        # 启动设备管理器（后台）
        def run_manager():
            try:
                start_managers.startServer(
                    node_ip,
                    start_managers.DeviceManagerActor,
                    'Device Manager',
                    blocking=False
                )
            except Exception as e:
                print(f"Device manager error: {e}", flush=True)

        import threading
        thread = threading.Thread(target=run_manager, daemon=True)
        thread.start()
        print("  Device Manager started", flush=True)
        results["started"].append("device_manager")
    except Exception as e:
        print(f"  Device Manager failed: {e}", flush=True)
        results["failed"].append("device_manager")
        results["errors"].append(f"Device Manager: {e}")

    # 4. 启动微波源管理器
    print("\n[4/5] Checking Uwave Manager...", flush=True)
    try:
        from lqcs.servers_control.start_server import start_uwave_server
        from lqcs import system_config

        node_ip = system_config.get_config()['ip']

        def run_manager():
            try:
                start_uwave_server.startServer(
                    node_ip,
                    start_uwave_server.UwaveServerActor,
                    'Uwave Manager',
                    blocking=False
                )
            except Exception as e:
                print(f"Uwave manager error: {e}", flush=True)

        import threading
        thread = threading.Thread(target=run_manager, daemon=True)
        thread.start()
        print("  Uwave Manager started", flush=True)
        results["started"].append("uwave_manager")
    except Exception as e:
        print(f"  Uwave Manager failed: {e}", flush=True)
        results["failed"].append("uwave_manager")
        results["errors"].append(f"Uwave Manager: {e}")

    # 5. 启动数据存储
    print("\n[5/5] Checking Data Store...", flush=True)
    try:
        from lqcs.servers_control.start_server import start_data_store
        from lqcs import system_config

        node_ip = system_config.get_config()['ip']

        def run_service():
            try:
                start_data_store.startServer(
                    node_ip,
                    start_data_store.DataStoreActor,
                    'Data Store',
                    blocking=False
                )
            except Exception as e:
                print(f"Data store error: {e}", flush=True)

        import threading
        thread = threading.Thread(target=run_service, daemon=True)
        thread.start()
        print("  Data Store started", flush=True)
        results["started"].append("datavault")
    except Exception as e:
        print(f"  Data Store failed: {e}", flush=True)
        results["failed"].append("datavault")
        results["errors"].append(f"Data Store: {e}")

    # 总结
    print("\n" + "=" * 50, flush=True)
    print("Summary:", flush=True)
    print(f"  Started: {results['started']}", flush=True)
    print(f"  Skipped: {results['skipped']}", flush=True)
    print(f"  Failed: {results['failed']}", flush=True)
    if results["errors"]:
        print("\nErrors:", flush=True)
        for err in results["errors"]:
            print(f"  - {err}", flush=True)

    return results

if __name__ == "__main__":
    results = start_services()
    # 输出 JSON 格式供 Express 解析
    print("\n[JSON]", flush=True)
    print(json.dumps(results, ensure_ascii=False), flush=True)
