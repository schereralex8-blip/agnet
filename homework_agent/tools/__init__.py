"""Tool registry assembly."""

from __future__ import annotations

from pathlib import Path

from homework_agent.config import Config
from homework_agent.tools.base import Tool, ToolError, ToolRegistry, ToolResult
from homework_agent.tools.calculator import CalculateTool
from homework_agent.tools.docx_edit import FillDocumentTool
from homework_agent.tools.files import ListFilesTool, ReadAssignmentTool, WriteFileTool
from homework_agent.tools.python_exec import RunPythonTool
from homework_agent.tools.tracker import (
    AddAssignmentTool,
    CompleteAssignmentTool,
    ListAssignmentsTool,
    Tracker,
)

# Server-side search, enabled with --search. Runs on Anthropic's infrastructure, so
# there is nothing to execute locally.
WEB_SEARCH_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 5,
}


def build_registry(config: Config) -> ToolRegistry:
    """The tools available for this run, per the student's flags."""
    workspace = Path(config.workspace)
    tracker = Tracker(config.state_dir() / "assignments.json")
    tools: list[Tool] = [
        CalculateTool(),
        ReadAssignmentTool(workspace),
        ListFilesTool(workspace),
        WriteFileTool(workspace),
        FillDocumentTool(workspace),
        AddAssignmentTool(tracker),
        ListAssignmentsTool(tracker),
        CompleteAssignmentTool(tracker),
    ]
    if config.allow_code:
        tools.append(RunPythonTool())
    return ToolRegistry(tools)


def server_tools(config: Config) -> list[dict]:
    return [WEB_SEARCH_TOOL] if config.web_search else []


__all__ = [
    "build_registry",
    "server_tools",
    "Tool",
    "ToolError",
    "ToolRegistry",
    "ToolResult",
    "Tracker",
    "WEB_SEARCH_TOOL",
]
