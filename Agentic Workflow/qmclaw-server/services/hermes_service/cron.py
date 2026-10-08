"""
services/hermes_service/cron.py - QMClaw Cron 定时任务模块

支持定时任务的 CRUD 和手动触发执行。
使用 APScheduler 进行调度。
"""

import asyncio
import json
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

# APScheduler 导入
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False
    BackgroundScheduler = None
    CronTrigger = None
    EVENT_JOB_EXECUTED = None
    EVENT_JOB_ERROR = None

# 任务类型
TaskType = Literal["quantum", "hermes", "script"]

# 任务状态
JobState = Literal["scheduled", "running", "paused", "completed", "error"]


@dataclass
class CronJob:
    """Cron 任务"""
    id: str
    name: str
    task_type: TaskType
    schedule: str  # cron 表达式
    prompt: str  # 任务描述/参数
    qubit: Optional[str] = None  # 指定的量子比特（用于量子测控任务）
    enabled: bool = True
    state: JobState = "scheduled"
    created_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    model: Optional[str] = None
    skills: list[str] = field(default_factory=list)
    repeat_total: Optional[int] = None
    repeat_completed: int = 0


@dataclass
class CronState:
    """Cron 状态"""
    jobs: list[CronJob] = field(default_factory=list)
    updated_at: Optional[datetime] = None

    @property
    def total(self) -> int:
        return len(self.jobs)

    @property
    def active(self) -> int:
        return sum(1 for j in self.jobs if j.enabled and j.state == "scheduled")

    @property
    def paused(self) -> int:
        return sum(1 for j in self.jobs if not j.enabled or j.state == "paused")

    @property
    def has_errors(self) -> bool:
        return any(j.last_error for j in self.jobs)


# 全局调度器和锁
_scheduler = None
_scheduler_lock = threading.Lock()

# WebSocket 通知管理器（延迟导入以避免循环依赖）
_ws_manager = None

def _get_ws_manager():
    """获取 WebSocket 通知管理器"""
    global _ws_manager
    if _ws_manager is None:
        try:
            from .approval_ws import WebSocketApprovalManager
            _ws_manager = WebSocketApprovalManager()
        except Exception as e:
            _log(f"Failed to get WebSocket manager: {e}")
            return None
    return _ws_manager


def _log(msg: str):
    """安全日志输出"""
    try:
        from ..base import _safe_print
        _safe_print(f"[cron] {msg}")
    except Exception:
        print(f"[cron] {msg}")


def _init_scheduler() -> Optional[BackgroundScheduler]:
    """初始化 APScheduler 调度器"""
    global _scheduler

    if not APSCHEDULER_AVAILABLE:
        _log("APScheduler not available, using manual mode")
        return None

    if _scheduler is None:
        with _scheduler_lock:
            if _scheduler is None:
                _scheduler = BackgroundScheduler(
                    timezone="local",
                    job_defaults={
                        "coalesce": True,       # 合并错过的执行
                        "max_instances": 1,      # 同一任务最多一个实例
                        "misfire_grace_time": 60,
                    }
                )

                # 监听任务执行事件
                _scheduler.add_listener(
                    _on_job_executed,
                    EVENT_JOB_EXECUTED | EVENT_JOB_ERROR
                )
                _scheduler.start()
                _log("APScheduler started")

    return _scheduler


def _on_job_executed(event):
    """任务执行完成回调"""
    job_id = event.job_id
    exception = event.exception
    _log(f"Job {job_id} executed, exception: {exception}")

    # 更新 jobs.json 中的状态
    try:
        jobs_data = _load_jobs()
        for job_dict in jobs_data:
            if job_dict["id"] == job_id:
                job_dict["last_run_at"] = datetime.now().isoformat()
                job_dict["last_status"] = "success" if exception is None else "error"
                job_dict["last_error"] = str(exception) if exception else None
                job_dict["state"] = "scheduled"
                break
        _save_jobs(jobs_data)

        # 通知 WebSocket 客户端
        _notify_job_result(job_id, exception is None, str(exception) if exception else None)
    except Exception as e:
        _log(f"Failed to update job status: {e}")


def _get_data_dir() -> Path:
    """获取 QMClaw Cron 数据目录"""
    current = Path(__file__).parent  # hermes_service
    current = current.parent  # services
    current = current.parent  # qmclaw-server
    data_dir = current / "data" / "cron"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _get_jobs_file() -> Path:
    """获取 jobs.json 路径"""
    return _get_data_dir() / "jobs.json"


def _load_jobs() -> list[dict]:
    """加载 jobs.json"""
    jobs_file = _get_jobs_file()
    if not jobs_file.exists():
        return []
    try:
        data = json.loads(jobs_file.read_text(encoding="utf-8"))
        return data.get("jobs", [])
    except (json.JSONDecodeError, OSError):
        return []


def _save_jobs(jobs: list[dict]) -> None:
    """保存 jobs.json"""
    jobs_file = _get_jobs_file()
    jobs_file.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "jobs": jobs,
        "updated_at": datetime.now().isoformat(),
    }
    jobs_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _job_to_dict(job: CronJob) -> dict:
    """将 CronJob 转换为字典"""
    return {
        "id": job.id,
        "name": job.name,
        "task_type": job.task_type,
        "schedule": job.schedule,
        "prompt": job.prompt,
        "qubit": job.qubit,
        "enabled": job.enabled,
        "state": job.state,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None,
        "last_run_at": job.last_run_at.isoformat() if job.last_run_at else None,
        "last_status": job.last_status,
        "last_error": job.last_error,
        "model": job.model,
        "skills": job.skills,
        "repeat_total": job.repeat_total,
        "repeat_completed": job.repeat_completed,
    }


def _dict_to_job(d: dict) -> CronJob:
    """将字典转换为 CronJob"""
    return CronJob(
        id=d.get("id", ""),
        name=d.get("name", "unnamed"),
        task_type=d.get("task_type", "quantum"),
        schedule=d.get("schedule", ""),
        prompt=d.get("prompt", ""),
        qubit=d.get("qubit"),
        enabled=d.get("enabled", True),
        state=d.get("state", "scheduled"),
        created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else None,
        next_run_at=datetime.fromisoformat(d["next_run_at"]) if d.get("next_run_at") else None,
        last_run_at=datetime.fromisoformat(d["last_run_at"]) if d.get("last_run_at") else None,
        last_status=d.get("last_status"),
        last_error=d.get("last_error"),
        model=d.get("model"),
        skills=d.get("skills", []),
        repeat_total=d.get("repeat_total"),
        repeat_completed=d.get("repeat_completed", 0),
    )


def collect_cron() -> CronState:
    """收集 Cron 状态"""
    jobs_data = _load_jobs()
    jobs = [_dict_to_job(d) for d in jobs_data]
    return CronState(jobs=jobs, updated_at=datetime.now())


def get_cron_state() -> dict:
    """获取完整 Cron 状态"""
    state = collect_cron()
    return {
        "jobs": [_job_to_dict(j) for j in state.jobs],
        "total": state.total,
        "active": state.active,
        "paused": state.paused,
        "has_errors": state.has_errors,
        "updated_at": state.updated_at.isoformat() if state.updated_at else None,
    }


def create_job(
    name: str,
    schedule: str,
    task_type: TaskType,
    prompt: str = "",
    model: Optional[str] = None,
    skills: Optional[list[str]] = None,
    qubit: Optional[str] = None,
) -> dict:
    """创建新任务"""
    if not name.strip():
        raise ValueError("name cannot be empty")
    if not schedule.strip():
        raise ValueError("schedule cannot be empty")

    job_id = str(uuid.uuid4())[:8]

    job = CronJob(
        id=job_id,
        name=name.strip(),
        task_type=task_type,
        schedule=schedule.strip(),
        prompt=prompt,
        qubit=qubit,
        model=model,
        skills=skills or [],
        created_at=datetime.now(),
        state="scheduled",
    )

    # 保存到 jobs.json
    jobs_data = _load_jobs()
    jobs_data.append(_job_to_dict(job))
    _save_jobs(jobs_data)

    # 注册到调度器
    _schedule_job(job)

    return {"ok": True, "job": _job_to_dict(job)}


def update_job(
    job_id: str,
    name: Optional[str] = None,
    schedule: Optional[str] = None,
    prompt: Optional[str] = None,
    enabled: Optional[bool] = None,
    qubit: Optional[str] = None,
) -> dict:
    """更新任务"""
    jobs_data = _load_jobs()
    for i, job_dict in enumerate(jobs_data):
        if job_dict["id"] == job_id:
            if name is not None:
                job_dict["name"] = name.strip()
            if schedule is not None:
                job_dict["schedule"] = schedule.strip()
                # 重新调度任务
                job = _dict_to_job(job_dict)
                _schedule_job(job)
            if prompt is not None:
                job_dict["prompt"] = prompt
            if enabled is not None:
                job_dict["enabled"] = enabled
                if enabled:
                    job_dict["state"] = "scheduled"
                    # 重新调度
                    job = _dict_to_job(job_dict)
                    _schedule_job(job)
                else:
                    job_dict["state"] = "paused"
                    # 取消调度
                    _unschedule_job(job_id)
            if qubit is not None:
                job_dict["qubit"] = qubit
            _save_jobs(jobs_data)
            return {"ok": True, "job": job_dict}

    raise ValueError(f"Job not found: {job_id}")


def delete_job(job_id: str) -> dict:
    """删除任务"""
    # 先从调度器移除
    _unschedule_job(job_id)

    jobs_data = _load_jobs()
    original_len = len(jobs_data)
    jobs_data = [j for j in jobs_data if j["id"] != job_id]

    if len(jobs_data) == original_len:
        raise ValueError(f"Job not found: {job_id}")

    _save_jobs(jobs_data)
    return {"ok": True}


def pause_job(job_id: str) -> dict:
    """暂停任务"""
    # 从调度器移除
    _unschedule_job(job_id)
    return update_job(job_id, enabled=False)


def resume_job(job_id: str) -> dict:
    """恢复任务"""
    result = update_job(job_id, enabled=True)
    # 重新调度
    jobs_data = _load_jobs()
    for job_dict in jobs_data:
        if job_dict["id"] == job_id:
            job = _dict_to_job(job_dict)
            _schedule_job(job)
            break
    return result


def run_job_now(job_id: str) -> dict:
    """立即运行任务（手动触发）"""
    jobs_data = _load_jobs()
    for job_dict in jobs_data:
        if job_dict["id"] == job_id:
            job = _dict_to_job(job_dict)

            # 更新状态为运行中
            job.state = "running"
            job.last_run_at = datetime.now()

            # 执行任务
            try:
                result = _execute_job(job)
                job.last_status = "success"
                job.last_error = None
            except Exception as e:
                job.last_status = "error"
                job.last_error = str(e)

            # 更新 jobs.json
            for j in jobs_data:
                if j["id"] == job_id:
                    j.update(_job_to_dict(job))
                    break
            _save_jobs(jobs_data)

            return {"ok": True, "job": _job_to_dict(job), "result": result}

    raise ValueError(f"Job not found: {job_id}")


def _schedule_job(job: CronJob) -> bool:
    """将任务添加到调度器"""
    if not APSCHEDULER_AVAILABLE:
        return False

    scheduler = _init_scheduler()
    if scheduler is None:
        return False

    try:
        trigger = CronTrigger.from_crontab(job.schedule)
        scheduler.add_job(
            func=_execute_job_async,
            trigger=trigger,
            id=job.id,
            name=job.name,
            args=[job.id],
            replace_existing=True,
        )
        _log(f"Scheduled job {job.id}: {job.name} ({job.schedule})")
        return True
    except Exception as e:
        _log(f"Failed to schedule job {job.id}: {e}")
        return False


def _unschedule_job(job_id: str) -> bool:
    """从调度器移除任务"""
    if not APSCHEDULER_AVAILABLE:
        return False

    scheduler = _init_scheduler()
    if scheduler is None:
        return False

    try:
        scheduler.remove_job(job_id)
        _log(f"Unscheduled job {job_id}")
        return True
    except Exception:
        # Job might not be scheduled
        return False


def _execute_job_async(job_id: str) -> None:
    """异步执行任务（调度器回调）"""
    # 从 jobs.json 读取最新配置
    jobs_data = _load_jobs()
    job_dict = None
    for jd in jobs_data:
        if jd["id"] == job_id:
            job_dict = jd
            break

    if job_dict is None:
        _log(f"Job {job_id} not found, skipping execution")
        return

    if not job_dict.get("enabled", True):
        _log(f"Job {job_id} is disabled, skipping execution")
        return

    job = _dict_to_job(job_dict)
    job.state = "running"
    job.last_run_at = datetime.now()

    try:
        result = _execute_job(job)
        job.last_status = "success"
        job.last_error = None
        _log(f"Job {job_id} executed successfully")
    except Exception as e:
        job.last_status = "error"
        job.last_error = str(e)
        _log(f"Job {job_id} execution failed: {e}")

    # 更新 jobs.json
    for jd in jobs_data:
        if jd["id"] == job_id:
            jd.update(_job_to_dict(job))
            break
    _save_jobs(jobs_data)


def _notify_job_result(job_id: str, success: bool, error: Optional[str] = None) -> None:
    """通过 WebSocket 发送任务结果"""
    # 获取任务信息
    jobs_data = _load_jobs()
    job_dict = None
    for jd in jobs_data:
        if jd["id"] == job_id:
            job_dict = jd
            break

    if job_dict is None:
        return

    event = {
        "type": "cron_job_result",
        "job_id": job_id,
        "job_name": job_dict.get("name", ""),
        "task_type": job_dict.get("task_type", ""),
        "success": success,
        "error": error,
        "last_run_at": datetime.now().isoformat(),
    }

    # 异步发送 WebSocket 通知
    try:
        ws_manager = _get_ws_manager()
        if ws_manager:
            asyncio.create_task(ws_manager.broadcast(event))
    except Exception as e:
        _log(f"Failed to notify WebSocket: {e}")


def _restore_scheduled_jobs() -> int:
    """启动时恢复所有启用的任务到调度器"""
    if not APSCHEDULER_AVAILABLE:
        return 0

    jobs_data = _load_jobs()
    count = 0
    for job_dict in jobs_data:
        if job_dict.get("enabled", True):
            job = _dict_to_job(job_dict)
            if _schedule_job(job):
                count += 1

    _log(f"Restored {count} scheduled jobs")
    return count


def shutdown_scheduler() -> None:
    """关闭调度器（用于应用退出）"""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        _log("Scheduler shutdown")


def init_cron() -> int:
    """初始化 Cron 模块 - 恢复已调度的任务"""
    return _restore_scheduled_jobs()


def _execute_job(job: CronJob) -> dict:
    """执行任务"""
    if job.task_type == "quantum":
        # 调用量子服务
        return _execute_quantum_job(job)
    elif job.task_type == "hermes":
        # 调用 Hermes
        return _execute_hermes_job(job)
    elif job.task_type == "script":
        # 执行脚本
        return _execute_script_job(job)
    else:
        raise ValueError(f"Unknown task type: {job.task_type}")


def _execute_quantum_job(job: CronJob) -> dict:
    """执行量子测控任务"""
    # 调用 quantum_service 执行 prompt 中的指令
    quantum_url = "http://localhost:3003/execute"

    payload = {
        "prompt": job.prompt,
    }

    # 如果指定了量子比特，加入参数
    if job.qubit:
        payload["qubit"] = job.qubit

    data = json.dumps(payload).encode("utf-8")

    try:
        req = urllib.request.Request(quantum_url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read().decode())
            _log(f"Quantum job {job.id} completed: {result}")
            return result
    except urllib.error.URLError as e:
        _log(f"Quantum service unreachable: {e}")
        raise RuntimeError(f"Quantum service unavailable: {e}")
    except Exception as e:
        _log(f"Quantum job {job.id} failed: {e}")
        raise


def _execute_hermes_job(job: CronJob) -> dict:
    """执行 Hermes Agent 任务"""
    # 调用 hermes_service 的 /chat 接口
    # 使用专用的 cron session_id
    hermes_url = "http://localhost:3012/chat"

    payload = {
        "message": job.prompt,
        "session_id": f"cron_{job.id}",
    }

    # 如果指定了模型，使用该模型
    if job.model:
        payload["model"] = job.model

    # 如果指定了 skills，加入参数
    if job.skills:
        payload["skills"] = job.skills

    data = json.dumps(payload).encode("utf-8")

    try:
        req = urllib.request.Request(hermes_url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=600) as resp:
            result = json.loads(resp.read().decode())
            _log(f"Hermes job {job.id} completed")
            return result
    except urllib.error.URLError as e:
        _log(f"Hermes service unreachable: {e}")
        raise RuntimeError(f"Hermes service unavailable: {e}")
    except Exception as e:
        _log(f"Hermes job {job.id} failed: {e}")
        raise


def _execute_script_job(job: CronJob) -> dict:
    """执行自定义脚本任务"""
    import subprocess

    try:
        # 在 Windows 上执行命令
        # prompt 字段包含要执行的命令
        result = subprocess.run(
            job.prompt,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,
            encoding="utf-8",
            errors="replace",
        )

        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        raise RuntimeError("Script execution timed out")
    except Exception as e:
        _log(f"Script job {job.id} failed: {e}")
        raise
