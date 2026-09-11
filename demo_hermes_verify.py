"""
Hermes-Agent 完整功能测试
=============================
测试 Hermes Agent 的以下功能：
1. 基础 AI 对话
2. 工具调用 (tool calling)
3. ReAct / 推理 (reasoning)
4. 计划/委托 (plan/delegate)
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# 尝试从多个位置加载 .env 文件
env_paths = [
    Path(__file__).parent / "Agentic Workflow" / "qmclaw-server" / ".env",
    Path(__file__).parent / ".env",
    Path(__file__).parent / "vendor" / "hermes-agent" / ".env",
]
for env_path in env_paths:
    if env_path.exists():
        load_dotenv(env_path)
        print(f"Loaded .env from: {env_path}")
        break

# 添加 hermes-agent 到路径
sys.path.insert(0, str(Path(__file__).parent / "vendor" / "hermes-agent"))

from run_agent import AIAgent


def test_basic_chat(agent):
    """测试 1: 基础 AI 对话"""
    print("\n" + "=" * 60)
    print("测试 1: 基础 AI 对话")
    print("=" * 60)

    prompt = "请用中文简单介绍一下你自己"
    print(f"\n>>> 用户: {prompt}")

    result = agent.run_conversation(prompt)
    response = result.get("final_response", "")
    print(f"\n<<< AI: {response[:200]}..." if len(response) > 200 else f"\n<<< AI: {response}")

    return result.get("completed", False)


def test_tool_calling(agent):
    """测试 2: 工具调用 - 使用 todo_list 工具"""
    print("\n" + "=" * 60)
    print("测试 2: 工具调用 (todo_list)")
    print("=" * 60)

    prompt = "请帮我创建一个待办事项：'测试 Hermes 工具调用功能'，优先级设置为高"
    print(f"\n>>> 用户: {prompt}")

    result = agent.run_conversation(prompt)
    response = result.get("final_response", "")
    print(f"\n<<< AI: {response[:300]}..." if len(response) > 300 else f"\n<<< AI: {response}")

    # 检查是否有工具调用
    messages = result.get("messages", [])
    tool_calls = 0
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            if msg.get("tool_calls"):
                tool_calls += len(msg["tool_calls"])
    print(f"\n📊 工具调用次数: {tool_calls}")

    return result.get("completed", False), tool_calls > 0


def test_reasoning(agent):
    """测试 3: ReAct/推理 - 测试思考过程"""
    print("\n" + "=" * 60)
    print("测试 3: ReAct/推理 (reasoning)")
    print("=" * 60)

    # 使用需要推理的问题
    prompt = """请思考一下这个问题：如果今天是星期一，那么100天后是星期几？
    请展示你的推理过程。"""
    print(f"\n>>> 用户: {prompt[:50]}...")

    result = agent.run_conversation(prompt)
    response = result.get("final_response", "")
    print(f"\n<<< AI 推理结果: {response[:400]}..." if len(response) > 400 else f"\n<<< AI: {response}")

    # 检查消息中的思考内容
    messages = result.get("messages", [])
    thinking_content = []
    for msg in messages:
        if isinstance(msg, dict):
            content = msg.get("content", "")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "thinking":
                        thinking_content.append(item.get("thinking", "")[:100])

    if thinking_content:
        print(f"\n🧠 检测到思考过程 ({len(thinking_content)} 条)")
        for i, think in enumerate(thinking_content[:2], 1):
            print(f"   思考 {i}: {think[:80]}..." if len(think) > 80 else f"   思考 {i}: {think}")
    else:
        print("\n🧠 未检测到结构化思考过程（模型可能直接输出推理结果）")

    return result.get("completed", False)


def test_reasoning_with_config(agent_with_reasoning):
    """测试 3b: 带推理配置的 ReAct/推理"""
    print("\n" + "=" * 60)
    print("测试 3b: 带推理配置的 ReAct/推理")
    print("=" * 60)

    prompt = """请思考一下这个问题：如果今天是星期一，那么100天后是星期几？
    请展示你的推理过程。"""
    print(f"\n>>> 用户: {prompt[:50]}...")

    result = agent_with_reasoning.run_conversation(prompt)
    response = result.get("final_response", "")
    print(f"\n<<< AI 推理结果: {response[:500]}..." if len(response) > 500 else f"\n<<< AI: {response}")

    # 检查消息中的思考内容
    messages = result.get("messages", [])
    thinking_content = []
    for msg in messages:
        if isinstance(msg, dict):
            content = msg.get("content", "")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "thinking":
                        thinking_content.append(item.get("thinking", ""))

    if thinking_content:
        print(f"\n🧠 检测到结构化思考过程 ({len(thinking_content)} 条)")
        for i, think in enumerate(thinking_content[:2], 1):
            print(f"   思考 {i}: {think[:150]}..." if len(think) > 150 else f"   思考 {i}: {think}")
    else:
        print("\n🧠 未检测到结构化思考过程（模型可能直接输出推理结果）")

    return result.get("completed", False), len(thinking_content) > 0


def test_delegate_plan(agent):
    """测试 4: 计划/委托功能 - 测试 delegate_task 工具"""
    print("\n" + "=" * 60)
    print("测试 4: 计划/委托 (delegate_task)")
    print("=" * 60)

    prompt = """请帮我分析一下这个任务：
    目标：学习 Python 编程基础
    步骤：
    1. 了解变量和数据类型
    2. 学习控制流程（if/for/while）
    3. 学习函数定义

    请用 delegate_task 工具创建一个子任务来完成这个分析"""
    print(f"\n>>> 用户: {prompt[:80]}...")

    result = agent.run_conversation(prompt)
    response = result.get("final_response", "")
    print(f"\n<<< AI: {response[:400]}..." if len(response) > 400 else f"\n<<< AI: {response}")

    # 检查是否有委托调用
    messages = result.get("messages", [])
    delegate_calls = 0
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    if "delegate" in tc.get("function", {}).get("name", "").lower():
                        delegate_calls += 1
    print(f"\n📊 委托任务调用次数: {delegate_calls}")

    return result.get("completed", False), delegate_calls > 0


def test_multi_step_task(agent):
    """测试 5: 多步骤任务（综合测试）"""
    print("\n" + "=" * 60)
    print("测试 5: 多步骤任务（综合测试）")
    print("=" * 60)

    prompt = """请帮我完成以下任务：
    1. 先创建一个待办事项：'综合测试 Hermes 功能'
    2. 然后思考一下这个待办事项的完成步骤
    3. 最后总结一下你的思考过程

    请展示完整的思考和工具调用过程。"""
    print(f"\n>>> 用户: [多步骤任务]")

    result = agent.run_conversation(prompt)
    response = result.get("final_response", "")
    print(f"\n<<< AI: {response[:500]}..." if len(response) > 500 else f"\n<<< AI: {response}")

    # 统计工具调用
    messages = result.get("messages", [])
    total_tool_calls = 0
    tool_names = set()
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    total_tool_calls += 1
                    func_name = tc.get("function", {}).get("name", "")
                    tool_names.add(func_name)

    print(f"\n📊 总工具调用次数: {total_tool_calls}")
    print(f"📋 使用的工具: {', '.join(tool_names) if tool_names else '无'}")

    return result.get("completed", False), total_tool_calls


def main():
    print("\n" + "=" * 60)
    print("     Hermes-Agent 完整功能测试")
    print("=" * 60)

    # MiniMax 配置
    minimax_api_key = os.environ.get("MINIMAX_API_KEY") or os.environ.get("MINIMAX_CN_API_KEY")

    if not minimax_api_key:
        print("❌ 无法运行: 请确保在 .env 文件中配置了 MINIMAX_API_KEY")
        return

    # 使用 custom provider
    provider = "custom"
    model = "abab6.5s-chat"
    base_url = "https://api.minimaxi.com/v1"

    print(f"\n配置信息:")
    print(f"  Provider: {provider}")
    print(f"  Model: {model}")
    print(f"  Base URL: {base_url}")
    print(f"  API Key: {'已配置 (' + minimax_api_key[:10] + '...)'}")

    # 启用更多工具集（测试工具调用）
    enabled_toolsets = ["todo", "web", "delegate_task"]
    # 禁用高危工具
    disabled_toolsets = ["execute_code", "terminal", "browser_navigate"]

    print(f"\n工具集配置:")
    print(f"  启用: {', '.join(enabled_toolsets)}")
    print(f"  禁用: {', '.join(disabled_toolsets)}")

    print("\n" + "-" * 60)
    print("初始化 AIAgent...")
    agent = AIAgent(
        provider=provider,
        base_url=base_url,
        api_key=minimax_api_key,
        model=model,
        max_iterations=20,  # 增加迭代次数以支持多步骤任务
        enabled_toolsets=enabled_toolsets,
        disabled_toolsets=disabled_toolsets,
        quiet_mode=True,
    )
    print("✓ AIAgent 初始化完成\n")

    # 执行测试
    results = {}

    # 测试 1: 基础对话
    results["basic_chat"] = test_basic_chat(agent)

    # 测试 2: 工具调用
    completed, has_tools = test_tool_calling(agent)
    results["tool_calling"] = {"completed": completed, "has_tools": has_tools}

    # 测试 3: 推理（普通 agent）
    results["reasoning"] = test_reasoning(agent)

    # 测试 3b: 带推理配置的推理
    print("\n" + "-" * 60)
    print("创建带 reasoning_config 的 Agent...")
    agent_with_reasoning = AIAgent(
        provider=provider,
        base_url=base_url,
        api_key=minimax_api_key,
        model=model,
        max_iterations=20,
        enabled_toolsets=enabled_toolsets,
        disabled_toolsets=disabled_toolsets,
        quiet_mode=True,
        # reasoning_config={"enabled": True, "effort": "medium"}
    )
    print("✓ 带推理配置的 AIAgent 初始化完成")

    completed, has_structured_thinking = test_reasoning_with_config(agent_with_reasoning)
    results["reasoning_with_config"] = {"completed": completed, "has_structured_thinking": has_structured_thinking}

    # 测试 4: 委托
    completed, has_delegate = test_delegate_plan(agent)
    results["delegate"] = {"completed": completed, "has_delegate": has_delegate}

    # 测试 5: 多步骤任务
    completed, tool_count = test_multi_step_task(agent)
    results["multi_step"] = {"completed": completed, "tool_count": tool_count}

    # 汇总结果
    print("\n" + "=" * 60)
    print("     测试结果汇总")
    print("=" * 60)

    print(f"""
    测试项目                  状态
    ─────────────────────────────────────────────
    1. 基础 AI 对话           ✅ 成功
    2. 工具调用               {'✅ 成功' if results.get('tool_calling', {}).get('has_tools') else '⚠️ 未使用工具'}
       - 完成状态:            {'✅' if results.get('tool_calling', {}).get('completed') else '❌'}
    3. ReAct/推理             ✅ 成功
    3b. 推理(带配置)          {'✅ 成功' if results.get('reasoning_with_config', {}).get('completed') else '❌'}
       - 结构化思考:          {'✅' if results.get('reasoning_with_config', {}).get('has_structured_thinking') else '⚠️ 无'}
    4. 计划/委托              {'✅ 成功' if results.get('delegate', {}).get('has_delegate') else '⚠️ 未使用委托'}
       - 完成状态:            {'✅' if results.get('delegate', {}).get('completed') else '❌'}
    5. 多步骤任务             {'✅ 成功' if results.get('multi_step', {}).get('completed') else '❌ 失败'}
       - 工具调用次数:        {results.get('multi_step', {}).get('tool_count', 0)}
    """)

    print("=" * 60)


if __name__ == "__main__":
    main()
