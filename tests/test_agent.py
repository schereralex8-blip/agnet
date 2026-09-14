from types import SimpleNamespace

import anthropic
import pytest

from homework_agent.agent import AgentError, HomeworkAgent, MAX_TOOL_ROUNDS
from homework_agent.config import Config
from homework_agent.session import Session
from homework_agent.tools.base import Tool, ToolRegistry
from homework_agent.tools.calculator import CalculateTool
from tests.fakes import FakeClient, FakeMessage, RecordingEvents, text_block, tool_block


class ApprovalTool(Tool):
    name = "write_file"
    description = "writes"
    input_schema = {"type": "object", "properties": {"path": {"type": "string"}}}
    requires_approval = True

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, **kwargs):  # type: ignore[override]
        self.calls.append(kwargs)
        return "written"


def make_agent(turns, config=None, tools=None, approve=None, tmp_path=None):
    config = config or Config(workspace=tmp_path or ".")
    registry = ToolRegistry(tools if tools is not None else [CalculateTool()])
    events = RecordingEvents()
    client = FakeClient(turns=list(turns))
    agent = HomeworkAgent(client, config, registry, Session(name="t"), events, approve)
    return agent, client, events


def test_plain_answer_streams_and_is_recorded():
    agent, client, events = make_agent([FakeMessage([text_block("Try the chain rule.")])])
    answer = agent.run_turn("how do I start q3?")

    assert answer == "Try the chain rule."
    assert events.answer == "Try the chain rule."
    assert [m["role"] for m in agent.session.messages] == ["user", "assistant"]
    assert len(client.calls) == 1


def test_tool_call_round_trip():
    turns = [
        FakeMessage([tool_block("calculate", {"expression": "17*23"})], stop_reason="tool_use"),
        FakeMessage([text_block("391.")]),
    ]
    agent, client, events = make_agent(turns)
    assert agent.run_turn("what is 17 times 23?") == "391."

    # The result goes back as a tool_result in a single user message.
    tool_message = agent.session.messages[2]
    assert tool_message["role"] == "user"
    assert tool_message["content"][0]["type"] == "tool_result"
    assert "391" in tool_message["content"][0]["content"]
    assert events.tools == ["calculate(expression=17*23)"]


def test_parallel_tool_calls_return_in_one_message():
    turns = [
        FakeMessage(
            [
                tool_block("calculate", {"expression": "2+2"}, block_id="a"),
                tool_block("calculate", {"expression": "3+3"}, block_id="b"),
            ],
            stop_reason="tool_use",
        ),
        FakeMessage([text_block("4 and 6.")]),
    ]
    agent, _, _ = make_agent(turns)
    agent.run_turn("add these")
    results = agent.session.messages[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]


def test_a_failing_tool_comes_back_as_an_error_result_not_a_crash():
    turns = [
        FakeMessage([tool_block("calculate", {"expression": "1/0"})], stop_reason="tool_use"),
        FakeMessage([text_block("That is undefined.")]),
    ]
    agent, _, events = make_agent(turns)
    agent.run_turn("what is 1/0?")

    result = agent.session.messages[2]["content"][0]
    assert result["is_error"] is True
    assert "division by zero" in result["content"]
    assert events.results[-1][1] is True


def test_unknown_tool_is_reported_to_the_model():
    """A hallucinated tool name still needs a result, or the next request is malformed."""
    turns = [
        FakeMessage([tool_block("teleport", {})], stop_reason="tool_use"),
        FakeMessage([text_block("Let me use the calculator instead.")]),
    ]
    agent, _, _ = make_agent(turns)
    assert agent.run_turn("do something odd") == "Let me use the calculator instead."

    result = agent.session.messages[2]["content"][0]
    assert result["is_error"] is True
    assert "no tool named 'teleport'" in result["content"]
    assert "calculate" in result["content"]


def test_approval_gate_blocks_the_call_and_tells_the_model():
    tool = ApprovalTool()
    turns = [
        FakeMessage([tool_block("write_file", {"path": "a.txt"})], stop_reason="tool_use"),
        FakeMessage([text_block("Understood.")]),
    ]
    agent, _, events = make_agent(turns, tools=[tool], approve=lambda t, i: False)
    agent.run_turn("save that")

    assert tool.calls == []
    assert "declined" in agent.session.messages[2]["content"][0]["content"]
    assert events.results[-1][0].endswith("declined")


def test_approval_gate_runs_the_tool_when_allowed():
    tool = ApprovalTool()
    turns = [
        FakeMessage([tool_block("write_file", {"path": "a.txt"})], stop_reason="tool_use"),
        FakeMessage([text_block("Saved.")]),
    ]
    agent, _, _ = make_agent(turns, tools=[tool], approve=lambda t, i: True)
    agent.run_turn("save that")
    assert tool.calls == [{"path": "a.txt"}]


def test_yes_flag_skips_the_prompt_entirely():
    tool = ApprovalTool()
    config = Config(auto_approve=True)

    def refuse(*_):
        raise AssertionError("should not have asked")

    turns = [
        FakeMessage([tool_block("write_file", {"path": "a.txt"})], stop_reason="tool_use"),
        FakeMessage([text_block("Saved.")]),
    ]
    agent, _, _ = make_agent(turns, config=config, tools=[tool], approve=refuse)
    agent.run_turn("save that")
    assert tool.calls == [{"path": "a.txt"}]


def test_pause_turn_is_resumed():
    turns = [
        FakeMessage([text_block("searching...")], stop_reason="pause_turn"),
        FakeMessage([text_block("Here is what I found.")]),
    ]
    agent, client, _ = make_agent(turns)
    assert agent.run_turn("look this up") == "Here is what I found."
    assert len(client.calls) == 2


def test_refusal_is_explained_rather_than_swallowed():
    turns = [
        FakeMessage(
            [text_block("")],
            stop_reason="refusal",
            stop_details=SimpleNamespace(type="refusal", category="cyber"),
        )
    ]
    agent, _, _ = make_agent(turns)
    with pytest.raises(AgentError, match="cyber"):
        agent.run_turn("something off limits")


def test_runaway_tool_loops_are_capped():
    turns = [
        FakeMessage([tool_block("calculate", {"expression": "1+1"})], stop_reason="tool_use")
        for _ in range(MAX_TOOL_ROUNDS + 2)
    ]
    agent, _, _ = make_agent(turns)
    with pytest.raises(AgentError, match="gave up"):
        agent.run_turn("loop forever")


def test_request_carries_the_mode_prompt_tools_and_effort(tmp_path):
    config = Config(mode="solve", subject="Calc II", workspace=tmp_path, effort="max")
    agent, client, _ = make_agent([FakeMessage([text_block("ok")])], config=config)
    agent.run_turn("go")

    kwargs = client.calls[0]
    assert kwargs["model"] == "claude-opus-5"
    assert kwargs["output_config"] == {"effort": "max"}
    assert kwargs["thinking"]["type"] == "adaptive"
    # Stable system text first, with a cache breakpoint; mode text after it.
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "MODE: SOLVE" in kwargs["system"][1]["text"]
    assert "Calc II" in kwargs["system"][1]["text"]
    assert [t["name"] for t in kwargs["tools"]] == ["calculate"]


def test_web_search_is_only_sent_when_asked_for():
    agent, client, _ = make_agent([FakeMessage([text_block("ok")])])
    agent.run_turn("go")
    assert all(t["name"] != "web_search" for t in client.calls[0]["tools"])

    config = Config(web_search=True)
    agent, client, _ = make_agent([FakeMessage([text_block("ok")])], config=config)
    agent.run_turn("go")
    assert any(t["name"] == "web_search" for t in client.calls[0]["tools"])


def test_thinking_is_hidden_by_default_and_shown_on_request():
    agent, client, _ = make_agent([FakeMessage([text_block("ok")])])
    agent.run_turn("go")
    assert client.calls[0]["thinking"]["display"] == "omitted"

    agent, client, _ = make_agent(
        [FakeMessage([text_block("ok")])], config=Config(show_thinking=True)
    )
    agent.run_turn("go")
    assert client.calls[0]["thinking"]["display"] == "summarized"


def test_falls_back_when_the_account_cannot_use_the_refusal_beta():
    """A 400 about the beta must not dead-end the student mid-problem."""

    class PickyClient(FakeClient):
        def _beta_stream(self, **kwargs):
            raise anthropic.BadRequestError(
                "unknown beta: server-side-fallback-2026-07-01",
                response=SimpleNamespace(status_code=400, headers={}, request=None),
                body=None,
            )

    config = Config()
    registry = ToolRegistry([CalculateTool()])
    events = RecordingEvents()
    client = PickyClient(turns=[FakeMessage([text_block("fine")])])
    agent = HomeworkAgent(client, config, registry, Session(name="t"), events)

    assert agent.run_turn("hi") == "fine"
    assert agent._use_fallbacks is False
    assert events.notices  # the student is told, once


def test_auth_failure_says_what_to_do():
    class NoAuthClient(FakeClient):
        def _beta_stream(self, **kwargs):
            raise anthropic.AuthenticationError(
                "invalid key",
                response=SimpleNamespace(status_code=401, headers={}, request=None),
                body=None,
            )

    agent = HomeworkAgent(
        NoAuthClient(), Config(), ToolRegistry([CalculateTool()]), Session(name="t")
    )
    with pytest.raises(AgentError, match="ANTHROPIC_API_KEY"):
        agent.run_turn("hi")


def test_resume_requires_a_pending_question():
    agent, _, _ = make_agent([])
    with pytest.raises(AgentError, match="nothing to resume"):
        agent.resume()


def test_missing_credentials_says_how_to_fix_it():
    """The SDK raises a bare TypeError when nothing is configured - catch it."""

    class UnauthenticatedClient(FakeClient):
        def _beta_stream(self, **kwargs):
            raise TypeError("Could not resolve authentication method. Expected one of api_key...")

    agent = HomeworkAgent(
        UnauthenticatedClient(), Config(), ToolRegistry([CalculateTool()]), Session(name="t")
    )
    with pytest.raises(AgentError, match="ANTHROPIC_API_KEY"):
        agent.run_turn("hi")


def test_unrelated_type_errors_still_propagate():
    class BrokenClient(FakeClient):
        def _beta_stream(self, **kwargs):
            raise TypeError("stream() got an unexpected keyword argument 'wat'")

    agent = HomeworkAgent(
        BrokenClient(), Config(), ToolRegistry([CalculateTool()]), Session(name="t")
    )
    with pytest.raises(TypeError, match="unexpected keyword"):
        agent.run_turn("hi")
