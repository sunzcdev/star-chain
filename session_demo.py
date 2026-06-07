"""
session_demo.py — OpenAI Agents SDK Session 持久化验证 (ChatCompletionsModel)

验证 SQLiteSession 能跨轮次保持对话上下文：
  1. 第一轮：告诉 agent "我叫振朝"
  2. 第二轮：问 "我叫什么名字？" — agent 应记得
  3. 验证 session db 文件已创建
"""

import asyncio
import os
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

if not os.environ.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = "sk-kynuspdhaccjcdkysiznyndssejxnhfyvhcaxpbikjwaiffr"
if not os.environ.get("OPENAI_BASE_URL"):
    os.environ["OPENAI_BASE_URL"] = "https://api.siliconflow.cn/v1"

from agents import Agent, Runner, RunConfig, SQLiteSession
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

SESSION_DB = "sessions.db"

# Create ChatCompletions model (SiliconFlow / DeepSeek)
deepseek_client = AsyncOpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"),
    base_url=os.environ.get("OPENAI_BASE_URL"),
)
deepseek_model = OpenAIChatCompletionsModel(
    model="deepseek-ai/DeepSeek-V3",
    openai_client=deepseek_client,
)
run_config = RunConfig(model=deepseek_model, tracing_disabled=True)


def main():
    agent = Agent(
        name="Assistant",
        instructions="回答用户问题，记住用户的个人信息。",
    )

    async def run_session():
        # 使用持久化 SQLiteSession，指定 db 文件
        session = SQLiteSession("test_001", db_path=SESSION_DB)

        # 第一轮：告诉 agent 名字
        print("=== Round 1: 告诉名字 ===")
        result = await Runner.run(agent, "我叫振朝", session=session, run_config=run_config)
        print(f"Agent: {result.final_output}")
        print(f"Session items in DB: {len(await session.get_items())}")
        print()

        # 第二轮：问名字 — 应记住
        print("=== Round 2: 问名字 ===")
        result = await Runner.run(agent, "我叫什么名字？", session=session, run_config=run_config)
        print(f"Agent: {result.final_output}")
        print(f"Session items in DB: {len(await session.get_items())}")
        print()

        # 验证 session db 文件存在
        db_exists = os.path.exists(SESSION_DB)
        print(f"Session DB file '{SESSION_DB}' exists: {db_exists}")

        # 列出 session 中的消息（只显示角色和内容前缀）
        print("\n=== Session 消息 ===")
        items = await session.get_items()
        for i, item in enumerate(items):
            role = item.get("role", "?")
            content = item.get("content", "")
            preview = content[:80] + "..." if len(content) > 80 else content
            print(f"  [{i}] {role}: {preview}")

    asyncio.run(run_session())


if __name__ == "__main__":
    main()
