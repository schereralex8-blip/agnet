import io

import pytest

from homework_agent import cli
from homework_agent.config import Config
from homework_agent.session import Session
from homework_agent.ui import Console


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMEWORK_AGENT_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOMEWORK_AGENT_WORKSPACE", str(tmp_path))
    return tmp_path


@pytest.fixture
def console():
    return Console(io.StringIO(), color=False)


def output(console):
    return console.stream.getvalue()


def parse(argv):
    return cli.build_parser().parse_args(argv)


def test_bare_invocation_starts_a_chat(monkeypatch):
    called = {}

    def fake_chat(args):
        called["args"] = args
        return 0

    monkeypatch.setattr(cli, "cmd_chat", fake_chat)
    assert cli.main([]) == 0
    assert called["args"].command == "chat"


def test_flags_become_config(tmp_path):
    args = parse(
        ["ask", "why", "--mode", "solve", "--allow-code", "--search", "-y", "-C", str(tmp_path)]
    )
    config = cli.config_from_args(args)
    assert (config.mode, config.allow_code, config.web_search, config.auto_approve) == (
        "solve", True, True, True,
    )
    assert config.workspace == tmp_path.resolve()


def test_solve_command_forces_solve_mode(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "run_once", lambda config, q, args: seen.update(mode=config.mode, q=q) or 0)
    cli.main(["solve", "integrate", "x*e^x"])
    assert seen == {"mode": "solve", "q": "integrate x*e^x"}


def test_check_command_builds_a_review_request(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "run_once", lambda config, q, args: seen.update(mode=config.mode, q=q) or 0)
    cli.main(["check", "proof.md", "question", "3"])
    assert seen["mode"] == "check"
    assert "proof.md" in seen["q"] and "question 3" in seen["q"]


def test_add_due_and_done_round_trip(capsys):
    assert cli.main(["add", "Problem", "set", "4", "--due", "2026-09-18"]) == 0
    assert cli.main(["due"]) == 0
    listing = capsys.readouterr().out
    assert "Problem set 4" in listing

    assignment_id = listing.strip().splitlines()[-1].split()[0]
    assert cli.main(["done", assignment_id]) == 0
    cli.main(["due"])
    assert "nothing outstanding" in capsys.readouterr().out


def test_add_rejects_a_vague_due_date(capsys):
    assert cli.main(["add", "Essay", "--due", "next friday"]) == 1
    assert "YYYY-MM-DD" in capsys.readouterr().out


def test_relative_due_dates():
    from datetime import date, timedelta

    assert cli._parse_relative_due("today") == date.today().isoformat()
    assert cli._parse_relative_due("tomorrow") == (date.today() + timedelta(days=1)).isoformat()
    assert cli._parse_relative_due("2026-01-01") == "2026-01-01"


def test_sessions_listing_is_empty_at_first(capsys):
    assert cli.main(["sessions"]) == 0
    assert "no saved sessions" in capsys.readouterr().out


# -- slash commands ----------------------------------------------------------


def run_command(text, config=None, session=None, console=None, pending=None):
    config = config or Config()
    session = session or Session(name="t")
    console = console or Console(io.StringIO(), color=False)
    agent = type("A", (), {"session": session})()
    result = cli.handle_command(text, config, session, console, agent, pending if pending is not None else [])
    return result, config, session, console


def test_mode_switch():
    _, config, session, _ = run_command("/solve")
    assert config.mode == "solve" and session.mode == "solve"

    _, config, _, _ = run_command("/mode check")
    assert config.mode == "check"


def test_bad_mode_is_rejected():
    _, config, _, console = run_command("/mode sideways")
    assert config.mode == "tutor"
    assert "mode must be one of" in output(console)


def test_exit_command():
    assert run_command("/exit")[0] == "exit"
    assert run_command("/quit")[0] == "exit"


def test_help_lists_the_modes():
    _, _, _, console = run_command("/help")
    assert "/attach" in output(console)


def test_attach_queues_the_file_for_the_next_message(tmp_path):
    (tmp_path / "pset.txt").write_text("Q1: find the limit")
    pending = []
    _, _, _, console = run_command("/attach pset.txt", config=Config(workspace=tmp_path), pending=pending)
    assert pending and "find the limit" in pending[0]
    assert "attached" in output(console)


def test_attach_reports_a_missing_file(tmp_path):
    pending = []
    _, _, _, console = run_command("/attach nope.txt", config=Config(workspace=tmp_path), pending=pending)
    assert pending == []
    assert "error" in output(console)


def test_clear_empties_the_history():
    session = Session(name="t")
    session.add_user("hi")
    run_command("/clear", session=session)
    assert session.messages == []


def test_thinking_toggles():
    _, config, _, _ = run_command("/thinking")
    assert config.show_thinking is True


def test_save_writes_a_session(tmp_path):
    session = Session(name="t")
    session.add_user("hi")
    _, config, session, console = run_command("/save calc-hw", session=session)
    assert "saved to" in output(console)
    assert cli.SessionStore(config.state_dir() / "sessions").exists("calc-hw")


def test_unknown_command():
    _, _, _, console = run_command("/teleport")
    assert "unknown command" in output(console)


# -- session plumbing --------------------------------------------------------


def test_resuming_restores_the_mode(console):
    config = Config(mode="check", subject="Calc II")
    session = Session(name="calc")
    session.add_user("q")
    session.add_assistant([{"type": "text", "text": "a"}])
    cli.save_session(config, session)

    config2 = Config()
    restored = cli.load_session(config2, "calc", console)
    assert config2.mode == "check"
    assert restored.subject == "Calc II"
    assert "resumed" in output(console)


def test_resuming_trims_an_interrupted_turn(console):
    config = Config()
    session = Session(name="calc")
    session.add_user("q")
    session.add_assistant([{"type": "text", "text": "a"}])
    session.add_user("interrupted")
    cli.save_session(config, session)

    restored = cli.load_session(Config(), "calc", console)
    assert len(restored.messages) == 2
    assert "dropped 1 unfinished" in output(console)


def test_unnamed_sessions_are_not_persisted(console):
    session = cli.load_session(Config(), None, console)
    assert session.name == "scratch"
    assert output(console) == ""
