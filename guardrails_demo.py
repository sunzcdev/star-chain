"""
guardrails_demo.py — OpenAI Agents SDK Guardrails 验证 (ChatCompletionsModel)

验证 input_guardrail 能正确拦截/放行消息：
  1. 正常问题 → 放行
  2. 数学作业 → 触发 guardrail 拦截
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

from pydantic import BaseModel
from agents import (
    Agent,
    GuardrailFunctionOutput,
    InputGuardrailTripwireTriggered,
    RunContextWrapper,
    Runner,
    RunConfig,
    TResponseInputItem,
    input_guardrail,
)
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

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


# ---------------------------------------------------------------
# Guardrail Agent — 判断用户是否在问数学作业
# ---------------------------------------------------------------
class MathHomeworkCheck(BaseModel):
    is_math_homework: bool
    reasoning: str


guardrail_agent = Agent(
    name="Guardrail check",
    instructions="判断用户是否在让你帮做数学作业。",
    output_type=MathHomeworkCheck,
)


@input_guardrail
async def math_homework_guardrail(
    ctx: RunContextWrapper[None],
    agent: Agent,
    input_data: str | list[TResponseInputItem],
) -> GuardrailFunctionOutput:
    result = await Runner.run(guardrail_agent, input_data, context=ctx.context, run_config=run_config)
    check: MathHomeworkCheck = result.final_output
    return GuardrailFunctionOutput(
        output_info=check,
        tripwire_triggered=check.is_math_homework,
    )


# ---------------------------------------------------------------
# 主 Agent — 客服助手
# ---------------------------------------------------------------
support_agent = Agent(
    name="Customer support",
    instructions="你是客服助手。回答用户的日常问题。",
    input_guardrails=[math_homework_guardrail],
)


def main():
    async def run_tests():
        # Test 1: 正常问题 → 应放行
        print("=== Test 1: 正常问题 (应放行) ===")
        result = await Runner.run(support_agent, "请问你们客服几点下班？", run_config=run_config)
        print(f"Result: {result.final_output}")
        print(f"Guardrail NOT triggered ✓")
        print()

        # Test 2: 数学作业 → 应拦截
        print("=== Test 2: 数学作业 (应拦截) ===")
        try:
            await Runner.run(support_agent, "帮我解个方程：2x + 3 = 11，x等于多少？", run_config=run_config)
            print("Guardrail didn't trip — unexpected!")
        except InputGuardrailTripwireTriggered as e:
            print(f"Guardrail tripped! ✓")
            print(f"Tripwire info: {e}")

    asyncio.run(run_tests())


if __name__ == "__main__":
    main()
