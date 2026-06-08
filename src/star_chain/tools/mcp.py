"""工具层 — MCP Client。

接入 MCP (Model Context Protocol) Server，
让 Agent 可以调用 MCP 工具。

提供两个工具：
  - mcp_list: 列出可用 MCP Server 及其工具
  - mcp_call: 调用指定 MCP Server 的工具

设计思路：
  - 通过 stdio 子进程启动 MCP Server
  - 管理多个 Server 连接（按名称缓存）
  - 懒加载：首次调用时连接 Server，连接并缓存
  - 配置通过 MCP_SERVERS 环境变量配置，格式：
      name1:command1:arg1,arg2|name2:command2
    或 JSON 格式：
      {"name1": {"command": "...", "args": [...]}}

安全约束：
  - 只允许连接配置中列出的 Server
  - 不允许动态添加新 Server
  - 有调用超时限制
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from agents import function_tool

logger = logging.getLogger(__name__)

# ── 常量 ───────────────────────────────────────────────────────────────────

DEFAULT_TIMEOUT = 30
MAX_TOOL_OUTPUT_CHARS = 50_000

DEFAULT_SERVERS_CONFIG_PATH = "~/.star-chain/mcp_servers.json"


@dataclass
class ServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: Optional[dict[str, str]] = None


def _parse_servers_from_env() -> dict[str, ServerConfig]:
    servers: dict[str, ServerConfig] = {}

    json_config = os.environ.get("MCP_SERVERS_JSON")
    if json_config:
        try:
            data = json.loads(json_config)
            for name, cfg in data.items():
                if isinstance(cfg, dict) and "command" in cfg:
                    servers[name] = ServerConfig(
                        name=name,
                        command=cfg["command"],
                        args=list(cfg.get("args", []) or []),
                        env=cfg.get("env"),
                    )
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Failed to parse MCP_SERVERS_JSON: %s", e)

    simple_config = os.environ.get("MCP_SERVERS")
    if simple_config:
        for entry in simple_config.split("|"):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split(":", 2)
            if len(parts) >= 2:
                name = parts[0].strip()
                command = parts[1].strip()
                args = []
                if len(parts) == 3 and parts[2].strip():
                    args = [a.strip() for a in parts[2].split(",") if a.strip()]
                if name and command and name not in servers:
                    servers[name] = ServerConfig(name=name, command=command, args=args)

    return servers


def _load_servers_from_file(path: str = DEFAULT_SERVERS_CONFIG_PATH) -> dict[str, ServerConfig]:
    config_path = Path(path).expanduser()
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            servers: dict[str, ServerConfig] = {}
            for name, cfg in data.items():
                if isinstance(cfg, dict) and "command" in cfg:
                    servers[name] = ServerConfig(
                        name=name,
                        command=cfg["command"],
                        args=list(cfg.get("args", []) or []),
                        env=cfg.get("env"),
                    )
            return servers
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load MCP servers config %s: %s", config_path, e)
    return {}


def get_available_servers() -> dict[str, ServerConfig]:
    servers = _load_servers_from_file()
    servers.update(_parse_servers_from_env())
    return servers


class MCPServerManager:
    def __init__(self):
        self._connections: dict[str, Any] = {}
        self._configs: dict[str, ServerConfig] = {}
        self._lock = asyncio.Lock()

    async def get_session(self, name: str) -> Any:
        if name not in self._connections:
            servers = get_available_servers()
            if name not in servers:
                available = ", ".join(sorted(servers.keys())) or "(none)"
                raise KeyError(
                    f"MCP Server 「{name}」未配置。可用 Server: {available}"
                )

            config = servers[name]
            session = await self._connect(config)
            self._connections[name] = session
            self._configs[name] = config

        return self._connections[name]

    async def _connect(self, config: ServerConfig) -> Any:
        from mcp.client.stdio import stdio_client
        from mcp import ClientSession

        read, write = await stdio_client(
            command=config.command,
            args=config.args or [],
            env=config.env,
        )
        session = ClientSession(read, write)
        await session.initialize()
        return session

    async def list_tools(self, name: str) -> list[dict]:
        session = await self.get_session(name)
        result = await session.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description or "",
            }
            for t in result.tools
        ]

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Optional[dict] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> str:
        session = await self.get_session(server_name)
        result = await asyncio.wait_for(
            session.call_tool(tool_name, arguments=arguments or {}),
            timeout=timeout,
        )
        output_parts = []
        for content in result.content:
            if hasattr(content, "text"):
                output_parts.append(content.text)
            elif hasattr(content, "json"):
                try:
                    output_parts.append(json.dumps(content.json, ensure_ascii=False, indent=2))
                except Exception:
                    output_parts.append(str(content))
            else:
                output_parts.append(str(content))
        output = "\n".join(output_parts)
        if len(output) > MAX_TOOL_OUTPUT_CHARS:
            output = output[:MAX_TOOL_OUTPUT_CHARS] + f"\n... [截断: 超过 {MAX_TOOL_OUTPUT_CHARS} 字符]"
        return output

    async def close_all(self) -> None:
        for name, session in list(self._connections.items()):
            try:
                await session.close()
            except Exception:
                pass
        self._connections.clear()
        self._configs.clear()


_manager: Optional[MCPServerManager] = None


def get_manager() -> MCPServerManager:
    global _manager
    if _manager is None:
        _manager = MCPServerManager()
    return _manager


def mcp_list(server: Optional[str] = None) -> str:
    servers = get_available_servers()
    if not servers:
        return "没有可用的 MCP Server 配置。\n\n请通过环境变量 MCP_SERVERS 或配置文件 ~/.star-chain/mcp_servers.json 配置。"

    if server is None:
        lines = ["可用的 MCP Server:"]
        for name in sorted(servers.keys()):
            cfg = servers[name]
            line = f"  - {name}: {cfg.command}"
            if cfg.args:
                line += " " + " ".join(cfg.args)
            lines.append(line)
        lines.append("")
        lines.append("使用 mcp_list(server=\"name\") 查看具体工具")
        return "\n".join(lines)

    if server not in servers:
        available = ", ".join(sorted(servers.keys()))
        return f"Server 「{server}」不存在。可用: {available}"

    try:
        manager = get_manager()
        loop = asyncio.get_event_loop()
        tools = loop.run_until_complete(asyncio.wait_for(
            manager.list_tools(server),
            timeout=DEFAULT_TIMEOUT,
        ))
        if tools:
            lines = [f"MCP Server: {server}", "", "工具:"]
            for t in tools:
                desc = t["description"] or "(无描述)"
                lines.append(f"  - {t['name']}: {desc[:100]}")
            return "\n".join(lines)
        else:
            return f"Server 「{server}」没有暴露工具"
    except asyncio.TimeoutError:
        return f"连接 Server 「{server}」超时"
    except Exception as e:
        return f"列出 Server 「{server}」工具失败: {e}"


def mcp_call(server: str, tool: str, arguments: Optional[str] = None) -> str:
    servers = get_available_servers()
    if server not in servers:
        available = ", ".join(sorted(servers.keys())) or "(none)"
        return f"Server 「{server}」未配置。可用: {available}"

    parsed_args: dict[str, Any] = {}
    if arguments:
        try:
            parsed_args = json.loads(arguments)
            if not isinstance(parsed_args, dict):
                return 'arguments 必须是 JSON 对象，例如 {"key": "value"}'
        except json.JSONDecodeError as e:
            return f"arguments 解析失败: {e}"

    try:
        manager = get_manager()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(asyncio.wait_for(
            manager.call_tool(server, tool, parsed_args),
            timeout=DEFAULT_TIMEOUT,
        ))
        return result
    except asyncio.TimeoutError:
        return f"调用 {server}.{tool} 调用超时（{DEFAULT_TIMEOUT}s）"
    except KeyError as e:
        return str(e)
    except Exception as e:
        return f"调用 MCP 工具失败: {e}"


tool_mcp_list = function_tool(mcp_list)
tool_mcp_call = function_tool(mcp_call)

ALL_MCP_TOOLS = [tool_mcp_list, tool_mcp_call]
