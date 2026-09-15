"""Write answers straight into a Word document, in place.

The fiddly part is that Word splits visible text across many runs - a blank you can
see as "________" is often three or four `<w:r>` elements, so a naive
`"________" in paragraph.text` match finds it but a per-run replace does not. The
helpers below work across run boundaries and keep the surrounding formatting.
"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any, Iterator

from homework_agent.tools.base import Tool, ToolError
from homework_agent.tools.files import resolve_in_workspace

EDITABLE_SUFFIXES = {".docx", ".dotx"}


def _require_docx() -> Any:
    try:
        import docx
    except ImportError:
        raise ToolError(
            "python-docx is not installed, so the document cannot be edited. "
            "Install it with 'pip install homework-agent[docx]'."
        ) from None
    return docx


def _normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def _iter_paragraphs(document: Any) -> Iterator[Any]:
    """Every paragraph in the document, including the ones inside table cells."""
    yield from document.paragraphs
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs
                for nested in cell.tables:
                    for nested_row in nested.rows:
                        for nested_cell in nested_row.cells:
                            yield from nested_cell.paragraphs


def find_anchor(document: Any, anchor: str) -> Any:
    """The one paragraph containing `anchor`, or a ToolError explaining why not."""
    needle = _normalize(anchor)
    if not needle:
        raise ToolError("anchor is empty - give the text of the question to write under")

    matches = [p for p in _iter_paragraphs(document) if needle in _normalize(p.text)]
    if len(matches) == 1:
        return matches[0]

    if not matches:
        sample = [p.text.strip() for p in _iter_paragraphs(document) if p.text.strip()][:15]
        listed = "\n".join(f"  - {text[:80]}" for text in sample)
        raise ToolError(
            f"no paragraph contains {anchor!r}. Quote the question exactly as it appears. "
            f"The document starts:\n{listed}"
        )

    shown = "\n".join(f"  - {p.text.strip()[:80]}" for p in matches[:5])
    raise ToolError(
        f"{anchor!r} matches {len(matches)} paragraphs, so it is ambiguous. Use a longer "
        f"quote that appears only once. Matches:\n{shown}"
    )


def replace_in_paragraph(paragraph: Any, needle: str, replacement: str) -> bool:
    """Replace `needle` inside a paragraph, even when it spans several runs.

    The first affected run keeps its formatting and receives the replacement; the
    rest of the matched text is removed from the runs that held it.
    """
    runs = list(paragraph.runs)
    if not runs:
        return False

    # Snapshot offsets before mutating anything - editing a run changes later offsets.
    spans: list[tuple[Any, int, int]] = []
    position = 0
    for run in runs:
        spans.append((run, position, position + len(run.text)))
        position += len(run.text)

    text = "".join(run.text for run in runs)
    start = text.find(needle)
    if start == -1:
        return False
    end = start + len(needle)

    first = True
    for run, run_start, run_end in spans:
        if run_end <= start or run_start >= end:
            continue
        local_start = max(start - run_start, 0)
        local_end = min(end - run_start, run_end - run_start)
        head, tail = run.text[:local_start], run.text[local_end:]
        run.text = head + replacement + tail if first else head + tail
        first = False
    return True


W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _anchor_indent(paragraph: Any) -> Any:
    """The anchor's effective left indent, whether set directly or by its style."""
    indent = paragraph.paragraph_format.left_indent
    if indent is not None:
        return indent
    style = paragraph.style
    while style is not None:
        style_indent = getattr(style.paragraph_format, "left_indent", None)
        if style_indent is not None:
            return style_indent
        style = getattr(style, "base_style", None)
    return None


def insert_paragraphs_after(paragraph: Any, texts: list[str], italic: bool = False) -> list[Any]:
    """Insert new paragraphs directly after `paragraph`, matching its indentation.

    The anchor's style and numbering are deliberately dropped: an answer written
    under "Q3" as a List Number paragraph becomes question 4 in the assignment's own
    numbering, and an answer under a heading becomes a heading. Indentation is
    carried over so the answer still lines up under its question.
    """
    from docx.text.paragraph import Paragraph

    indent = _anchor_indent(paragraph)
    created = []
    previous = paragraph
    for text in texts:
        element = copy.deepcopy(paragraph._p)
        # Keep the paragraph shell, drop the anchor's content...
        for child in list(element):
            if child.tag in (f"{W_NS}r", f"{W_NS}hyperlink", f"{W_NS}ins", f"{W_NS}del"):
                element.remove(child)
        # ...and the properties that would make this look like part of the question.
        for properties in element.findall(f"{W_NS}pPr"):
            for tag in (f"{W_NS}numPr", f"{W_NS}pStyle", f"{W_NS}outlineLvl"):
                for unwanted in properties.findall(tag):
                    properties.remove(unwanted)

        previous._p.addnext(element)
        new_paragraph = Paragraph(element, paragraph._parent)
        if indent is not None:
            new_paragraph.paragraph_format.left_indent = indent
        run = new_paragraph.add_run(text)
        run.italic = italic
        created.append(new_paragraph)
        previous = new_paragraph
    return created


def _split_answer(answer: str) -> list[str]:
    """One paragraph per line the model wrote; blank lines are separators."""
    lines = [line.strip() for line in answer.replace("\r\n", "\n").split("\n")]
    return [line for line in lines if line] or [answer.strip()]


class FillDocumentTool(Tool):
    name = "fill_document"
    description = (
        "Write answers directly into a Word document (.docx), in place. Give a list of edits; "
        "each one anchors to text already in the document. Use where='after' to put the answer "
        "in a new paragraph below the question (the default), where='replace' to fill a blank "
        "such as '________' or the word 'Answer:', and where='append' to continue the question's "
        "own paragraph. Anchors must quote the document exactly and match one paragraph only - "
        "read the file first. When replacing, 'anchor' finds the paragraph and 'blank' is the "
        "text replaced inside it: to fill one of two blanks in the same question, anchor on the "
        "question and set blank to '________'. Leave blank out only when the anchor is itself the "
        "text to replace. Two blanks in one paragraph take two edits - each fills the first one "
        "still empty. A backup of the original is kept automatically. Send all the edits for one "
        "document in a single call."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The .docx file, relative to the folder."},
            "edits": {
                "type": "array",
                "description": "The edits to apply, in document order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "anchor": {
                            "type": "string",
                            "description": (
                                "Text quoted from the document that says where this goes - "
                                "the question itself, or the blank to fill."
                            ),
                        },
                        "answer": {
                            "type": "string",
                            "description": "The answer. Newlines become separate paragraphs.",
                        },
                        "blank": {
                            "type": "string",
                            "description": (
                                "where='replace' only: the exact text to replace inside the "
                                "anchored paragraph, e.g. '________' or 'Answer:'. Defaults to "
                                "the anchor itself."
                            ),
                        },
                        "where": {
                            "type": "string",
                            "enum": ["after", "replace", "append"],
                            "description": "Default 'after'.",
                        },
                        "italic": {
                            "type": "boolean",
                            "description": "Italicise the inserted answer. Default false.",
                        },
                    },
                    "required": ["anchor", "answer"],
                },
            },
        },
        "required": ["path", "edits"],
    }
    requires_approval = True

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)

    def describe_call(self, tool_input: dict) -> str:  # type: ignore[override]
        edits = tool_input.get("edits") or []
        count = len(edits) if isinstance(edits, list) else 0
        return f"fill_document({tool_input.get('path')}, {count} answer(s))"

    def run(self, path: str, edits: list[dict]) -> str:  # type: ignore[override]
        docx = _require_docx()
        target = resolve_in_workspace(self.workspace, path)

        if not target.exists():
            raise ToolError(f"{path!r} does not exist.")
        if target.suffix.lower() not in EDITABLE_SUFFIXES:
            raise ToolError(
                f"{path!r} is not a Word document, so answers cannot be typed into it. "
                "Only .docx and .dotx can be edited in place - use write_file for anything else."
            )
        if not isinstance(edits, list) or not edits:
            raise ToolError("edits is empty - pass at least one {anchor, answer} object")

        backup = self._back_up(target)

        try:
            document = docx.Document(str(target))
        except Exception as exc:  # noqa: BLE001 - a bad zip raises any number of things
            raise ToolError(f"could not open {path!r}: {exc}") from None

        applied = []
        for index, edit in enumerate(edits, 1):
            if not isinstance(edit, dict):
                raise ToolError(f"edit {index} is not an object with anchor and answer")
            anchor = str(edit.get("anchor", ""))
            answer = str(edit.get("answer", ""))
            where = str(edit.get("where") or "after").lower()
            italic = bool(edit.get("italic", False))
            if not answer.strip():
                raise ToolError(f"edit {index} ({anchor[:40]!r}) has an empty answer")

            paragraph = find_anchor(document, anchor)

            if where == "after":
                insert_paragraphs_after(paragraph, _split_answer(answer), italic=italic)
            elif where == "replace":
                # The anchor locates the paragraph; `blank` is what gets overwritten. Keeping
                # them separate means a long, unambiguous anchor does not delete the question.
                blank = str(edit.get("blank") or anchor)
                if not self._replace(paragraph, blank, answer):
                    raise ToolError(
                        f"edit {index}: found the paragraph but {blank!r} is not in it - quote "
                        f"the blank exactly as it appears, or leave 'blank' out to replace the "
                        f"anchor itself. The paragraph reads: {paragraph.text.strip()[:120]!r}"
                    )
            elif where == "append":
                run = paragraph.add_run(f" {answer.strip()}")
                run.italic = italic
            else:
                raise ToolError(
                    f"edit {index}: where must be 'after', 'replace', or 'append', not {where!r}"
                )
            applied.append(anchor.strip()[:50])

        try:
            document.save(str(target))
        except OSError as exc:
            raise ToolError(f"could not save {path!r}: {exc}") from None

        listed = "\n".join(f"  - {anchor}" for anchor in applied)
        return f"wrote {len(applied)} answer(s) into {path} (original saved as {backup.name}):\n{listed}"

    @staticmethod
    def _replace(paragraph: Any, anchor: str, answer: str) -> bool:
        """Replace the anchor text, trying the exact string before a normalized match."""
        if replace_in_paragraph(paragraph, anchor, answer.strip()):
            return True
        # The model quoted the blank with different spacing than the document uses.
        stripped = anchor.strip()
        return bool(stripped != anchor and replace_in_paragraph(paragraph, stripped, answer.strip()))

    @staticmethod
    def _back_up(target: Path) -> Path:
        """Keep one pristine copy of the student's original, made on the first edit."""
        backup = target.with_name(f"{target.stem}.original{target.suffix}")
        if not backup.exists():
            shutil.copy2(target, backup)
        return backup
