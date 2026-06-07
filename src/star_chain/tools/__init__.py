"""工具层 — Code 原子能力、Web Search、Skill、MCP。

工具集合分层导出，供不同角色的 Agent 按需取用：
  - READ_ONLY_TOOLS: Plan Agent 专用（不允许修改）
  - ALL_TOOLS: Executor Agent 专用（全套能力）
"""

from .code import (
    ALL_CODE_TOOLS,
    tool_read_file,
    tool_search_files,
    tool_write_file,
    tool_patch,
    tool_terminal,
    tool_execute_code,
)
from .web import ALL_WEB_TOOLS, tool_web_search
from .skill import ALL_SKILL_TOOLS

READ_ONLY_TOOLS = [
    tool_read_file,
    tool_search_files,
    tool_web_search,
]

ALL_TOOLS = READ_ONLY_TOOLS + [
    t for t in ALL_CODE_TOOLS
    if t not in READ_ONLY_TOOLS
] + [
    t for t in ALL_WEB_TOOLS
    if t not in READ_ONLY_TOOLS
] + ALL_SKILL_TOOLS

__all__ = [
    "ALL_CODE_TOOLS",
    "ALL_WEB_TOOLS",
    "ALL_SKILL_TOOLS",
    "READ_ONLY_TOOLS",
    "ALL_TOOLS",
    "tool_read_file",
    "tool_search_files",
    "tool_write_file",
    "tool_patch",
    "tool_terminal",
    "tool_execute_code",
    "tool_web_search",
]
