"""
handoff_chain_agnes.py — OpenAI Agents SDK 三 Agent handoff demo (Agnes API)

Architecture:
  Router (入口/出口网关)
    → handoff → Chat Agent   (闲聊、概念讨论)
    → handoff → Plan Agent   (出方案)
    → handoff → Execute Agent (执行)

  每次 handoff 后，子 Agent 必须显式 handoff 回 Router，
  Router 再决定下一步。

Test flow:
  1. 纯聊天 → Router → Chat
  2. 任务需求 → Router → Plan
  3. 执行指令 → Router → Execute
"""

import asyncio
import os
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

from agents import Agent, Runner, handoff, RunConfig
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.extensions.handoff_prompt import RECOMMENDED_PROMPT_PREFIX

# Create Agnes-compatible Chat Completions model
agnes_client = AsyncOpenAI(
    api_key="sk-oV0ohjOKy2TT6GwRyrN6NxzloasiqQl7rg61FYn79bJxVLFu",
    base_url="https://apihub.agnes-ai.com/v1",
)
agnes_model = OpenAIChatCompletionsModel(
    model="agnes-2.0-flash",
    openai_client=agnes_client,
)
run_config = RunConfig(model=agnes_model, tracing_disabled=True)


def main():
    # ---------------------------------------------------------------
    # 1. Router Agent — 先定义，子 Agent 直接引用
    # ---------------------------------------------------------------
    router_agent = Agent(
        name="Router",
        instructions=f"""{RECOMMENDED_PROMPT_PREFIX}
你是路由网关。你是用户唯一的对话入口和出口。

判断用户意图并 handoff：
- 闲聊、概念讨论 → handoff 到 Chat
- 有明确任务需求 → handoff 到 Plan
- 用户说'执行'/'开始干' → handoff 到 Execute

子 Agent 完成后会 handoff 回你，你再把结果回复给用户。
""",
        handoffs=[],  # 稍后填充
    )

    # ---------------------------------------------------------------
    # 2. Chat Agent — 只聊天，不碰工具
    # ---------------------------------------------------------------
    chat_agent = Agent(
        name="Chat",
        instructions=f"""{RECOMMENDED_PROMPT_PREFIX}
你是聊天助手。只回答用户的问题和概念讨论。
如果用户有明确的任务需求，handoff 回 Router。
""",
        handoffs=[handoff(router_agent)],
    )

    # ---------------------------------------------------------------
    # 3. Plan Agent — 分析需求，输出方案
    # ---------------------------------------------------------------
    plan_agent = Agent(
        name="Plan",
        instructions=f"""{RECOMMENDED_PROMPT_PREFIX}
你是方案规划师。分析用户需求，输出详细执行方案。
方案应包括步骤、依赖、预期结果。
完成后 handoff 回 Router 让用户确认。
""",
        handoffs=[handoff(router_agent)],
    )

    # ---------------------------------------------------------------
    # 4. Execute Agent — 按方案执行
    # ---------------------------------------------------------------
    execute_agent = Agent(
        name="Execute",
        instructions=f"""{RECOMMENDED_PROMPT_PREFIX}
你是执行者。严格按照方案直接干活。
输出执行结果、文件变更、关键数据。
完成后 handoff 回 Router 报告结果。
""",
        handoffs=[handoff(router_agent)],
    )

    # ---------------------------------------------------------------
    # 5. 补全 Router 的 handoffs
    # ---------------------------------------------------------------
    router_agent.handoffs = [chat_agent, plan_agent, execute_agent]

    # ---------------------------------------------------------------
    # 6. 执行测试
    # ---------------------------------------------------------------
    async def run_tests():
        # Test 1: 纯聊天
        print("=== Test 1: Chat (概念讨论) ===")
        result = await Runner.run(router_agent, "给我讲讲递归是什么", max_turns=10, run_config=run_config)
        print(f"Result: {result.final_output}")
        print(f"Last Agent: {result.last_agent.name if result.last_agent else 'none'}")
        print()

        # Test 2: 任务 → 方案
        print("=== Test 2: Task → Plan (任务需求) ===")
        result = await Runner.run(router_agent, "帮我想一个斐波那契的 Python 实现方案", max_turns=10, run_config=run_config)
        print(f"Result: {result.final_output}")
        print(f"Last Agent: {result.last_agent.name if result.last_agent else 'none'}")
        print()

        # Test 3: 执行指令
        print("=== Test 3: Execute (执行指令) ===")
        result = await Runner.run(router_agent, "帮我把 'Hello, World!' 写到 hello.txt 文件", max_turns=10, run_config=run_config)
        print(f"Result: {result.final_output}")
        print(f"Last Agent: {result.last_agent.name if result.last_agent else 'none'}")
        print()

    asyncio.run(run_tests())


if __name__ == "__main__":
    main()
