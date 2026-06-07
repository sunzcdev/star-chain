"""Agent 定义 — Chat / Plan / Executor 三段式协作。

三 Agent 通过 OpenAI Agents SDK 的原生 handoff 机制协作：
  Chat (入口/出口) <-> Plan (方案规划) <-> Executor (执行)

每个 Agent 有明确的工具权限边界：
  - Chat: 无工具（纯对话角色）
  - Plan: 只读工具（read_file, search_files, web_search）
  - Executor: 全工具（Code + Web + Skill + MCP）
"""

from agents import Agent, handoff
from agents.extensions.handoff_prompt import RECOMMENDED_PROMPT_PREFIX

from ..tools import READ_ONLY_TOOLS, ALL_TOOLS


def build_chat_agent(plan_agent: Agent) -> Agent:
    """构建 Chat Agent — 对话入口/出口网关。

    Args:
        plan_agent: Plan Agent 实例，用于 handoff。

    Returns:
        配置好的 Chat Agent。
    """
    return Agent(
        name="Chat",
        instructions=f"""{RECOMMENDED_PROMPT_PREFIX}
你是**聊天助手**和**入口/出口网关**。你是用户唯一的对话界面。

职责：
- 闲聊、概念讨论、探索想法
- 当用户有明确需求时，handoff 给 Plan Agent 去分析
- 接收 Plan/Executor 的 handoff 回复，把结果转述给用户
- 你是最终回复用户的人

注意：
- 不要自己执行任务——你是聊天角色，不是执行者
- 执行任务请 handoff 给 Plan
""",
        handoffs=[
            handoff(
                plan_agent,
                tool_name_override="handoff_to_planner",
                tool_description_override="用户有明确需求，转给 Planner 出方案",
            ),
        ],
    )


def build_plan_agent(executor_agent: Agent, chat_agent: Agent) -> Agent:
    """构建 Plan Agent — 方案规划师。

    Args:
        executor_agent: Executor Agent 实例，用于 handoff。
        chat_agent: Chat Agent 实例，用于 handoff。

    Returns:
        配置好的 Plan Agent，绑定只读工具。
    """
    return Agent(
        name="Plan",
        instructions=f"""{RECOMMENDED_PROMPT_PREFIX}
你是**方案规划师**。你的职责是分析需求，输出可执行的方案。

工作方式：
- 接到需求后，先理解上下文，可能需要调研
- 输出方案应包含：做什么、怎么做、步骤列表、预期结果
- 可以用只读工具做调研（读文件、搜网页）
- 方案确认后 handoff 给 Executor
- 如果需求不清晰，handoff 回 Chat 让用户补充
""",
        tools=READ_ONLY_TOOLS,
        handoffs=[
            handoff(
                executor_agent,
                tool_name_override="handoff_to_executor",
                tool_description_override="方案已就绪，转给 Executor 执行",
            ),
            handoff(
                chat_agent,
                tool_name_override="handoff_to_chat",
                tool_description_override="需求不清晰或需要用户补充信息，handoff 回 Chat",
            ),
        ],
    )


def build_executor_agent(chat_agent: Agent) -> Agent:
    """构建 Executor Agent — 执行者。

    Args:
        chat_agent: Chat Agent 实例，用于 handoff。

    Returns:
        配置好的 Executor Agent，绑定全套工具。
    """
    return Agent(
        name="Executor",
        instructions=f"""{RECOMMENDED_PROMPT_PREFIX}
你是**执行者**。你的职责是按方案直接执行，产出结果。

执行原则：
- 严格按照方案步骤执行，不要自由发挥
- 调用合适的工具完成任务
- 执行完毕报告结果、文件变更、关键数据
- 遇到问题主动 handoff 回 Chat 讨论
- 完成后 handoff 回 Chat 报告结果

你有全套工具可用：代码操作、网页搜索、Skill 调用、MCP 调用。
""",
        tools=ALL_TOOLS,
        handoffs=[
            handoff(
                chat_agent,
                tool_name_override="handoff_to_chat",
                tool_description_override="执行完毕或遇到问题，handoff 回 Chat 讨论并报告结果",
            ),
        ],
    )


_CHAT_INSTRUCTIONS = f"""{RECOMMENDED_PROMPT_PREFIX}
你是**聊天助手**和**入口/出口网关**。你是用户唯一的对话界面。

职责：
- 闲聊、概念讨论、探索想法
- 当用户有明确需求时，handoff 给 Plan Agent 去分析
- 接收 Plan/Executor 的 handoff 回复，把结果转述给用户
- 你是最终回复用户的人

注意：
- 不要自己执行任务——你是聊天角色，不是执行者
- 执行任务请 handoff 给 Plan
"""


def build_agent_topology() -> Agent:
    """构建完整的三 Agent 拓扑，返回入口 Agent（Chat）。

    按依赖顺序创建以解决循环引用：
        1. 先创建 Chat（空壳，handoffs 稍后补充）
        2. 创建 Executor（依赖 Chat）
        3. 创建 Plan（依赖 Executor 和 Chat）
        4. 补充 Chat 的 handoffs（依赖 Plan）

    Returns:
        Chat Agent 作为整个流水线的入口。
    """
    chat_agent = Agent(
        name="Chat",
        instructions=_CHAT_INSTRUCTIONS,
        handoffs=[],
    )

    executor_agent = build_executor_agent(chat_agent)
    plan_agent = build_plan_agent(executor_agent, chat_agent)

    chat_agent.handoffs = [
        handoff(
            plan_agent,
            tool_name_override="handoff_to_planner",
            tool_description_override="用户有明确需求，转给 Planner 出方案",
        ),
    ]

    return chat_agent


__all__ = [
    "build_chat_agent",
    "build_plan_agent",
    "build_executor_agent",
    "build_agent_topology",
    "READ_ONLY_TOOLS",
    "ALL_TOOLS",
]
