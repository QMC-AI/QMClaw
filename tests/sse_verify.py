"""
SSE 流式传输验证脚本

这个脚本验证 Server-Sent Events 的流式传输能力。
可以用两种方式运行：
1. 启动本地 SSE 服务器并用 curl 测试
2. 直接测试远程 qmclaw-server 的 SSE 端点
"""

import asyncio
import json
import time
import sys
import argparse
from typing import AsyncGenerator

# ── 简单的 SSE 服务器实现 ──────────────────────────────────────────────────────

async def sse_server_demo():
    """演示简单的 SSE 服务器端实现"""
    import os
    from aiohttp import web

    async def sse_handler(request):
        """SSE 端点处理器"""
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

        # 模拟流式发送多个事件
        events = [
            {"event": "status", "data": {"message": "正在初始化..."}},
            {"event": "thinking", "data": {"content": "让我思考一下这个问题..."}},
            {"event": "response", "data": {"content": "第"}},
            {"event": "response", "data": {"content": "一个"}},
            {"event": "response", "data": {"content": "测试"}},
            {"event": "response", "data": {"content": "响应"}},
            {"event": "tool_call", "data": {"tool": "sq.t1", "args": {"qubit": "q10lu1"}}},
            {"event": "done", "data": {"completed": True, "final_response": "测试完成！"}},
        ]

        for i, event in enumerate(events):
            sse_data = f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
            await response.write(sse_data.encode('utf-8'))
            print(f"[Server] 发送事件 {i+1}: {event['event']}")
            await asyncio.sleep(0.5)  # 模拟延迟

        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_get('/stream', sse_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 8080)
    await site.start()

    print("=" * 60)
    print("SSE 服务器已启动: http://localhost:8080/stream")
    print("使用以下命令测试:")
    print("  curl -N http://localhost:8080/stream")
    print("按 Ctrl+C 停止服务器")
    print("=" * 60)

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n正在停止服务器...")

    await runner.cleanup()


# ── SSE 客户端实现 ────────────────────────────────────────────────────────────

async def sse_client_demo():
    """演示 SSE 客户端实现"""
    import aiohttp

    print("=" * 60)
    print("SSE 客户端演示")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        async with session.get('http://localhost:8080/stream') as response:
            print(f"状态码: {response.status}")
            print(f"Content-Type: {response.headers.get('Content-Type')}")
            print("-" * 60)

            accumulated_content = ""
            async for line in response.content:
                line = line.decode('utf-8').strip()
                if not line:
                    continue

                if line.startswith('event:'):
                    event_type = line.split(':', 1)[1].strip()
                    print(f"\n📨 收到事件: {event_type}")
                elif line.startswith('data:'):
                    data = line.split(':', 1)[1].strip()
                    try:
                        parsed = json.loads(data)
                        print(f"   数据: {json.dumps(parsed, ensure_ascii=False)[:100]}")

                        # 累积响应内容
                        if event_type == 'response' and 'content' in parsed:
                            accumulated_content += parsed['content']
                    except json.JSONDecodeError:
                        print(f"   原始数据: {data[:100]}")

            print("-" * 60)
            print(f"累积内容: {accumulated_content}")
            print("✅ SSE 流式传输完成!")


# ── 测试远程 qmclaw-server ────────────────────────────────────────────────────

async def test_remote_sse(base_url: str = "http://localhost:3002"):
    """测试远程 qmclaw-server 的 Hermes SSE 端点"""
    import aiohttp

    print("=" * 60)
    print(f"测试远程 SSE 端点: {base_url}")
    print("=" * 60)

    # 测试消息
    test_message = "你好，请简单介绍一下自己"
    session_id = f"test_sse_{int(time.time())}"

    print(f"\n发送消息: {test_message}")
    print(f"Session ID: {session_id}")
    print("-" * 60)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/api/hermes/chat/stream",
                json={
                    "message": test_message,
                    "model": "MiniMax-M2.7",
                    "session_id": session_id
                },
                headers={"Content-Type": "application/json"}
            ) as response:

                print(f"状态码: {response.status}")
                print(f"Content-Type: {response.headers.get('Content-Type')}")

                if response.status != 200:
                    print(f"❌ 请求失败: {await response.text()}")
                    return False

                event_count = 0
                accumulated_content = ""
                thinking_content = ""
                tool_calls = []

                print("\n📡 开始接收 SSE 事件流...\n")

                async for line in response.content:
                    line = line.decode('utf-8').strip()
                    if not line:
                        continue

                    # 处理 SSE 前缀 (如果有)
                    if line.startswith('SSE:'):
                        # 提取实际的 SSE 数据
                        parts = line.split('|', 1)
                        if len(parts) > 1:
                            line = parts[1].strip()

                    if line.startswith('event:'):
                        event_type = line.split(':', 1)[1].strip()
                        event_count += 1
                        print(f"[事件 {event_count}] 类型: {event_type}")

                    elif line.startswith('data:'):
                        data_str = line.split(':', 1)[1].strip()
                        try:
                            data = json.loads(data_str)
                            # 根据事件类型处理数据
                            if 'content' in data:
                                if event_type == 'thinking':
                                    thinking_content = data['content']
                                    print(f"   💭 思考: {data['content'][:80]}...")
                                elif event_type == 'response':
                                    accumulated_content += data['content']
                                    print(f"   ✨ 响应片段: '{data['content']}' (累积: {len(accumulated_content)} 字符)")
                            if 'tool' in data:
                                tool_calls.append(data['tool'])
                                print(f"   🔧 工具调用: {data['tool']}")
                            if 'message' in data and event_type == 'status':
                                print(f"   📌 状态: {data['message']}")
                            if 'error' in data:
                                print(f"   ❌ 错误: {data['error']}")
                        except json.JSONDecodeError as e:
                            print(f"   ⚠️ JSON 解析失败: {e}, 原始数据: {data_str[:50]}")

                print("\n" + "-" * 60)
                print(f"📊 统计:")
                print(f"   总事件数: {event_count}")
                print(f"   思考内容: {thinking_content[:100]}..." if thinking_content else "   思考内容: 无")
                print(f"   最终响应 ({len(accumulated_content)} 字符):")
                print(f"   {accumulated_content[:200]}..." if len(accumulated_content) > 200 else f"   {accumulated_content}")
                print(f"   工具调用: {', '.join(tool_calls) if tool_calls else '无'}")
                print("-" * 60)
                print("✅ SSE 流式传输测试完成!")

                return True

    except aiohttp.ClientError as e:
        print(f"\n❌ 连接失败: {e}")
        print("\n请确保 qmclaw-server 正在运行 (默认: http://localhost:3002)")
        return False
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


# ── 使用 curl 测试 ────────────────────────────────────────────────────────────

def print_curl_test():
    """打印 curl 测试命令"""
    print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║                         SSE 流式传输 curl 测试命令                            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  1. 测试本地演示服务器 (需要先启动):                                          ║
║     curl -N http://localhost:8080/stream                                      ║
║                                                                              ║
║  2. 测试 qmclaw-server 的 Hermes SSE 端点:                                    ║
║     curl -N -X POST http://localhost:3002/api/hermes/chat/stream \\            ║
║       -H "Content-Type: application/json" \\                                   ║
║       -d '{"message":"你好","model":"MiniMax-M2.7"}'                          ║
║                                                                              ║
║  3. 参数说明:                                                                ║
║     -N, --no-buffer: 禁用输出缓冲，实时显示流                                  ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")


# ── 主函数 ────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="SSE 流式传输验证工具")
    parser.add_argument(
        'mode',
        nargs='?',
        choices=['server', 'client', 'remote', 'curl', 'all'],
        default='curl',
        help='运行模式: server(本地服务器), client(客户端), remote(测试远程), curl(显示命令), all(全部)'
    )
    parser.add_argument(
        '--url',
        default='http://localhost:3002',
        help='远程服务器地址 (用于 remote 模式)'
    )

    args = parser.parse_args()

    if args.mode == 'server':
        print("🚀 启动本地 SSE 服务器...")
        await sse_server_demo()

    elif args.mode == 'client':
        print("🔌 启动 SSE 客户端...")
        try:
            await sse_client_demo()
        except ImportError:
            print("❌ 需要安装 aiohttp: pip install aiohttp")
            print("   或者使用 curl 模式测试")

    elif args.mode == 'remote':
        print(f"🌐 测试远程服务器: {args.url}")
        success = await test_remote_sse(args.url)
        sys.exit(0 if success else 1)

    elif args.mode == 'curl':
        print_curl_test()

    elif args.mode == 'all':
        print_curl_test()
        print("\n" + "=" * 60)
        print("开始远程测试...")
        print("=" * 60)
        success = await test_remote_sse(args.url)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        SSE 流式传输验证工具 v1.0                              ║
║                                                                              ║
║  使用方法:                                                                   ║
║    python sse_verify.py curl        # 显示 curl 测试命令                     ║
║    python sse_verify.py remote      # 测试远程 qmclaw-server                 ║
║    python sse_verify.py server      # 启动本地 SSE 服务器 (需要 aiohttp)      ║
║    python sse_verify.py all         # 显示命令 + 运行远程测试                 ║
║                                                                              ║
║  依赖安装:                                                                   ║
║    pip install aiohttp                                                             ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
    asyncio.run(main())
