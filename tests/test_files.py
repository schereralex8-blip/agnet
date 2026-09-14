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


def test_word_documents_are_refused_with_a_way_forward(workspace):
    """A .docx is a zip - reading it as text would feed the model mojibake."""
    (workspace / "lab.docx").write_bytes(b"PK\x03\x04binary-zip-contents")
    with pytest.raises(ToolError, match="Word document"):
        ReadAssignmentTool(workspace).run(path="lab.docx")


@pytest.mark.parametrize("name", ["scan.png", "deck.pptx", "data.xlsx", "bundle.zip"])
def test_other_binary_formats_are_named(workspace, name):
    (workspace / name).write_bytes(b"\x89PNG\x00\x01\x02")
    with pytest.raises(ToolError, match="Export it to PDF or plain text"):
        ReadAssignmentTool(workspace).run(path=name)


def test_unlabelled_binary_is_caught_by_its_contents(workspace):
    (workspace / "mystery.dat").write_bytes(b"\x00\x01\x02\x03" * 100)
    with pytest.raises(ToolError, match="binary file"):
        ReadAssignmentTool(workspace).run(path="mystery.dat")


def test_latin1_text_still_reads(workspace):
    """An assignment saved from Word as Latin-1 should not be refused."""
    (workspace / "notes.txt").write_bytes("café naïve".encode("latin-1"))
    assert "caf" in ReadAssignmentTool(workspace).run(path="notes.txt")


def test_utf8_text_reads_exactly(workspace):
    (workspace / "q.txt").write_text("∫ x² dx = x³/3 + C", encoding="utf-8")
    assert "∫ x² dx" in ReadAssignmentTool(workspace).run(path="q.txt")


def test_extensionless_text_file_reads(workspace):
    (workspace / "README").write_text("Q1: prove it")
    assert "prove it" in ReadAssignmentTool(workspace).run(path="README")
