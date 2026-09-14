import pytest

from homework_agent.tools.base import ToolError
from homework_agent.tools.files import (
    ListFilesTool,
    ReadAssignmentTool,
    WriteFileTool,
    resolve_in_workspace,
)


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "pset.txt").write_text("Q1: integrate x^2 dx")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "notes.md").write_text("chain rule")
    return tmp_path


def test_reads_a_file(workspace):
    out = ReadAssignmentTool(workspace).run(path="pset.txt")
    assert "integrate x^2" in out


def test_lists_files_recursively(workspace):
    out = ListFilesTool(workspace).run()
    assert "pset.txt" in out
    assert "sub/notes.md" in out


def test_list_skips_dot_directories(workspace):
    (workspace / ".git").mkdir()
    (workspace / ".git" / "config").write_text("x")
    assert ".git" not in ListFilesTool(workspace).run()


@pytest.mark.parametrize("path", ["../secrets.txt", "../../etc/passwd", "/etc/passwd"])
def test_paths_cannot_escape_the_workspace(workspace, path):
    with pytest.raises(ToolError, match="outside the homework folder"):
        resolve_in_workspace(workspace, path)


def test_symlink_out_of_the_workspace_is_blocked(workspace, tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret")
    (workspace / "link.txt").symlink_to(outside)
    with pytest.raises(ToolError, match="outside the homework folder"):
        ReadAssignmentTool(workspace).run(path="link.txt")


def test_missing_file_points_at_list_files(workspace):
    with pytest.raises(ToolError, match="list_files"):
        ReadAssignmentTool(workspace).run(path="nope.txt")


def test_reading_a_directory_is_an_error(workspace):
    with pytest.raises(ToolError, match="folder"):
        ReadAssignmentTool(workspace).run(path="sub")


def test_write_creates_parent_directories(workspace):
    out = WriteFileTool(workspace).run(path="notes/summary.md", content="hello")
    assert (workspace / "notes" / "summary.md").read_text() == "hello"
    assert out.startswith("wrote")


def test_write_reports_an_overwrite(workspace):
    tool = WriteFileTool(workspace)
    tool.run(path="a.txt", content="one")
    assert tool.run(path="a.txt", content="two").startswith("overwrote")


def test_writing_needs_approval():
    assert WriteFileTool(".").requires_approval is True
    assert ReadAssignmentTool(".").requires_approval is False


def test_large_files_are_refused(workspace):
    (workspace / "big.txt").write_text("x" * 500_000)
    with pytest.raises(ToolError, match="too large"):
        ReadAssignmentTool(workspace).run(path="big.txt")


def test_pdf_without_pypdf_explains_itself(workspace, monkeypatch):
    (workspace / "assignment.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setitem(__import__("sys").modules, "pypdf", None)
    with pytest.raises(ToolError):
        ReadAssignmentTool(workspace).run(path="assignment.pdf")
