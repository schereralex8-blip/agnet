"""Runtime configuration for the homework agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Opus 5 is the default: homework questions are reasoning-heavy, and a tutor that
# quietly gets the maths wrong is worse than no tutor at all.
DEFAULT_MODEL = "claude-opus-5"

MODES = ("solve", "tutor", "check")


@dataclass
class Config:
    """Everything the agent needs to know about how it should behave."""

    model: str = DEFAULT_MODEL
    # Streaming is always on, so we can afford a roomy output cap.
    max_tokens: int = 32000
    effort: str = "high"
    mode: str = "solve"
    workspace: Path = field(default_factory=Path.cwd)
    # Running model-written Python is opt-in, and every run still asks first
    # unless the student also passed --yes.
    allow_code: bool = False
    auto_approve: bool = False
    web_search: bool = False
    show_thinking: bool = False
    subject: str | None = None

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            model=os.environ.get("HOMEWORK_AGENT_MODEL", DEFAULT_MODEL),
            max_tokens=int(os.environ.get("HOMEWORK_AGENT_MAX_TOKENS", "32000")),
            effort=os.environ.get("HOMEWORK_AGENT_EFFORT", "high"),
            workspace=Path(os.environ.get("HOMEWORK_AGENT_WORKSPACE", ".")).resolve(),
        )

    def state_dir(self) -> Path:
        """Where sessions and the assignment tracker live."""
        override = os.environ.get("HOMEWORK_AGENT_HOME")
        base = Path(override).expanduser() if override else Path.home() / ".homework-agent"
        return base
