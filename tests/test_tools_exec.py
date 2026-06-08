"""Test the execution tools available to Executor Agent.

Covers:
  - terminal (E5): echo, timeout, workdir
  - execute_code (E6): simple, security block, syntax error
"""

import os
import sys
import tempfile
from pathlib import Path

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.star_chain.tools.code import terminal, execute_code


# ── terminal ──────────────────────────────────────────────────────────


def test_tool_terminal_echo():
    """Run a simple shell command."""
    result = terminal("echo hello world", timeout=10)
    assert "hello world" in result
    assert "exit code: 0" in result or "exit_code: 0" in result
    print("✓ test_tool_terminal_echo PASSED")


def test_tool_terminal_timeout():
    """Command that times out returns timeout message."""
    result = terminal("sleep 10", timeout=1)
    assert "超时" in result or "timeout" in result.lower() or "Timeout" in result
    print("✓ test_tool_terminal_timeout PASSED")


def test_tool_terminal_workdir():
    """Run command in a specific working directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = terminal("pwd", timeout=10, workdir=tmpdir)
        resolved = str(Path(tmpdir).resolve())
        assert resolved in result
    print("✓ test_tool_terminal_workdir PASSED")


# ── execute_code ──────────────────────────────────────────────────────


def test_tool_execute_code_simple():
    """Execute a simple Python expression."""
    result = execute_code("print('hello from sandbox')")
    assert "hello from sandbox" in result
    print("✓ test_tool_execute_code_simple PASSED")


def test_tool_execute_code_security():
    """Blocked import should be intercepted."""
    result = execute_code("import os\nprint(os.name)")
    assert "安全拦截" in result or "禁止" in result
    print("✓ test_tool_execute_code_security PASSED")


def test_tool_execute_code_syntax_error():
    """Syntax error should return error message."""
    result = execute_code("def broken(")
    assert "语法错误" in result or "SyntaxError" in result
    print("✓ test_tool_execute_code_syntax_error PASSED")


if __name__ == "__main__":
    test_tool_terminal_echo()
    test_tool_terminal_timeout()
    test_tool_terminal_workdir()
    test_tool_execute_code_simple()
    test_tool_execute_code_security()
    test_tool_execute_code_syntax_error()
    print("\n✅ All exec tool tests passed!")
