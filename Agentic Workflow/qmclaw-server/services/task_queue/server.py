"""
services/task_queue/server.py - 任务队列服务

提供分布式任务队列功能：
- 任务提交和调度
- 优先级队列
- 任务状态追踪
- 重试机制
"""

import json
import time
import threading
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field, asdict
from enum import Enum
from collections import defaultdict

from ..base import BaseService, ServiceConfig, run_service, _safe_print


def _log(msg: str):
    """安全日志输出"""
    _safe_print(f"[task_queue] {msg}")


class TaskType(Enum):
    """任务类型"""
    EXPERIMENT = "experiment"
    ANALYSIS = "analysis"
    AGENT_CHAT = "agent_chat"
    WORKFLOW = "workflow"
    IMAGE_CLASSIFY = "image_classify"
    GENERAL = "general"


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"       # 等待中
    QUEUED = "queued"         # 已入队
    RUNNING = "running"       # 执行中
    COMPLETED = "completed"   # 已完成
    FAILED = "failed"         # 失败
    CANCELLED = "cancelled"   # 已取消
    TIMEOUT = "timeout"       # 超时


@dataclass
class Task:
    """任务定义"""
    id: str
    type: TaskType
    payload: Dict[str, Any]
    status: TaskStatus = TaskStatus.PENDING
    priority: int = 5  # 1-10, 10 最高
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    timeout: int = 300  # 秒
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        d = asdict(self)
        d["type"] = self.type.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Task":
        """从字典创建"""
        d["type"] = TaskType(d["type"])
        d["status"] = TaskStatus(d["status"])
        return cls(**d)


class PriorityQueue:
    """优先级队列实现（内存版，Redis 可替换）"""

    def __init__(self):
        self._queues: Dict[int, List[Task]] = defaultdict(list)
        self._task_map: Dict[str, Task] = {}
        self._lock = threading.Lock()

    def enqueue(self, task: Task) -> bool:
        """入队"""
        with self._lock:
            if task.id in self._task_map:
                return False
            self._task_map[task.id] = task
            self._queues[task.priority].append(task)
            # 按创建时间排序
            self._queues[task.priority].sort(key=lambda t: t.created_at)
            return True

    def dequeue(self) -> Optional[Task]:
        """出队（最高优先级最早的任务）"""
        with self._lock:
            # 从高优先级到低优先级查找
            for priority in range(10, 0, -1):
                if self._queues[priority]:
                    task = self._queues[priority].pop(0)
                    return task
            return None

    def get(self, task_id: str) -> Optional[Task]:
        """获取任务"""
        with self._lock:
            return self._task_map.get(task_id)

    def update(self, task: Task) -> bool:
        """更新任务"""
        with self._lock:
            if task.id not in self._task_map:
                return False
            self._task_map[task.id] = task
            return True

    def remove(self, task_id: str) -> bool:
        """移除任务"""
        with self._lock:
            if task_id not in self._task_map:
                return False
            task = self._task_map.pop(task_id)
            # 从优先级队列中移除
            if task.priority in self._queues:
                try:
                    self._queues[task.priority].remove(task)
                except ValueError:
                    pass
            return True

    def list_by_status(self, status: TaskStatus) -> List[Task]:
        """按状态列出任务"""
        with self._lock:
            return [t for t in self._task_map.values() if t.status == status]

    def list_by_type(self, task_type: TaskType) -> List[Task]:
        """按类型列出任务"""
        with self._lock:
            return [t for t in self._task_map.values() if t.type == task_type]

    def list_all(self) -> List[Task]:
        """列出所有任务"""
        with self._lock:
            return list(self._task_map.values())

    def size(self) -> int:
        """队列大小"""
        with self._lock:
            return sum(len(q) for q in self._queues.values())


class RedisBackend:
    """Redis 后端（可选，如果 Redis 可用）"""

    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0):
        self._host = host
        self._port = port
        self._db = db
        self._client = None
        self._available = False
        self._try_connect()

    def _try_connect(self):
        """尝试连接 Redis"""
        try:
            import redis
            self._client = redis.Redis(
                host=self._host,
                port=self._port,
                db=self._db,
                decode_responses=True,
                socket_timeout=2,
            )
            self._client.ping()
            self._available = True
            _log("Redis backend connected")
        except Exception as e:
            self._available = False
            _log(f"Redis not available, using memory backend: {e}")

    @property
    def is_available(self) -> bool:
        return self._available

    def set(self, key: str, value: str, expire: Optional[int] = None) -> bool:
        """设置键值"""
        if not self._available:
            return False
        try:
            if expire:
                self._client.setex(key, expire, value)
            else:
                self._client.set(key, value)
            return True
        except Exception:
            return False

    def get(self, key: str) -> Optional[str]:
        """获取值"""
        if not self._available:
            return None
        try:
            return self._client.get(key)
        except Exception:
            return None

    def delete(self, key: str) -> bool:
        """删除键"""
        if not self._available:
            return False
        try:
            self._client.delete(key)
            return True
        except Exception:
            return False

    def lpush(self, key: str, value: str) -> int:
        """左推入列表"""
        if not self._available:
            return 0
        try:
            return self._client.lpush(key, value)
        except Exception:
            return 0

    def rpop(self, key: str) -> Optional[str]:
        """右弹出列表"""
        if not self._available:
            return None
        try:
            return self._client.rpop(key)
        except Exception:
            return None

    def llen(self, key: str) -> int:
        """列表长度"""
        if not self._available:
            return 0
        try:
            return self._client.llen(key)
        except Exception:
            return 0

    def keys(self, pattern: str) -> List[str]:
        """模式匹配键"""
        if not self._available:
            return []
        try:
            return self._client.keys(pattern)
        except Exception:
            return []


class TaskQueueService(BaseService):
    """任务队列服务

    核心功能:
    - 任务提交和管理
    - 优先级调度
    - 任务状态追踪
    - 重试机制
    """

    def __init__(self, port: int = 3009):
        cfg = ServiceConfig(
            name="task_queue",
            host="localhost",
            port=port,
        )
        super().__init__(cfg)

        # 内存队列
        self._queue = PriorityQueue()
        self._lock = threading.Lock()

        # Redis 后端（可选）
        self._redis = RedisBackend()

        # 任务处理器映射
        self._handlers: Dict[TaskType, callable] = {}

        # 运行中的任务
        self._running_tasks: Dict[str, threading.Thread] = {}

        # 持久化路径
        self._persist_dir = Path(__file__).parent.parent.parent.parent / "config" / "tasks"
        self._persist_dir.mkdir(parents=True, exist_ok=True)

        _log("Task queue service initialized")
        _log(f"Redis available: {self._redis.is_available}")

    def register_handler(self, task_type: TaskType, handler: callable):
        """注册任务处理器"""
        self._handlers[task_type] = handler
        _log(f"Registered handler for: {task_type.value}")

    def _generate_id(self) -> str:
        """生成任务 ID"""
        return f"task_{uuid.uuid4().hex[:12]}"

    def _persist_task(self, task: Task):
        """持久化任务"""
        if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            # 只持久化已完成和取消的任务
            persist_path = self._persist_dir / f"{task.id}.json"
            try:
                with open(persist_path, "w", encoding="utf-8") as f:
                    json.dump(task.to_dict(), f, ensure_ascii=False, indent=2)
            except Exception as e:
                _log(f"Persist error: {e}")

    def _run_task(self, task: Task):
        """执行任务"""
        _log(f"Running task {task.id} (type={task.type.value})")

        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        self._queue.update(task)

        try:
            # 获取处理器
            handler = self._handlers.get(task.type)
            if not handler:
                # 默认处理器
                result = self._default_handler(task)
            else:
                result = handler(task)

            task.result = result
            task.status = TaskStatus.COMPLETED
            _log(f"Task {task.id} completed")

        except Exception as e:
            _log(f"Task {task.id} error: {e}\n{traceback.format_exc()}")
            task.error = str(e)

            # 检查是否需要重试
            if task.retry_count < task.max_retries:
                task.retry_count += 1
                task.status = TaskStatus.PENDING
                _log(f"Task {task.id} will retry ({task.retry_count}/{task.max_retries})")
                # 重新入队
                self._queue.enqueue(task)
            else:
                task.status = TaskStatus.FAILED

        finally:
            task.completed_at = time.time()
            self._queue.update(task)
            self._persist_task(task)

            # 移除运行记录
            with self._lock:
                self._running_tasks.pop(task.id, None)

    def _default_handler(self, task: Task) -> Dict[str, Any]:
        """默认任务处理器"""
        return {
            "message": f"Task {task.id} processed",
            "type": task.type.value,
            "payload": task.payload,
        }

    def handle_request(self, method: str, path: str, data: Dict[str, Any], query: Dict[str, List[str]]) -> Dict[str, Any]:
        """处理请求"""
        if path == "/health":
            return self._handle_health()
        elif path == "/submit":
            return self._handle_submit(data)
        elif path == "/status":
            return self._handle_status(data)
        elif path == "/list":
            return self._handle_list(query)
        elif path == "/cancel":
            return self._handle_cancel(data)
        elif path == "/retry":
            return self._handle_retry(data)
        elif path == "/stats":
            return self._handle_stats()
        elif path == "/poll":
            return self._handle_poll()
        elif path == "/complete":
            return self._handle_complete(data)
        else:
            raise ValueError(f"Unknown path: {path}")

    def _handle_health(self) -> Dict[str, Any]:
        """健康检查"""
        return {
            "status": "healthy",
            "service": "task_queue",
            "queue_size": self._queue.size(),
            "running_tasks": len(self._running_tasks),
            "redis_available": self._redis.is_available,
        }

    def _handle_submit(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """提交任务"""
        task_type_str = data.get("type", "general")
        try:
            task_type = TaskType(task_type_str)
        except ValueError:
            return {"error": f"Invalid task type: {task_type_str}"}

        payload = data.get("payload", {})
        priority = min(10, max(1, data.get("priority", 5)))
        timeout = data.get("timeout", 300)
        max_retries = data.get("max_retries", 3)
        metadata = data.get("metadata", {})

        # 创建任务
        task = Task(
            id=self._generate_id(),
            type=task_type,
            payload=payload,
            priority=priority,
            timeout=timeout,
            max_retries=max_retries,
            metadata=metadata,
        )

        # 入队
        if self._queue.enqueue(task):
            task.status = TaskStatus.QUEUED
            _log(f"Task {task.id} submitted (type={task_type.value}, priority={priority})")

            return {
                "success": True,
                "task_id": task.id,
                "status": task.status.value,
            }
        else:
            return {"error": "Failed to enqueue task"}

    def _handle_status(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """获取任务状态"""
        task_id = data.get("task_id")
        if not task_id:
            return {"error": "task_id is required"}

        task = self._queue.get(task_id)
        if not task:
            return {"error": "Task not found"}

        return {
            "task_id": task.id,
            "type": task.type.value,
            "status": task.status.value,
            "priority": task.priority,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
            "result": task.result,
            "error": task.error,
            "retry_count": task.retry_count,
        }

    def _handle_list(self, query: Dict[str, List[str]]) -> Dict[str, Any]:
        """列出任务"""
        status_filter = query.get("status", [None])[0] if query.get("status") else None
        type_filter = query.get("type", [None])[0] if query.get("type") else None
        limit = int(query.get("limit", ["100"])[0]) if query.get("limit") else 100

        tasks = self._queue.list_all()

        # 过滤
        if status_filter:
            try:
                status_enum = TaskStatus(status_filter)
                tasks = [t for t in tasks if t.status == status_enum]
            except ValueError:
                pass

        if type_filter:
            try:
                type_enum = TaskType(type_filter)
                tasks = [t for t in tasks if t.type == type_enum]
            except ValueError:
                pass

        # 排序（最新优先）
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        tasks = tasks[:limit]

        return {
            "tasks": [t.to_dict() for t in tasks],
            "count": len(tasks),
        }

    def _handle_cancel(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """取消任务"""
        task_id = data.get("task_id")
        if not task_id:
            return {"error": "task_id is required"}

        task = self._queue.get(task_id)
        if not task:
            return {"error": "Task not found"}

        if task.status == TaskStatus.RUNNING:
            return {"error": "Cannot cancel running task"}

        task.status = TaskStatus.CANCELLED
        task.completed_at = time.time()
        self._queue.update(task)
        self._persist_task(task)

        return {"success": True, "task_id": task_id}

    def _handle_retry(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """重试任务"""
        task_id = data.get("task_id")
        if not task_id:
            return {"error": "task_id is required"}

        task = self._queue.get(task_id)
        if not task:
            return {"error": "Task not found"}

        if task.status not in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            return {"error": "Can only retry failed or cancelled tasks"}

        task.status = TaskStatus.PENDING
        task.retry_count = 0
        task.error = None
        task.result = None
        task.completed_at = None

        self._queue.enqueue(task)

        return {"success": True, "task_id": task_id}

    def _handle_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        tasks = self._queue.list_all()

        stats = {
            "total": len(tasks),
            "by_status": {},
            "by_type": {},
        }

        for task in tasks:
            # 按状态统计
            status = task.status.value
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

            # 按类型统计
            task_type = task.type.value
            stats["by_type"][task_type] = stats["by_type"].get(task_type, 0) + 1

        # 计算平均执行时间
        completed = [t for t in tasks if t.completed_at and t.started_at]
        if completed:
            exec_times = [t.completed_at - t.started_at for t in completed]
            stats["avg_execution_time"] = sum(exec_times) / len(exec_times)
            stats["max_execution_time"] = max(exec_times)
            stats["min_execution_time"] = min(exec_times)

        return stats

    def _handle_poll(self) -> Dict[str, Any]:
        """轮询获取任务（工作者使用）"""
        task = self._queue.dequeue()
        if not task:
            return {"task": None}

        # 在新线程中执行
        with self._lock:
            thread = threading.Thread(target=self._run_task, args=(task,))
            thread.daemon = True
            thread.start()
            self._running_tasks[task.id] = thread

        return {
            "task": task.to_dict(),
        }

    def _handle_complete(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """标记任务完成（工作者调用）"""
        task_id = data.get("task_id")
        result = data.get("result")
        error = data.get("error")

        if not task_id:
            return {"error": "task_id is required"}

        task = self._queue.get(task_id)
        if not task:
            return {"error": "Task not found"}

        task.result = result
        task.error = error
        task.status = TaskStatus.COMPLETED if not error else TaskStatus.FAILED
        task.completed_at = time.time()

        self._queue.update(task)
        self._persist_task(task)

        return {"success": True, "task_id": task_id}

    def get_health(self) -> Dict[str, Any]:
        """获取健康状态"""
        return {
            "status": "healthy",
            "service": "task_queue",
            "queue_size": self._queue.size(),
            "running_tasks": len(self._running_tasks),
            "redis_available": self._redis.is_available,
        }


def main():
    """主入口"""
    service = TaskQueueService(port=3009)
    run_service(service)


if __name__ == "__main__":
    main()
