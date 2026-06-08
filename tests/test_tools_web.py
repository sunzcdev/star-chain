"""Test the web and skill tools available to Executor Agent.

Covers:
  - web_extract (E8): URL fetch (mocked)
  - run_skill (E9): skill not found
  - call_claude_code (E10): CLI not installed
  - call_open_code (E11): CLI not installed
"""

import os
import sys
from unittest.mock import patch as mock_patch

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.star_chain.tools.web import web_extract
from src.star_chain.tools.skill import run_skill, call_claude_code, call_open_code


# ── web_extract ───────────────────────────────────────────────────────


def test_tool_web_extract():
    """Fetch a URL and extract text (mocked)."""
    mock_html = "<html><head><title>Test Page</title></head><body><p>Hello world</p></body></html>"

    with mock_patch("httpx.Client") as mock_client:
        mc = mock_client.return_value.__enter__.return_value
        mc.get.return_value.text = mock_html
        mc.get.return_value.raise_for_status.return_value = None

        result = web_extract("https://example.com")
        assert "Test Page" in result
        assert "Hello world" in result
        assert "example.com" in result

    print("✓ test_tool_web_extract PASSED")


# ── run_skill ─────────────────────────────────────────────────────────


def test_tool_run_skill_not_found():
    """Run a non-existent skill returns 'skill not found'."""
    result = run_skill("__nonexistent_skill_xyz__")
    assert "未找到" in result or "not found" in result.lower() or "技能未找到" in result
    print("✓ test_tool_run_skill_not_found PASSED")


# ── call_claude_code ──────────────────────────────────────────────────


def test_tool_call_claude_code_not_installed():
    """Call Claude Code when not installed returns install instructions."""
    with mock_patch("shutil.which", return_value=None):
        result = call_claude_code("test prompt")
        assert "未安装" in result or "not installed" in result.lower() or "未安装" in result
    print("✓ test_tool_call_claude_code_not_installed PASSED")


# ── call_open_code ────────────────────────────────────────────────────


def test_tool_call_open_code_not_installed():
    """Call OpenCode when not installed returns install instructions."""
    with mock_patch("shutil.which", return_value=None):
        result = call_open_code("test prompt")
        assert "未安装" in result or "not installed" in result.lower() or "未安装" in result
    print("✓ test_tool_call_open_code_not_installed PASSED")


if __name__ == "__main__":
    test_tool_web_extract()
    test_tool_run_skill_not_found()
    test_tool_call_claude_code_not_installed()
    test_tool_call_open_code_not_installed()
    print("\n✅ All web & skill tool tests passed!")
