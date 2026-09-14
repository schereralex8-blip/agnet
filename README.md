# Homework Agent

Point it at an assignment and it does the assignment. It reads the file, answers every
question with the working shown, checks its own arithmetic, and writes the finished
answers to a file. It also tracks what's due.

```
$ hw do pset3.pdf
  ... read_assignment(path=pset3.pdf)
  ... calculate(expression=(-3 + sqrt(9 + 24)) / 4)
  ... write_file(path=answers.md)
Answered all six questions. Q4 asked for three significant figures, so I rounded
1.68 from 1.6771. Everything checks out on substitution.
answers written to /home/sam/calc-hw/answers.md
```

If you'd rather work through it yourself, `--mode tutor` gives hints and next steps
instead. Both are there; doing the work is the default.

## Install

```bash
pip install -e ".[files]"   # [files] adds PDF and Word support - worth having
export ANTHROPIC_API_KEY=sk-ant-...
```

Python 3.10+. Get a key from [console.anthropic.com](https://console.anthropic.com/),
or run `ant auth login` if you have the Anthropic CLI — the SDK picks that up too.

**Assignment formats:** plain text, Markdown, LaTeX, CSV, code, PDF, and Word
(`.docx`/`.dotx`) — the last two need the `[files]` extra above. Word tables are read
in place, so a data table stays attached to the question it belongs to. The old binary
`.doc` format and scanned images are refused with a message saying what to do instead.

## Use

```bash
hw fill worksheet.docx                # answers typed into the Word file itself
hw do pset3.pdf                       # the whole assignment -> answers.md
hw do essay.docx                      # Word works too
hw do pset3.pdf -o hw3.md             # ...somewhere else
hw do pset3.pdf skip question 5        # extra instructions go last
hw solve "integrate x*e^x dx"         # one problem, worked out
hw                                    # chat
hw check my_proof.md                  # grade work you already did
hw chat -s calc-hw                    # a named session you can come back to
hw due                                # what's due, soonest first
```

`hw do` and `hw fill` write without stopping to ask — that's the point of those
commands. Everywhere else, writing a file asks first.

### Typing into the document

`hw fill` puts the answers in the assignment itself rather than a separate file:

```bash
hw fill worksheet.docx                # edits the file, keeps worksheet.original.docx
hw fill worksheet.docx -o mine.docx   # fills a copy, original untouched
hw fill quiz.docx answers only, no working
```

Each answer goes in a new paragraph under its question, or replaces the blank the
question leaves (`________`, `Answer:`) — including blanks inside tables, and blanks
Word has split across several runs, which is most of them. Answers are written as
normal body text with the question's indentation, so an answer under a numbered
question doesn't become the next numbered question.

The first edit saves an untouched copy as `<name>.original.docx`, and later passes
leave that copy alone, so there's always a clean version to go back to.

Word only — `.docx` and `.dotx`. For a PDF or anything else, `hw do` gives you the
answers in a separate file.

### Modes

| Mode    | What it does                                                          |
| ------- | --------------------------------------------------------------------- |
| `solve` | Does the work: every part, working shown, answers verified. **Default.** |
| `tutor` | Hints and next steps, you turn the crank. Ask outright and it answers. |
| `check` | You bring the work, it finds the first step that goes wrong and fixes it. |

Switch mid-conversation with `/solve`, `/tutor`, `/check`.

In solve mode it produces finished work — full essays and lab reports at the length
asked for, complete programs, every numbered part answered in the assignment's own
notation and significant figures. Where an assignment is ambiguous it states the
reading it took in one line rather than stopping to ask.

### In-chat commands

```
/help                 /mode tutor|solve|check     /attach PATH
/due                  /thinking                   /save [NAME]
/sessions             /clear                      /exit
```

`/attach pset.pdf` pulls a file into your next question. `/thinking` shows the model's
reasoning as it works.

### Flags

| Flag            | Effect                                                               |
| --------------- | -------------------------------------------------------------------- |
| `-C PATH`       | Homework folder the file tools may touch (default: current directory) |
| `--subject`     | Course or level, e.g. `--subject "AP Physics 1"` — pitches the answers |
| `--allow-code`  | Let it run Python for solving, statistics, simulations                |
| `--search`      | Let it search the web                                                |
| `--thinking`    | Show reasoning                                                        |
| `-s NAME`       | Name the session so it saves and resumes                              |
| `-y`            | Skip approval prompts                                                 |
| `--effort`      | `low`…`max` — how hard the model works (default `high`)               |
| `-o PATH`       | (`hw do` only) where the answers go, default `answers.md`             |
| `--model`       | Override the model (default `claude-opus-5`)                          |

### Tracking assignments

```bash
hw add "Problem set 4" --course "Calc II" --due 2026-09-18
hw add "Essay draft" --due tomorrow
hw due
hw done 61f776
```

The agent shares this list: mention a deadline in conversation and it can add it, and
"what should I work on tonight?" makes it check what's actually due.

## Tools the agent can use

| Tool                          | What it's for                                    | Asks first |
| ----------------------------- | ------------------------------------------------ | ---------- |
| `calculate`                   | Exact arithmetic, so it stops fumbling numbers    | no         |
| `read_assignment`, `list_files` | Your problem set, draft, data, or code — text, PDF, Word | no |
| `write_file`                   | Saving notes or code you asked for               | **yes**    |
| `fill_document`                | Typing answers into a Word assignment in place   | **yes**    |
| `run_python`                   | Solving, stats, simulation (`--allow-code`)      | **yes**    |
| `add/list/complete_assignment` | The tracker above                                | no         |
| `web_search`                   | Looking things up (`--search`)                   | no         |

Anything that writes a file or runs code asks you first, every time, unless you pass
`-y`. Approval prompts show the exact call.

## Notes on safety and scope

- **File access is confined to one folder** — the one you're in, or whatever `-C`
  points at. Paths that climb out of it, including through symlinks, are refused.
- **`calculate` is not `eval`.** It walks the expression tree and permits arithmetic
  and a fixed list of maths functions. There is no way to import, read a file, or
  reach an attribute through it.
- **`run_python` is isolation, not a sandbox.** Snippets run as a separate `-I`
  interpreter in a temporary directory with a 20-second timeout and no access to your
  files — but as your OS user. That's why it's off unless you pass `--allow-code`, and
  why it asks before every run.
- **Sessions are stored in plain JSON** under `~/.homework-agent/` (override with
  `HOMEWORK_AGENT_HOME`), along with your assignment list.

## Development

```bash
pip install -e ".[dev]"
pytest                       # 152 tests, no API key or network needed
```

The test suite drives the whole agent loop against a fake client that replays scripted
turns (`tests/fakes.py`), so tool round-trips, approval gates, interrupts, and error
paths are all covered offline.

```
homework_agent/
  cli.py          commands (do, solve, chat, check...), chat loop, slash commands
  agent.py        the streaming request → tool → repeat loop
  prompts.py      the system prompts; how much each mode gives away lives here
  session.py      conversation persistence and interrupt repair
  ui.py           terminal output and approval prompts
  tools/          calculator, files, docx editing, python, assignment tracker
```

### Configuration by environment

| Variable                   | Meaning                                   |
| -------------------------- | ----------------------------------------- |
| `ANTHROPIC_API_KEY`        | API credentials                           |
| `HOMEWORK_AGENT_MODEL`     | Default model                             |
| `HOMEWORK_AGENT_EFFORT`    | Default effort level                      |
| `HOMEWORK_AGENT_WORKSPACE` | Default homework folder                   |
| `HOMEWORK_AGENT_HOME`      | Where sessions and assignments are stored |
| `NO_COLOR`                 | Turn off ANSI colour                      |
