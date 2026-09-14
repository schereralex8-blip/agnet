"""Run short Python snippets for the maths a calculator cannot do.

This is isolation, not a sandbox: the snippet runs as a separate interpreter process
with -I (no user site, no inherited environment), a CPU/wall timeout, and a temporary
working directory - but it runs as the same OS user. That is why the tool is opt-in
(--allow-code) and asks for approval before every run.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from homework_agent.tools.base import Tool, ToolError

MAX_OUTPUT = 8_000


class RunPythonTool(Tool):
    name = "run_python"
    description = (
        "Run a short Python snippet and return its stdout. Use it for work the calculator "
        "cannot express: solving equations (sympy if available), statistics, simulations, "
        "checking a recurrence, or verifying the student's code against sample input. "
        "Print what you want to see - nothing is returned automatically. The snippet runs in "
        "an empty temporary folder with no network and cannot see the student's files; pass "
        "any data you need inline in the code."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python source to execute."},
            "purpose": {
                "type": "string",
                "description": "One short line on what this computes, shown to the student.",
            },
        },
        "required": ["code"],
    }
    requires_approval = True

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    def describe_call(self, tool_input: dict) -> str:  # type: ignore[override]
        purpose = tool_input.get("purpose")
        code = str(tool_input.get("code", ""))
        lines = len(code.splitlines())
        return f"run_python ({lines} lines){': ' + purpose if purpose else ''}"

    def run(self, code: str, purpose: str | None = None) -> str:  # type: ignore[override]
        if not code.strip():
            raise ToolError("code is empty")
        with tempfile.TemporaryDirectory(prefix="homework-agent-") as workdir:
            script = Path(workdir) / "snippet.py"
            script.write_text(code, encoding="utf-8")
            try:
                completed = subprocess.run(
                    [sys.executable, "-I", str(script)],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    cwd=workdir,
                    env={"PATH": "/usr/bin:/bin", "HOME": workdir, "PYTHONIOENCODING": "utf-8"},
                    check=False,
                )
            except subprocess.TimeoutExpired:
                raise ToolError(
                    f"the snippet ran longer than {self.timeout:g}s and was stopped - "
                    "it is probably an infinite loop or too large a computation"
                ) from None
            except OSError as exc:
                raise ToolError(f"could not start Python: {exc}") from None

        return _format_output(completed.stdout, completed.stderr, completed.returncode)


def _format_output(stdout: str, stderr: str, returncode: int) -> str:
    parts: list[str] = []
    if stdout.strip():
        parts.append(_clip(stdout.rstrip()))
    if returncode != 0:
        # The traceback is the useful part - surface it, trimmed to the tail.
        tail = "\n".join(stderr.rstrip().splitlines()[-25:])
        parts.append(f"[exit {returncode}]\n{_clip(tail)}")
    elif stderr.strip():
        parts.append(f"[stderr]\n{_clip(stderr.rstrip())}")
    if not parts:
        return "(the snippet ran and printed nothing - add a print() to see a result)"
    return "\n\n".join(parts)


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + f"\n[output truncated at {MAX_OUTPUT} characters]"
