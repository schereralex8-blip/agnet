"""Workspace-confined file tools, so the agent can read the actual assignment."""

from __future__ import annotations

from pathlib import Path

from homework_agent.tools.base import Tool, ToolError

# Reading a 200MB file into the context window helps nobody.
MAX_READ_BYTES = 400_000
MAX_CHARS = 60_000
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".DS_Store"}
TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".tex", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".py", ".ipynb", ".java", ".c", ".h", ".cpp", ".cs", ".js", ".ts", ".html", ".css",
    ".sql", ".r", ".m", ".sh", ".log", "",
}


def resolve_in_workspace(workspace: Path, relative: str) -> Path:
    """Resolve `relative` inside the workspace, refusing anything that escapes it."""
    workspace = workspace.resolve()
    candidate = (workspace / relative).expanduser()
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise ToolError(f"cannot resolve path {relative!r}: {exc}") from None
    if resolved != workspace and workspace not in resolved.parents:
        raise ToolError(
            f"{relative!r} is outside the homework folder ({workspace}). "
            "Only paths inside it can be read or written."
        )
    return resolved


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ToolError(
            f"{path.name} is a PDF and pypdf is not installed. "
            "Install it with 'pip install homework-agent[pdf]', or ask the student to paste "
            "the relevant question as text."
        ) from None
    try:
        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - malformed PDFs raise all sorts
        raise ToolError(f"could not read PDF {path.name}: {exc}") from None
    text = "\n\n".join(f"--- page {i} ---\n{body}" for i, body in enumerate(pages, 1) if body)
    if not text.strip():
        raise ToolError(
            f"{path.name} has no extractable text - it is probably a scan. "
            "Ask the student to type out the question."
        )
    return text


class ReadAssignmentTool(Tool):
    name = "read_assignment"
    description = (
        "Read a file from the student's homework folder: the problem set, their draft, their "
        "code, a data file. Always read the file before answering questions about 'the "
        "assignment' or a numbered question. Handles text files and PDFs."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path relative to the homework folder, e.g. 'pset3.pdf'.",
            }
        },
        "required": ["path"],
    }

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)

    def run(self, path: str) -> str:  # type: ignore[override]
        target = resolve_in_workspace(self.workspace, path)
        if not target.exists():
            raise ToolError(f"{path!r} does not exist. Use list_files to see what is there.")
        if target.is_dir():
            raise ToolError(f"{path!r} is a folder - use list_files on it instead.")
        size = target.stat().st_size
        if size > MAX_READ_BYTES:
            raise ToolError(f"{path!r} is {size // 1024}KB, too large to read in full.")

        if target.suffix.lower() == ".pdf":
            text = _read_pdf(target)
        elif target.suffix.lower() in TEXT_SUFFIXES or size < 100_000:
            try:
                text = target.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise ToolError(f"could not read {path!r}: {exc}") from None
        else:
            raise ToolError(f"{path!r} does not look like a text file ({target.suffix}).")

        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + f"\n\n[truncated at {MAX_CHARS} characters]"
        return f"{path}:\n\n{text}"


class ListFilesTool(Tool):
    name = "list_files"
    description = (
        "List the files in the student's homework folder so you can find the assignment. "
        "Returns paths relative to that folder, with sizes."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "subdirectory": {
                "type": "string",
                "description": "Optional subfolder to list. Defaults to the homework folder.",
            }
        },
    }

    def __init__(self, workspace: Path, max_entries: int = 200) -> None:
        self.workspace = Path(workspace)
        self.max_entries = max_entries

    def run(self, subdirectory: str = ".") -> str:  # type: ignore[override]
        root = resolve_in_workspace(self.workspace, subdirectory)
        if not root.exists():
            raise ToolError(f"{subdirectory!r} does not exist.")
        if not root.is_dir():
            raise ToolError(f"{subdirectory!r} is a file, not a folder.")

        entries: list[str] = []
        for path in sorted(root.rglob("*")):
            if any(part in SKIP_DIRS or part.startswith(".") for part in path.parts[len(root.parts):]):
                continue
            rel = path.relative_to(self.workspace.resolve())
            if path.is_dir():
                entries.append(f"{rel}/")
            else:
                entries.append(f"{rel}  ({path.stat().st_size} bytes)")
            if len(entries) >= self.max_entries:
                entries.append(f"[stopped at {self.max_entries} entries]")
                break
        if not entries:
            return f"{subdirectory} is empty."
        return "\n".join(entries)


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Write a file in the student's homework folder - study notes, a summary of what you "
        "worked through, or code they asked you to save. Ask before overwriting something they "
        "wrote. Do not use this to hand in work on their behalf."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the homework folder."},
            "content": {"type": "string", "description": "Full file contents."},
        },
        "required": ["path", "content"],
    }
    requires_approval = True

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)

    def run(self, path: str, content: str) -> str:  # type: ignore[override]
        target = resolve_in_workspace(self.workspace, path)
        if target.is_dir():
            raise ToolError(f"{path!r} is a folder.")
        existed = target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise ToolError(f"could not write {path!r}: {exc}") from None
        verb = "overwrote" if existed else "wrote"
        return f"{verb} {path} ({len(content)} characters)"
