"""AgentRuntime — 飞书通道层与 OpenAI Agents SDK 的桥梁。

管理每用户会话，运行 Chat → Plan → Executor 三段式流水线。
"""

import asyncio
import logging
import os
from pathlib import Path

from openai import AsyncOpenAI
from agents import Runner, RunConfig
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

from .agents import build_agent_topology
from .session import SessionContext

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
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
        model: str | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        api_timeout: int = DEFAULT_API_TIMEOUT,
        session_dir: str = DEFAULT_SESSION_DIR,
    ) -> None:
        resolved_base = base_url or os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL
        resolved_key = api_key or os.environ.get("DEEPSEEK_API_KEY") or ""
        resolved_model = model or os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL

        self._client = AsyncOpenAI(
            base_url=resolved_base,
            api_key=resolved_key,
            timeout=api_timeout,
        )
        self._model = OpenAIChatCompletionsModel(
            model=resolved_model,
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
            resolved_model, resolved_base, max_turns,
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
            error_msg = "请求超时，请稍后再试。"
            session.add_assistant_message(error_msg)
            session.save()
            return error_msg
        except Exception as e:
            logger.error("handle_message error for %s: %s", user_id, e, exc_info=True)
            error_msg = f"处理失败：{e}"
            session.add_assistant_message(error_msg)
            session.save()
            return error_msg

    async def new_session(self, user_id: str) -> None:
        session = self._get_or_create_session(user_id)
        session.reset()
        logger.info("session reset for user %s", user_id)

    # ---- internal ----

    def _build_agents(self):
        """构建三 Agent 流水线，委托给 agents 模块。

        Returns:
            Chat Agent 作为整个流水线的入口。
        """
        return build_agent_topology()

    def _get_or_create_session(self, user_id: str) -> SessionContext:
        if user_id not in self._sessions:
            self._sessions[user_id] = SessionContext(
                user_id=user_id,
                storage_dir=str(self._session_dir),
            )
        return self._sessions[user_id]
