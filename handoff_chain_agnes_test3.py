"""
handoff_chain_agnes_test3.py — Run only Test 3 (Execute) with Agnes API
"""
import asyncio
from openai import AsyncOpenAI
from agents import Agent, Runner, handoff, RunConfig
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.extensions.handoff_prompt import RECOMMENDED_PROMPT_PREFIX

agnes_client = AsyncOpenAI(
    api_key="sk-oV0ohjOKy2TT6GwRyrN6NxzloasiqQl7rg61FYn79bJxVLFu",
    base_url="https://apihub.agnes-ai.com/v1",
)
agnes_model = OpenAIChatCompletionsModel(model="agnes-2.0-flash", openai_client=agnes_client)
run_config = RunConfig(model=agnes_model, tracing_disabled=True)

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
    handoffs=[],
)

chat_agent = Agent(
    name="Chat",
    instructions=f"""{RECOMMENDED_PROMPT_PREFIX}
你是聊天助手。只回答用户的问题和概念讨论。
如果用户有明确的任务需求，handoff 回 Router。
""",
    handoffs=[handoff(router_agent)],
)

plan_agent = Agent(
    name="Plan",
    instructions=f"""{RECOMMENDED_PROMPT_PREFIX}
你是方案规划师。分析用户需求，输出详细执行方案。
方案应包括步骤、依赖、预期结果。
完成后 handoff 回 Router 让用户确认。
""",
    handoffs=[handoff(router_agent)],
)

execute_agent = Agent(
    name="Execute",
    instructions=f"""{RECOMMENDED_PROMPT_PREFIX}
你是执行者。严格按照方案直接干活。
输出执行结果、文件变更、关键数据。
完成后 handoff 回 Router 报告结果。
""",
    handoffs=[handoff(router_agent)],
)

router_agent.handoffs = [chat_agent, plan_agent, execute_agent]

async def main():
    print("=== Test 3 only: Execute (执行指令) with Agnes ===")
    result = await Runner.run(router_agent, "帮我把 'Hello, World!' 写到 hello.txt 文件", max_turns=10, run_config=run_config)
    print(f"Result: {result.final_output}")
    print(f"Last Agent: {result.last_agent.name if result.last_agent else 'none'}")
    print("Done!")

asyncio.run(main())
