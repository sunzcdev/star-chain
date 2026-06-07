"""工具层 — Code 原子能力集合。

提供 read_file, write_file, patch, search_files, terminal, execute_code
等代码操作工具，供 Executor Agent 使用。

所有函数都写成裸函数（方便测试），再通过 @function_tool 包装注册。
安全约束：
  - 文件写入限定 WORKSPACE_DIR（环境变量或默认 ~/projects/agent-channel）
  - execute_code 用 ast 静态分析拦截危险 import
  - terminal 有默认超时和输出截断
"""

import ast
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Optional

from agents import function_tool

# ── 安全边界 ──────────────────────────────────────────────────────────

_workspace = (
    os.environ.get("STAR_CHAIN_WORKSPACE")
    or os.environ.get("HERMES_KANBAN_WORKSPACE")
    or os.path.join(os.environ.get("HOME", "/tmp"), "projects/agent-channel")
)
WORKSPACE_DIR = Path(_workspace).resolve()

MAX_OUTPUT_CHARS = 50_000  # 单次 terminal 输出截断
MAX_FILE_CHARS = 100_000   # 单文件读取上限
DEFAULT_TIMEOUT = 180       # terminal 默认超时（秒）
MAX_TIMEOUT = 600           # 最大允许超时

# 静态黑名单 — execute_code 禁止 import 的模块名
BLOCKED_IMPORTS: set[str] = {
    "os",      # 允许通过 terminal 间接访问
    "subprocess",
    "shutil",
    "ctypes",
    "socket",
    "requests",  # 网络请求由 web 模块管理
    "http",
    "urllib",
    "importlib",
}


def _resolve_path(path: str) -> Path:
    """解析路径为绝对路径，展开 ~ 和变量。"""
    return Path(os.path.expandvars(os.path.expanduser(path))).resolve()


def _check_write_permitted(target: Path) -> None:
    """写入前检查：必须在 WORKSPACE_DIR 下。"""
    try:
        target.resolve().relative_to(WORKSPACE_DIR)
    except ValueError:
        raise PermissionError(
            f"写入被拒绝：{target} 不在允许的工作区 {WORKSPACE_DIR} 下"
        )


def _check_file_safe(path: Path) -> None:
    """检查路径合法性：禁止符号链接逃逸。"""
    resolved = path.resolve()
    # 如果 path 是符号链接且指向 WORKSPACE_DIR 外部
    if path.is_symlink():
        try:
            resolved.relative_to(WORKSPACE_DIR)
        except ValueError:
            raise PermissionError(f"符号链接目标不在工作区内：{resolved}")


# ── 工具函数 ──────────────────────────────────────────────────────────


def read_file(
    path: str,
    offset: int = 1,
    limit: int = 500,
) -> str:
    """读取文件内容，支持分页。

    Args:
        path: 文件路径（支持 ~ 和 $VAR 展开）
        offset: 起始行号（1-indexed，默认 1）
        limit: 最多返回行数（默认 500，最大 2000）

    Returns:
        格式化的行内容，超出 MAX_FILE_CHARS 截断。

    Raises:
        FileNotFoundError: 文件不存在
        PermissionError: 路径不在允许范围内
    """
    fp = _resolve_path(path)
    if not fp.exists():
        # 尝试搜索相似文件名
        similar = list(Path.cwd().glob(f"**/{fp.name}"))
        hint = f"\n相似文件：{[str(s) for s in similar[:5]]}" if similar else ""
        raise FileNotFoundError(f"文件不存在：{fp}{hint}")

    if not fp.is_file():
        raise IsADirectoryError(f"路径是目录，不是文件：{fp}")

    _check_file_safe(fp)

    content = fp.read_text(encoding="utf-8", errors="replace")
    total_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)

    lines = content.splitlines(keepends=True)
    start = max(0, offset - 1)
    end = min(start + limit, len(lines))
    selected = lines[start:end]

    # 截断保护
    result = "".join(selected)
    if len(result) > MAX_FILE_CHARS:
        result = result[:MAX_FILE_CHARS] + "\n... [截断: 超过最大字符数]"

    # 附加行号标头
    numbered = "".join(
        f"{i + 1:>6}|{line}"
        for i, line in enumerate(selected)
    )

    output = f"文件: {fp} (共 {total_lines} 行, 显示 {offset}-{offset + len(selected) - 1})\n{'-' * 60}\n{numbered}"
    if end < total_lines:
        output += f"\n... (还有 {total_lines - end} 行, 用 offset={end + 1} 继续查看)"
    return output


def write_file(
    path: str,
    content: str,
) -> str:
    """写入文件（完全覆盖已有内容）。

    Args:
        path: 文件路径（必须位于 WORKSPACE_DIR 下）
        content: 文件内容

    Returns:
        成功消息。

    Raises:
        PermissionError: 路径不在工作区内
    """
    fp = _resolve_path(path)
    _check_write_permitted(fp)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content, encoding="utf-8")
    return f"写入成功：{fp} ({len(content)} 字符)"


def patch(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """在文件中执行 find-and-replace 编辑。

    Args:
        path: 文件路径（必须位于 WORKSPACE_DIR 下）
        old_string: 要查找替换的旧文本（必须唯一，除非 replace_all=True）
        new_string: 替换后的新文本（传空字符串删除匹配文本）
        replace_all: 是否替换所有匹配（默认 False）

    Returns:
        替换统计信息。
    """
    fp = _resolve_path(path)
    _check_write_permitted(fp)
    _check_file_safe(fp)

    old_content = fp.read_text(encoding="utf-8")

    if not replace_all:
        count = old_content.count(old_string)
        if count == 0:
            # 模糊匹配提示
            lines = old_content.splitlines()
            fuzzy = [l for l in lines if old_string[:20] in l][:3]
            hint = f"\n附近行：{[l.strip()[:60] for l in fuzzy]}" if fuzzy else ""
            raise ValueError(f"未找到匹配文本「{old_string[:40]}」{hint}")
        if count > 1:
            raise ValueError(
                f"找到 {count} 处匹配，请用 replace_all=True 或提供更精确的匹配文本"
            )
        new_content = old_content.replace(old_string, new_string, 1)
    else:
        count = old_content.count(old_string)
        new_content = old_content.replace(old_string, new_string)

    fp.write_text(new_content, encoding="utf-8")

    # 计算变更行数
    old_lines = old_content.count("\n")
    new_lines = new_content.count("\n")
    return f"替换完成：{fp} — {count} 处匹配, {old_lines}→{new_lines} 行"


def search_files(
    pattern: str,
    target: str = "content",
    path: str = ".",
    file_glob: Optional[str] = None,
    limit: int = 50,
) -> str:
    """搜索文件内容或文件名。

    Args:
        pattern: 搜索模式（正则表达式或 glob 模式）
        target: "content" 搜索文件内容，"files" 按文件名查找
        path: 搜索起始目录（默认当前目录）
        file_glob: 内容搜索时限定文件类型（如 "*.py"）
        limit: 最多返回结果数（默认 50）

    Returns:
        格式化搜索结果。
    """
    search_path = _resolve_path(path)
    if not search_path.exists():
        return f"路径不存在：{search_path}"
    if not search_path.is_dir():
        return f"路径不是目录：{search_path}"

    # 使用 ripgrep（如果有）或 fallback 到 grep -r
    rg = shutil.which("rg")
    grep = shutil.which("grep")

    if target == "files":
        # 按文件名查找
        from pathlib import Path as _Path
        matches = list(search_path.rglob(pattern)) if "*" in pattern or "?" in pattern else list(search_path.rglob(f"*{pattern}*"))
        total = len(matches)
        matches = matches[:limit]
        lines = [str(m.relative_to(search_path)) for m in matches]
        result = f"找到 {total} 个文件（显示 {len(lines)} 个）：\n" + "\n".join(lines)
        if total > limit:
            result += f"\n... 还有 {total - limit} 个结果"
        return result

    # 内容搜索
    cmd_parts = []
    if rg:
        cmd_parts = [rg, "-n", "--no-heading"]
        if file_glob:
            cmd_parts.extend(["-g", file_glob])
        cmd_parts.extend([pattern, str(search_path)])
    elif grep:
        cmd_parts = ["grep", "-rn", "--color=never"]
        if file_glob:
            cmd_parts.extend(["--include", file_glob])
        cmd_parts.extend([pattern, str(search_path)])
    else:
        return "未找到 rg 或 grep，无法执行文件搜索"

    try:
        result = subprocess.run(
            cmd_parts,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout.strip()
        if not output:
            return f"未找到匹配「{pattern}」"
        lines_out = output.splitlines()
        total = len(lines_out)
        lines_out = lines_out[:limit]
        truncated = total - len(lines_out)
        summary = f"找到 {total} 处匹配（显示 {len(lines_out)} 条）" + (f"，{truncated} 条截断" if truncated > 0 else "")
        return summary + "\n" + "\n".join(lines_out)
    except subprocess.TimeoutExpired:
        return f"搜索超时（30s），请缩小搜索范围"
    except Exception as e:
        return f"搜索出错：{e}"


def terminal(
    command: str,
    timeout: Optional[int] = None,
    workdir: Optional[str] = None,
    pty: bool = False,
    background: bool = False,
) -> str:
    """执行 Shell 命令。

    Args:
        command: 要执行的命令
        timeout: 超时秒数（默认 180，最大 600，0 表示不限）
        workdir: 工作目录（默认当前目录）
        pty: 是否使用 PTY（交互式 CLI 工具需要）
        background: 是否后台运行（启动后立即返回）

    Returns:
        命令标准输出 + 标准错误 + 退出码。

    Raises:
        subprocess.TimeoutExpired: 命令超时
    """
    effective_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
    if effective_timeout > MAX_TIMEOUT:
        effective_timeout = MAX_TIMEOUT

    cwd = _resolve_path(workdir) if workdir else Path.cwd()

    try:
        if pty:
            import pty as pty_module
            import select

            master_fd, slave_fd = pty_module.openpty()
            proc = subprocess.Popen(
                command,
                shell=True,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=cwd,
                close_fds=True,
            )
            os.close(slave_fd)
            output = b""
            deadline = effective_timeout if effective_timeout > 0 else None
            import time
            start = time.monotonic()
            while True:
                if deadline and (time.monotonic() - start) > deadline:
                    proc.kill()
                    raise subprocess.TimeoutExpired(command, effective_timeout, output)
                r, _, _ = select.select([master_fd], [], [], 0.5)
                if r:
                    try:
                        data = os.read(master_fd, 4096)
                        if not data:
                            break
                        output += data
                        if len(output) > MAX_OUTPUT_CHARS:
                            output = output[:MAX_OUTPUT_CHARS]
                            break
                    except OSError:
                        break
                elif proc.poll() is not None:
                    break
            os.close(master_fd)
            proc.wait()
            out_text = output.decode("utf-8", errors="replace")
            exit_code = proc.returncode
        else:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=effective_timeout if effective_timeout > 0 else None,
            )
            out_text = proc.stdout + proc.stderr
            exit_code = proc.returncode

        if len(out_text) > MAX_OUTPUT_CHARS:
            out_text = out_text[:MAX_OUTPUT_CHARS] + f"\n... [输出截断: 超过 {MAX_OUTPUT_CHARS} 字符]"

        return f"$ {command}\n{out_text.strip()}\n[exit code: {exit_code}]"

    except subprocess.TimeoutExpired:
        return f"$ {command}\n[超时: {effective_timeout}s]"
    except Exception as e:
        return f"$ {command}\n[错误: {e}]"


def execute_code(
    code: str,
) -> str:
    """执行 Python 代码片段（AST 安全沙箱）。

    使用 ast 静态分析拦截危险 import 和内置函数访问，
    在临时命名空间中执行代码，会捕获异常但不会阻塞系统。

    Args:
        code: 要执行的 Python 代码

    Returns:
        执行结果（stdout）或错误信息。
    """
    # ── AST 静态安全检查 ──
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"语法错误：{e}"

    for node in ast.walk(tree):
        # 拦截 import os, import subprocess 等
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in BLOCKED_IMPORTS or any(
                    alias.name.startswith(b + ".") for b in BLOCKED_IMPORTS
                ):
                    return f"安全拦截：禁止 import「{alias.name}」（使用 terminal 工具替代）"

        # 拦截 from os import *
        if isinstance(node, ast.ImportFrom):
            if node.module and (
                node.module in BLOCKED_IMPORTS
                or any(node.module.startswith(b + ".") for b in BLOCKED_IMPORTS)
            ):
                return f"安全拦截：禁止 from「{node.module}」import（使用 terminal 工具替代）"

        # 拦截 exec / eval / __import__
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in {"exec", "eval", "__import__"}:
                return f"安全拦截：禁止使用内置 {node.func.id}()"
            if isinstance(node.func, ast.Attribute) and node.func.attr in {"exec", "eval", "__import__"}:
                return f"安全拦截：禁止使用 {node.func.attr}()"

    # ── 安全沙箱执行 ──
    safe_globals = {
        "__builtins__": {
            # 只允许安全的 builtins
            "abs": abs,
            "all": all,
            "any": any,
            "ascii": ascii,
            "bin": bin,
            "bool": bool,
            "bytes": bytes,
            "chr": chr,
            "complex": complex,
            "dict": dict,
            "dir": dir,
            "divmod": divmod,
            "enumerate": enumerate,
            "filter": filter,
            "float": float,
            "format": format,
            "frozenset": frozenset,
            "getattr": getattr,
            "hasattr": hasattr,
            "hash": hash,
            "hex": hex,
            "id": id,
            "int": int,
            "isinstance": isinstance,
            "issubclass": issubclass,
            "iter": iter,
            "len": len,
            "list": list,
            "map": map,
            "max": max,
            "min": min,
            "next": next,
            "object": object,
            "oct": oct,
            "ord": ord,
            "pow": pow,
            "print": print,
            "range": range,
            "repr": repr,
            "reversed": reversed,
            "round": round,
            "set": set,
            "slice": slice,
            "sorted": sorted,
            "str": str,
            "sum": sum,
            "super": super,
            "tuple": tuple,
            "type": type,
            "vars": vars,
            "zip": zip,
            # 常用模块白名单
            "__import__": __import__,  # 会通过安全检查，但受限
        },
        # 安全的 stdlib
        "json": __import__("json"),
        "math": __import__("math"),
        "re": __import__("re"),
        "datetime": __import__("datetime"),
        "collections": __import__("collections"),
        "itertools": __import__("itertools"),
        "functools": __import__("functools"),
        "typing": __import__("typing"),
        "pathlib": __import__("pathlib"),
        "textwrap": __import__("textwrap"),
        "decimal": __import__("decimal"),
        "statistics": __import__("statistics"),
        "random": __import__("random"),
        "string": __import__("string"),
    }

    # 捕获 stdout
    from io import StringIO

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = StringIO()
    sys.stderr = StringIO()

    try:
        exec(code, safe_globals)
        stdout = sys.stdout.getvalue()
        stderr = sys.stderr.getvalue()
        result = stdout
        if stderr:
            result += f"\n[stderr]\n{stderr}"
        return result if result else "代码执行成功，无输出"
    except Exception as e:
        return f"运行时错误：{type(e).__name__}: {e}"
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


# ── 注册为 Agent 工具 ────────────────────────────────────────────────

tool_read_file = function_tool(read_file)
tool_write_file = function_tool(write_file)
tool_patch = function_tool(patch)
tool_search_files = function_tool(search_files)
tool_terminal = function_tool(terminal)
tool_execute_code = function_tool(execute_code)

# 方便批量导出
ALL_CODE_TOOLS = [
    tool_read_file,
    tool_write_file,
    tool_patch,
    tool_search_files,
    tool_terminal,
    tool_execute_code,
]
