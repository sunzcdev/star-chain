"""工具层 — Skill 桥接。

提供 skill 调用能力，兼容：
  - run_skill: 调用已安装的 Hermes Skill
  - call_claude_code: 委托 Claude Code CLI 执行
  - call_open_code: 委托 OpenCode CLI 执行

安全约束：
  - run_skill 只读取已安装 skill，不执行任意脚本
  - CLI 调用有超时限制，防止 runaway
  - Claude Code / OpenCode 以 print mode（非交互式）运行
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from agents import function_tool

# ── 常量 ──────────────────────────────────────────────────────────────

DEFAULT_SKILL_TIMEOUT = 300   # 默认 skill 调用超时（5分钟）
CLI_TIMEOUT = 600              # CLI 工具最大超时（10分钟）
MAX_OUTPUT_CHARS = 50_000      # 输出截断

# Hermes 技能目录
HERMES_PROFILES_DIR = Path.home() / ".hermes" / "profiles"
HERMES_SKILLS_DIR = Path.home() / ".hermes" / "skills"


def _find_skill_dir(name: str) -> Optional[Path]:
    """搜索已安装的 Skill 目录。
    
    搜索顺序：
    1. 当前 profile 的 skills 目录
    2. ~/.hermes/skills/ 公共技能目录
    3. 其他 profile 的 skills 目录
    """
    # 当前 profile
    profile = os.environ.get("HERMES_PROFILE", "executor")
    profile_skills = HERMES_PROFILES_DIR / profile / "skills"
    candidates = []

    # 递归搜索
    for root in [profile_skills, HERMES_SKILLS_DIR]:
        if root.exists():
            # 直接匹配 skill name 的目录
            for d in root.rglob(name):
                if d.is_dir() and (d / "SKILL.md").exists():
                    candidates.append(d)

    if candidates:
        return candidates[0]

    # 尝试搜索任何包含 SKILL.md 且名称匹配的目录
    for root in [profile_skills, HERMES_SKILLS_DIR] + list(HERMES_PROFILES_DIR.glob("*/skills")):
        if root.exists():
            for skill_dir in root.glob(f"*/{name}"):
                if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
                    return skill_dir

    return None


def _read_skill_metadata(skill_dir: Path) -> dict:
    """读取 SKILL.md 的 YAML frontmatter（简化解析）。"""
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        return {}

    content = skill_file.read_text(encoding="utf-8")
    # 简单的 frontmatter 解析（不依赖 yaml 库）
    meta = {}
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    key, _, val = line.partition(":")
                    meta[key.strip()] = val.strip().strip('"').strip("'")
    return meta


def _run_cli(
    cmd: list[str],
    timeout: int,
    workdir: Optional[str] = None,
    env: Optional[dict] = None,
) -> str:
    """运行 CLI 命令并返回格式化输出。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workdir or os.getcwd(),
            env={**os.environ, **(env or {})},
        )
        output = (proc.stdout + "\n" + proc.stderr).strip()
        exit_code = proc.returncode

        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + f"\n... [输出截断: 超过 {MAX_OUTPUT_CHARS} 字符]"

        if exit_code == 0:
            return output if output else "执行成功（无输出）"
        else:
            return f"退出码: {exit_code}\n{output}"

    except subprocess.TimeoutExpired:
        return f"执行超时（{timeout}s）"
    except FileNotFoundError:
        return f"命令未找到: {cmd[0]}"
    except Exception as e:
        return f"执行错误: {e}"


# ── 工具函数 ──────────────────────────────────────────────────────────


def run_skill(
    name: str,
    arguments: Optional[str] = None,
) -> str:
    """运行已安装的 Hermes Skill。

    读取指定 Skill 的 SKILL.md 文件，将其上下文加载到当前对话中。
    注意：本工具返回 Skill 的内容描述而非自动执行其步骤，
    Agent 需根据返回内容自行决定如何执行。

    Args:
        name: Skill 名称（如 "github-code-review", "test-driven-development"）
        arguments: 可选的 JSON 参数，传递给 Skill（仅在某些 Skill 中生效）

    Returns:
        Skill 的 SKILL.md 内容 + 可用的关联文件列表。
    """
    skill_dir = _find_skill_dir(name)
    if not skill_dir:
        # 尝试从 skills_list 获取帮助
        hint = ""
        profile_skills = HERMES_PROFILES_DIR / (os.environ.get("HERMES_PROFILE", "executor")) / "skills"
        if profile_skills.exists():
            available = [d.name for d in profile_skills.iterdir() if d.is_dir()]
            if available:
                hint = f"\n可用技能: {', '.join(sorted(available)[:20])}"
        return f"技能未找到: {name}{hint}"

    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        return f"技能目录 {skill_dir} 中没有 SKILL.md"

    content = skill_file.read_text(encoding="utf-8")

    # 列出关联文件
    linked = []
    for subdir_name in ["references", "templates", "scripts", "assets"]:
        subdir = skill_dir / subdir_name
        if subdir.exists():
            linked.extend([str(f.relative_to(skill_dir)) for f in sorted(subdir.rglob("*")) if f.is_file()])

    result = content
    if linked:
        result += f"\n\n---\n关联文件 ({len(linked)}):\n" + "\n".join(f"  {l}" for l in linked)

    if arguments:
        try:
            parsed = json.loads(arguments)
            result += f"\n\n---\n传入参数:\n{json.dumps(parsed, indent=2, ensure_ascii=False)}"
        except json.JSONDecodeError:
            result += f"\n\n---\n传入参数（原始）:\n{arguments}"

    return result


def call_claude_code(
    prompt: str,
    workdir: Optional[str] = None,
    timeout: int = 300,
) -> str:
    """委托 Claude Code CLI 执行编码任务。

    以 print mode（非交互式）调用 claude 或 claude-code CLI。
    Claude Code 被设计为全栈编码助手，适合复杂重构和新功能开发。

    Args:
        prompt: 要执行的编码任务描述
        workdir: 工作目录（默认当前目录）
        timeout: 超时秒数（默认 300，最大 600）

    Returns:
        Claude Code 执行输出。

    Raises:
        RuntimeError: Claude Code CLI 未安装
    """
    # 检测 claude CLI
    claude_bin = shutil.which("claude") or shutil.which("claude-code")
    if not claude_bin:
        return (
            "Claude Code CLI 未安装。\n"
            "安装方式：npm install -g @anthropic-ai/claude-code\n"
            "或使用 call_open_code / terminal 工具代替。"
        )

    cwd = workdir or os.getcwd()
    eff_timeout = min(timeout, CLI_TIMEOUT)

    # print mode: --print 或 -p 输出结果后退出（非交互式）
    cmd = [claude_bin, "--print", prompt]

    return _run_cli(cmd, eff_timeout, cwd)


def call_open_code(
    prompt: str,
    workdir: Optional[str] = None,
    timeout: int = 300,
) -> str:
    """委托 OpenCode CLI 执行编码任务。

    以非交互式模式调用 opencode CLI。
    OpenCode 适合特征开发和 PR 创建等工作。

    Args:
        prompt: 要执行的编码任务描述
        workdir: 工作目录（默认当前目录）
        timeout: 超时秒数（默认 300，最大 600）

    Returns:
        OpenCode 执行输出。

    Raises:
        RuntimeError: OpenCode CLI 未安装
    """
    opencode_bin = shutil.which("opencode")
    if not opencode_bin:
        return (
            "OpenCode CLI 未安装。\n"
            "安装方式：npm install -g @openai/codex\n"
            "或使用 call_claude_code / terminal 工具代替。"
        )

    cwd = workdir or os.getcwd()
    eff_timeout = min(timeout, CLI_TIMEOUT)

    # 非交互式模式
    cmd = [opencode_bin, prompt]

    return _run_cli(cmd, eff_timeout, cwd)


# ── 注册为 Agent 工具 ────────────────────────────────────────────────

tool_run_skill = function_tool(run_skill)
tool_call_claude_code = function_tool(call_claude_code)
tool_call_open_code = function_tool(call_open_code)

ALL_SKILL_TOOLS = [
    tool_run_skill,
    tool_call_claude_code,
    tool_call_open_code,
]
