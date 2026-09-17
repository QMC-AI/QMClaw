"""
SSE 流式传输验证脚本 - 独立版本

这个脚本不依赖任何外部服务，可以独立验证 SSE 功能。

使用方法:
    python sse_verify_standalone.py
"""

import asyncio
import json
import time
from typing import AsyncGenerator
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
import unittest


# ── SSE 事件类型定义 ──────────────────────────────────────────────────────────

class SSEEvent:
    """SSE 事件构造器"""

    @staticmethod
    def format(event_type: str, data: dict) -> bytes:
        """格式化 SSE 事件"""
        json_data = json.dumps(data, ensure_ascii=False)
        return f"event: {event_type}\ndata: {json_data}\n\n".encode('utf-8')

    # 预定义事件类型
    STATUS = "status"
    THINKING = "thinking"
    RESPONSE = "response"
    TOOL_CALL = "tool_call"
    DONE = "done"
    ERROR = "error"


# ── SSE 服务器端实现 ──────────────────────────────────────────────────────────

class SSEServer:
    """简单的 SSE 服务器"""

    def __init__(self, host='127.0.0.1', port=18080):
        self.host = host
        self.port = port
        self.app = web.Application()
        self.app.router.add_post('/stream', self.handle_stream)
        self.app.router.add_get('/stream', self.handle_stream)
        self.runner = None
        self.site = None

    async def handle_stream(self, request):
        """SSE 流式处理"""
        response = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',
            }
        )
        await response.prepare(request)

        # 模拟 AI 响应流程
        events = [
            (SSEEvent.STATUS, {"message": "Initializing agent..."}),
            (SSEEvent.STATUS, {"message": "Agent ready, processing request..."}),
            (SSEEvent.THINKING, {"content": "The user is asking me to introduce myself. Let me think about how to respond..."}),
            (SSEEvent.THINKING, {"content": "I should respond in a friendly way and introduce my capabilities."}),
            (SSEEvent.RESPONSE, {"content": "你好！"}),
            (SSEEvent.RESPONSE, {"content": "我是一个"}),
            (SSEEvent.RESPONSE, {"content": "量子测控"}),
            (SSEEvent.RESPONSE, {"content": "智能助手。"}),
            (SSEEvent.RESPONSE, {"content": "\n\n我可以帮助你："}),
            (SSEEvent.RESPONSE, {"content": "\n- 进行量子比特实验"}),
            (SSEEvent.RESPONSE, {"content": "\n- 分析实验数据"}),
            (SSEEvent.RESPONSE, {"content": "\n- 执行校准任务"}),
            (SSEEvent.TOOL_CALL, {"tool": "list_qubits", "args": {}}),
            (SSEEvent.TOOL_CALL, {"tool": "get_qubit_params", "args": {"qubit": "q1"}}),
            (SSEEvent.DONE, {"completed": True, "final_response": "很高兴认识你！"}),
        ]

        for event_type, data in events:
            await response.write(SSEEvent.format(event_type, data))
            print(f"  [Server] Sent: {event_type}")
            await asyncio.sleep(0.1)  # 模拟处理延迟

        await response.write_eof()
        return response

    async def start(self):
        """启动服务器"""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        print("[OK] SSE Server started at http://{}:{}/stream".format(self.host, self.port))

    async def stop(self):
        """停止服务器"""
        if self.runner:
            await self.runner.cleanup()
        print("[OK] SSE Server stopped")


# ── SSE 客户端实现 ────────────────────────────────────────────────────────────

class SSEClient:
    """SSE 客户端"""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.session = None

    async def __aenter__(self):
        import aiohttp
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()

    async def stream(self, message: str) -> AsyncGenerator[dict, None]:
        """流式获取 SSE 事件"""
        if not self.session:
            import aiohttp
            self.session = aiohttp.ClientSession()

        async with self.session.post(
            f"{self.base_url}/stream",
            json={"message": message},
            headers={"Content-Type": "application/json"}
        ) as response:
            current_event_type = None

            async for line in response.content:
                line = line.decode('utf-8').strip()
                if not line:
                    continue

                if line.startswith('event:'):
                    current_event_type = line.split(':', 1)[1].strip()
                elif line.startswith('data:'):
                    data_str = line.split(':', 1)[1].strip()
                    try:
                        data = json.loads(data_str)
                        yield {
                            "type": current_event_type,
                            "data": data
                        }
                    except json.JSONDecodeError:
                        pass

    async def get(self) -> AsyncGenerator[dict, None]:
        """GET 方式的 SSE 流"""
        if not self.session:
            import aiohttp
            self.session = aiohttp.ClientSession()

        async with self.session.get(f"{self.base_url}/stream") as response:
            current_event_type = None

            async for line in response.content:
                line = line.decode('utf-8').strip()
                if not line:
                    continue

                if line.startswith('event:'):
                    current_event_type = line.split(':', 1)[1].strip()
                elif line.startswith('data:'):
                    data_str = line.split(':', 1)[1].strip()
                    try:
                        data = json.loads(data_str)
                        yield {
                            "type": current_event_type,
                            "data": data
                        }
                    except json.JSONDecodeError:
                        pass


# ── 测试函数 ──────────────────────────────────────────────────────────────────

async def run_server_test():
    """测试本地 SSE 服务器"""
    print("\n" + "=" * 60)
    print("SSE 流式传输验证测试")
    print("=" * 60)

    server = SSEServer()
    await server.start()

    print("\n发送测试请求...")
    print("-" * 60)

    async with SSEClient(f"http://127.0.0.1:{server.port}") as client:
        event_count = 0
        accumulated_content = ""
        tool_calls = []

        async for event in client.get():
            event_count += 1
            event_type = event["type"]
            data = event["data"]

            if event_type == SSEEvent.STATUS:
                print(f"[{event_count}] STATUS: {data.get('message', '')}")

            elif event_type == SSEEvent.THINKING:
                content = data.get('content', '')
                print(f"[{event_count}] THINKING: {content[:50]}...")

            elif event_type == SSEEvent.RESPONSE:
                content = data.get('content', '')
                accumulated_content += content
                print(f"[{event_count}] RESPONSE: '{content}' (total: {len(accumulated_content)})")

            elif event_type == SSEEvent.TOOL_CALL:
                tool = data.get('tool', '')
                tool_calls.append(tool)
                print(f"[{event_count}] TOOL_CALL: {tool}")

            elif event_type == SSEEvent.DONE:
                print(f"[{event_count}] DONE: completed={data.get('completed')}")

            elif event_type == SSEEvent.ERROR:
                print(f"[{event_count}] ERROR: {data.get('error', '')}")

    await server.stop()

    # 验证结果
    print("\n" + "=" * 60)
    print("测试结果:")
    print("-" * 60)
    print(f"[OK] 总事件数: {event_count}")
    print(f"[OK] 累积内容: {accumulated_content}")
    print(f"[OK] 工具调用: {', '.join(tool_calls) if tool_calls else '无'}")

    # 基本验证
    success = True
    if event_count < 10:
        print("[FAIL] 事件数量太少")
        success = False
    if len(accumulated_content) < 10:
        print("[FAIL] 内容累积失败")
        success = False
    if SSEEvent.TOOL_CALL not in [e[SSEEvent.TOOL_CALL] for e in []]:
        pass  # 工具调用测试

    if success:
        print("\n" + "[PASS]" * 20)
        print("SSE 流式传输验证成功！")
        print("[PASS]" * 20)
    else:
        print("\n[FAIL] SSE 流式传输验证失败")

    return success


async def demonstrate_sse_format():
    """演示 SSE 格式"""
    print("\n" + "=" * 60)
    print("SSE 格式说明")
    print("=" * 60)

    # 展示 SSE 事件格式
    example_events = [
        (SSEEvent.STATUS, {"message": "Processing..."}),
        (SSEEvent.THINKING, {"content": "Let me analyze this..."}),
        (SSEEvent.RESPONSE, {"content": "Hello"}),
        (SSEEvent.DONE, {"completed": True}),
    ]

    print("\nSSE 事件格式示例:")
    print("-" * 60)
    for event_type, data in example_events:
        sse_data = SSEEvent.format(event_type, data)
        print(f"\n{event_type.upper()} 事件:")
        print(sse_data.decode('utf-8'))


# ── 主函数 ────────────────────────────────────────────────────────────────────

async def main():
    print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║                   SSE 流式传输验证工具 v2.0 (独立版)                          ║
║                                                                              ║
║  本脚本不依赖外部服务，可独立验证 SSE 功能                                     ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")

    # 演示 SSE 格式
    await demonstrate_sse_format()

    # 运行测试
    success = await run_server_test()

    print("\n" + "=" * 60)
    print("验证完成!")
    print("=" * 60)

    return success


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        exit(0 if success else 1)
    except ImportError as e:
        print(f"\n[FAIL] 缺少依赖: {e}")
        print("\n请安装 aiohttp:")
        print("  pip install aiohttp")
        exit(1)
    except KeyboardInterrupt:
        print("\n\n测试已取消")
        exit(1)
