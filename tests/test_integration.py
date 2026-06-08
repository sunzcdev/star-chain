"""Integration tests for StarChain — Phase 1 through Phase 3.

Covers end-to-end integration across all layers:
  - Phase 1: Feishu Channel Adapter (message in / out lifecycle)
  - Phase 2: Agent Topology (Chat → Plan → Executor handoff chain)
  - Phase 3: Tool Layer integration (Code + Web + Skill + MCP)

All tests use mocks where network / subprocess calls would be required.
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch as mock_patch, MagicMock, AsyncMock

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


# ── helpers ─────────────────────────────────────────────────────────────

def _resolve_agent(handoff_obj):
    return handoff_obj._agent_ref()


# =========================================================================
# PHASE 1 — Channel Layer
# =========================================================================

class TestPhase1ChannelLayer:
    """Phase 1 integration: Feishu channel adapter end-to-end."""

    def _mocked_channel(self):
        from lark_oapi.channel.types import Conversation, Identity, InboundMessage, Mention

        ch = MagicMock()
        ch.connect_until_ready = AsyncMock()
        ch.disconnect = AsyncMock()
        ch.send = AsyncMock(return_value=MagicMock(
            success=True, message_id="mid_test", error=None
        ))
        return ch

    def _make_msg(self, sender_id="ou_alice", text="hello", chat_type="p2p", mentioned_bot=False, mentions=None):
        from lark_oapi.channel.types import Conversation, Identity, InboundMessage, Mention

        return InboundMessage(
            id="msg_001",
            create_time=1234567890,
            conversation=Conversation(chat_id="oc_test", chat_type=chat_type),
            sender=Identity(open_id=sender_id),
            mentions=mentions or [],
            mentioned_all=False,
            reply=None,
            content=None,
            raw={},
            content_text=text,
            resources=[],
            mentioned_bot=mentioned_bot,
            raw_content_type="text",
        )

    @pytest.mark.asyncio
    async def test_feishu_p2p_roundtrip(self):
        """P2P message flows in → callback fires → response sends back."""
        from src.star_chain.feishu_adapter import FeishuAdapter

        with mock_patch("src.star_chain.feishu_adapter.FeishuChannel") as MockChannel:
            MockChannel.return_value = self._mocked_channel()

            adapter = FeishuAdapter(app_id="x", app_secret="y")
            received = []

            async def on_message(event):
                received.append(event)

            await adapter.start(on_message)

            msg = self._make_msg(sender_id="ou_alice", text="你好呀")
            await adapter._handle_message(msg)

            assert len(received) == 1
            assert received[0].user_id == "ou_alice"
            assert received[0].text == "你好呀"

            result = await adapter.send_message("ou_alice", "收到")
            assert result.success is True

            await adapter.stop()
        print("✓ test_feishu_p2p_roundtrip PASSED")

    @pytest.mark.asyncio
    async def test_feishu_group_mention_stripped(self):
        """Group chat @bot mention is stripped before reaching the handler."""
        from src.star_chain.feishu_adapter import FeishuAdapter
        from lark_oapi.channel.types import Mention

        with mock_patch("src.star_chain.feishu_adapter.FeishuChannel") as MockChannel:
            MockChannel.return_value = self._mocked_channel()

            adapter = FeishuAdapter(app_id="x", app_secret="y")
            received = []

            async def on_message(event):
                received.append(event)

            await adapter.start(on_message)

            bot_mention = Mention(key="@_user_1", open_id="ou_bot", is_bot=True)
            msg = self._make_msg(
                sender_id="ou_bob",
                text="@_user_1 帮我写代码",
                chat_type="group",
                mentioned_bot=True,
                mentions=[bot_mention],
            )
            await adapter._handle_message(msg)

            assert len(received) == 1
            assert received[0].text == "帮我写代码"
            await adapter.stop()
        print("✓ test_feishu_group_mention_stripped PASSED")

    @pytest.mark.asyncio
    async def test_feishu_chat_map_persistence(self):
        """chat_map maps open_id → chat_id for group replies."""
        from src.star_chain.feishu_adapter import FeishuAdapter
        from lark_oapi.channel.types import Mention

        with mock_patch("src.star_chain.feishu_adapter.FeishuChannel") as MockChannel:
            ch = self._mocked_channel()
            MockChannel.return_value = ch

            adapter = FeishuAdapter(app_id="x", app_secret="y")
            await adapter.start(lambda e: None)

            bot_mention = Mention(key="@_user_1", open_id="ou_bot", is_bot=True)
            msg = self._make_msg(
                sender_id="ou_carol",
                text="@_user_1 hi",
                chat_type="group",
                mentioned_bot=True,
                mentions=[bot_mention],
            )
            # Override conversation for this specific msg
            from lark_oapi.channel.types import Conversation
            msg.conversation = Conversation(chat_id="oc_group_123", chat_type="group")

            await adapter._handle_message(msg)

            assert adapter._chat_map.get("ou_carol") == "oc_group_123"
            await adapter.send_message("ou_carol", "回复")
            # Should use chat_id (oc_...) not open_id (ou_...)
            call_target = ch.send.await_args_list[-1].args[0]
            assert call_target == "oc_group_123"

            await adapter.stop()
        print("✓ test_feishu_chat_map_persistence PASSED")


# =========================================================================
# PHASE 2 — Agent Topology
# =========================================================================

class TestPhase2AgentTopology:
    """Phase 2 integration: three-agent handoff chain + role permissions."""

    def test_chat_has_no_tools(self):
        """Chat Agent is pure conversation — no tools at all."""
        from src.star_chain.agents import build_agent_topology

        chat = build_agent_topology()
        assert chat.name == "Chat"
        assert len(chat.tools) == 0, "Chat Agent should have zero tools"
        assert len(chat.handoffs) == 1
        print("✓ test_chat_has_no_tools PASSED")

    def test_plan_has_only_readonly(self):
        """Plan Agent only gets readonly tools (no write/exec tools)."""
        from src.star_chain.agents import build_agent_topology
        from src.star_chain.tools import READ_ONLY_TOOLS

        chat = build_agent_topology()
        plan = _resolve_agent(chat.handoffs[0])

        assert plan.name == "Plan"
        plan_tool_names = {getattr(t, "name", str(t)) for t in plan.tools}
        readonly_names = {getattr(t, "name", str(t)) for t in READ_ONLY_TOOLS}

        assert plan_tool_names == readonly_names
        forbidden = {"write_file", "patch", "terminal", "execute_code"}
        assert plan_tool_names.isdisjoint(forbidden)
        print("✓ test_plan_has_only_readonly PASSED")

    def test_executor_has_all_tools(self):
        """Executor Agent gets every tool available."""
        from src.star_chain.agents import build_agent_topology
        from src.star_chain.tools import ALL_TOOLS

        chat = build_agent_topology()
        plan = _resolve_agent(chat.handoffs[0])
        exec_handoff = next(h for h in plan.handoffs if h.tool_name == "handoff_to_executor")
        executor = _resolve_agent(exec_handoff)

        assert executor.name == "Executor"
        exec_tool_names = sorted(getattr(t, "name", str(t)) for t in executor.tools)
        all_names = sorted(getattr(t, "name", str(t)) for t in ALL_TOOLS)

        assert exec_tool_names == all_names
        assert "mcp_list" in exec_tool_names
        assert "mcp_call" in exec_tool_names
        print("✓ test_executor_has_all_tools PASSED")

    def test_complete_handoff_cycle(self):
        """Full cycle: Chat → Plan → Executor → Chat (same instance)."""
        from src.star_chain.agents import build_agent_topology

        chat = build_agent_topology()

        plan = _resolve_agent(chat.handoffs[0])
        assert plan.name == "Plan"

        to_exec = next(h for h in plan.handoffs if h.tool_name == "handoff_to_executor")
        executor = _resolve_agent(to_exec)
        assert executor.name == "Executor"

        back_chat_from_exec = _resolve_agent(executor.handoffs[0])
        back_chat_from_plan = _resolve_agent(
            next(h for h in plan.handoffs if h.tool_name == "handoff_to_chat")
        )

        assert back_chat_from_exec is chat
        assert back_chat_from_plan is chat
        print("✓ test_complete_handoff_cycle PASSED")

    def test_plan_handoff_names(self):
        """Plan Agent has both handoff_to_executor and handoff_to_chat."""
        from src.star_chain.agents import build_agent_topology

        chat = build_agent_topology()
        plan = _resolve_agent(chat.handoffs[0])
        handoff_names = sorted(h.tool_name for h in plan.handoffs)
        assert handoff_names == ["handoff_to_chat", "handoff_to_executor"]
        print("✓ test_plan_handoff_names PASSED")


# =========================================================================
# PHASE 3 — Tool Layer Integration
# =========================================================================

class TestPhase3ToolLayer:
    """Phase 3 integration: all tool categories wired up correctly."""

    def test_code_tools_present(self):
        """All 6 code tools should be registered."""
        from src.star_chain.tools import ALL_CODE_TOOLS

        names = sorted(getattr(t, "name", str(t)) for t in ALL_CODE_TOOLS)
        assert names == [
            "execute_code",
            "patch",
            "read_file",
            "search_files",
            "terminal",
            "write_file",
        ]
        print("✓ test_code_tools_present PASSED")

    def test_web_tools_present(self):
        """Both web_search and web_extract should be present."""
        from src.star_chain.tools import ALL_WEB_TOOLS

        names = sorted(getattr(t, "name", str(t)) for t in ALL_WEB_TOOLS)
        assert names == ["web_extract", "web_search"]
        print("✓ test_web_tools_present PASSED")

    def test_skill_tools_present(self):
        """All 3 skill tools should be registered."""
        from src.star_chain.tools import ALL_SKILL_TOOLS

        names = sorted(getattr(t, "name", str(t)) for t in ALL_SKILL_TOOLS)
        assert names == ["call_claude_code", "call_open_code", "run_skill"]
        print("✓ test_skill_tools_present PASSED")

    def test_mcp_tools_present(self):
        """Both MCP tools should be registered."""
        from src.star_chain.tools import ALL_MCP_TOOLS

        names = sorted(getattr(t, "name", str(t)) for t in ALL_MCP_TOOLS)
        assert names == ["mcp_call", "mcp_list"]
        print("✓ test_mcp_tools_present PASSED")

    def test_tool_category_disjointness(self):
        """Tool categories should have no overlapping tools."""
        from src.star_chain.tools import (
            READ_ONLY_TOOLS,
            ALL_CODE_TOOLS,
            ALL_WEB_TOOLS,
            ALL_SKILL_TOOLS,
            ALL_MCP_TOOLS,
        )
        write_code_names = {
            getattr(t, "name", str(t))
            for t in ALL_CODE_TOOLS
            if getattr(t, "name", str(t)) not in {"read_file", "search_files"}
        }
        web_names = {getattr(t, "name", str(t)) for t in ALL_WEB_TOOLS}
        skill_names = {getattr(t, "name", str(t)) for t in ALL_SKILL_TOOLS}
        mcp_names = {getattr(t, "name", str(t)) for t in ALL_MCP_TOOLS}

        readonly_names = {getattr(t, "name", str(t)) for t in READ_ONLY_TOOLS}

        assert readonly_names.isdisjoint(write_code_names)
        assert readonly_names.isdisjoint(skill_names)
        assert readonly_names.isdisjoint(mcp_names)
        assert write_code_names.isdisjoint(skill_names)
        assert write_code_names.isdisjoint(mcp_names)
        assert skill_names.isdisjoint(mcp_names)
        print("✓ test_tool_category_disjointness PASSED")

    def test_executor_tool_count(self):
        """Executor should have exactly len(ALL_TOOLS) tools."""
        from src.star_chain.agents import build_agent_topology
        from src.star_chain.tools import ALL_TOOLS

        chat = build_agent_topology()
        plan = _resolve_agent(chat.handoffs[0])
        to_exec = next(h for h in plan.handoffs if h.tool_name == "handoff_to_executor")
        executor = _resolve_agent(to_exec)

        assert len(executor.tools) == len(ALL_TOOLS)
        print(f"✓ test_executor_tool_count PASSED ({len(ALL_TOOLS)} tools)")

    def test_read_file_roundtrip(self):
        """read_file writes content that write_file produces."""
        from src.star_chain.tools.code import read_file, write_file, WORKSPACE_DIR

        tmp = WORKSPACE_DIR / "__test_integration_tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            fp = tmp / "hello.txt"
            write_result = write_file(str(fp), "hello integration test")
            assert "写入成功" in write_result

            read_result = read_file(str(fp))
            assert "hello integration test" in read_result
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        print("✓ test_read_file_roundtrip PASSED")

    def test_terminal_echo(self):
        """terminal tool runs a simple command."""
        from src.star_chain.tools.code import terminal

        result = terminal("echo star_chain_test", timeout=10)
        assert "star_chain_test" in result
        assert "exit code: 0" in result
        print("✓ test_terminal_echo PASSED")

    def test_execute_code_sandbox(self):
        """execute_code runs Python and blocks os module."""
        from src.star_chain.tools.code import execute_code

        ok = execute_code("print(2 ** 10)")
        assert "1024" in ok

        blocked = execute_code("import os; print(os.getcwd())")
        assert "安全拦截" in blocked or "禁止" in blocked
        print("✓ test_execute_code_sandbox PASSED")

    def test_web_search_mock(self):
        """web_search uses httpx and returns formatted results."""
        from src.star_chain.tools.web import web_search

        mock_html = """
        <html><body>
        <div class="result">
          <a class="result__a" href="https://integration.test">IntTest</a>
          <a class="result__snippet">integration test snippet</a>
        </div>
        </body></html>
        """
        with mock_patch("httpx.Client") as mock_client:
            mc = mock_client.return_value.__enter__.return_value
            mc.post.return_value.text = mock_html
            mc.post.return_value.raise_for_status.return_value = None

            result = web_search("integration test")
            assert "IntTest" in result
            assert "https://integration.test" in result
        print("✓ test_web_search_mock PASSED")

    def test_mcp_tools_exported(self):
        """MCP tools are properly reachable via the public module API."""
        from src.star_chain.tools import (
            tool_mcp_list,
            tool_mcp_call,
            ALL_MCP_TOOLS,
        )
        assert tool_mcp_list in ALL_MCP_TOOLS
        assert tool_mcp_call in ALL_MCP_TOOLS
        assert getattr(tool_mcp_list, "name", None) == "mcp_list"
        assert getattr(tool_mcp_call, "name", None) == "mcp_call"
        print("✓ test_mcp_tools_exported PASSED")


# =========================================================================
# CROSS-PHASE — Runtime + Session Integration
# =========================================================================

class TestCrossPhaseIntegration:
    """Cross-layer: Runtime wires channel → agents → tools together."""

    def test_runtime_construction(self):
        """AgentRuntime builds without real API credentials."""
        from src.star_chain.runtime import AgentRuntime

        rt = AgentRuntime(
            base_url="https://example.com/v1",
            api_key="sk-fake",
            model="fake-model",
            max_turns=3,
            session_dir="/tmp/star_chain_test_sessions",
        )
        assert rt._entry_agent is not None
        assert rt._entry_agent.name == "Chat"
        print("✓ test_runtime_construction PASSED")

    def test_session_persistence(self):
        """Session messages persist across SessionContext instances."""
        from src.star_chain.session import SessionContext

        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = SessionContext(user_id="u_integ", storage_dir=tmpdir)
            s1.add_user_message("first message")
            s1.add_assistant_message("first reply")
            s1.save()

            s2 = SessionContext(user_id="u_integ", storage_dir=tmpdir)
            assert len(s2.history) == 2
            assert s2.history[0]["content"] == "first message"
            assert s2.history[1]["content"] == "first reply"
        print("✓ test_session_persistence PASSED")

    def test_session_cap_enforced(self):
        """Session history is capped at MAX_HISTORY_SIZE."""
        from src.star_chain.session import SessionContext, MAX_HISTORY_SIZE

        with tempfile.TemporaryDirectory() as tmpdir:
            s = SessionContext(user_id="u_cap", storage_dir=tmpdir)
            for i in range(MAX_HISTORY_SIZE + 20):
                s.add_user_message(f"msg_{i}")
                s.add_assistant_message(f"resp_{i}")
            s.save()

            assert len(s.history) <= MAX_HISTORY_SIZE
            # Most recent messages should be kept
            last_msg = s.history[-1]
            assert last_msg["role"] == "assistant"
            assert "resp_" in last_msg["content"]
        print("✓ test_session_cap_enforced PASSED")

    def test_new_session_command(self):
        """/new command resets a user's session."""
        from src.star_chain.runtime import AgentRuntime

        with tempfile.TemporaryDirectory() as tmpdir:
            rt = AgentRuntime(
                base_url="https://example.com/v1",
                api_key="sk-fake",
                model="fake-model",
                session_dir=tmpdir,
            )
            session = rt._get_or_create_session("u_reset")
            session.add_user_message("old msg")
            session.add_assistant_message("old reply")
            session.save()

            # Simulate /new command via handle_message
            # (Runner.run would fail without a real API; we just check /new path)
            import asyncio
            reply = asyncio.run(rt.handle_message("u_reset", "/new"))
            assert "重置" in reply or "reset" in reply.lower() or "会话" in reply

            session2 = rt._get_or_create_session("u_reset")
            assert len(session2.history) == 0
        print("✓ test_new_session_command PASSED")

    def test_all_tool_categories_in_executor_instructions(self):
        """Executor Agent's instructions mention every tool category."""
        from src.star_chain.agents import build_agent_topology

        chat = build_agent_topology()
        plan = _resolve_agent(chat.handoffs[0])
        to_exec = next(h for h in plan.handoffs if h.tool_name == "handoff_to_executor")
        executor = _resolve_agent(to_exec)

        instructions = executor.instructions
        assert "Code" in instructions
        assert "Web" in instructions
        assert "Skill" in instructions
        assert "MCP" in instructions
        assert "mcp_list" in instructions
        assert "mcp_call" in instructions
        print("✓ test_all_tool_categories_in_executor_instructions PASSED")


# =========================================================================
# CRITICAL — Agent Model Binding & Interaction Flow
# =========================================================================

class TestAgentModelBinding:
    """Critical tests: every Agent must carry a custom model to avoid
    falling back to the default OpenAI provider (which causes timeout /
    'Missing credentials' errors in production).
    """

    def test_all_three_agents_have_model_via_runtime(self):
        """Chat, Plan, Executor — all three must have model bound via AgentRuntime."""
        from src.star_chain.runtime import AgentRuntime

        with tempfile.TemporaryDirectory() as tmpdir:
            rt = AgentRuntime(
                base_url="https://example.com/v1",
                api_key="sk-test",
                model="test-model",
                session_dir=tmpdir,
            )
            chat = rt._entry_agent
            assert chat.model is not None, "Chat Agent must have a model"

            plan = _resolve_agent(chat.handoffs[0])
            assert plan.model is not None, "Plan Agent must have a model"

            to_exec = next(h for h in plan.handoffs if h.tool_name == "handoff_to_executor")
            executor = _resolve_agent(to_exec)
            assert executor.model is not None, "Executor Agent must have a model"

            print("✓ test_all_three_agents_have_model_via_runtime PASSED")

    def test_build_topology_accepts_model_param(self):
        """build_agent_topology(model=...) propagates model to every agent."""
        from src.star_chain.agents import build_agent_topology
        from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
        from openai import AsyncOpenAI

        client = AsyncOpenAI(base_url="https://example.com", api_key="sk-x", timeout=5)
        model = OpenAIChatCompletionsModel(model="m-1", openai_client=client)

        chat = build_agent_topology(model=model)
        assert chat.model is model

        plan = _resolve_agent(chat.handoffs[0])
        assert plan.model is model

        to_exec = next(h for h in plan.handoffs if h.tool_name == "handoff_to_executor")
        executor = _resolve_agent(to_exec)
        assert executor.model is model

        print("✓ test_build_topology_accepts_model_param PASSED")

    def test_runtime_uses_custom_client_not_default_provider(self):
        """AgentRuntime must use its own AsyncOpenAI client (not default OPENAI_API_KEY).

        This is the root cause of the original '请求超时' bug — if agents
        don't carry a model, the SDK falls back to the default provider
        which looks for OPENAI_API_KEY env var, fails, and eventually
        times out.
        """
        from src.star_chain.runtime import AgentRuntime
        from agents import Runner
        import openai

        with tempfile.TemporaryDirectory() as tmpdir, \
             mock_patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):

            rt = AgentRuntime(
                base_url="https://api.deepseek.com",
                api_key="sk-invalid-for-test",
                model="deepseek-v4-flash",
                session_dir=tmpdir,
            )

            async def _run():
                resp = await rt.handle_message("u_model_test", "hello")
                return resp

            # If model binding is broken, we'd see 'Missing credentials'
            # from the default OpenAI provider. Correct behaviour is an
            # auth error from OUR client talking to deepseek.com.
            resp = asyncio.run(_run())
            assert "Missing credentials" not in resp, (
                "Agents fell back to default OpenAI provider — model not bound!"
            )
            # Our client should reach deepseek.com and get a 401 (bad key)
            assert (
                "401" in resp
                or "Authentication" in resp
                or "invalid" in resp.lower()
                or "处理失败" in resp
            )
            print("✓ test_runtime_uses_custom_client_not_default_provider PASSED")


class TestFullInteractionFlow:
    """Full user-interaction flow tests with Runner mocked.

    Simulates the complete journey:
      1. User sends message via Feishu
      2. Runtime feeds session history into Runner
      3. Runner produces final_output
      4. Runtime saves history and returns reply
      5. Reply is sent back through adapter
    """

    def test_single_message_round_trip(self):
        """Single user message → Agent reply → history saved correctly."""
        from src.star_chain.runtime import AgentRuntime
        from agents import Runner

        with tempfile.TemporaryDirectory() as tmpdir:
            rt = AgentRuntime(
                base_url="https://example.com/v1",
                api_key="sk-fake",
                model="fake-model",
                session_dir=tmpdir,
            )

            with mock_patch.object(Runner, "run", new_callable=AsyncMock) as mock_run:
                mock_result = MagicMock()
                mock_result.final_output = "Hello, I can help with that!"
                mock_run.return_value = mock_result

                reply = asyncio.run(rt.handle_message("u_flow1", "请帮我写个 Python 脚本"))

                assert reply == "Hello, I can help with that!"

                # Runner.run should have been called once
                assert mock_run.await_count == 1
                call_args = mock_run.await_args
                assert call_args.kwargs["max_turns"] == rt._max_turns

                # History should contain user + assistant messages
                session = rt._get_or_create_session("u_flow1")
                assert len(session.history) == 2
                assert session.history[0]["role"] == "user"
                assert session.history[0]["content"] == "请帮我写个 Python 脚本"
                assert session.history[1]["role"] == "assistant"
                assert session.history[1]["content"] == "Hello, I can help with that!"

                print("✓ test_single_message_round_trip PASSED")

    def test_multi_turn_conversation_preserves_history(self):
        """Multi-turn conversation accumulates history across turns."""
        from src.star_chain.runtime import AgentRuntime
        from agents import Runner

        with tempfile.TemporaryDirectory() as tmpdir:
            rt = AgentRuntime(
                base_url="https://example.com/v1",
                api_key="sk-fake",
                model="fake-model",
                session_dir=tmpdir,
            )

            replies = ["Sure!", "Let me check that...", "Done!"]
            turn_idx = [0]

            async def fake_run(*args, **kwargs):
                result = MagicMock()
                result.final_output = replies[turn_idx[0]]
                turn_idx[0] += 1
                return result

            with mock_patch.object(Runner, "run", side_effect=fake_run):
                r1 = asyncio.run(rt.handle_message("u_multi", "first"))
                r2 = asyncio.run(rt.handle_message("u_multi", "second"))
                r3 = asyncio.run(rt.handle_message("u_multi", "third"))

                assert [r1, r2, r3] == replies

                session = rt._get_or_create_session("u_multi")
                # 3 turns × 2 messages each = 6
                assert len(session.history) == 6
                roles = [h["role"] for h in session.history]
                assert roles == ["user", "assistant", "user", "assistant", "user", "assistant"]

                print("✓ test_multi_turn_conversation_preserves_history PASSED")

    def test_new_command_clears_history_between_conversations(self):
        """/new wipes history; subsequent message starts fresh."""
        from src.star_chain.runtime import AgentRuntime
        from agents import Runner

        with tempfile.TemporaryDirectory() as tmpdir:
            rt = AgentRuntime(
                base_url="https://example.com/v1",
                api_key="sk-fake",
                model="fake-model",
                session_dir=tmpdir,
            )

            with mock_patch.object(Runner, "run", new_callable=AsyncMock) as mock_run:
                mock_result = MagicMock()
                mock_result.final_output = "Ack"
                mock_run.return_value = mock_result

                # Turn 1
                asyncio.run(rt.handle_message("u_new", "hello"))
                assert len(rt._get_or_create_session("u_new").history) == 2

                # /new
                reply = asyncio.run(rt.handle_message("u_new", "/new"))
                assert "重置" in reply or "会话" in reply
                assert len(rt._get_or_create_session("u_new").history) == 0

                # Turn 2 (fresh)
                asyncio.run(rt.handle_message("u_new", "fresh start"))
                assert len(rt._get_or_create_session("u_new").history) == 2

                print("✓ test_new_command_clears_history_between_conversations PASSED")

    def test_session_history_persists_on_disk(self):
        """After handle_message, session data must be written to disk."""
        from src.star_chain.runtime import AgentRuntime
        from src.star_chain.session import SessionContext
        from agents import Runner

        with tempfile.TemporaryDirectory() as tmpdir:
            rt = AgentRuntime(
                base_url="https://example.com/v1",
                api_key="sk-fake",
                model="fake-model",
                session_dir=tmpdir,
            )

            with mock_patch.object(Runner, "run", new_callable=AsyncMock) as mock_run:
                mock_result = MagicMock()
                mock_result.final_output = "persisted reply"
                mock_run.return_value = mock_result

                asyncio.run(rt.handle_message("u_disk", "persist me"))

                # Load a FRESH SessionContext from the same dir
                fresh = SessionContext(user_id="u_disk", storage_dir=tmpdir)
                assert len(fresh.history) == 2
                assert fresh.history[1]["content"] == "persisted reply"

                print("✓ test_session_history_persists_on_disk PASSED")

    def test_feishu_adapter_runtime_full_pipeline(self):
        """FeishuAdapter + AgentRuntime working together end-to-end."""
        from src.star_chain.runtime import AgentRuntime
        from src.star_chain.feishu_adapter import FeishuAdapter
        from lark_oapi.channel.types import InboundMessage, Conversation, Identity
        from agents import Runner

        with tempfile.TemporaryDirectory() as tmpdir, \
             mock_patch("src.star_chain.feishu_adapter.FeishuChannel") as MockChannel, \
             mock_patch.object(Runner, "run", new_callable=AsyncMock) as mock_run:

            fake_ch = MagicMock()
            fake_ch.connect_until_ready = AsyncMock()
            fake_ch.disconnect = AsyncMock()
            fake_ch.send = AsyncMock(return_value=MagicMock(success=True, message_id="m1"))
            MockChannel.return_value = fake_ch

            mock_result = MagicMock()
            mock_result.final_output = "Pipeline OK"
            mock_run.return_value = mock_result

            rt = AgentRuntime(
                base_url="https://example.com/v1",
                api_key="sk-fake",
                model="fake-model",
                session_dir=tmpdir,
            )

            adapter = FeishuAdapter(app_id="x", app_secret="y")

            async def _test():
                async def on_message(event):
                    reply = await rt.handle_message(event.user_id, event.text)
                    await adapter.send_message(event.user_id, reply)

                await adapter.start(on_message)

                msg = InboundMessage(
                    id="m_pipe",
                    create_time=1,
                    conversation=Conversation(chat_id="ou_pipe", chat_type="p2p"),
                    sender=Identity(open_id="ou_pipe"),
                    mentions=[],
                    mentioned_all=False,
                    reply=None,
                    content=None,
                    raw={},
                    content_text="pipeline test",
                    resources=[],
                    mentioned_bot=False,
                    raw_content_type="text",
                )
                await adapter._handle_message(msg)

                assert fake_ch.send.await_count == 1
                send_args = fake_ch.send.await_args
                assert send_args.args[0] == "ou_pipe"
                assert send_args.args[1]["text"] == "Pipeline OK"

                await adapter.stop()

            asyncio.run(_test())
            print("✓ test_feishu_adapter_runtime_full_pipeline PASSED")


class TestToolInvocationFlow:
    """Tests that the tool layer is correctly wired and each tool is callable.

    These are smoke tests for every tool that Executor has access to, to
    make sure no import / wiring regressions have occurred.
    """

    def test_all_executor_tools_are_callable_objects(self):
        """Every tool in ALL_TOOLS must be a valid FunctionTool with on_invoke_tool."""
        from src.star_chain.tools import ALL_TOOLS

        expected_names = {
            "read_file", "search_files", "web_search",
            "write_file", "patch", "terminal", "execute_code",
            "web_extract",
            "any_search", "any_search_domains", "any_search_batch", "any_search_extract",
            "run_skill", "call_claude_code", "call_open_code",
            "mcp_list", "mcp_call",
        }

        actual_names = set()
        for t in ALL_TOOLS:
            name = getattr(t, "name", None)
            assert name is not None, f"Tool without name: {t}"
            assert hasattr(t, "on_invoke_tool"), f"Tool {name} missing on_invoke_tool"
            assert t.on_invoke_tool is not None, f"Tool {name} has null invoker"
            actual_names.add(name)

        assert actual_names == expected_names, (
            f"Tool set mismatch. Missing: {expected_names - actual_names}, "
            f"Extra: {actual_names - expected_names}"
        )
        print(f"✓ test_all_executor_tools_are_callable_objects PASSED ({len(ALL_TOOLS)} tools)")

    def test_executor_tool_permissions_match_instructions(self):
        """Executor's actual tool set must match what its instructions claim."""
        from src.star_chain.agents import build_agent_topology
        from src.star_chain.tools import ALL_TOOLS

        chat = build_agent_topology()
        plan = _resolve_agent(chat.handoffs[0])
        to_exec = next(h for h in plan.handoffs if h.tool_name == "handoff_to_executor")
        executor = _resolve_agent(to_exec)

        tool_names = {getattr(t, "name", str(t)) for t in executor.tools}
        all_names = {getattr(t, "name", str(t)) for t in ALL_TOOLS}
        assert tool_names == all_names

        instructions = executor.instructions
        for name in tool_names:
            assert name in instructions, (
                f"Executor instructions don't mention tool '{name}'"
            )
        print("✓ test_executor_tool_permissions_match_instructions PASSED")

    def test_plan_tools_are_readonly(self):
        """Plan Agent must NEVER have write / exec tools."""
        from src.star_chain.agents import build_agent_topology

        chat = build_agent_topology()
        plan = _resolve_agent(chat.handoffs[0])

        plan_names = {getattr(t, "name", str(t)) for t in plan.tools}
        forbidden = {"write_file", "patch", "terminal", "execute_code", "web_extract",
                     "run_skill", "call_claude_code", "call_open_code", "mcp_list", "mcp_call"}
        assert plan_names.isdisjoint(forbidden), (
            f"Plan Agent has forbidden tools: {plan_names & forbidden}"
        )
        assert plan_names == {"read_file", "search_files", "any_search", "web_search"}
        print("✓ test_plan_tools_are_readonly PASSED")

    def test_chat_agent_has_zero_tools(self):
        """Chat Agent must have zero tools — pure conversation gateway."""
        from src.star_chain.agents import build_agent_topology

        chat = build_agent_topology()
        assert len(chat.tools) == 0, f"Chat has tools: {[t.name for t in chat.tools]}"
        print("✓ test_chat_agent_has_zero_tools PASSED")


if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))
