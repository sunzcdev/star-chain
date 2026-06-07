"""Functional tests for agent-channel core components."""

import json
import os
import sys
import tempfile
from pathlib import Path

# Ensure project root is on sys.path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.star_chain.session import SessionContext


def test_session_context():
    """Test SessionContext: create, add messages, save, reload."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session = SessionContext(user_id="test_user", storage_dir=tmpdir)

        # Initially empty
        assert session.history == [], f"Expected empty history, got {session.history}"

        # Add messages
        session.add_user_message("你好")
        session.add_assistant_message("你好！有什么可以帮你的？")
        session.save()

        # Verify file was written
        file_path = Path(tmpdir) / "test_user.json"
        assert file_path.exists(), "Session file not created"

        data = json.loads(file_path.read_text(encoding="utf-8"))
        assert data["user_id"] == "test_user"
        assert len(data["history"]) == 2
        assert data["history"][0] == {"role": "user", "content": "你好"}
        assert data["history"][1] == {"role": "assistant", "content": "你好！有什么可以帮你的？"}

        # Reload from disk
        session2 = SessionContext(user_id="test_user", storage_dir=tmpdir)
        assert len(session2.history) == 2
        assert session2.history[0]["content"] == "你好"

        # Clear
        session2.clear()
        assert session2.history == []

        # Verify file updated
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

        # Reload and check
        session2 = SessionContext(user_id="cap_test", storage_dir=tmpdir)
        assert len(session2.history) <= 50

    print("✓ test_session_history_cap PASSED")


def test_runtime_import():
    """Test that AgentRuntime can be constructed."""
    from src.star_chain.runtime import AgentRuntime
    # Just verify it can be instantiated — actual API calls need a real key
    rt = AgentRuntime(
        base_url="https://api.deepseek.com/v1",
        api_key="sk-test",
        model="deepseek-ai/DeepSeek-V3",
        max_turns=5,
    )
    assert rt._router is not None
    assert rt._router.name == "Router"
    assert len(rt._router.handoffs) == 3  # Chat, Plan, Execute
    print("✓ test_runtime_import PASSED")


if __name__ == "__main__":
    test_session_context()
    test_session_history_cap()
    test_runtime_import()
    print("\n✅ All tests passed!")
