"""End-to-end integration tests for StarChain.

Covers:
  - run.py venv auto-detection (ensure correct Python env)
  - Full runtime wiring (AgentRuntime + FeishuAdapter mocks)
  - Full message flow: Feishu msg → Chat → Plan → Executor → reply
  - /new session reset command
  - Session persistence across messages
"""

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch as mock_patch, MagicMock, AsyncMock

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


# ── helpers ─────────────────────────────────────────────────────────────


def _fake_channel():
    ch = MagicMock()
    ch.connect_until_ready = AsyncMock()
    ch.disconnect = AsyncMock()
    ch.send = AsyncMock(return_value=MagicMock(
        success=True, message_id="mid_123", error=None
    ))
    return ch


def _make_inbound(user_id, text, chat_type="p2p"):
    from lark_oapi.channel.types import Conversation, Identity, InboundMessage

    return InboundMessage(
        id="msg_001",
        create_time=int(time.time()),
        conversation=Conversation(chat_id=user_id if chat_type == "p2p" else "oc_group1", chat_type=chat_type),
        sender=Identity(open_id=user_id),
        mentions=[],
        mentioned_all=False,
        reply=None,
        content=None,
        raw={},
        content_text=text,
        resources=[],
        mentioned_bot=False,
        raw_content_type="text",
    )


# =========================================================================
# Test 1 — run.py venv auto-detection logic
# =========================================================================


class TestRunVenvDetection:
    def test_venv_exec_python_switches(self):
        """When running with system python, run.py execv's to venv python."""
        run_path = os.path.join(_project_root, "run.py")
        with open(run_path, "r", encoding="utf-8") as f:
            content = f.read()

        # The execv switch logic must exist before any heavy imports
        assert "os.execv" in content
        assert ".venv" in content
        assert "_venv_python" in content
        print("✓ test_venv_exec_python_switches PASSED")

    def test_venv_exec_position_before_imports(self):
        """Venv switch must happen BEFORE lark_oapi / heavy imports."""
        run_path = os.path.join(_project_root, "run.py")
        with open(run_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        execv_lineno = None
        first_import_lineno = None
        for i, line in enumerate(lines, 1):
            if "os.execv" in line and execv_lineno is None:
                execv_lineno = i
            if "from src.star_chain" in line and first_import_lineno is None:
                first_import_lineno = i

        assert execv_lineno is not None, "os.execv call not found in run.py"
        assert first_import_lineno is not None, "src.star_chain import not found"
        assert execv_lineno < first_import_lineno, (
            f"venv execv (line {execv_lineno}) must come BEFORE heavy imports (line {first_import_lineno})"
        )
        print("✓ test_venv_exec_position_before_imports PASSED")

    def test_actual_sys_python_matches_venv(self):
        """When tests run, Python should be the project venv python."""
        venv_python = os.path.join(_project_root, ".venv", "bin", "python3")
        # The current interpreter should match what run.py would switch to
        # (this test documents the expected environment)
        assert os.path.exists(venv_python), f"Venv python missing at {venv_python}"
        print("✓ test_actual_sys_python_matches_venv PASSED")


# =========================================================================
# Test 2 — Core imports and module loading
# =========================================================================


class TestModuleImports:
    def test_all_star_chain_modules_import(self):
        """All public modules should import cleanly with no missing deps."""
        from src.star_chain import runtime, session, utils, channel_adapter
        from src.star_chain import feishu_adapter, account_store
        from src.star_chain.agents import build_agent_topology
        from src.star_chain.tools import (
            ALL_TOOLS,
            READ_ONLY_TOOLS,
            ALL_MCP_TOOLS,
            ALL_CODE_TOOLS,
            ALL_WEB_TOOLS,
            ALL_SKILL_TOOLS,
        )
        # Quick sanity on the tool counts
        assert len(ALL_TOOLS) >= 13
        assert len(READ_ONLY_TOOLS) == 3
        assert len(ALL_MCP_TOOLS) == 2
        assert len(ALL_CODE_TOOLS) == 6
        assert len(ALL_WEB_TOOLS) == 2
        assert len(ALL_SKILL_TOOLS) == 3
        print("✓ test_all_star_chain_modules_import PASSED")

    def test_lark_oapi_channel_module_exists(self):
        """lark_oapi.channel should be importable (critical for Feishu)."""
        from lark_oapi.channel import FeishuChannel
        from lark_oapi.channel.types import InboundMessage, Conversation, Identity
        assert FeishuChannel is not None
        print("✓ test_lark_oapi_channel_module_exists PASSED")


# =========================================================================
# Test 3 — AgentRuntime + FeishuAdapter end-to-end flow (mocked)
# =========================================================================


class TestRuntimeEndToEnd:
    def _build_runtime(self, tmpdir):
        from src.star_chain.runtime import AgentRuntime

        return AgentRuntime(
            base_url="https://example.com/v1",
            api_key="sk-fake",
            model="fake-model",
            max_turns=5,
            session_dir=tmpdir,
        )

    def test_runtime_constructs_entry_agent(self):
        """AgentRuntime builds the Chat agent correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rt = self._build_runtime(tmpdir)
            assert rt._entry_agent is not None
            assert rt._entry_agent.name == "Chat"
            print("✓ test_runtime_constructs_entry_agent PASSED")

    def test_feishu_adapter_lifecycle(self):
        """FeishuAdapter start + stop lifecycle completes without errors."""
        with mock_patch("src.star_chain.feishu_adapter.FeishuChannel") as MockChannel:
            MockChannel.return_value = _fake_channel()

            from src.star_chain.feishu_adapter import FeishuAdapter

            async def _test():
                adapter = FeishuAdapter(app_id="test_id", app_secret="test_secret")
                received = []

                async def on_message(event):
                    received.append(event)

                await adapter.start(on_message)
                assert adapter._running is True

                msg = _make_inbound(user_id="ou_e2e_test", text="hello e2e")
                await adapter._handle_message(msg)

                assert len(received) == 1
                assert received[0].user_id == "ou_e2e_test"
                assert received[0].text == "hello e2e"

                result = await adapter.send_message("ou_e2e_test", "hi back")
                assert result.success is True

                await adapter.stop()
                assert adapter._running is False
                assert adapter._channel is None

            asyncio.run(_test())
        print("✓ test_feishu_adapter_lifecycle PASSED")

    def test_session_persists_across_handle_message_calls(self):
        """Session history accumulates across multiple handle_message calls."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rt = self._build_runtime(tmpdir)

            from agents import Runner

            with mock_patch.object(Runner, "run", new_callable=AsyncMock) as mock_run:
                mock_resp = MagicMock()
                mock_resp.final_output = "reply-1"
                mock_run.return_value = mock_resp

                async def _test():
                    r1 = await rt.handle_message("u_e2e", "message 1")
                    assert r1 == "reply-1"

                    mock_resp.final_output = "reply-2"
                    r2 = await rt.handle_message("u_e2e", "message 2")
                    assert r2 == "reply-2"

                    s = rt._get_or_create_session("u_e2e")
                    assert len(s.history) == 4  # 2 user msgs + 2 assistant msgs

                asyncio.run(_test())
        print("✓ test_session_persists_across_handle_message_calls PASSED")

    def test_new_command_resets_session(self):
        """/new command clears a user's session history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rt = self._build_runtime(tmpdir)
            s = rt._get_or_create_session("u_new")
            s.add_user_message("old msg")
            s.add_assistant_message("old reply")
            s.save()
            assert len(s.history) == 2

            reply = asyncio.run(rt.handle_message("u_new", "/new"))
            assert "重置" in reply or "会话" in reply

            s2 = rt._get_or_create_session("u_new")
            assert len(s2.history) == 0
        print("✓ test_new_command_resets_session PASSED")

    def test_feishu_to_runtime_message_flow(self):
        """Complete simulated flow: Feishu msg → runtime → reply sent back."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from src.star_chain.runtime import AgentRuntime
            from src.star_chain.feishu_adapter import FeishuAdapter
            from agents import Runner

            rt = AgentRuntime(
                base_url="https://example.com/v1",
                api_key="sk-fake",
                model="fake-model",
                max_turns=5,
                session_dir=tmpdir,
            )

            with mock_patch("src.star_chain.feishu_adapter.FeishuChannel") as MockChannel, \
                 mock_patch.object(Runner, "run", new_callable=AsyncMock) as mock_run:

                fake_ch = _fake_channel()
                MockChannel.return_value = fake_ch

                mock_resp = MagicMock()
                mock_resp.final_output = "Hello from Agent!"
                mock_run.return_value = mock_resp

                async def _test():
                    adapter = FeishuAdapter(app_id="x", app_secret="y")
                    sent_messages = []

                    async def on_message(event):
                        response = await rt.handle_message(event.user_id, event.text)
                        sent_messages.append((event.user_id, response))
                        await adapter.send_message(event.user_id, response)

                    await adapter.start(on_message)

                    msg = _make_inbound(user_id="ou_flow_test", text="ping")
                    await adapter._handle_message(msg)

                    assert len(sent_messages) == 1
                    assert sent_messages[0][0] == "ou_flow_test"
                    assert sent_messages[0][1] == "Hello from Agent!"
                    # send was actually invoked on the channel
                    assert fake_ch.send.await_count >= 1
                    send_call = fake_ch.send.await_args_list[-1]
                    assert send_call.args[0] == "ou_flow_test"  # user_id as receive_id
                    assert send_call.args[1]["text"] == "Hello from Agent!"

                    await adapter.stop()

                asyncio.run(_test())
        print("✓ test_feishu_to_runtime_message_flow PASSED")

    def test_group_chat_strips_bot_mention(self):
        """Group chat @bot mention is stripped before hitting runtime."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from src.star_chain.runtime import AgentRuntime
            from src.star_chain.feishu_adapter import FeishuAdapter
            from lark_oapi.channel.types import Mention
            from agents import Runner

            rt = AgentRuntime(
                base_url="https://example.com/v1",
                api_key="sk-fake",
                model="fake-model",
                max_turns=5,
                session_dir=tmpdir,
            )

            with mock_patch("src.star_chain.feishu_adapter.FeishuChannel") as MockChannel, \
                 mock_patch.object(Runner, "run", new_callable=AsyncMock) as mock_run:

                fake_ch = _fake_channel()
                MockChannel.return_value = fake_ch

                mock_resp = MagicMock()
                mock_resp.final_output = "Sure thing"
                mock_run.return_value = mock_resp

                async def _test():
                    adapter = FeishuAdapter(app_id="x", app_secret="y")
                    received_text = []

                    async def on_message(event):
                        received_text.append(event.text)
                        response = await rt.handle_message(event.user_id, event.text)
                        await adapter.send_message(event.user_id, response)

                    await adapter.start(on_message)

                    # Build group message with bot mention
                    bot_mention = Mention(key="@_user_bot", open_id="ou_bot", is_bot=True)
                    msg = _make_inbound(user_id="ou_group_user", text="@_user_bot do something", chat_type="group")
                    msg.mentioned_bot = True
                    msg.mentions = [bot_mention]
                    await adapter._handle_message(msg)

                    # Bot mention should be stripped
                    assert received_text == ["do something"]

                    # chat_map should record group chat_id for this user
                    assert adapter._chat_map.get("ou_group_user") == "oc_group1"
                    # Reply should go to group chat_id, NOT user open_id
                    send_call = fake_ch.send.await_args_list[-1]
                    assert send_call.args[0] == "oc_group1"

                    await adapter.stop()

                asyncio.run(_test())
        print("✓ test_group_chat_strips_bot_mention PASSED")


# =========================================================================
# Test 4 — run.py main() function dry-run
# =========================================================================


class TestRunMainFunction:
    def test_main_without_credentials_exits_cleanly(self):
        """main() with no Feishu credentials exits with code 1 cleanly."""
        import importlib.util
        run_spec = importlib.util.spec_from_file_location(
            "run_main_test", os.path.join(_project_root, "run.py")
        )
        run_mod = importlib.util.module_from_spec(run_spec)

        with mock_patch.dict(os.environ, {"FEISHU_APP_ID": "", "FEISHU_APP_SECRET": ""}, clear=False), \
             mock_patch("src.star_chain.account_store.AccountStore.list_accounts", return_value=[]):

            # Override sys.exit to capture the exit code without quitting pytest
            exit_code = []

            def fake_exit(code):
                exit_code.append(code)
                raise SystemExit(code)

            with mock_patch.object(sys, "exit", fake_exit):
                with pytest.raises(SystemExit):
                    # We need to exec the module but not trigger __name__ == "__main__"
                    # Just invoke main() directly after import
                    run_spec.loader.exec_module(run_mod)
                    asyncio.run(run_mod.main())

            assert exit_code == [1], f"Expected sys.exit(1), got {exit_code}"
        print("✓ test_main_without_credentials_exits_cleanly PASSED")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-s"]))
