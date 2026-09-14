# Homework Agent

A homework tutor that runs in your terminal. It reads your assignment, works through
problems with you, checks your answers, and keeps track of what's due.

By default it is a *tutor*, not an answer key: it gives you the next step and lets you
take it. When you actually want the full worked solution, you ask for it.

```
$ hw
Homework Agent
mode: tutor  model: claude-opus-5  folder: /home/sam/calc-hw
/help for commands, /exit to leave, Ctrl-C to interrupt

you > I'm stuck on question 3 of pset.pdf
  ... read_assignment(path=pset.pdf)
Question 3 asks for d/dx of x^x. You can't use the power rule here - the exponent
isn't constant - and you can't use the exponential rule either, because the base
isn't constant. What's a way to turn an exponent into a coefficient?

you > take the log of both sides?
That's it. Set y = x^x, take ln of both sides, and differentiate implicitly.
What do you get for the left side?
```

## Install

```bash
pip install -e .            # plus: pip install -e ".[pdf]" to read PDF assignments
export ANTHROPIC_API_KEY=sk-ant-...
```

Python 3.10+. Get a key from [console.anthropic.com](https://console.anthropic.com/),
or run `ant auth login` if you have the Anthropic CLI — the SDK picks that up too.

## Use

```bash
hw                                    # chat, tutor mode
hw ask "why does u-substitution work?"
hw solve "integrate x*e^x dx"         # full worked solution
hw check my_proof.md                  # grade work you already did
hw chat -s calc-hw                    # a named session you can come back to
hw due                                # what's due, soonest first
```

### Modes

| Mode    | What it does                                                        |
| ------- | ------------------------------------------------------------------- |
| `tutor` | Hints and next steps. It asks what you've tried. **Default.**        |
| `solve` | The full worked solution, with a check of the answer at the end.     |
| `check` | You bring the work, it finds the first step that goes wrong.         |

Switch mid-conversation with `/solve`, `/tutor`, `/check`. Tutor mode won't refuse you
a final answer or lecture you about it — it tells you `/mode solve` exists, and if you
ask again, you get it. What it won't do in any mode is ghost-write an essay you'll hand
in as your own; it'll outline, critique, or demonstrate instead.

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
| `read_assignment`, `list_files` | Reading your problem set, draft, data, or code  | no         |
| `write_file`                   | Saving notes or code you asked for               | **yes**    |
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
pytest                       # 104 tests, no API key or network needed
```

The test suite drives the whole agent loop against a fake client that replays scripted
turns (`tests/fakes.py`), so tool round-trips, approval gates, interrupts, and error
paths are all covered offline.

```
homework_agent/
  cli.py          commands, chat loop, slash commands
  agent.py        the streaming request → tool → repeat loop
  prompts.py      the system prompts; the modes live here
  session.py      conversation persistence and interrupt repair
  ui.py           terminal output and approval prompts
  tools/          calculator, files, python, assignment tracker
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
