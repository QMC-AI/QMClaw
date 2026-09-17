"""
Self-Reflection Engine - 自我反思引擎

在任务完成后分析执行过程，生成反思报告。
提供：
1. 任务质量评估
2. 成功/失败因素提取
3. 教训生成
4. 技能获取建议
"""

import json
import os
import re
from typing import Dict, List, Optional, Any
from datetime import datetime

# 导入记忆存储
from memory_store import (
    create_episode, get_episode, update_episode_reflection,
    create_skill, match_skills, get_recent_episodes_for_qubit,
    get_failed_episodes
)


# ── LLM 调用 ──────────────────────────────────────────────────────────────

def _get_api_key_for_provider(provider: str) -> str:
    """获取 API key"""
    provider_keys = {
        "openai": "OPENAI_API_KEY",
        "minimax": "MINIMAX_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }
    env_var = provider_keys.get(provider, "OPENAI_API_KEY")
    return os.environ.get(env_var, "")


def _call_llm_for_reflection(
    system_prompt: str,
    user_message: str,
    model: str = "gpt-4o-mini",
    provider: str = "openai"
) -> str:
    """调用 LLM 生成反思内容"""
    try:
        api_key = _get_api_key_for_provider(provider)
        if not api_key:
            # 尝试 MiniMax
            provider = "minimax"
            api_key = _get_api_key_for_provider("minimax")

        if provider == "minimax":
            return _call_minimax_reflection(system_prompt, user_message, api_key)
        else:
            return _call_openai_reflection(system_prompt, user_message, api_key, model)

    except Exception as e:
        print(f"ReflectionEngine: LLM call failed: {e}")
        return f"[反思生成失败: {e}]"


def _call_openai_reflection(system_prompt: str, user_message: str, api_key: str, model: str) -> str:
    """使用 OpenAI API"""
    import openai
    client = openai.OpenAI(api_key=api_key)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        temperature=0.3,
        max_tokens=1500
    )
    return response.choices[0].message.content or ""


def _call_minimax_reflection(system_prompt: str, user_message: str, api_key: str) -> str:
    """使用 MiniMax API"""
    from urllib.request import urlopen, Request

    endpoint = "https://api.minimax.chat/v1/text/chatcompletion_v2"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "MiniMax-M2.7",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        "max_tokens": 1500,
    }

    req = Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        resp = urlopen(req, timeout=30)
        result = json.loads(resp.read().decode("utf-8"))
        choices = result.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
    except Exception as e:
        print(f"ReflectionEngine: MiniMax call failed: {e}")
    return ""


# ── 反思分析 ──────────────────────────────────────────────────────────────

def analyze_task(
    task: str,
    status: str,
    steps: List[Dict],
    metrics: Dict[str, float],
    qubit: str = None,
    mode: str = "react"
) -> Dict:
    """
    分析任务执行，生成反思报告。

    Returns:
        反思报告 dict，包含：
        - summary: 执行摘要
        - quality: 质量评分 (0-100)
        - successFactors: 成功因素
        - failureFactors: 失败因素
        - lessons: 教训
        - suggestions: 改进建议
        - skillSuggestion: 技能获取建议
    """

    # 构建分析上下文
    context_parts = [f"任务: {task}"]
    if qubit:
        context_parts.append(f"量子比特: {qubit}")
    context_parts.append(f"执行模式: {mode}")
    context_parts.append(f"状态: {status}")

    if metrics:
        context_parts.append("\n关键指标:")
        for k, v in metrics.items():
            context_parts.append(f"  - {k}: {v}")

    context_parts.append(f"\n执行步骤 (共 {len(steps)} 步):")
    for i, step in enumerate(steps[:10], 1):  # 最多10步
        tool = step.get("tool", "unknown")
        thought = step.get("thought", "")
        obs = step.get("observation", {})
        if isinstance(obs, dict):
            obs_str = json.dumps(obs, ensure_ascii=False)[:200]
        else:
            obs_str = str(obs)[:200]
        context_parts.append(f"  {i}. [{tool}] {thought[:100] if thought else ''}")
        context_parts.append(f"     观察: {obs_str}")

    context = "\n".join(context_parts)

    # 获取历史参考
    history_context = ""
    if qubit:
        recent = get_recent_episodes_for_qubit(qubit, limit=3)
        if recent:
            history_context = f"\n\n{qubit} 的历史经验 ({len(recent)} 条):"
            for ep in recent:
                history_context += f"\n  - {ep.get('timestamp', '')}: {ep.get('status', '')}"

    # LLM 反思 prompt
    system_prompt = """你是一个量子测控智能体的自我反思引擎。

你的任务是分析任务执行结果，生成结构化的反思报告。

请严格按照以下 JSON 格式输出（仅输出 JSON，不要其他内容）：
{
  "summary": "一句话总结本次执行",
  "quality": 质量评分 0-100,
  "successFactors": ["成功因素1", "成功因素2"],
  "failureFactors": ["失败因素1"],  // 如果成功则为空数组
  "lessons": ["关键教训1", "关键教训2"],
  "suggestions": ["下次改进建议1", "建议2"],
  "skillSuggestion": {
    "shouldCreate": true/false,
    "name": "建议的技能名称",
    "keywords": ["关键词1", "关键词2"],
    "description": "技能描述"
  }
}

评分标准：
- 100: 完美执行，达到预期目标
- 80-99: 成功，但有小问题
- 60-79: 部分成功，有改进空间
- 40-59: 失败，但有可借鉴之处
- 0-39: 严重失败

教训应该具体且可操作，例如：
- "T1 随温度升高而延长，下次实验前应检查温控"
- "使用多次数平均可显著降低噪声"

仅返回 JSON，不要其他内容。"""

    user_message = f"""分析以下任务执行：

{context}
{history_context}

请生成反思报告。"""

    # 调用 LLM
    try:
        llm_response = _call_llm_for_reflection(system_prompt, user_message)

        # 解析 JSON
        # 提取 JSON 部分
        json_match = re.search(r'\{[\s\S]*\}', llm_response)
        if json_match:
            report = json.loads(json_match.group())
        else:
            report = _fallback_reflection(task, status, steps, metrics)

    except Exception as e:
        print(f"ReflectionEngine: Analysis failed: {e}")
        report = _fallback_reflection(task, status, steps, metrics)

    # 清理和验证
    report = _validate_report(report)
    return report


def _fallback_reflection(task: str, status: str, steps: List, metrics: Dict) -> Dict:
    """当 LLM 调用失败时的默认反思"""
    quality = 100 if status == "success" else 50

    lessons = []
    suggestions = []

    if status == "success":
        lessons.append("任务成功完成")
        if metrics:
            suggestions.append(f"当前指标: {metrics}")
    else:
        lessons.append("任务执行遇到问题")
        suggestions.append("建议检查参数设置和实验配置")

    # 从 steps 提取信息
    tools_used = list(set(s.get("tool", "") for s in steps if s.get("tool")))
    if tools_used:
        lessons.append(f"使用了工具: {', '.join(tools_used)}")

    return {
        "summary": f"任务{'成功' if status == 'success' else '部分完成'}",
        "quality": quality,
        "successFactors": ["任务完成"] if status == "success" else [],
        "failureFactors": ["存在执行问题"] if status != "success" else [],
        "lessons": lessons,
        "suggestions": suggestions,
        "skillSuggestion": {
            "shouldCreate": False,
            "name": "",
            "keywords": [],
            "description": ""
        }
    }


def _validate_report(report: Dict) -> Dict:
    """验证和补全反思报告"""
    # 确保必要字段存在
    required_fields = [
        "summary", "quality", "successFactors", "failureFactors",
        "lessons", "suggestions", "skillSuggestion"
    ]

    for field in required_fields:
        if field not in report:
            report[field] = "" if field in ["summary"] else []

    # 确保嵌套字段存在
    if isinstance(report["skillSuggestion"], dict):
        ss = report["skillSuggestion"]
        for field in ["shouldCreate", "name", "keywords", "description"]:
            if field not in ss:
                ss[field] = False if field == "shouldCreate" else ""
    else:
        report["skillSuggestion"] = {
            "shouldCreate": False,
            "name": "",
            "keywords": [],
            "description": ""
        }

    return report


# ── 反思流程 ──────────────────────────────────────────────────────────────

def reflect_after_task(
    task: str,
    status: str,
    steps: List[Dict],
    metrics: Dict[str, float],
    qubit: str = None,
    mode: str = "react"
) -> Dict:
    """
    完整的反思流程：
    1. 创建 Episode
    2. 生成反思报告
    3. 更新 Episode

    Returns:
        包含 episode 和 reflection 的完整报告
    """

    # 1. 创建 Episode
    tags = []
    if qubit:
        tags.append(qubit)
    if status == "success":
        tags.append("成功")
    else:
        tags.append("失败")

    episode = create_episode(
        task=task,
        steps=steps,
        status=status,
        metrics=metrics,
        qubit=qubit,
        tags=tags,
        mode=mode
    )

    episode_id = episode["id"]

    # 2. 生成反思报告
    reflection = analyze_task(
        task=task,
        status=status,
        steps=steps,
        metrics=metrics,
        qubit=qubit,
        mode=mode
    )

    # 3. 更新 Episode
    update_episode_reflection(episode_id, reflection)

    # 4. 标记是否生成了技能
    skill_created = None
    if reflection.get("skillSuggestion", {}).get("shouldCreate"):
        ss = reflection["skillSuggestion"]
        try:
            skill = create_skill(
                name=ss.get("name", f"从任务 {episode_id} 学习的技能"),
                trigger_keywords=ss.get("keywords", []),
                steps=steps,
                source_episode_id=episode_id,
                description=ss.get("description", "")
            )
            skill_created = skill["id"]

            # 更新 episode 标记
            episode["skillCreated"] = skill_created
            update_episode_reflection(episode_id, reflection)  # 更新

            print(f"ReflectionEngine: Created skill {skill['id']}", flush=True)
        except Exception as e:
            print(f"ReflectionEngine: Failed to create skill: {e}")

    # 5. 构建最终报告
    return {
        "episode": {
            "id": episode_id,
            "task": task,
            "status": status,
            "qubit": qubit
        },
        "reflection": reflection,
        "skillCreated": skill_created
    }


def format_reflection_report(reflection_result: Dict) -> str:
    """
    将反思结果格式化为用户友好的文本报告
    """
    reflection = reflection_result.get("reflection", {})
    episode = reflection_result.get("episode", {})

    lines = []
    lines.append("─" * 50)
    lines.append("💭 反思报告")
    lines.append("─" * 50)

    # 基本信息
    lines.append(f"")
    lines.append(f"📋 任务: {episode.get('task', '')}")
    if episode.get("qubit"):
        lines.append(f"🔬 量子比特: {episode['qubit']}")
    lines.append(f"📊 状态: {'✅ 成功' if episode.get('status') == 'success' else '⚠️ 部分完成'}")

    # 质量评分
    quality = reflection.get("quality", 0)
    quality_emoji = "🟢" if quality >= 80 else "🟡" if quality >= 60 else "🔴"
    lines.append(f"")
    lines.append(f"📈 质量评分: {quality_emoji} {quality}/100")

    # 执行摘要
    summary = reflection.get("summary", "")
    if summary:
        lines.append(f"")
        lines.append(f"📝 执行摘要:")
        lines.append(f"   {summary}")

    # 成功因素
    success_factors = reflection.get("successFactors", [])
    if success_factors:
        lines.append(f"")
        lines.append(f"✨ 成功因素:")
        for factor in success_factors:
            lines.append(f"   • {factor}")

    # 失败因素
    failure_factors = reflection.get("failureFactors", [])
    if failure_factors:
        lines.append(f"")
        lines.append(f"⚠️ 失败因素:")
        for factor in failure_factors:
            lines.append(f"   • {factor}")

    # 教训
    lessons = reflection.get("lessons", [])
    if lessons:
        lines.append(f"")
        lines.append(f"📚 关键教训:")
        for lesson in lessons:
            lines.append(f"   • {lesson}")

    # 改进建议
    suggestions = reflection.get("suggestions", [])
    if suggestions:
        lines.append(f"")
        lines.append(f"💡 改进建议:")
        for suggestion in suggestions:
            lines.append(f"   • {suggestion}")

    # 技能建议
    skill_suggestion = reflection.get("skillSuggestion", {})
    if skill_suggestion.get("shouldCreate"):
        lines.append(f"")
        lines.append(f"🎯 技能获取建议:")
        lines.append(f"   名称: {skill_suggestion.get('name', '')}")
        if skill_suggestion.get("keywords"):
            lines.append(f"   触发词: {', '.join(skill_suggestion['keywords'])}")

    lines.append("")
    lines.append("─" * 50)

    return "\n".join(lines)


# ── 记忆召回 ──────────────────────────────────────────────────────────────

def recall_for_task(task: str, qubit: str = None) -> Dict:
    """
    召回与当前任务相关的记忆

    Returns:
        包含相关记忆的上下文，用于增强 Agent prompt
    """
    result = {
        "hasMemory": False,
        "relevantSkills": [],
        "recentEpisodes": [],
        "qubitHistory": [],
        "context": ""
    }

    # 1. 匹配技能
    skills = match_skills(task)
    result["relevantSkills"] = [
        {
            "id": s["id"],
            "name": s["name"],
            "keywords": s.get("triggerKeywords", []),
            "successRate": s.get("successCount", 0) / max(s.get("successCount", 1) + s.get("failureCount", 1), 1)
        }
        for s in skills
    ]

    # 2. 同比特最近经验
    if qubit:
        recent = get_recent_episodes_for_qubit(qubit, limit=3)
        result["recentEpisodes"] = recent
        result["qubitHistory"] = recent

    # 3. 如果有相关记忆，构建上下文
    if result["relevantSkills"] or result["recentEpisodes"]:
        result["hasMemory"] = True

        context_parts = []
        if result["relevantSkills"]:
            context_parts.append("📚 相关已学技能:")
            for s in result["relevantSkills"]:
                rate = s.get("successRate", 0) * 100
                context_parts.append(f"   • {s['name']} (成功率: {rate:.0f}%)")

        if result["recentEpisodes"]:
            context_parts.append("")
            context_parts.append(f"📋 {qubit or '同类'}任务的历史经验:")
            for ep in result["recentEpisodes"]:
                ts = ep.get("timestamp", "")[:16]  # 只取日期时间
                status = "✅" if ep.get("status") == "success" else "⚠️"
                context_parts.append(f"   • {ts} {status}")

        result["context"] = "\n".join(context_parts)

    return result


# ── 初始化 ──────────────────────────────────────────────────────────────

def init_reflection_engine():
    """初始化反思引擎"""
    print("ReflectionEngine: Initialized", flush=True)


if __name__ == "__main__":
    init_reflection_engine()

    # 测试
    test_result = reflect_after_task(
        task="对 q10lu1 执行 T1 实验并优化校准参数",
        status="success",
        steps=[
            {"tool": "run_experiment", "input": {"fn": "sq.t1"}, "observation": {"T1": 42e-6}},
            {"tool": "analyze_results", "input": {}, "observation": {"improvement": 0.15}}
        ],
        metrics={"T1": 42e-6, "improvement": 0.15},
        qubit="q10lu1"
    )

    print(format_reflection_report(test_result))
