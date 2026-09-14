"""An assignment tracker the agent shares with the student.

Stored as JSON so `hw due` and the model see exactly the same list.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from homework_agent.tools.base import Tool, ToolError


@dataclass
class Assignment:
    id: str
    title: str
    course: str | None = None
    due: str | None = None  # ISO date, YYYY-MM-DD
    notes: str | None = None
    done: bool = False
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def days_left(self, today: date | None = None) -> int | None:
        if not self.due:
            return None
        try:
            due = date.fromisoformat(self.due)
        except ValueError:
            return None
        return (due - (today or date.today())).days

    def format(self, today: date | None = None) -> str:
        bits = [f"[{'x' if self.done else ' '}] {self.title}"]
        if self.course:
            bits.append(f"({self.course})")
        if self.due:
            days = self.days_left(today)
            if days is None:
                when = self.due
            elif self.done:
                when = f"due {self.due}"
            elif days < 0:
                when = f"due {self.due} - {abs(days)}d OVERDUE"
            elif days == 0:
                when = f"due {self.due} - TODAY"
            elif days == 1:
                when = f"due {self.due} - tomorrow"
            else:
                when = f"due {self.due} - in {days}d"
            bits.append(when)
        if self.notes:
            bits.append(f"- {self.notes}")
        return f"{self.id}  " + " ".join(bits)


class Tracker:
    """JSON-backed store of assignments."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> list[Assignment]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ToolError(f"the assignment list at {self.path} is unreadable: {exc}") from None
        items = []
        for entry in raw.get("assignments", []):
            known = {k: v for k, v in entry.items() if k in Assignment.__dataclass_fields__}
            items.append(Assignment(**known))
        return items

    def save(self, assignments: list[Assignment]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"assignments": [asdict(a) for a in assignments]}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, title: str, course: str | None, due: str | None, notes: str | None) -> Assignment:
        assignments = self.load()
        item = Assignment(id=uuid.uuid4().hex[:6], title=title, course=course, due=due, notes=notes)
        assignments.append(item)
        self.save(assignments)
        return item

    def find(self, assignment_id: str) -> tuple[list[Assignment], Assignment]:
        assignments = self.load()
        for item in assignments:
            if item.id == assignment_id:
                return assignments, item
        raise ToolError(f"no assignment with id {assignment_id!r}; call list_assignments first")

    def complete(self, assignment_id: str, done: bool = True) -> Assignment:
        assignments, item = self.find(assignment_id)
        item.done = done
        self.save(assignments)
        return item

    def remove(self, assignment_id: str) -> Assignment:
        assignments, item = self.find(assignment_id)
        assignments.remove(item)
        self.save(assignments)
        return item

    def listing(self, include_done: bool = False, today: date | None = None) -> str:
        assignments = [a for a in self.load() if include_done or not a.done]
        if not assignments:
            return "nothing tracked yet" if include_done else "nothing outstanding"
        # Undated work sorts last; otherwise soonest first.
        assignments.sort(key=lambda a: (a.done, a.due or "9999-12-31", a.title.lower()))
        return "\n".join(a.format(today) for a in assignments)


def validate_due(due: str | None) -> str | None:
    if due is None or not due.strip():
        return None
    try:
        return date.fromisoformat(due.strip()).isoformat()
    except ValueError:
        raise ToolError(f"due date {due!r} must be YYYY-MM-DD") from None


class AddAssignmentTool(Tool):
    name = "add_assignment"
    description = (
        "Track a piece of homework with its due date. Use it when the student mentions work "
        "they have to hand in, so it shows up in their list later. Confirm the due date with "
        "them if they said something relative like 'next Friday'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "What is due, e.g. 'Problem set 4'."},
            "course": {"type": "string", "description": "Course name, if known."},
            "due": {"type": "string", "description": "Due date as YYYY-MM-DD."},
            "notes": {"type": "string", "description": "Anything short worth remembering."},
        },
        "required": ["title"],
    }

    def __init__(self, tracker: Tracker) -> None:
        self.tracker = tracker

    def run(self, title: str, course: str | None = None, due: str | None = None,
            notes: str | None = None) -> str:  # type: ignore[override]
        if not title.strip():
            raise ToolError("title is empty")
        item = self.tracker.add(title.strip(), course, validate_due(due), notes)
        return f"tracked: {item.format()}"


class ListAssignmentsTool(Tool):
    name = "list_assignments"
    description = (
        "Show the student's tracked assignments, soonest due first. Check this when they ask "
        "what is due, what to work on next, or how to plan their week."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "include_done": {
                "type": "boolean",
                "description": "Include finished assignments. Defaults to false.",
            }
        },
    }

    def __init__(self, tracker: Tracker) -> None:
        self.tracker = tracker

    def run(self, include_done: bool = False) -> str:  # type: ignore[override]
        return self.tracker.listing(include_done=include_done)


class CompleteAssignmentTool(Tool):
    name = "complete_assignment"
    description = "Mark a tracked assignment as done, by its id from list_assignments."
    input_schema = {
        "type": "object",
        "properties": {"assignment_id": {"type": "string", "description": "The id, e.g. '4f2a9c'."}},
        "required": ["assignment_id"],
    }

    def __init__(self, tracker: Tracker) -> None:
        self.tracker = tracker

    def run(self, assignment_id: str) -> str:  # type: ignore[override]
        item = self.tracker.complete(assignment_id)
        return f"done: {item.format()}"
