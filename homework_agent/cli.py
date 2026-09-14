"""Command line interface."""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from homework_agent import __version__
from homework_agent.agent import AgentError, HomeworkAgent
from homework_agent.config import DEFAULT_MODEL, MODES, Config
from homework_agent.session import Session, SessionStore, rollback_incomplete
from homework_agent.tools import build_registry
from homework_agent.tools.base import Tool, ToolError
from homework_agent.tools.docx_edit import EDITABLE_SUFFIXES
from homework_agent.tools.files import ReadAssignmentTool, resolve_in_workspace
from homework_agent.tools.tracker import Tracker, validate_due
from homework_agent.ui import Console

HELP = """commands
  /help              this list
  /mode solve|tutor|check
  /tutor /solve /check   shorthand for the above
  /attach PATH       pull a file into the next question
  /due               your tracked assignments
  /thinking          show or hide Claude's reasoning
  /save [NAME]       save this conversation
  /sessions          list saved conversations
  /clear             forget this conversation and start over
  /exit              leave (Ctrl-D works too)

modes
  solve   the work, done for you (default)
  tutor   hints and next steps, you do the work
  check   you paste your work, it gets graded
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hw",
        description="Does your homework: reads the assignment, answers it, shows the working.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples\n"
            "  hw do pset3.pdf              do the whole assignment, answers to answers.md\n"
            "  hw fill worksheet.docx       type the answers into the Word file itself\n"
            "  hw solve 'integrate x*e^x dx'\n"
            "  hw                           start a chat\n"
            "  hw ask 'why does u-substitution work?' --mode tutor\n"
            "  hw check my_proof.md         grade work you already did\n"
            "  hw due                       what is due, soonest first\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"homework-agent {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--model", default=None, help=f"model id (default {DEFAULT_MODEL})")
    common.add_argument("--mode", choices=MODES, default=None, help="tutor, solve, or check")
    common.add_argument("--subject", default=None, help="course or level, e.g. 'AP Physics 1'")
    common.add_argument(
        "-C", "--folder", default=None, help="homework folder the file tools may use (default: .)"
    )
    common.add_argument("--effort", default=None, choices=["low", "medium", "high", "xhigh", "max"])
    common.add_argument("--allow-code", action="store_true", help="let the agent run Python")
    common.add_argument("--search", action="store_true", help="let the agent search the web")
    common.add_argument("--thinking", action="store_true", help="show Claude's reasoning")
    common.add_argument("-y", "--yes", action="store_true", help="skip approval prompts")
    common.add_argument("-s", "--session", default=None, help="name this conversation (resumes it)")

    subparsers = parser.add_subparsers(dest="command")

    do = subparsers.add_parser(
        "do", parents=[common], help="do a whole assignment and write the answers to a file"
    )
    do.add_argument("path", help="the assignment file")
    do.add_argument(
        "-o", "--out", default=None, help="where to write the answers (default answers.md)"
    )
    do.add_argument(
        "instructions",
        nargs="*",
        help="anything to add, e.g. 'skip question 5' - put these last",
    )
    do.set_defaults(func=cmd_do)

    fill = subparsers.add_parser(
        "fill", parents=[common], help="type the answers straight into a Word assignment"
    )
    fill.add_argument("path", help="the .docx assignment to fill in")
    fill.add_argument(
        "-o", "--out", default=None, help="fill a copy at this path, leaving the original alone"
    )
    fill.add_argument(
        "instructions", nargs="*", help="anything to add, e.g. 'answers only, no working'"
    )
    fill.set_defaults(func=cmd_fill)

    chat = subparsers.add_parser("chat", parents=[common], help="interactive session")
    chat.set_defaults(func=cmd_chat)

    ask = subparsers.add_parser("ask", parents=[common], help="ask one question and exit")
    ask.add_argument("question", nargs="+")
    ask.set_defaults(func=cmd_ask)

    solve = subparsers.add_parser("solve", parents=[common], help="ask for a full worked solution")
    solve.add_argument("question", nargs="+")
    solve.set_defaults(func=cmd_solve)

    check = subparsers.add_parser("check", parents=[common], help="grade work you already did")
    check.add_argument("path", help="file containing your work")
    check.add_argument("question", nargs="*", help="anything to add, e.g. 'question 3 only'")
    check.set_defaults(func=cmd_check)

    sessions = subparsers.add_parser("sessions", help="list or delete saved conversations")
    sessions.add_argument("--delete", metavar="NAME", default=None)
    sessions.set_defaults(func=cmd_sessions)

    due = subparsers.add_parser("due", help="list tracked assignments")
    due.add_argument("--all", action="store_true", help="include finished work")
    due.set_defaults(func=cmd_due)

    add = subparsers.add_parser("add", help="track an assignment")
    add.add_argument("title", nargs="+")
    add.add_argument("--course", default=None)
    add.add_argument("--due", default=None, metavar="YYYY-MM-DD", help="or 'today'/'tomorrow'")
    add.add_argument("--notes", default=None)
    add.set_defaults(func=cmd_add)

    done = subparsers.add_parser("done", help="mark a tracked assignment finished")
    done.add_argument("assignment_id")
    done.set_defaults(func=cmd_done)

    return parser


# -- configuration -----------------------------------------------------------


def config_from_args(args: argparse.Namespace) -> Config:
    config = Config.from_env()
    if getattr(args, "model", None):
        config.model = args.model
    if getattr(args, "mode", None):
        config.mode = args.mode
    if getattr(args, "subject", None):
        config.subject = args.subject
    if getattr(args, "folder", None):
        config.workspace = Path(args.folder).expanduser().resolve()
    if getattr(args, "effort", None):
        config.effort = args.effort
    config.allow_code = bool(getattr(args, "allow_code", False))
    config.web_search = bool(getattr(args, "search", False))
    config.show_thinking = bool(getattr(args, "thinking", False))
    config.auto_approve = bool(getattr(args, "yes", False))
    return config


def make_client() -> Any:
    import anthropic

    return anthropic.Anthropic()


def make_agent(config: Config, session: Session, console: Console) -> HomeworkAgent:
    def approve(tool: Tool, tool_input: dict) -> bool:
        return console.confirm(f"allow {tool.describe_call(tool_input)}?")

    return HomeworkAgent(
        client=make_client(),
        config=config,
        registry=build_registry(config),
        session=session,
        events=console,
        approve=approve,
    )


def load_session(config: Config, name: str | None, console: Console) -> Session:
    store = SessionStore(config.state_dir() / "sessions")
    if not name:
        return Session(name="scratch", mode=config.mode, subject=config.subject)
    if store.exists(name):
        session = store.load(name)
        config.mode = session.mode
        config.subject = session.subject or config.subject
        removed = rollback_incomplete(session)
        console.info(
            f"resumed '{name}' ({len(session.messages)} messages, mode {session.mode})"
            + (f", dropped {removed} unfinished" if removed else "")
        )
        return session
    return Session(name=name, mode=config.mode, subject=config.subject)


def save_session(config: Config, session: Session) -> Path:
    session.mode = config.mode
    session.subject = config.subject
    return SessionStore(config.state_dir() / "sessions").save(session)


# -- one-shot commands -------------------------------------------------------


def run_once(config: Config, question: str, args: argparse.Namespace) -> int:
    console = Console()
    session = load_session(config, args.session, console)
    agent = make_agent(config, session, console)
    try:
        agent.run_turn(question)
    except AgentError as exc:
        console.error(str(exc))
        return 1
    except KeyboardInterrupt:
        rollback_incomplete(session)
        console.error("interrupted")
        return 130
    console.ensure_newline()
    if args.session:
        save_session(config, session)
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    config = config_from_args(args)
    return run_once(config, " ".join(args.question), args)


def cmd_solve(args: argparse.Namespace) -> int:
    config = config_from_args(args)
    config.mode = "solve"
    return run_once(config, " ".join(args.question), args)


def cmd_check(args: argparse.Namespace) -> int:
    config = config_from_args(args)
    config.mode = "check"
    extra = " ".join(args.question)
    question = f"Here is my work in {args.path}. Please check it."
    if extra:
        question += f" {extra}"
    question += " Read the file first."
    return run_once(config, question, args)


def cmd_do(args: argparse.Namespace) -> int:
    """Read an assignment, answer all of it, and leave the answers in a file."""
    config = config_from_args(args)
    config.mode = "solve"
    # Writing the answer file is the entire point of this command, so it does not
    # stop to ask; --allow-code still gates running anything.
    config.auto_approve = True

    out = args.out or "answers.md"
    question = (
        f"Do the assignment in {args.path}. Read it first, then answer every question in it "
        f"completely, and save the finished answers to {out} with write_file. Keep the "
        "assignment's own numbering so the answers line up with the questions."
    )
    extra = " ".join(args.instructions)
    if extra:
        question += f" {extra}"

    code = run_once(config, question, args)
    written = Path(config.workspace) / out
    console = Console()
    if code == 0 and written.exists():
        console.success(f"answers written to {written}")
    elif code == 0:
        console.info(f"no file was written - the answers are above (expected {out})")
    return code


def cmd_fill(args: argparse.Namespace) -> int:
    """Answer an assignment by typing into the Word document itself."""
    config = config_from_args(args)
    config.mode = "solve"
    # Editing the document is what was asked for, so it does not stop to confirm.
    config.auto_approve = True
    console = Console()

    if Path(args.path).suffix.lower() not in EDITABLE_SUFFIXES:
        console.error(
            f"{args.path} is not a Word document. Answers can only be typed into .docx files - "
            "for anything else use 'hw do' and get the answers in a separate file."
        )
        return 1

    target = args.path
    if args.out:
        try:
            source = resolve_in_workspace(config.workspace, args.path)
            destination = resolve_in_workspace(config.workspace, args.out)
        except ToolError as exc:
            console.error(str(exc))
            return 1
        if not source.exists():
            console.error(f"{args.path} does not exist")
            return 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        target = args.out
        console.info(f"filling a copy at {args.out}")

    question = (
        f"Read {target}, then type your answers directly into it with the fill_document tool. "
        "Put each answer in a new paragraph immediately after the question it answers, or, "
        "where the question leaves a blank or an 'Answer:' label, replace that instead. Leave "
        "the existing question text exactly as it is. Send every edit in one fill_document call."
    )
    extra = " ".join(args.instructions)
    if extra:
        question += f" {extra}"

    return run_once(config, question, args)


def cmd_sessions(args: argparse.Namespace) -> int:
    console = Console()
    config = Config.from_env()
    store = SessionStore(config.state_dir() / "sessions")
    if args.delete:
        if store.delete(args.delete):
            console.success(f"deleted session '{args.delete}'")
            return 0
        console.error(f"no session named '{args.delete}'")
        return 1
    sessions = store.list()
    if not sessions:
        console.info("no saved sessions yet - start one with: hw chat -s calc-hw")
        return 0
    for session in sessions:
        console.line(f"{session.name}  [{session.mode}]  {session.updated}")
        console.info(f"    {session.summary()}")
    return 0


def _parse_relative_due(due: str | None) -> str | None:
    if not due:
        return None
    key = due.strip().lower()
    if key == "today":
        return date.today().isoformat()
    if key == "tomorrow":
        return (date.today() + timedelta(days=1)).isoformat()
    return due


def tracker_for(config: Config) -> Tracker:
    return Tracker(config.state_dir() / "assignments.json")


def cmd_due(args: argparse.Namespace) -> int:
    console = Console()
    console.line(tracker_for(Config.from_env()).listing(include_done=args.all))
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    console = Console()
    try:
        due = validate_due(_parse_relative_due(args.due))
    except ToolError as exc:
        console.error(str(exc))
        return 1
    item = tracker_for(Config.from_env()).add(
        " ".join(args.title).strip(), args.course, due, args.notes
    )
    console.success(f"tracked: {item.format()}")
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    console = Console()
    try:
        item = tracker_for(Config.from_env()).complete(args.assignment_id)
    except ToolError as exc:
        console.error(str(exc))
        return 1
    console.success(f"done: {item.format()}")
    return 0


# -- chat --------------------------------------------------------------------


def cmd_chat(args: argparse.Namespace) -> int:
    config = config_from_args(args)
    console = Console()
    session = load_session(config, args.session, console)
    console.banner(config, args.session)
    agent = make_agent(config, session, console)

    pending: list[str] = []
    while True:
        try:
            raw = console.prompt()
        except (EOFError, KeyboardInterrupt):
            console.ensure_newline()
            break

        text = raw.strip()
        if not text:
            continue

        if text.startswith("/"):
            action = handle_command(text, config, session, console, agent, pending)
            if action == "exit":
                break
            continue

        if pending:
            text = "\n\n".join(pending + [text])
            pending.clear()

        try:
            agent.run_turn(text)
        except AgentError as exc:
            rollback_incomplete(session)
            console.error(str(exc))
        except KeyboardInterrupt:
            removed = rollback_incomplete(session)
            console.ensure_newline()
            console.info(f"interrupted (dropped {removed} message(s) from this turn)")
        console.ensure_newline()

        if args.session:
            save_session(config, session)

    if args.session and session.messages:
        path = save_session(config, session)
        console.info(f"saved to {path}")
    console.info("bye - good luck with it")
    return 0


def handle_command(
    text: str,
    config: Config,
    session: Session,
    console: Console,
    agent: HomeworkAgent,
    pending: list[str],
) -> str | None:
    """Handle a /command. Returns 'exit' to end the chat."""
    parts = text.split(maxsplit=1)
    command = parts[0][1:].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""

    if command in {"exit", "quit", "q"}:
        return "exit"

    if command in {"help", "h", "?"}:
        console.line(HELP)
        return None

    if command in MODES:
        config.mode = command
        session.mode = command
        console.success(f"mode: {command}")
        return None

    if command == "mode":
        if argument in MODES:
            config.mode = argument
            session.mode = argument
            console.success(f"mode: {argument}")
        else:
            console.error(f"mode must be one of: {', '.join(MODES)} (currently {config.mode})")
        return None

    if command == "attach":
        if not argument:
            console.error("usage: /attach path/to/assignment.pdf")
            return None
        try:
            content = ReadAssignmentTool(config.workspace).run(path=argument)
        except ToolError as exc:
            console.error(str(exc))
            return None
        pending.append(content)
        console.success(f"attached {argument} - it goes with your next message")
        return None

    if command == "due":
        console.line(tracker_for(config).listing())
        return None

    if command == "thinking":
        config.show_thinking = not config.show_thinking
        console.success(f"thinking: {'shown' if config.show_thinking else 'hidden'}")
        return None

    if command == "save":
        if argument:
            session.name = argument
        path = save_session(config, session)
        console.success(f"saved to {path}")
        return None

    if command == "sessions":
        store = SessionStore(config.state_dir() / "sessions")
        saved = store.list()
        if not saved:
            console.info("no saved sessions")
        for item in saved:
            console.line(f"{item.name}  [{item.mode}]  {item.updated}  {item.summary()}")
        return None

    if command == "clear":
        session.messages.clear()
        agent.session = session
        console.success("cleared - fresh start")
        return None

    console.error(f"unknown command {text.split()[0]} - try /help")
    return None


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # Bare `hw` drops straight into a chat.
        args = parser.parse_args(["chat", *(argv or [])])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
