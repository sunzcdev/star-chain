"""AgentRuntime — bridge between channel adapters and OpenAI Agents SDK."""

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

# Default configuration
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-ai/DeepSeek-V3"
DEFAULT_MAX_TURNS = 30
DEFAULT_API_TIMEOUT = 60  # seconds
DEFAULT_SESSION_DIR = "~/.agent-channel/sessions"


class AgentRuntime:
    """Bridge between channel adapters and OpenAI Agents SDK.

    Manages per-user sessions and orchestrates the three-agent pipeline:
    Router → Chat (conversation) / Plan (planning) / Execute (execution).
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
        resolved_base = (base_url or os.environ.get("DEEPSEEK_BASE_URL")
                         or DEFAULT_BASE_URL)
        resolved_key = (api_key or os.environ.get("DEEPSEEK_API_KEY")
                        or "")

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

        # Build agent pipeline
        self._router = self._build_agents()

        logger.info(
            "AgentRuntime initialized (model=%s, base=%s, max_turns=%d)",
            model, resolved_base, max_turns,
        )

    # ---- public API ----

    async def handle_message(self, user_id: str, text: str) -> str:
        """Process a user message and return agent response.

        Handles the ``/new`` command by resetting the session.
        """
        # Handle special commands
        if text.strip().lower() == "/new":
            await self.new_session(user_id)
            return "会话已重置。有什么可以帮你的？"

        session = self._get_or_create_session(user_id)
        session.add_user_message(text)

        try:
            result = await asyncio.wait_for(
                Runner.run(
                    self._router,
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
            error_msg = "系统繁忙，请稍后再试。"
            # Don't save error to session
            return error_msg

    async def new_session(self, user_id: str) -> None:
        """Reset a user's session (clear history)."""
        session = self._get_or_create_session(user_id)
        session.reset()
        logger.info("session reset for user %s", user_id)

    # ---- internal ----

    def _build_agents(self):
        """Build the three-agent pipeline: Router → Chat/Plan/Execute."""
        # Router agent placeholder
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

        # Chat agent
        chat_agent = Agent(
            name="Chat",
            instructions=f"""{RECOMMENDED_PROMPT_PREFIX}
你是聊天助手。只回答用户的问题和概念讨论。
如果用户有明确的任务需求，handoff 回 Router。
""",
            handoffs=[handoff(router_agent)],
        )

        # Plan agent
        plan_agent = Agent(
            name="Plan",
            instructions=f"""{RECOMMENDED_PROMPT_PREFIX}
你是方案规划师。分析用户需求，输出详细执行方案。
方案应包括步骤、依赖、预期结果。
完成后 handoff 回 Router 让用户确认。
""",
            handoffs=[handoff(router_agent)],
        )

        # Execute agent
        execute_agent = Agent(
            name="Execute",
            instructions=f"""{RECOMMENDED_PROMPT_PREFIX}
你是执行者。严格按照方案直接干活。
输出执行结果、文件变更、关键数据。
完成后 handoff 回 Router 报告结果。
""",
            handoffs=[handoff(router_agent)],
        )

        # Wire up Router's handoffs
        router_agent.handoffs = [chat_agent, plan_agent, execute_agent]

        return router_agent

    def _get_or_create_session(self, user_id: str) -> SessionContext:
        if user_id not in self._sessions:
            self._sessions[user_id] = SessionContext(
                user_id=user_id,
                storage_dir=str(self._session_dir),
            )
        return self._sessions[user_id]
