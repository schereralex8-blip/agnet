"""A stand-in for anthropic.Anthropic that replays scripted turns."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any


class Block(SimpleNamespace):
    """A content block. Only the fields the agent reads are present."""


def text_block(text: str) -> Block:
    return Block(type="text", text=text)


def tool_block(name: str, tool_input: dict, block_id: str = "tu_1") -> Block:
    return Block(type="tool_use", name=name, input=tool_input, id=block_id)


def thinking_block(text: str) -> Block:
    return Block(type="thinking", thinking=text, signature="sig")


@dataclass
class FakeMessage:
    content: list[Block]
    stop_reason: str = "end_turn"
    stop_details: Any = None


class _FakeStream:
    def __init__(self, message: FakeMessage) -> None:
        self.message = message

    def __enter__(self) -> "_FakeStream":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def __iter__(self):
        """Emit the deltas a real stream would emit for this message."""
        for block in self.message.content:
            if block.type == "text":
                yield SimpleNamespace(
                    type="content_block_delta",
                    delta=SimpleNamespace(type="text_delta", text=block.text),
                )
            elif block.type == "thinking":
                yield SimpleNamespace(
                    type="content_block_delta",
                    delta=SimpleNamespace(type="thinking_delta", thinking=block.thinking),
                )
        yield SimpleNamespace(type="message_stop")

    def get_final_message(self) -> FakeMessage:
        return self.message


@dataclass
class FakeClient:
    """Replays `turns` one per request, recording the kwargs it was called with."""

    # Each entry is either a scripted reply or an exception to raise for that request.
    turns: list[Any] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.messages = SimpleNamespace(stream=self._stream)
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._beta_stream))

    def _next(self, kwargs: dict) -> _FakeStream:
        # messages is a live reference to the session history, so snapshot it -
        # otherwise every recorded call shows the final state.
        self.calls.append({**kwargs, "messages": copy.deepcopy(kwargs.get("messages", []))})
        if not self.turns:
            raise AssertionError("the agent made more requests than the test scripted")
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return _FakeStream(turn)

    def _stream(self, **kwargs: Any) -> _FakeStream:
        return self._next(kwargs)

    def _beta_stream(self, **kwargs: Any) -> _FakeStream:
        return self._next(kwargs)


class RecordingEvents:
    def __init__(self) -> None:
        self.text: list[str] = []
        self.thinking: list[str] = []
        self.tools: list[str] = []
        self.results: list[tuple[str, bool]] = []
        self.notices: list[str] = []

    def on_text(self, text: str) -> None:
        self.text.append(text)

    def on_thinking(self, text: str) -> None:
        self.thinking.append(text)

    def on_tool_start(self, description: str) -> None:
        self.tools.append(description)

    def on_tool_result(self, description: str, is_error: bool) -> None:
        self.results.append((description, is_error))

    def on_notice(self, message: str) -> None:
        self.notices.append(message)

    @property
    def answer(self) -> str:
        return "".join(self.text)
