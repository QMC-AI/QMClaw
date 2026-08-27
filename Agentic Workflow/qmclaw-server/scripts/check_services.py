"""
QmClaw Service Control Script
检查测控服务状态

Usage:
    python check_services.py
"""

import sys
import json
from typing import Dict, Any

def check_port(port: int) -> tuple[bool, int | None]:
    """检查端口是否被占用"""
    import subprocess
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if sys.platform == 'win32' else 0
        )
        for line in result.stdout.split("\n"):
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                if len(parts) >= 5:
                    return True, int(parts[-1])
        return False, None
    except:
        return False, None

def get_service_status() -> Dict[str, Any]:
    """获取所有服务状态"""
    status = {
        "labrad": {},
        "ray": {},
        "overall": "unknown",
        "services": {},
        "issues": [],
    }

    # 检查 LabRAD
    occupied, pid = check_port(7682)
    status["labrad"] = {
        "port": 7682,
        "running": occupied,
        "pid": pid,
    }

    # 检查 LabRAD 连接
    if occupied:
        try:
            import labrad
            cxn = labrad.connect()
            status["labrad"]["connected"] = cxn.connected
            cxn.disconnect()
        except Exception as e:
            status["labrad"]["connected"] = False
            status["labrad"]["error"] = str(e)

    # 检查 Ray
    try:
        import ray
        status["ray"] = {
            "initialized": ray.is_initialized(),
        }
    except Exception as e:
        status["ray"] = {
            "initialized": False,
            "error": str(e),
        }

    # 检查其他端口
    ports_to_check = [
        (3001, "web"),
        (3002, "express"),
    ]

    for port, name in ports_to_check:
        occupied, pid = check_port(port)
        status["services"][name] = {
            "port": port,
            "running": occupied,
            "pid": pid,
        }

    # 判断整体状态
    if status["labrad"].get("running") and status["labrad"].get("connected"):
        if status["ray"].get("initialized"):
            status["overall"] = "ready"
        else:
            status["overall"] = "partial"
    elif status["labrad"].get("running"):
        status["overall"] = "limited"
    else:
        status["overall"] = "unavailable"

    # 收集问题
    if not status["labrad"].get("running"):
        status["issues"].append("LabRAD server not running")
    elif not status["labrad"].get("connected"):
        status["issues"].append("Cannot connect to LabRAD")

    if not status["ray"].get("initialized"):
        status["issues"].append("Ray not initialized")

    return status

if __name__ == "__main__":
    result = get_service_status()
    print(json.dumps(result, indent=2, ensure_ascii=False))
