# SPDX-FileCopyrightText: Copyright (c) 2026 QMClaw. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
WebSocket Approval Manager for Hermes Service

管理 WebSocket 连接和 Approval 确认流程。
"""

import asyncio
import logging
import threading
from typing import Dict, Optional, Callable, Awaitable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

# Approval 超时时间（秒）
APPROVAL_TIMEOUT = 120


class ApprovalResult(Enum):
    """Approval 响应类型"""
    APPROVE = "approve"      # 一次性批准
    DENY = "deny"           # 拒绝
    SESSION = "session"     # 本次会话批准
    ALWAYS = "always"        # 始终批准
    TIMEOUT = "timeout"      # 超时


@dataclass
class ApprovalRequest:
    """Approval 请求"""
    command: str
    description: str
    timeout: int = APPROVAL_TIMEOUT
    future: asyncio.Future = field(default_factory=asyncio.Future)
    created_at: float = field(default_factory=lambda: asyncio.get_event_loop().time())


class WebSocketApprovalManager:
    """管理 WebSocket 连接和 Approval 确认

    这个管理器负责：
    1. 管理 WebSocket 连接
    2. 处理 Approval 请求和响应
    3. 处理 Clarify 回调
    """

    def __init__(self):
        self._connections: Dict[str, 'WebSocket'] = {}
        self._pending_approvals: Dict[str, ApprovalRequest] = {}
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """获取事件循环"""
        if self._loop is None:
            self._loop = asyncio.get_event_loop()
        return self._loop

    async def register(self, session_id: str, websocket: 'WebSocket'):
        """注册 WebSocket 连接

        Args:
            session_id: 会话 ID
            websocket: WebSocket 连接（新版 websockets 库已自动接受连接）
        """
        # 新版 websockets 库：连接已在 handler 中自动接受，无需调用 accept()
        with self._lock:
            self._connections[session_id] = websocket
        logger.info(f"WebSocket connected: session={session_id}")

    async def unregister(self, session_id: str):
        """注销 WebSocket 连接

        Args:
            session_id: 会话 ID
        """
        with self._lock:
            websocket = self._connections.pop(session_id, None)

            # 取消待处理的 approval
            if session_id in self._pending_approvals:
                request = self._pending_approvals.pop(session_id)
                if not request.future.done():
                    request.future.set_result(ApprovalResult.DENY.value)

        if websocket:
            logger.info(f"WebSocket disconnected: session={session_id}")

    async def request_approval(
        self,
        session_id: str,
        command: str,
        description: str,
        timeout: int = APPROVAL_TIMEOUT
    ) -> str:
        """请求 Approval 确认

        Args:
            session_id: 会话 ID
            command: 要执行的命令
            description: 命令描述
            timeout: 超时时间（秒）

        Returns:
            ApprovalResult 值之一

        Raises:
            asyncio.TimeoutError: 超时
        """
        websocket = self._connections.get(session_id)
        if not websocket:
            logger.warning(f"No WebSocket for approval: session={session_id}")
            return ApprovalResult.DENY.value

        # 创建 Future 等待响应
        request = ApprovalRequest(
            command=command,
            description=description,
            timeout=timeout,
        )

        with self._lock:
            self._pending_approvals[session_id] = request

        try:
            # 发送 Approval 请求到前端
            await websocket.send_json({
                "type": "approval_request",
                "command": command,
                "description": description,
                "timeout": timeout,
            })

            logger.info(f"Approval requested: session={session_id}, command={command[:50]}...")

            # 等待响应（带超时）
            try:
                result = await asyncio.wait_for(
                    request.future,
                    timeout=timeout
                )
                logger.info(f"Approval response: session={session_id}, result={result}")
                return result
            except asyncio.TimeoutError:
                logger.warning(f"Approval timeout: session={session_id}")
                # 发送超时通知
                await websocket.send_json({
                    "type": "approval_timeout",
                    "command": command,
                })
                return ApprovalResult.TIMEOUT.value

        finally:
            with self._lock:
                self._pending_approvals.pop(session_id, None)

    async def handle_response(self, session_id: str, response: str):
        """处理用户响应

        Args:
            session_id: 会话 ID
            response: 响应（approve/deny/session/always）
        """
        with self._lock:
            request = self._pending_approvals.get(session_id)

        if request and not request.future.done():
            request.future.set_result(response)
            logger.info(f"Approval response received: session={session_id}, response={response}")
        else:
            logger.warning(f"No pending approval for response: session={session_id}")

    async def handle_clarify(
        self,
        session_id: str,
        question: str,
        choices: list = None,
        multi_select: bool = False
    ) -> str:
        """处理 Clarify 回调

        Args:
            session_id: 会话 ID
            question: 问题
            choices: 选项列表
            multi_select: 是否多选

        Returns:
            用户选择的答案
        """
        description = question
        if choices:
            description = f"{question}\n\n选项: {', '.join(choices)}"

        return await self.request_approval(
            session_id=session_id,
            command="clarify",
            description=description,
        )

    async def emit_event(self, session_id: str, event_type: str, data: any):
        """发送事件到 WebSocket

        Args:
            session_id: 会话 ID
            event_type: 事件类型
            data: 事件数据
        """
        websocket = self._connections.get(session_id)
        if websocket:
            try:
                # 使用 websockets 库兼容的发送方式
                import json
                msg = json.dumps({
                    "type": event_type,
                    "data": data,
                })
                await websocket.send(msg)
            except Exception as e:
                logger.warning(f"Failed to send event: session={session_id}, error={e}")

    def is_connected(self, session_id: str) -> bool:
        """检查会话是否已连接

        Args:
            session_id: 会话 ID

        Returns:
            是否已连接
        """
        with self._lock:
            return session_id in self._connections

    def get_pending_approval(self, session_id: str) -> Optional[ApprovalRequest]:
        """获取待处理的 Approval 请求

        Args:
            session_id: 会话 ID

        Returns:
            ApprovalRequest 或 None
        """
        with self._lock:
            return self._pending_approvals.get(session_id)

    def get_connection_count(self) -> int:
        """获取当前连接数

        Returns:
            WebSocket 连接数
        """
        with self._lock:
            return len(self._connections)

    def get_pending_count(self) -> int:
        """获取待处理的 Approval 数

        Returns:
            待处理 Approval 数
        """
        with self._lock:
            return len(self._pending_approvals)

    async def broadcast(self, event: dict) -> int:
        """广播事件到所有连接的 WebSocket 客户端

        Args:
            event: 要广播的事件数据（包含 type 字段）

        Returns:
            成功发送的客户端数量
        """
        import json
        sent_count = 0
        with self._lock:
            connections = list(self._connections.items())

        for session_id, websocket in connections:
            try:
                await websocket.send_json(event)
                sent_count += 1
            except Exception as e:
                logger.warning(f"Failed to broadcast to {session_id}: {e}")

        logger.info(f"Broadcast sent to {sent_count} clients: {event.get('type', 'unknown')}")
        return sent_count


# 类型提示（避免循环导入）
# 注意：实际运行时使用 websockets 库的 WebSocket 类型
# from fastapi import WebSocket  # 不再使用 FastAPI WebSocket
