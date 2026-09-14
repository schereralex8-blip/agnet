"""Conversation persistence, so a student can close the laptop and come back."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]+")


def slugify(name: str) -> str:
    slug = SAFE_NAME.sub("-", name.strip()).strip("-.")
    return slug[:60] or "session"


def to_jsonable(value: Any) -> Any:
    """Convert SDK content blocks (pydantic models) into plain JSON data.

    exclude_none matters: thinking blocks carry a signature that must survive a
    round-trip, while null-valued optional fields would be rejected on replay.
    """
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if hasattr(value, "__dict__") and not isinstance(value, type):
        # Any other block-like object (a stub, a non-pydantic SDK type).
        return {k: to_jsonable(v) for k, v in vars(value).items() if v is not None}
    return value


@dataclass
class Session:
    """A named conversation: the message history plus the mode it was last in."""

    name: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    mode: str = "solve"
    subject: str | None = None
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def add_user(self, content: Any) -> None:
        self.messages.append({"role": "user", "content": to_jsonable(content)})

    def add_assistant(self, content: Any) -> None:
        self.messages.append({"role": "assistant", "content": to_jsonable(content)})

    def last_assistant_text(self) -> str:
        for message in reversed(self.messages):
            if message["role"] != "assistant":
                continue
            content = message["content"]
            if isinstance(content, str):
                return content
            texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            if texts:
                return "\n".join(t for t in texts if t).strip()
        return ""

    def summary(self) -> str:
        """First thing the student asked, for the session list."""
        for message in self.messages:
            if message["role"] != "user":
                continue
            content = message["content"]
            text = content if isinstance(content, str) else " ".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )
            text = " ".join(text.split())
            if text:
                return text[:70] + ("..." if len(text) > 70 else "")
        return "(empty)"


class SessionStore:
    """Sessions on disk, one JSON file each."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def path_for(self, name: str) -> Path:
        return self.directory / f"{slugify(name)}.json"

    def exists(self, name: str) -> bool:
        return self.path_for(name).exists()

    def load(self, name: str) -> Session:
        path = self.path_for(name)
        if not path.exists():
            raise FileNotFoundError(f"no saved session named {name!r}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return Session(
            name=data.get("name", name),
            messages=data.get("messages", []),
            mode=data.get("mode", "solve"),
            subject=data.get("subject"),
            created=data.get("created", ""),
            updated=data.get("updated", ""),
        )

    def save(self, session: Session) -> Path:
        session.updated = datetime.now().isoformat(timespec="seconds")
        path = self.path_for(session.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "name": session.name,
            "mode": session.mode,
            "subject": session.subject,
            "created": session.created,
            "updated": session.updated,
            "messages": session.messages,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    def list(self) -> list[Session]:
        if not self.directory.exists():
            return []
        sessions = []
        for path in self.directory.glob("*.json"):
            try:
                sessions.append(self.load(path.stem))
            except (json.JSONDecodeError, OSError):
                continue
        sessions.sort(key=lambda s: s.updated, reverse=True)
        return sessions

    def delete(self, name: str) -> bool:
        path = self.path_for(name)
        if path.exists():
            path.unlink()
            return True
        return False


def _has_tool_use(message: dict[str, Any]) -> bool:
    content = message.get("content")
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
    return False


def rollback_incomplete(session: Session) -> int:
    """Trim a half-finished turn so the history is safe to send again.

    An interrupted turn can leave the history ending on a student message with no
    reply, or on an assistant message whose tool calls never got results - both are
    rejected on the next request. Drop trailing messages until the history ends on a
    completed assistant turn.
    """
    removed = 0
    while session.messages:
        last = session.messages[-1]
        if last["role"] == "assistant" and not _has_tool_use(last):
            break
        session.messages.pop()
        removed += 1
    return removed
