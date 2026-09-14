"""Terminal rendering. No dependencies - just ANSI, and none of it when piped."""

from __future__ import annotations

import os
import sys
from typing import Any

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"


def colors_enabled(stream: Any = None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


class Console:
    """Writes the agent's output. Tool activity is dimmed so the answer stands out."""

    def __init__(self, stream: Any = None, color: bool | None = None) -> None:
        self.stream = stream or sys.stdout
        self.color = colors_enabled(self.stream) if color is None else color
        self._at_line_start = True
        self._in_thinking = False

    # -- primitives ---------------------------------------------------------

    def _paint(self, text: str, *codes: str) -> str:
        if not self.color or not codes:
            return text
        return "".join(codes) + text + RESET

    def write(self, text: str) -> None:
        if not text:
            return
        self.stream.write(text)
        self.stream.flush()
        self._at_line_start = text.endswith("\n")

    def line(self, text: str = "", *codes: str) -> None:
        self.ensure_newline()
        self.write(self._paint(text, *codes) + "\n")

    def ensure_newline(self) -> None:
        if not self._at_line_start:
            self.write("\n")

    # -- agent events -------------------------------------------------------

    def on_text(self, text: str) -> None:
        if self._in_thinking:
            self.ensure_newline()
            self._in_thinking = False
        self.write(text)

    def on_thinking(self, text: str) -> None:
        if not self._in_thinking:
            self.ensure_newline()
            self.write(self._paint("thinking: ", DIM))
            self._in_thinking = True
        self.write(self._paint(text, DIM))

    def on_tool_start(self, description: str) -> None:
        self.line(f"  ... {description}", DIM)

    def on_tool_result(self, description: str, is_error: bool) -> None:
        if is_error:
            self.line(f"  !   {description} failed", YELLOW)

    def on_notice(self, message: str) -> None:
        self.line(f"  note: {message}", DIM)

    # -- chrome -------------------------------------------------------------

    def banner(self, config: Any, session_name: str | None = None) -> None:
        self.line("Homework Agent", BOLD, CYAN)
        bits = [f"mode: {config.mode}", f"model: {config.model}", f"folder: {config.workspace}"]
        if session_name:
            bits.append(f"session: {session_name}")
        if config.allow_code:
            bits.append("python: on")
        if config.web_search:
            bits.append("search: on")
        self.line("  ".join(bits), DIM)
        self.line("/help for commands, /exit to leave, Ctrl-C to interrupt", DIM)
        self.line()

    def error(self, message: str) -> None:
        self.ensure_newline()
        self.line(f"error: {message}", RED)

    def info(self, message: str) -> None:
        self.line(message, DIM)

    def success(self, message: str) -> None:
        self.line(message, GREEN)

    def prompt(self, label: str = "you") -> str:
        self.ensure_newline()
        return input(self._paint(f"{label} > ", BOLD, CYAN) if self.color else f"{label} > ")

    def confirm(self, question: str) -> bool:
        """Approval gate for tools that write files or execute code."""
        self.ensure_newline()
        text = self._paint(f"{question} [y/N] ", YELLOW)
        try:
            answer = input(text)
        except EOFError:
            return False
        return answer.strip().lower() in {"y", "yes"}
