"""Test the MCP tools available to Executor Agent.

Covers:
  - Server config parsing (env vars, JSON file)
  - mcp_list: no servers, list all servers, list nonexistent server
  - mcp_call: nonexistent server, invalid arguments
  - MCP tools correctly exported in ALL_TOOLS
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch as mock_patch, MagicMock, AsyncMock

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


# ── config parsing ───────────────────────────────────────────────────────


def test_mcp_config_no_env():
    """No MCP_SERVERS env var returns empty dict."""
    from src.star_chain.tools.mcp import _parse_servers_from_env

    with mock_patch.dict(os.environ, {}, clear=True):
        servers = _parse_servers_from_env()
        assert isinstance(servers, dict)
        assert len(servers) == 0
    print("✓ test_mcp_config_no_env PASSED")


def test_mcp_config_simple_format():
    """Parse MCP_SERVERS simple format: name:command:arg1,arg2."""
    from src.star_chain.tools.mcp import _parse_servers_from_env

    with mock_patch.dict(os.environ, {"MCP_SERVERS": "files:python3:-m,mcp.server.filesystem"}):
        servers = _parse_servers_from_env()
        assert "files" in servers
        cfg = servers["files"]
        assert cfg.name == "files"
        assert cfg.command == "python3"
        assert cfg.args == ["-m", "mcp.server.filesystem"]
    print("✓ test_mcp_config_simple_format PASSED")


def test_mcp_config_multiple_servers():
    """Multiple servers separated by |."""
    from src.star_chain.tools.mcp import _parse_servers_from_env

    with mock_patch.dict(os.environ, {"MCP_SERVERS": "s1:cmd1|s2:cmd2:a,b"}):
        servers = _parse_servers_from_env()
        assert "s1" in servers
        assert "s2" in servers
        assert servers["s1"].command == "cmd1"
        assert servers["s1"].args == []
        assert servers["s2"].args == ["a", "b"]
    print("✓ test_mcp_config_multiple_servers PASSED")


def test_mcp_config_json_format():
    """Parse MCP_SERVERS_JSON format."""
    from src.star_chain.tools.mcp import _parse_servers_from_env

    cfg_json = json.dumps({
        "my_server": {
            "command": "python",
            "args": ["-m", "my_mcp"],
        }
    })
    with mock_patch.dict(os.environ, {"MCP_SERVERS_JSON": cfg_json}):
        servers = _parse_servers_from_env()
        assert "my_server" in servers
        assert servers["my_server"].command == "python"
        assert servers["my_server"].args == ["-m", "my_mcp"]
    print("✓ test_mcp_config_json_format PASSED")


def test_mcp_config_file():
    """Load servers from JSON config file."""
    from src.star_chain.tools.mcp import _load_servers_from_file

    with tempfile.TemporaryDirectory() as tmpdir:
        cfg_path = Path(tmpdir) / "mcp_servers.json"
        cfg_path.write_text(json.dumps({
            "fileserver": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", tmpdir],
            }
        }), encoding="utf-8")

        servers = _load_servers_from_file(str(cfg_path))
        assert "fileserver" in servers
        assert servers["fileserver"].command == "npx"
    print("✓ test_mcp_config_file PASSED")


# ── mcp_list ────────────────────────────────────────────────────────────


def test_mcp_list_no_servers():
    """mcp_list with no servers returns helpful message."""
    from src.star_chain.tools.mcp import mcp_list

    with mock_patch("src.star_chain.tools.mcp.get_available_servers", return_value={}):
        result = mcp_list()
        assert "没有可用的" in result or "无可用" in result or "MCP" in result
    print("✓ test_mcp_list_no_servers PASSED")


def test_mcp_list_all_servers():
    """mcp_list with no argument lists all configured servers."""
    from src.star_chain.tools.mcp import mcp_list, ServerConfig

    fake_servers = {
        "files": ServerConfig(name="files", command="python3", args=["-m", "fs"]),
        "weather": ServerConfig(name="weather", command="npx"),
    }
    with mock_patch("src.star_chain.tools.mcp.get_available_servers", return_value=fake_servers):
        result = mcp_list()
        assert "files" in result
        assert "weather" in result
        assert "python3" in result
    print("✓ test_mcp_list_all_servers PASSED")


def test_mcp_list_nonexistent_server():
    """mcp_list for non-existent server returns error message."""
    from src.star_chain.tools.mcp import mcp_list, ServerConfig

    fake_servers = {"files": ServerConfig(name="files", command="python3")}
    with mock_patch("src.star_chain.tools.mcp.get_available_servers", return_value=fake_servers):
        result = mcp_list(server="nonexistent")
        assert "不存在" in result or "not found" in result.lower() or "未配置" in result
    print("✓ test_mcp_list_nonexistent_server PASSED")


# ── mcp_call ────────────────────────────────────────────────────────────


def test_mcp_call_nonexistent_server():
    """mcp_call to non-existent server returns error."""
    from src.star_chain.tools.mcp import mcp_call

    with mock_patch("src.star_chain.tools.mcp.get_available_servers", return_value={}):
        result = mcp_call(server="ghost", tool="anything")
        assert "未配置" in result or "not configured" in result.lower()
    print("✓ test_mcp_call_nonexistent_server PASSED")


def test_mcp_call_invalid_json_arguments():
    """mcp_call with invalid JSON arguments returns parse error."""
    from src.star_chain.tools.mcp import mcp_call, ServerConfig

    fake_servers = {"files": ServerConfig(name="files", command="python3")}
    with mock_patch("src.star_chain.tools.mcp.get_available_servers", return_value=fake_servers):
        result = mcp_call(server="files", tool="read", arguments="{not valid json}")
        assert "解析失败" in result or "JSONDecodeError" in result or "JSON" in result
    print("✓ test_mcp_call_invalid_json_arguments PASSED")


def test_mcp_call_non_dict_arguments():
    """mcp_call with non-object JSON (e.g. array) returns error."""
    from src.star_chain.tools.mcp import mcp_call, ServerConfig

    fake_servers = {"files": ServerConfig(name="files", command="python3")}
    with mock_patch("src.star_chain.tools.mcp.get_available_servers", return_value=fake_servers):
        result = mcp_call(server="files", tool="read", arguments='[1, 2, 3]')
        assert "必须是 JSON 对象" in result or "object" in result.lower()
    print("✓ test_mcp_call_non_dict_arguments PASSED")


# ── exports ─────────────────────────────────────────────────────────────


def test_mcp_tools_in_all_tools():
    """MCP tools should be included in ALL_TOOLS."""
    from src.star_chain.tools import ALL_TOOLS, ALL_MCP_TOOLS, tool_mcp_list, tool_mcp_call

    assert len(ALL_MCP_TOOLS) == 2

    all_names = {getattr(t, "name", str(t)) for t in ALL_TOOLS}
    assert "mcp_list" in all_names
    assert "mcp_call" in all_names
    print("✓ test_mcp_tools_in_all_tools PASSED")


def test_mcp_tools_not_in_readonly():
    """MCP tools should NOT be in READ_ONLY_TOOLS (only Executor has them)."""
    from src.star_chain.tools import READ_ONLY_TOOLS

    readonly_names = {getattr(t, "name", str(t)) for t in READ_ONLY_TOOLS}
    assert "mcp_list" not in readonly_names
    assert "mcp_call" not in readonly_names
    print("✓ test_mcp_tools_not_in_readonly PASSED")


# ── MCPServerManager ────────────────────────────────────────────────────


def test_mcp_manager_get_session_missing():
    """MCPServerManager.get_session raises KeyError for missing server."""
    import pytest
    from src.star_chain.tools.mcp import MCPServerManager

    mgr = MCPServerManager()

    async def _test():
        with mock_patch("src.star_chain.tools.mcp.get_available_servers", return_value={}):
            with pytest.raises(KeyError):
                await mgr.get_session("missing")

    import asyncio
    asyncio.run(_test())
    print("✓ test_mcp_manager_get_session_missing PASSED")


if __name__ == "__main__":
    test_mcp_config_no_env()
    test_mcp_config_simple_format()
    test_mcp_config_multiple_servers()
    test_mcp_config_json_format()
    test_mcp_config_file()
    test_mcp_list_no_servers()
    test_mcp_list_all_servers()
    test_mcp_list_nonexistent_server()
    test_mcp_call_nonexistent_server()
    test_mcp_call_invalid_json_arguments()
    test_mcp_call_non_dict_arguments()
    test_mcp_tools_in_all_tools()
    test_mcp_tools_not_in_readonly()
    test_mcp_manager_get_session_missing()
    print("\n✅ All MCP tool tests passed!")
