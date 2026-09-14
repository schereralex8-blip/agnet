"""End-to-end exercise of the interactive loop with a scripted client."""

import builtins
from types import SimpleNamespace

import anthropic
import pytest

from homework_agent import cli
from tests.fakes import FakeClient, FakeMessage, text_block, tool_block


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMEWORK_AGENT_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOMEWORK_AGENT_WORKSPACE", str(tmp_path))
    return tmp_path


def drive(monkeypatch, inputs, turns, argv=("chat",)):
    """Run the chat command against scripted student input and scripted replies."""
    pending = list(inputs)

    def fake_input(_prompt=""):
        if not pending:
            raise EOFError
        return pending.pop(0)

    client = FakeClient(turns=list(turns))
    monkeypatch.setattr(builtins, "input", fake_input)
    monkeypatch.setattr(cli, "make_client", lambda: client)
    code = cli.main(list(argv))
    return code, client


def test_a_two_turn_conversation(monkeypatch, capsys):
    code, client = drive(
        monkeypatch,
        ["how do I start question 3?", "ok, and then?"],
        [
            FakeMessage([text_block("What have you tried?")]),
            FakeMessage([text_block("Differentiate.")]),
        ],
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "What have you tried?" in out
    assert "Differentiate." in out
    # Second request carries the whole history: q, a, q.
    assert len(client.calls[1]["messages"]) == 3


def test_mode_switch_mid_chat_changes_the_system_prompt(monkeypatch, capsys):
    _, client = drive(
        monkeypatch,
        ["/solve", "integrate x*e^x"],
        [FakeMessage([text_block("By parts: (x-1)e^x + C.")])],
    )
    assert "MODE: SOLVE" in client.calls[0]["system"][1]["text"]
    assert "mode: solve" in capsys.readouterr().out


def test_attached_file_travels_with_the_next_question(monkeypatch, tmp_path, capsys):
    (tmp_path / "pset.txt").write_text("Q3: find dy/dx of x^x")
    _, client = drive(
        monkeypatch,
        ["/attach pset.txt", "what is q3 asking?"],
        [FakeMessage([text_block("Logarithmic differentiation.")])],
    )
    sent = client.calls[0]["messages"][0]["content"]
    assert "find dy/dx of x^x" in sent
    assert "what is q3 asking?" in sent


def test_tool_use_is_shown_to_the_student(monkeypatch, capsys):
    drive(
        monkeypatch,
        ["what is 17*23?"],
        [
            FakeMessage([tool_block("calculate", {"expression": "17*23"})], stop_reason="tool_use"),
            FakeMessage([text_block("391.")]),
        ],
    )
    out = capsys.readouterr().out
    assert "... calculate(expression=17*23)" in out
    assert "391." in out


def test_an_api_error_does_not_end_the_chat(monkeypatch, capsys):
    """One bad turn should leave the student where they were, not drop them out."""
    code, client = drive(
        monkeypatch,
        ["first question", "second question"],
        [
            FakeMessage([text_block("An answer.")]),
            anthropic.RateLimitError(
                "slow down",
                response=SimpleNamespace(status_code=429, headers={}, request=None),
                body=None,
            ),
        ],
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "An answer." in out
    assert "rate limited" in out
    # The failed turn is rolled back, so the saved history stays sendable.
    assert len(client.calls) == 2


def test_named_sessions_persist_and_resume(monkeypatch, capsys):
    drive(
        monkeypatch,
        ["help with limits"],
        [FakeMessage([text_block("Start with the definition.")])],
        argv=("chat", "-s", "calc-hw"),
    )
    capsys.readouterr()

    _, client = drive(
        monkeypatch,
        ["carry on"],
        [FakeMessage([text_block("Next, factor the numerator.")])],
        argv=("chat", "-s", "calc-hw"),
    )
    out = capsys.readouterr().out
    assert "resumed 'calc-hw'" in out
    # History from the first run is replayed to the model.
    assert len(client.calls[0]["messages"]) == 3


def test_exit_command_leaves_cleanly(monkeypatch, capsys):
    code, client = drive(monkeypatch, ["/exit", "never asked"], [])
    assert code == 0
    assert client.calls == []


def test_do_command_writes_the_answers_without_asking(monkeypatch, tmp_path, capsys):
    """`hw do` runs the write_file tool end to end - no approval prompt in the way."""
    (tmp_path / "pset.txt").write_text("Q1: d/dx of x^2\nQ2: d/dx of x^3")

    client = FakeClient(
        turns=[
            FakeMessage([tool_block("read_assignment", {"path": "pset.txt"}, "r1")],
                        stop_reason="tool_use"),
            FakeMessage(
                [tool_block("write_file", {"path": "answers.md", "content": "1. 2x\n2. 3x^2"}, "w1")],
                stop_reason="tool_use",
            ),
            FakeMessage([text_block("Done - both derivatives are in answers.md.")]),
        ]
    )

    def explode(*_):
        raise AssertionError("hw do must not stop to ask about writing the answers")

    monkeypatch.setattr(builtins, "input", explode)
    monkeypatch.setattr(cli, "make_client", lambda: client)

    assert cli.main(["do", "pset.txt", "-C", str(tmp_path)]) == 0

    assert (tmp_path / "answers.md").read_text() == "1. 2x\n2. 3x^2"
    out = capsys.readouterr().out
    assert "answers written to" in out
    assert "MODE: SOLVE" in client.calls[0]["system"][1]["text"]


def test_fill_command_edits_the_document_end_to_end(monkeypatch, tmp_path, capsys):
    """`hw fill` runs fill_document against the real file, with no approval prompt."""
    docx = pytest.importorskip("docx")

    document = docx.Document()
    document.add_paragraph("Q1: Differentiate x^3.")
    document.add_paragraph("Q2: Integrate 2x.")
    document.save(str(tmp_path / "worksheet.docx"))

    client = FakeClient(
        turns=[
            FakeMessage(
                [tool_block("read_assignment", {"path": "worksheet.docx"}, "r1")],
                stop_reason="tool_use",
            ),
            FakeMessage(
                [
                    tool_block(
                        "fill_document",
                        {
                            "path": "worksheet.docx",
                            "edits": [
                                {"anchor": "Q1: Differentiate x^3.", "answer": "3x^2"},
                                {"anchor": "Q2: Integrate 2x.", "answer": "x^2 + C"},
                            ],
                        },
                        "f1",
                    )
                ],
                stop_reason="tool_use",
            ),
            FakeMessage([text_block("Both answers are in the document.")]),
        ]
    )

    def explode(*_):
        raise AssertionError("hw fill must not stop to ask about editing the document")

    monkeypatch.setattr(builtins, "input", explode)
    monkeypatch.setattr(cli, "make_client", lambda: client)

    assert cli.main(["fill", "worksheet.docx", "-C", str(tmp_path)]) == 0

    filled = [p.text for p in docx.Document(str(tmp_path / "worksheet.docx")).paragraphs]
    assert filled == ["Q1: Differentiate x^3.", "3x^2", "Q2: Integrate 2x.", "x^2 + C"]
    # The untouched original is still there to fall back on.
    assert (tmp_path / "worksheet.original.docx").exists()
    assert "fill_document(worksheet.docx, 2 answer(s))" in capsys.readouterr().out
