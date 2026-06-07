"""AgentRuntime — 飞书通道层与 OpenAI Agents SDK 的桥梁。

管理每用户会话，运行 Chat → Plan → Executor 三段式流水线。
"""

import asyncio
import logging
import os
from pathlib import Path

from openai import AsyncOpenAI
from agents import Agent, Runner, handoff, RunConfig
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.extensions.handoff_prompt import RECOMMENDED_PROMPT_PREFIX

from .session import SessionContext

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-ai/DeepSeek-V3"
DEFAULT_MAX_TURNS = 30
DEFAULT_API_TIMEOUT = 60
DEFAULT_SESSION_DIR = "~/.star-chain/sessions"


class AgentRuntime:
    """AgentRuntime — 管理多用户会话和三 Agent 流水线。

    三 Agent (Chat → Plan → Executor) 通过 OpenAI Agents SDK 的
    原生 handoff 机制协作，天然继承多 Agent 通信能力。
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        max_turns: int = DEFAULT_MAX_TURNS,
        api_timeout: int = DEFAULT_API_TIMEOUT,
        session_dir: str = DEFAULT_SESSION_DIR,
    ) -> None:
        resolved_base = base_url or os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL
        resolved_key = api_key or os.environ.get("DEEPSEEK_API_KEY") or ""

        self._client = AsyncOpenAI(
            base_url=resolved_base,
            api_key=resolved_key,
            timeout=api_timeout,
        )
        self._model = OpenAIChatCompletionsModel(
            model=model,
            openai_client=self._client,
        )
        self._max_turns = max_turns
        self._session_dir = Path(session_dir).expanduser()
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, SessionContext] = {}
        self._run_config = RunConfig(
            model=self._model,
            tracing_disabled=True,
        )

        # 构建三 Agent 流水线
        self._entry_agent = self._build_agents()

        logger.info(
            "AgentRuntime initialized (model=%s, base=%s, max_turns=%d)",
            model, resolved_base, max_turns,
        )

    # ---- public API ----

    async def handle_message(self, user_id: str, text: str) -> str:
        """处理用户消息，返回 Agent 回复。"""
        if text.strip().lower() == "/new":
            await self.new_session(user_id)
            return "会话已重置。有什么可以帮你的？"

        session = self._get_or_create_session(user_id)
        session.add_user_message(text)

        try:
            result = await asyncio.wait_for(
                Runner.run(
                    self._entry_agent,
                    input=session.history,
                    max_turns=self._max_turns,
                    run_config=self._run_config,
                ),
                timeout=120,
            )
            response = result.final_output
            if not isinstance(response, str):
                response = str(response)

            session.add_assistant_message(response)
            session.save()
            return response

        except asyncio.TimeoutError:
            error_msg = "暂时无法处理，请稍后再试。"
            session.add_assistant_message(error_msg)
            session.save()
            return error_msg
        except Exception as e:
            logger.error("handle_message error for %s: %s", user_id, e)
            return "系统繁忙，请稍后再试。"

    async def new_session(self, user_id: str) -> None:
        session = self._get_or_create_session(user_id)
        session.reset()
        logger.info("session reset for user %s", user_id)

    # ---- internal ----

    def _build_agents(self):
        """构建三 Agent 流水线：Chat → Plan → Executor。

        按依赖顺序定义（先定义 Executor / Plan，再定义 Chat 作为入口）。
        """
        # === 1. Executor Agent（最后被引用，先定义）===
        executor_agent = Agent(
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
            handoffs=[],  # 在 Chat 定义后补充
        )

        # === 2. Plan Agent（引用 Executor）===
        plan_agent = Agent(
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
            tools=[],  # TODO: 只读工具（read_file, search_files, web_search）
            handoffs=[],  # 在 Chat 定义后补充
        )

        # === 3. Chat Agent（入口，引用 Plan）===
        chat_agent = Agent(
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
                handoff(plan_agent,
                        tool_name_override="handoff_to_planner",
                        tool_description_override="用户有明确需求，转给 Planner 出方案"),
            ],
        )

        # === 4. 补充 handoff 回链 ===
        executor_agent.handoffs = [
            handoff(chat_agent,
                    tool_name_override="handoff_to_chat",
                    tool_description_override="执行完毕或遇到问题，handoff 回 Chat 讨论并报告结果"),
        ]
        plan_agent.handoffs = [
            handoff(executor_agent,
                    tool_name_override="handoff_to_executor",
                    tool_description_override="方案已就绪，转给 Executor 执行"),
            handoff(chat_agent,
                    tool_name_override="handoff_to_chat",
                    tool_description_override="需求不清晰或需要用户补充信息，handoff 回 Chat"),
        ]

        return chat_agent

    def _get_or_create_session(self, user_id: str) -> SessionContext:
        if user_id not in self._sessions:
            self._sessions[user_id] = SessionContext(
                user_id=user_id,
                storage_dir=str(self._session_dir),
            )
        return self._sessions[user_id]
