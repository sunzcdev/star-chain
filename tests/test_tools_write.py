"""Test the write tools available to Executor Agent.

Covers:
  - write_file (E2): normal write, permission denied
  - patch (E3): single replace, replace_all, not found

Note: write_file and patch restrict writes to WORKSPACE_DIR.
We use a subdirectory under the project root for normal tests.
"""

import os
import sys
import tempfile
from pathlib import Path

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.star_chain.tools.code import write_file, patch, WORKSPACE_DIR


def _workspace_tmp() -> Path:
    """Create a temp dir inside WORKSPACE_DIR for write tests."""
    tmp = WORKSPACE_DIR / "__test_write_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


# ── write_file ────────────────────────────────────────────────────────


def test_tool_write_file_normal():
    """Write content to a file successfully (inside WORKSPACE_DIR)."""
    tmpdir = _workspace_tmp()
    try:
        fp = tmpdir / "output.txt"
        result = write_file(str(fp), "hello world")
        assert fp.exists()
        assert fp.read_text(encoding="utf-8") == "hello world"
        assert "写入成功" in result
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    print("✓ test_tool_write_file_normal PASSED")


def test_tool_write_file_permission():
    """Write to a path outside the workspace raises PermissionError."""
    try:
        write_file("/etc/evil.txt", "bad")
        assert False, "Expected PermissionError"
    except PermissionError:
        pass
    print("✓ test_tool_write_file_permission PASSED")


# ── patch ─────────────────────────────────────────────────────────────


def test_tool_patch_single():
    """Replace a single occurrence in a file (inside WORKSPACE_DIR)."""
    tmpdir = _workspace_tmp()
    try:
        fp = tmpdir / "config.txt"
        fp.write_text("hello world\ngoodbye world\n", encoding="utf-8")

        result = patch(str(fp), "hello", "hi", replace_all=False)
        content = fp.read_text(encoding="utf-8")
        assert content == "hi world\ngoodbye world\n"
        assert "替换完成" in result
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    print("✓ test_tool_patch_single PASSED")


def test_tool_patch_replace_all():
    """Replace all occurrences in a file."""
    tmpdir = _workspace_tmp()
    try:
        fp = tmpdir / "config.txt"
        fp.write_text("hello world\nhello again\n", encoding="utf-8")

        result = patch(str(fp), "hello", "hi", replace_all=True)
        content = fp.read_text(encoding="utf-8")
        assert content == "hi world\nhi again\n"
        assert "替换完成" in result
        assert "2 处" in result
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    print("✓ test_tool_patch_replace_all PASSED")


def test_tool_patch_not_found():
    """Attempt to patch a non-existent string raises ValueError."""
    tmpdir = _workspace_tmp()
    try:
        fp = tmpdir / "data.txt"
        fp.write_text("existing content\n", encoding="utf-8")

        try:
            patch(str(fp), "nonexistent", "replacement")
            assert False, "Expected ValueError"
        except ValueError:
            pass
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    print("✓ test_tool_patch_not_found PASSED")


if __name__ == "__main__":
    test_tool_write_file_normal()
    test_tool_write_file_permission()
    test_tool_patch_single()
    test_tool_patch_replace_all()
    test_tool_patch_not_found()
    print("\n✅ All write tool tests passed!")
