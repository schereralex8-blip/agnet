"""Tool plumbing: the base class, the registry, and result formatting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable


class ToolError(Exception):
    """Raised by a tool when the call cannot be completed.

    The message is handed back to the model as an is_error tool_result, so write it
    for the model: say what went wrong and what a valid call would look like.
    """


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


class Tool:
    """A callable the model can invoke.

    Subclasses set name/description/input_schema and implement run().
    """

    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = {}
    # Tools that touch the filesystem or execute code ask the student first.
    requires_approval: bool = False

    def run(self, **kwargs: Any) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def spec(self) -> dict[str, Any]:
        """The tool definition sent to the Messages API."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def describe_call(self, tool_input: dict[str, Any]) -> str:
        """One-line human-readable summary, shown in the transcript and approval prompt."""
        if not tool_input:
            return self.name
        parts = []
        for key, value in tool_input.items():
            text = str(value).replace("\n", " ")
            if len(text) > 60:
                text = text[:57] + "..."
            parts.append(f"{key}={text}")
        return f"{self.name}({', '.join(parts)})"


class ToolRegistry:
    """The set of tools available for a run."""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.add(tool)

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name {tool.name!r}")
        self._tools[tool.name] = tool

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self):
        return iter(self._tools.values())

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolError(f"no such tool {name!r}") from None

    def specs(self, extra: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """Tool definitions for the API, including any server-tool dicts in `extra`."""
        specs = [tool.spec() for tool in self._tools.values()]
        if extra:
            specs.extend(extra)
        return specs

    def execute(self, name: str, tool_input: dict[str, Any]) -> ToolResult:
        """Run a tool, turning any failure into an error result the model can read."""
        try:
            tool = self.get(name)
            output = tool.run(**tool_input)
        except ToolError as exc:
            return ToolResult(str(exc), is_error=True)
        except TypeError as exc:
            # Bad arguments from the model - tell it what the schema wants.
            return ToolResult(f"invalid arguments for {name}: {exc}", is_error=True)
        except Exception as exc:  # noqa: BLE001 - a tool crash must not kill the session
            return ToolResult(f"{name} failed: {type(exc).__name__}: {exc}", is_error=True)
        return ToolResult(output if output.strip() else "(no output)")


ApprovalCallback = Callable[[Tool, dict[str, Any]], bool]
