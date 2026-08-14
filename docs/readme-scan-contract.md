# Frozen contract — how `verify_release.py` reads the README

Status: **frozen 2026-08-13**. Written by hand, before the implementation and
before the tests, because the implementer and the test author work in parallel
and will otherwise disagree at this seam.

## Why this document exists

`scripts/verify_release.py` has two checks that assert the README does not lie:

- `readme-pip-install` — every distribution name the README tells a user to
  `pip install` must be a real one.
- `readme-commands` — every subcommand the README shows being typed must exist.

Both were written to scan the whole README as flat text. Against the real README
they produce two false failures, and the failures are instructive because they
are *different* mistakes:

1. Prose was read as shell. The sentence

   > So `pip install model-migration-kit` does not work today. Install from a checkout:

   yielded the "package names" `does`, `not`, `work`, `today.`, `Install`,
   `from`, `a`, `checkout:`, `#`, `Windows:`, `pip`, `install`.

2. A **string inside** a shell example was read as an invocation. This line, from
   a `case` statement showing how to gate CI on the exit code,

   ```bash
   *) echo "migkit failed"                    ; exit 1 ;;
   ```

   yielded the subcommand `failed`, and `migkit failed --help` exits 3.

Restricting the scan to fenced code blocks fixes (1) and **not** (2): the second
defect is inside a fenced block. Both rules below are therefore load-bearing.

## Rule 1 — only fenced code blocks are shell

`fenced_code_blocks(text) -> list[str]` returns the body of each fenced block.

- A fence **opens** on a line whose stripped form begins with three or more
  backticks or three or more tildes, optionally followed by an info string
  (` ```bash `, ` ```console `, ` ``` `).
- It **closes** on the next line whose stripped form is three or more of the
  *same* character and carries no info string, and whose run is at least as long
  as the opening run. A fence of the *other* character inside an open block is
  body text, not a delimiter.
- An unterminated block at end of input yields its body up to the end. This is a
  malformed README, but silently discarding the tail would hide commands.
- Fence lines themselves are never part of any body.
- Four-space indented code blocks are **not** recognised. CommonMark says they
  are code; this project's README does not use them, and treating an indented
  prose block as shell is the mistake this document exists to stop.

Everything outside a fenced block — including inline `` `code spans` `` — is
prose. An inline span is a *mention*, not an instruction; the README's own
`pip install model-migration-kit` mention is a claim that the command does not
work yet.

## Rule 2 — only text in command position is a command

Within a code-block body, each line is processed as follows.

0. Normalise line endings: `\r\n` and `\r` both become `\n` before anything else
   looks at the text. This repository is developed on Windows and its history
   already contains one CRLF defect.
1. Strip a leading shell prompt: `$`, `>`, or a PowerShell `PS ...>`, when it is
   followed by whitespace.
2. Split the line into segments at every shell separator — `&&`, `||`, `|`, `;`,
   `&` — and at every `#` that starts the line or is preceded by whitespace. The
   `#` case is what makes the second half of

       python -m pip install .      # Windows: python.exe -m pip install .

   reachable: a comment is where a second platform's command lives, and without
   this the trailing comment is not a segment of its own, the words after it are
   not at command position, and the argument filter reads `#`, `Windows:`, `pip`,
   `install` as package names — the exact tail of defect (1) above. A `#` not
   preceded by whitespace (a URL fragment, `foo#bar`) does not split.
3. **Discard any segment that begins inside a quoted string.** A segment is
   inside a string when the text preceding it *on that line* holds an odd number
   of unescaped `"` or an odd number of unescaped `'`. Splitting without tracking
   quotes does not merely miss matches — it invents them: `echo "a && migkit
   demo"` splits into `echo "a` and `migkit demo"`, and the second half sits at
   command position and yields a `demo` that nobody typed. Quote *parity* is
   cheap and settles it; a shell parser is not needed.
4. If a segment begins with `#` (it now always does when rule 2 split there),
   strip the `#` and then an optional label of the form `Word:` or `Some words:`
   — up to 30 characters, ending in a colon and whitespace.
5. Strip leading whitespace, then an optional opening quote (`"` or `'`), then an
   optional path prefix — any run of non-whitespace ending in `/` or `\`.

What survives is in **command position**. A match anywhere else is ignored.

The helper that performs steps 0–5 is frozen as
`command_segments(line: str) -> list[str]`, returning the segments already
reduced to command position, in order of appearance, with discarded segments
absent rather than blank. Each returned segment is stripped at **both** ends:
leading whitespace hides the head of the command, and trailing whitespace is an
artifact of where the separator happened to fall. Neither carries meaning.

### Accepted over-reports

Step 5 strips an opening quote unconditionally, which is what lets
`& "$tmp\Scripts\migkit.exe" demo` work. The cost is that a line consisting only
of a quoted string — `"pip install nonsense"` — is treated as a command and
yields `nonsense`. This is accepted deliberately: it fails **loud**, and a loud
false positive on a line no real README contains is a better failure than a
narrower rule that hides a genuine wrong package name. Do not "fix" it by making
the quote strip conditional.

### `readme_pip_install_targets`

A segment is an install command when, at command position, it matches

    [<python> -m ] pip[3] install <args...>

where `<python>` is `python`, `python3`, `py`, or any of those with `.exe`. The
existing argument filter is unchanged and still correct: flags, flag values,
anything containing `/` or `\`, anything starting with `.`, and `*.whl`,
`*.tar.gz`, `*.zip`, `*.txt` are dropped; a surviving token is truncated at the
first of `< > = ! ~ [`.

### `readme_cli_commands`

A segment invokes the CLI when, at command position, it matches the program name
`migkit`, optionally `.exe`, optionally followed by a closing quote, then
whitespace, then a subcommand matching `[a-z][a-z0-9-]*`. The path-prefix strip
in step 4 is what makes `.venv\Scripts\migkit.exe demo` and
`& "$tmp\Scripts\migkit.exe" demo` both work.

A bare `migkit` with no following word is prose. `migkit:` — the program's own
log prefix, which appears throughout the README's pasted output — is not an
invocation, because a colon is not whitespace and is not a closing quote.

## Hand-derived expected values

These were derived by reading the rules, not by running any code. They are the
acceptance oracle for both the implementation and the tests.

### `fenced_code_blocks`

| # | Input | Expected |
|---|---|---|
| F1 | `a\n```\nb\n```\nc` | `["b\n"]` |
| F2 | `a\n~~~\nb\n~~~\nc` | `["b\n"]` |
| F3 | ` ```bash\nb\n``` ` | `["b\n"]` |
| F4 | ` ```\na\n~~~\nb\n``` ` | `["a\n~~~\nb\n"]` (inner `~~~` is body) |
| F5 | ` ```\nx\n` (unterminated) | `["x\n"]` |
| F6 | `no fences here` | `[]` |
| F7 | ` ```\na\n```\nprose\n```\nb\n``` ` | `["a\n", "b\n"]` |
| F8 | ` ````\na\n```\nb\n```` ` | `["a\n```\nb\n"]` (3 < 4, inner is body) |
| F9 | `    indented code` | `[]` (rule 1, indented blocks are not recognised) |

### `readme_pip_install_targets`

| # | Input | Expected |
|---|---|---|
| P1 | prose: ``So `pip install model-migration-kit` does not work today.`` | `[]` |
| P2 | fenced: `pip install model-migration-kit` | `["model-migration-kit"]` |
| P3 | fenced: `.venv/bin/python -m pip install .` | `[]` |
| P4 | fenced: `python -m pip install -e ".[dev]"` | `[]` |
| P5 | fenced: `# Windows: python -m pip install jinja2` | `["jinja2"]` |
| P6 | fenced: `echo "pip install nonsense"` | `[]` (not command position) |
| P7 | fenced: `pip install rich && pip install jinja2` | `["rich", "jinja2"]` |
| P8 | fenced: `$ pip install opik-rigor>=0.1` | `["opik-rigor"]` |
| P9 | fenced: `pip install ./dist/x.whl` | `[]` |
| P10 | fenced: `PS> py -m pip install rich` | `["rich"]` |

### `readme_cli_commands`

| # | Input | Expected |
|---|---|---|
| C1 | fenced: `migkit demo` | `["demo"]` |
| C2 | fenced: `*) echo "migkit failed" ; exit 1 ;;` | `[]` |
| C3 | fenced: `.venv\Scripts\migkit.exe demo` | `["demo"]` |
| C4 | fenced: `& "$tmp\Scripts\migkit.exe" demo` | `["demo"]` |
| C5 | fenced: `migkit: sampling fake-baseline-v1` | `[]` (log prefix) |
| C6 | fenced: `$ migkit report .\does-not-exist.jsonl` | `["report"]` |
| C7 | prose: ``` `migkit demo` runs the whole flow ``` | `[]` |
| C8 | fenced: `migkit run --n 20 && migkit compare --baseline x` | `["compare", "run"]` (sorted) |
| C9 | fenced: `...\migkit-demo-report.html` | `[]` |
| C10 | fenced: `migkit` alone on a line | `[]` |

### Against the real README

With both rules applied to `README.md` as it stands on 2026-08-13:

- `readme_pip_install_targets` returns exactly `["model-migration-kit"]` — no,
  it returns `[]`: the only unfenced mention is prose (P1) and every fenced
  install targets a path. **Expected: `[]`**, and the check reports "no
  `pip install <name>` line in README.md to get wrong".
- `readme_cli_commands` returns exactly `["compare", "demo", "report", "run"]`.
  `failed` is gone; the other four are real subcommands.

## What must not happen

Do not fix these checks by editing the README. The README is correct; it was
verified command by command against executed output. A check that is wrong about
a correct document gets fixed in the check.

Do not weaken the checks to passing. If a rule here makes a real defect
invisible, amend this document first and say why.
