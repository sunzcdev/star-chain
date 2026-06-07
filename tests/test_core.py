"""Functional tests for agent-channel core components."""

import json
import os
import sys
import tempfile
from pathlib import Path

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.star_chain.session import SessionContext


def test_session_context():
    """Test SessionContext: create, add messages, save, reload."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session = SessionContext(user_id="test_user", storage_dir=tmpdir)

        assert session.history == [], f"Expected empty history, got {session.history}"

        session.add_user_message("你好")
        session.add_assistant_message("你好！有什么可以帮你的？")
        session.save()

        file_path = Path(tmpdir) / "test_user.json"
        assert file_path.exists(), "Session file not created"

        data = json.loads(file_path.read_text(encoding="utf-8"))
        assert data["user_id"] == "test_user"
        assert len(data["history"]) == 2
        assert data["history"][0] == {"role": "user", "content": "你好"}
        assert data["history"][1] == {"role": "assistant", "content": "你好！有什么可以帮你的？"}

        session2 = SessionContext(user_id="test_user", storage_dir=tmpdir)
        assert len(session2.history) == 2
        assert session2.history[0]["content"] == "你好"

        session2.clear()
        assert session2.history == []

        data2 = json.loads(file_path.read_text(encoding="utf-8"))
        assert data2["history"] == []

    print("✓ test_session_context PASSED")


def test_session_history_cap():
    """Test that history is capped at MAX_HISTORY_SIZE."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session = SessionContext(user_id="cap_test", storage_dir=tmpdir)
        for i in range(60):
            session.add_user_message(f"msg_{i}")
            session.add_assistant_message(f"resp_{i}")
        session.save()

        assert len(session.history) <= 50, f"History exceeds cap: {len(session.history)}"

        session2 = SessionContext(user_id="cap_test", storage_dir=tmpdir)
        assert len(session2.history) <= 50

    print("✓ test_session_history_cap PASSED")


def test_runtime_import():
    """Test that AgentRuntime can be constructed with correct topology."""
    from src.star_chain.runtime import AgentRuntime
    rt = AgentRuntime(
        base_url="https://api.deepseek.com/v1",
        api_key="sk-test",
        model="deepseek-ai/DeepSeek-V3",
        max_turns=5,
    )
    assert rt._entry_agent is not None
    assert rt._entry_agent.name == "Chat"
    assert len(rt._entry_agent.handoffs) == 1
    print("✓ test_runtime_import PASSED")


def _resolve_agent(handoff_obj):
    """Resolve the target Agent from a Handoff via its weakref."""
    return handoff_obj._agent_ref()


def test_agent_topology_chat():
    """Test Chat Agent structure — no tools, single handoff to Plan."""
    from src.star_chain.agents import build_agent_topology

    chat_agent = build_agent_topology()

    assert chat_agent.name == "Chat"
    assert len(chat_agent.tools) == 0, "Chat Agent should have no tools"
    assert len(chat_agent.handoffs) == 1, "Chat should handoff to exactly one Agent"

    plan_handoff = chat_agent.handoffs[0]
    plan_agent = _resolve_agent(plan_handoff)
    assert plan_agent.name == "Plan", "Chat should handoff to Plan Agent"
    assert plan_handoff.tool_name == "handoff_to_planner"

    print("✓ test_agent_topology_chat PASSED")


def test_agent_topology_plan():
    """Test Plan Agent structure — readonly tools, handoffs to Executor and Chat."""
    from src.star_chain.agents import build_agent_topology
    from src.star_chain.tools import READ_ONLY_TOOLS

    chat_agent = build_agent_topology()
    plan_agent = _resolve_agent(chat_agent.handoffs[0])

    assert plan_agent.name == "Plan"
    assert len(plan_agent.tools) == len(READ_ONLY_TOOLS), (
        f"Plan Agent should have {len(READ_ONLY_TOOLS)} readonly tools, "
        f"got {len(plan_agent.tools)}"
    )

    tool_names = sorted(getattr(t, "name", str(t)) for t in plan_agent.tools)
    expected = sorted(getattr(t, "name", str(t)) for t in READ_ONLY_TOOLS)
    assert tool_names == expected, f"Plan tools mismatch: {tool_names} vs {expected}"

    handoff_names = sorted(h.tool_name for h in plan_agent.handoffs)
    assert handoff_names == ["handoff_to_chat", "handoff_to_executor"], (
        f"Plan handoffs: {handoff_names}"
    )

    targets = {h.tool_name: _resolve_agent(h).name for h in plan_agent.handoffs}
    assert targets["handoff_to_executor"] == "Executor"
    assert targets["handoff_to_chat"] == "Chat"

    print("✓ test_agent_topology_plan PASSED")


def test_agent_topology_executor():
    """Test Executor Agent structure — full tools, single handoff to Chat."""
    from src.star_chain.agents import build_agent_topology
    from src.star_chain.tools import ALL_TOOLS

    chat_agent = build_agent_topology()
    plan_agent = _resolve_agent(chat_agent.handoffs[0])
    executor_handoff = next(h for h in plan_agent.handoffs if h.tool_name == "handoff_to_executor")
    executor_agent = _resolve_agent(executor_handoff)

    assert executor_agent.name == "Executor"
    assert len(executor_agent.tools) == len(ALL_TOOLS), (
        f"Executor should have {len(ALL_TOOLS)} tools, got {len(executor_agent.tools)}"
    )

    assert len(executor_agent.handoffs) == 1
    chat_back = executor_agent.handoffs[0]
    assert chat_back.tool_name == "handoff_to_chat"
    assert _resolve_agent(chat_back).name == "Chat"

    print("✓ test_agent_topology_executor PASSED")


def test_tool_permissions_boundary():
    """Verify Plan Agent only has readonly tools (no write tools)."""
    from src.star_chain.agents import build_agent_topology

    chat_agent = build_agent_topology()
    plan_agent = _resolve_agent(chat_agent.handoffs[0])
    plan_handoff = next(h for h in plan_agent.handoffs if h.tool_name == "handoff_to_executor")
    executor_agent = _resolve_agent(plan_handoff)

    write_tool_names = {"write_file", "patch", "terminal", "execute_code"}
    plan_tool_names = {getattr(t, "name", str(t)) for t in plan_agent.tools}
    executor_tool_names = {getattr(t, "name", str(t)) for t in executor_agent.tools}

    assert plan_tool_names.isdisjoint(write_tool_names), (
        f"Plan Agent should not have write tools, but has: {plan_tool_names & write_tool_names}"
    )
    assert write_tool_names.issubset(executor_tool_names), (
        f"Executor missing write tools: {write_tool_names - executor_tool_names}"
    )

    print("✓ test_tool_permissions_boundary PASSED")


def test_handoff_chain_end_to_end():
    """Verify the full handoff chain: Chat → Plan → Executor → Chat."""
    from src.star_chain.agents import build_agent_topology

    chat = build_agent_topology()

    plan = _resolve_agent(chat.handoffs[0])
    assert plan.name == "Plan"

    handoff_to_exec = next(h for h in plan.handoffs if h.tool_name == "handoff_to_executor")
    executor = _resolve_agent(handoff_to_exec)
    assert executor.name == "Executor"

    handoff_back_chat = executor.handoffs[0]
    chat_back = _resolve_agent(handoff_back_chat)
    assert chat_back.name == "Chat"

    handoff_to_chat_from_plan = next(h for h in plan.handoffs if h.tool_name == "handoff_to_chat")
    assert _resolve_agent(handoff_to_chat_from_plan).name == "Chat"

    assert chat_back is chat, "Executor should handoff back to the same Chat instance"

    print("✓ test_handoff_chain_end_to_end PASSED")


def test_tools_module_exports():
    """Verify tools module exports are correct and consistent."""
    from src.star_chain.tools import (
        READ_ONLY_TOOLS,
        ALL_TOOLS,
        ALL_CODE_TOOLS,
        ALL_WEB_TOOLS,
        ALL_SKILL_TOOLS,
    )

    assert len(READ_ONLY_TOOLS) >= 3, "Should have at least 3 readonly tools"
    assert len(ALL_TOOLS) > len(READ_ONLY_TOOLS), "ALL_TOOLS should be strictly larger"

    for t in READ_ONLY_TOOLS:
        assert t in ALL_TOOLS, f"Readonly tool {t} should be in ALL_TOOLS"

    readonly_names = {getattr(t, "name", str(t)) for t in READ_ONLY_TOOLS}
    all_names = {getattr(t, "name", str(t)) for t in ALL_TOOLS}
    assert readonly_names.issubset(all_names)

    print("✓ test_tools_module_exports PASSED")


if __name__ == "__main__":
    test_session_context()
    test_session_history_cap()
    test_runtime_import()
    test_agent_topology_chat()
    test_agent_topology_plan()
    test_agent_topology_executor()
    test_tool_permissions_boundary()
    test_handoff_chain_end_to_end()
    test_tools_module_exports()
    print("\n✅ All tests passed!")
