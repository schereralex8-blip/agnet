"""The agent loop: stream a turn, run any tools Claude asks for, repeat."""

from __future__ import annotations

from typing import Any, Callable, Protocol

import anthropic

from homework_agent.config import Config
from homework_agent.prompts import system_blocks
from homework_agent.session import Session, to_jsonable
from homework_agent.tools import ToolRegistry, server_tools
from homework_agent.tools.base import Tool

# A single turn should never need more rounds than this; if it does, something is
# looping and the student should not pay for it.
MAX_TOOL_ROUNDS = 12
# Server tools can pause a long turn; resume it, but not forever.
MAX_PAUSE_RESUMES = 5


NO_CREDENTIALS = (
    "no API credentials found. Set one of these and try again:\n"
    "  export ANTHROPIC_API_KEY=sk-ant-...      (from console.anthropic.com)\n"
    "  ant auth login                           (if you have the Anthropic CLI)"
)


class AgentError(Exception):
    """A turn could not be completed. The message is written for the student."""


class Events(Protocol):
    """Hooks the UI implements. Every one is optional in practice."""

    def on_text(self, text: str) -> None: ...
    def on_thinking(self, text: str) -> None: ...
    def on_tool_start(self, description: str) -> None: ...
    def on_tool_result(self, description: str, is_error: bool) -> None: ...
    def on_notice(self, message: str) -> None: ...


class NullEvents:
    def on_text(self, text: str) -> None: ...
    def on_thinking(self, text: str) -> None: ...
    def on_tool_start(self, description: str) -> None: ...
    def on_tool_result(self, description: str, is_error: bool) -> None: ...
    def on_notice(self, message: str) -> None: ...


ApprovalCallback = Callable[[Tool, dict[str, Any]], bool]


def approve_everything(tool: Tool, tool_input: dict[str, Any]) -> bool:
    return True


class HomeworkAgent:
    """Owns one conversation with Claude."""

    def __init__(
        self,
        client: Any,
        config: Config,
        registry: ToolRegistry,
        session: Session,
        events: Events | None = None,
        approve: ApprovalCallback | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self.registry = registry
        self.session = session
        self.events: Events = events or NullEvents()
        self.approve = approve or approve_everything
        # Degrade gracefully if this account cannot use the refusal-fallback beta.
        self._use_fallbacks = True

    # -- public API ---------------------------------------------------------

    def run_turn(self, user_input: str) -> str:
        """Send one student message and run to completion. Returns the reply text."""
        self.session.add_user(user_input)
        return self._run()

    def resume(self) -> str:
        """Continue from a history that already ends with a student message."""
        if not self.session.messages or self.session.messages[-1]["role"] != "user":
            raise AgentError("there is nothing to resume - the last message is not yours")
        return self._run()

    # -- the loop -----------------------------------------------------------

    def _run(self) -> str:
        pauses = 0
        for _ in range(MAX_TOOL_ROUNDS):
            response = self._stream_once()

            if response.stop_reason == "pause_turn":
                # A server tool ran long; re-send to let it finish.
                pauses += 1
                if pauses > MAX_PAUSE_RESUMES:
                    raise AgentError("the search kept pausing - try a narrower question")
                self.session.add_assistant(response.content)
                continue

            self.session.add_assistant(response.content)

            if response.stop_reason == "refusal":
                detail = getattr(response, "stop_details", None)
                category = getattr(detail, "category", None) or "unspecified"
                raise AgentError(
                    f"Claude declined to answer this one (category: {category}). "
                    "Rephrase the question, or ask about the underlying concept instead."
                )

            if response.stop_reason != "tool_use":
                return self._text_of(response)

            tool_results = self._run_tools(response)
            if not tool_results:
                return self._text_of(response)
            self.session.add_user(tool_results)

        raise AgentError(
            f"gave up after {MAX_TOOL_ROUNDS} rounds of tool calls - "
            "ask a narrower question, or start a fresh session"
        )

    def _run_tools(self, response: Any) -> list[dict[str, Any]]:
        """Execute every tool_use block in the response and collect the results.

        All results go back in a single user message - splitting them teaches the
        model to stop making parallel calls.
        """
        results: list[dict[str, Any]] = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            # Server tools (web_search) arrive as `server_tool_use` blocks and run on
            # Anthropic's side, so a plain tool_use naming something we do not have is a
            # hallucinated call. It still needs a result, or the next request is
            # malformed - tell the model what it may actually call.
            if block.name not in self.registry:
                available = ", ".join(sorted(tool.name for tool in self.registry))
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"no tool named {block.name!r}. Available tools: {available}",
                        "is_error": True,
                    }
                )
                self.events.on_tool_result(block.name, True)
                continue

            tool = self.registry.get(block.name)
            tool_input = dict(block.input or {})
            description = tool.describe_call(tool_input)

            if tool.requires_approval and not self.config.auto_approve:
                if not self.approve(tool, tool_input):
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": (
                                "The student declined this action. Do not retry it - continue "
                                "without it, or explain what you would have done."
                            ),
                        }
                    )
                    self.events.on_tool_result(f"{description} - declined", False)
                    continue

            self.events.on_tool_start(description)
            outcome = self.registry.execute(block.name, tool_input)
            self.events.on_tool_result(description, outcome.is_error)
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": outcome.content,
                    **({"is_error": True} if outcome.is_error else {}),
                }
            )
        return results

    # -- transport ----------------------------------------------------------

    def _request_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "system": system_blocks(
                self.config.mode, self.config.subject, str(self.config.workspace)
            ),
            "messages": self.session.messages,
            "tools": self.registry.specs(server_tools(self.config)),
            "output_config": {"effort": self.config.effort},
            "thinking": {
                "type": "adaptive",
                "display": "summarized" if self.config.show_thinking else "omitted",
            },
        }
        return kwargs

    def _stream_once(self) -> Any:
        """One streamed request, rendering deltas as they arrive."""
        kwargs = self._request_kwargs()
        try:
            return self._stream(kwargs, with_fallbacks=self._use_fallbacks)
        except anthropic.BadRequestError as exc:
            message = str(exc)
            if self._use_fallbacks and ("fallback" in message.lower() or "beta" in message.lower()):
                # This account cannot use server-side refusal fallbacks; carry on without.
                self._use_fallbacks = False
                self.events.on_notice("server-side fallbacks unavailable; continuing without them")
                return self._stream(kwargs, with_fallbacks=False)
            raise AgentError(f"the API rejected the request: {message}") from exc
        except anthropic.AuthenticationError as exc:
            raise AgentError(NO_CREDENTIALS) from exc
        except TypeError as exc:
            # The SDK raises a bare TypeError when it finds no credentials at all.
            if "authentication" in str(exc).lower():
                raise AgentError(NO_CREDENTIALS) from exc
            raise
        except anthropic.RateLimitError as exc:
            raise AgentError("rate limited - wait a moment and ask again") from exc
        except anthropic.APIStatusError as exc:
            raise AgentError(f"the API returned {exc.status_code}: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise AgentError(f"could not reach the API - check your connection ({exc})") from exc

    def _stream(self, kwargs: dict[str, Any], with_fallbacks: bool) -> Any:
        if with_fallbacks:
            # Route around a classifier refusal automatically instead of dead-ending
            # a student mid-problem.
            stream_ctx = self.client.beta.messages.stream(
                **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
            )
        else:
            stream_ctx = self.client.messages.stream(**kwargs)

        with stream_ctx as stream:
            for event in stream:
                self._render(event)
            return stream.get_final_message()

    def _render(self, event: Any) -> None:
        if getattr(event, "type", None) != "content_block_delta":
            return
        delta = event.delta
        kind = getattr(delta, "type", None)
        if kind == "text_delta":
            self.events.on_text(delta.text)
        elif kind == "thinking_delta" and self.config.show_thinking:
            self.events.on_thinking(delta.thinking)

    @staticmethod
    def _text_of(response: Any) -> str:
        parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        return "\n".join(part for part in parts if part).strip()


def snapshot(response: Any) -> list[dict[str, Any]]:
    """Helper for tests and debugging: response content as plain JSON."""
    return to_jsonable(response.content)
