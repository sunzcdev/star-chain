"""Agnes handoff test — Chat + Plan routing"""
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

router = Agent(
    name="Router",
    instructions=RECOMMENDED_PROMPT_PREFIX + """You are a routing gateway. Determine user intent and handoff:
- Chat or conceptual discussion -> handoff to Chat
- Clear task requirement -> handoff to Plan
After the sub-agent finishes, it will handoff back to you.""",
    handoffs=[],
)

chat = Agent(
    name="Chat",
    instructions=RECOMMENDED_PROMPT_PREFIX + "You are a chat assistant. Answer user questions.",
    handoffs=[handoff(router)],
)

plan = Agent(
    name="Plan",
    instructions=RECOMMENDED_PROMPT_PREFIX + "You are a planner. Output a detailed plan then handoff back to Router.",
    handoffs=[handoff(router)],
)

router.handoffs = [chat, plan]

async def run_tests():
    # Test 1: Chat
    print("=== Test 1: Chat (recursion question) ===")
    try:
        result = await Runner.run(router, "给我讲讲递归是什么", max_turns=10, run_config=run_config)
        print(f"Result: {result.final_output[:200]}")
        print(f"Last Agent: {result.last_agent.name if result.last_agent else 'none'}")
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
    print()

    # Test 2: Plan
    print("=== Test 2: Plan (design a greeting program) ===")
    try:
        result = await Runner.run(router, "帮我想一个Python问候程序方案", max_turns=10, run_config=run_config)
        print(f"Result: {result.final_output[:200]}")
        print(f"Last Agent: {result.last_agent.name if result.last_agent else 'none'}")
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
    print()

    # Test 3: Execute
    print("=== Test 3: Execute (write file) ===")
    execute_agent = Agent(
        name="Execute",
        instructions=RECOMMENDED_PROMPT_PREFIX + "You are an executor. Follow instructions and output results.",
        handoffs=[handoff(router)],
    )
    router.handoffs = [chat, plan, execute_agent]
    try:
        result = await Runner.run(router, "帮我把 'Hello, World!' 写到 hello.txt 文件", max_turns=10, run_config=run_config)
        print(f"Result: {result.final_output[:200]}")
        print(f"Last Agent: {result.last_agent.name if result.last_agent else 'none'}")
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")

asyncio.run(run_tests())
