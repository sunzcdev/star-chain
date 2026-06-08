"""Test the readonly tools available to Plan Agent (and Executor).

Covers:
  - read_file (P1/E1): normal, not found, offset/limit
  - search_files (P2/E4): content search, filename search
  - web_search (P3/E7): basic search (mock httpx)
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch as mock_patch

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.star_chain.tools.code import read_file, search_files
from src.star_chain.tools.web import web_search


# ── read_file ──────────────────────────────────────────────────────────


def test_tool_read_file_normal():
    """Read an existing file successfully."""
    with tempfile.TemporaryDirectory() as tmpdir:
        fp = Path(tmpdir) / "hello.txt"
        fp.write_text("line1\nline2\nline3\n", encoding="utf-8")

        result = read_file(str(fp))
        assert "line1" in result
        assert "line2" in result
        assert "line3" in result
        assert "3 行" in result or "3行" in result
    print("✓ test_tool_read_file_normal PASSED")


def test_tool_read_file_not_found():
    """Read a non-existent file raises FileNotFoundError."""
    try:
        read_file("/tmp/__nonexistent_file_xyz__")
        assert False, "Expected FileNotFoundError"
    except FileNotFoundError:
        pass
    print("✓ test_tool_read_file_not_found PASSED")


def test_tool_read_file_offset_limit():
    """Read file with offset and limit parameters."""
    with tempfile.TemporaryDirectory() as tmpdir:
        fp = Path(tmpdir) / "lines.txt"
        lines = [f"line_{i}\n" for i in range(100)]
        fp.write_text("".join(lines), encoding="utf-8")

        result = read_file(str(fp), offset=10, limit=5)
        assert "line_9" in result   # 10th line (1-indexed)
        assert "line_13" in result  # 14th line
        assert "line_14" not in result  # beyond limit
        assert "5 行" in result or "5行" in result or "显示 10-14" in result
    print("✓ test_tool_read_file_offset_limit PASSED")


# ── search_files ──────────────────────────────────────────────────────


def test_tool_search_files_by_content():
    """Search file content by pattern."""
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "src").mkdir()
        (Path(tmpdir) / "src" / "app.py").write_text(
            "def hello():\n    print('hello world')\n", encoding="utf-8"
        )
        (Path(tmpdir) / "src" / "utils.py").write_text(
            "def helper():\n    pass\n", encoding="utf-8"
        )

        result = search_files("hello", target="content", path=tmpdir, file_glob="*.py")
        assert "hello" in result
        assert "app.py" in result
    print("✓ test_tool_search_files_by_content PASSED")


def test_tool_search_files_by_name():
    """Search files by filename pattern."""
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "README.md").write_text("# readme", encoding="utf-8")
        (Path(tmpdir) / "src").mkdir()
        (Path(tmpdir) / "src" / "main.py").write_text("", encoding="utf-8")

        result = search_files("README*", target="files", path=tmpdir)
        assert "README.md" in result
        assert "1 个" in result or "1个" in result or "found" in result.lower()
    print("✓ test_tool_search_files_by_name PASSED")


# ── web_search ────────────────────────────────────────────────────────


def test_tool_web_search():
    """Web search — mock httpx to return fake results."""
    mock_html = """
    <html><body>
    <div class="result">
      <a class="result__a" href="https://example.com">Example</a>
      <a class="result__snippet">This is a test snippet</a>
    </div>
    </body></html>
    """

    with mock_patch("httpx.Client") as mock_client:
        mc = mock_client.return_value.__enter__.return_value
        mc.post.return_value.text = mock_html
        mc.post.return_value.raise_for_status.return_value = None

        result = web_search("test query")
        assert "Example" in result
        assert "https://example.com" in result
        assert "test query" in result or "Test Query" in result

    print("✓ test_tool_web_search PASSED")


if __name__ == "__main__":
    test_tool_read_file_normal()
    test_tool_read_file_not_found()
    test_tool_read_file_offset_limit()
    test_tool_search_files_by_content()
    test_tool_search_files_by_name()
    test_tool_web_search()
    print("\n✅ All readonly tool tests passed!")
