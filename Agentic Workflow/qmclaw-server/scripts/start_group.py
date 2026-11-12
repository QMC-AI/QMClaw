"""scripts/start_group.py - 按"进程组"启动服务（端口税，路线图 §3 #6）

Usage:
    python scripts/start_group.py --list
    python scripts/start_group.py --group core --dry-run
    python scripts/start_group.py --group core

`core` 组（workflow :3008 + task_queue :3009）会在**同一个 Python 进程**里
各起一个 HTTP server（每服务一个线程），因此改 `dsl/` 或账本逻辑后只需重启
一次这个进程。端口号一个都不变，调用方无需改 URL。

为什么只合 `core`：`services/common/service_groups.py` 里逐条写了阻塞理由
（LabRAD/Ray 独占、import 副作用、线程池争用……）。不要把设备服务塞进来。

退出码：0 正常停止；1 启动/运行失败；2 用法或定义有问题（含不可合并的组）。
"""

from __future__ import annotations

import argparse
import importlib
import signal
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.common.service_groups import (  # noqa: E402
    GROUPS,
    SERVICE_ENTRYPOINTS,
    SERVICE_PORTS,
    describe_groups,
    plan_group,
)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def build_service(name: str):
    """按登记表实例化服务对象（不启动）。"""
    module_name, class_name = SERVICE_ENTRYPOINTS[name]
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    return cls(port=SERVICE_PORTS[name])


def print_groups() -> None:
    for item in describe_groups():
        mode = "同进程" if item["merged"] else "独立进程"
        ports = ", ".join(f"{n}:{p}" for n, p in item["ports"].items())
        print(f"- {item['group']:<8} [{mode}] {item['description']}")
        print(f"          成员 {ports}")


def run_group(name: str, dry_run: bool = False) -> int:
    plan = plan_group(name)
    if not plan["known"]:
        print(f"[start_group] 未知进程组：{name!r}（可用：{', '.join(sorted(GROUPS))}）")
        return 2
    if plan["conflicts"]:
        print("[start_group] 组定义有问题：")
        for problem in plan["conflicts"]:
            print("  -", problem)
        return 2

    print(f"[start_group] 组 {plan['group']}：{', '.join(plan['members'])}")
    if dry_run:
        mode = "同进程" if plan["merged"] else "独立进程"
        print(f"[start_group] 计划模式：{mode}")
        for member, port in plan["ports"].items():
            print(f"  - {member} -> :{port}")
        return 0

    if not plan["merged"]:
        print("[start_group] 该组不能同进程启动：")
        for member, reason in (plan["blockers"] or {}).items():
            print(f"  - {member}: {reason}")
        print("[start_group] 请用 scripts/start_all_services.py 分别启动。")
        return 2

    services = []
    for member in plan["members"]:
        try:
            services.append((member, build_service(member)))
        except Exception as exc:
            print(f"[start_group] 构造 {member} 失败：{exc}")
            return 1

    stop_event = threading.Event()

    def _handle_signal(signum, _frame):
        print(f"[start_group] 收到信号 {signum}，正在停止整组…")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except Exception:
            pass

    threads = []
    for member, service in services:
        thread = threading.Thread(target=service.run, name=f"svc-{member}", daemon=True)
        thread.start()
        threads.append((member, thread))

    # 等所有服务把端口开起来（最多 15 s），否则直接判失败。
    deadline = time.time() + 15
    while time.time() < deadline:
        if all(getattr(svc, "status", None) is not None and getattr(svc, "start_time", 0)
               for _, svc in services):
            break
        time.sleep(0.1)

    for member, service in services:
        port = getattr(getattr(service, "config", None), "port", "?")
        print(f"[start_group] {member} 已在 :{port} 启动（同进程）")

    code = 0
    while not stop_event.is_set():
        for member, thread in threads:
            if not thread.is_alive():
                print(f"[start_group] {member} 的线程已退出，停止整组")
                stop_event.set()
                code = 1
                break
        time.sleep(0.5)

    for _, service in services:
        try:
            service.stop()
        except Exception as exc:
            print(f"[start_group] 停止服务时出错：{exc}")
    for _, thread in threads:
        thread.join(timeout=5)
    print("[start_group] 整组已停止")
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description="按进程组启动微服务（端口不变，一个进程多个服务）")
    parser.add_argument("--group", help="进程组名（core / lab）")
    parser.add_argument("--list", action="store_true", help="列出所有进程组")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不启动")
    args = parser.parse_args()

    if args.list or not args.group:
        print_groups()
        return 0
    return run_group(args.group, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
