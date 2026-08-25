# JOB-14 — What a hostile or malformed evidence log can do to the machine that reads it

MacBook (audit side), 2026-08-24. Defensive review of `migkit report` against a log
the reader did not write. Repo left untouched (`git status --porcelain` empty);
everything below runs out of `$SP/job14-security/`.

**Interpreter:** `/Users/ericw/IdeaProjects/model-migration-kit/.venv/bin/python` (the macOS
second-operator venv from `SETUP.md`, not the Windows path in `CLAUDE.md`).

**Harness.** `$SP/job14-security/mk.py` copies `$SP/fx/A` into `lab/<case>/`, rewrites every
recorded path to the new directory, then applies one mutation to the last
`migkit.comparison` payload. Each case is one directory holding `evidence.jsonl` plus the
run artifacts, i.e. exactly the shape a colleague hands over.

**Targets.** Every "sensitive" file read in this report is one I created
(`$SP/job14-security/secret/*`) containing synthetic markers. No real credential was
touched; no network was touched.

---

## Ranked by what an operator running `migkit report` on a colleague's log would wrongly believe is safe

| # | finding | verdict | reachable via plain `migkit report`? |
|---|---|---|---|
| 1 | A blank `goldenset_hash` silently disables the "fabricated exhibit" identity gate; the page shows an em dash and nothing else | **SURVIVES** | yes — bare log, no filesystem help |
| 2 | Recorded paths are confined *textually*, never resolved; a symlink shipped beside the log reads and quotes a file outside the tree, and the page names the symlink | **SURVIVES** | yes — if the log arrives as a directory (tar/zip/git), no if it arrives as a lone file |
| 3 | `_contained` measures containment against the **process CWD**, not the log's directory; the same log renders two different documents from two directories, and the one read from the log's own directory loses the confinement disclosure | **SURVIVES** | yes — and the triggering shape is exactly what this project's Windows box writes |
| 4 | One evidence line costs ~5.4x its bytes resident; a flip-heavy log costs ~29x and produces an unbounded HTML | **SURVIVES** (design documented, hostile-writer case not) | yes |
| 5 | A FIFO named as the golden set inside the evidence directory hangs the reader indefinitely | **SURVIVES** (low) | tar only — zip and git cannot carry a FIFO |
| 6 | A deeply nested payload is reported as "a bug in this tool ... Please report it" | **WEAKENED** | yes |

### Classes tested and found sound

| class | verdict | what was tried |
|---|---|---|
| `..` traversal, UNC / `\\?\` device, `//host/share` | **REFUTED** — refused before any open, exit 3, sentence names the path and the remedy | 4 forms |
| absolute path outside the log's directory | **REFUTED** — refused, and the refusal appears in **five** places on the page | `goldenset_path`, `baseline.artifact`, `judged_artifact` |
| NUL byte in a recorded path | **REFUTED** — `ValueError: embedded null byte`, exit 3, nothing opened | |
| `config_path` | **REFUTED** — never opened anywhere; display-only | grep of every `open`/`read_*` in `report.py` |
| zip-slip / zip symlink in `verify_release.py` | **REFUTED** — proven confined | 4 malicious members |
| tar-slip in `verify_release.py` | **REFUTED** — the sdist is never extracted, only `getnames()` | |
| `n_per_item = 10**9`, `records = 10**12` | **REFUTED** — integer arithmetic only, 0.21 s, 50 MB RSS | |
| any *other* unsanitised write to stdout/stderr beyond the known `cli.py:437` | **REFUTED** — ESC injected into every payload string, three flag combinations, 11 forced-exception paths: zero raw ESC anywhere else | |

---

## Finding 1 — a hostile log turns off the golden-set identity gate by blanking one field, and the page says nothing

`report.py:2146-2151`, `_load_goldenset`:

```python
recorded_hash = str(payload.get("goldenset_hash", "") or "")
...
if recorded_hash and loaded.hash != recorded_hash:
```

The guard is `if recorded_hash and ...`. An empty recorded hash short-circuits it, and the
function's own docstring is what the check is for:

> *"Pairing today's file with last week's outputs would be a fabricated exhibit, and it
> would be indistinguishable from a real one."*

**The proof.** Two directories, identical in every byte except one payload field, each
shipping a golden set whose twelve inputs are **not** the ones the run was measured on:

```bash
.venv/bin/python $SP/job14-security/t_hashgate.py
cd $SP/job14-security/lab/hash_blank && \
  .venv/bin/python -m model_migration_kit.cli report evidence.jsonl --html out.html --no-terminal
```

`hash_kept` (`goldenset_hash` left as written) — rendered verbatim:

```
  golden set
r1/goldenset.jsonl
      3f519e187067bcfbb8b0a764c28308dfdbda61aeb6669c0d16b8c31e755bbe0d
      — not available: the golden set at r1/goldenset.jsonl no longer matches the one that
        was run (492ae5e01033a0e1 now, 3f519e187067bcfb then), so the inputs are not shown.
        Pairing today's file with last week's outputs would be a fabricated exhibit.
```

`hash_blank` (`"goldenset_hash": ""`) — rendered verbatim:

```
  golden set
r1/goldenset.jsonl
      —
      — 12 items,
      0 with a reference,
      0 untagged
```

and the swapped inputs are quoted into the change sections: `grep -c 'SWAPPED-INPUT'`
returns **3** on `hash_blank` and **0** on `hash_kept`.

**What the reader wrongly concludes.** The block reads as a golden set that loaded and
was accepted. There is no sentence anywhere on the page saying the identity of this set
was never checked. The em dash *is* the project's absence marker, so "never recorded" is
distinguishable from a hash — but the *consequence* of that absence is not: an unverified
set and a verified set render the same success shape, and the reader is shown item inputs
under both.

The repo already has the third state elsewhere. `series.py:719-720`:

```python
if not _recorded(key.goldenset_hash) or not _recorded(against.goldenset_hash):
    return _unrecorded_hash("golden-set", key.goldenset_hash, against.goldenset_hash)
```

The timeline treats an unrecorded golden-set hash as its own outcome. The provenance
block and the inputs gate do not.

**Adversarial pass — SURVIVES.** The strongest counter is that the em dash discloses the
absence, so this is not "an absence rendering as a measurement" in the literal sense. It
is the adjacent failure: an absence rendering as a *passed check*. The band that teaches
a reader what a failed identity check looks like is exactly what goes missing.

**Reachability: yes, unqualified.** No symlink, no filesystem help, no override. A single
edited field in a log delivered as a bare `evidence.jsonl`. `report.py`'s own module
docstring puts "a hand-edited log, a future or older writer" in scope, and every writer in
`src/` records the field, so a blank one *is* the edited-log signal — and it is the one
tampering signal `_tampered_form` does not look for.

---

## Finding 2 — the confinement is textual; a symlink beside the log reads a file outside the tree and the page names the symlink

`_contained` (`report.py:2048-2060`) is explicit that this is a trade:

> *"String comparison after `abspath`/`normcase`, deliberately, and never `Path.resolve()`:
> resolving touches the filesystem, and doing I/O to decide whether I/O is allowed is the
> bug this function exists to prevent."*

The trade is defensible. What is not recorded anywhere is the residual: **the eventual
`open()` still follows symlinks, and the report names the recorded path rather than the
file that was actually read.**

**The proof.** A repro directory of the shape a colleague would hand over — evidence log,
`r1/`, artifacts — in which `r1/goldenset.jsonl` is a symlink to a file *outside* the
directory, and the payload's `flips` list names all twelve item ids so every line of the
linked file is quoted:

```bash
.venv/bin/python $SP/job14-security/t_exfil_all.py
cd $SP/job14-security/lab/exfil_all && \
  .venv/bin/python -m model_migration_kit.cli report evidence.jsonl --html o.html --no-terminal
```

The mutation is three lines:

```python
p["goldenset_path"] = "r1/goldenset.jsonl"    # relative, inside the log's directory
p["goldenset_hash"] = ""                      # Finding 1, used as an enabler
os.symlink(W/"secret"/"PROPRIETARY.jsonl", d/"r1"/"goldenset.jsonl")
```

Result — **12 of 12 lines of the out-of-tree file quoted onto the page**:

```
    CONFIDENTIAL-PROMPT-01 synthetic-secret-marker-ZZQ9
    CONFIDENTIAL-PROMPT-02 synthetic-secret-marker-ZZQ9
    ... (12 total)
```

and the provenance block, verbatim:

```
  golden set
r1/goldenset.jsonl
      —
      — 12 items,
```

`grep -ci 'symlink\|PROPRIETARY\|secret/'` over the rendered page returns **0**. The
document does not say which file it read.

The same works for `baseline.artifact` and `judged_artifact` (`lab/symlink` shows both
symlinks followed; the run artifact's content did not render there only because a
credentials `.txt` is not parseable as a run artifact — the failure message quotes the
parse position, not the content: `malformed record at line 1 of loot/baseline.jsonl:
Expecting value: line 1 column 1 (char 0)`).

**How much can be exfiltrated, and of what.** Bounded by format, not by the confinement.
The target must parse as the format the loader expects — golden-set JSONL for
`goldenset_path`. That excludes `.env`, `id_rsa`, `/etc/passwd`. It does **not** exclude
the thing worth taking here: another team's golden set (proprietary prompts and
references), another evidence log, any `.jsonl` under the reader's home. Once it parses,
the attacker chooses the ids in `flips`/`gains`, and every matching item's `input` is
quoted — so the exfiltration is total, not partial, and it lands in an HTML file the
operator's next move is to *share*.

Two smaller leaks need no parse success at all: the tag histogram and item count of any
parseable set render whether or not any id joins (`internal-tag-secret: 12` appeared in
`lab/exfil`), and `goldenset.py:207` names unknown JSON *keys* in its refusal.

**Adversarial pass — SURVIVES, with the reachability narrowed.** This is not reachable
from a lone `evidence.jsonl` — the containment logic genuinely holds against every path
string I could write (see the sound-classes table). It needs the attacker to place one
symlink in the delivered directory. That is not exotic: `tar` preserves symlinks, `git`
stores them, and the brief's own example — "a downloaded reproduction" — is a directory.
It is *not* reachable through a CI artifact download that unpacks a zip, because Python's
`zipfile` and most zip writers do not carry symlinks.

Finding 1 is used as an enabler here (so the swapped set is not caught by the hash). With
`goldenset_hash` intact the symlink is followed, the file is read and hashed, and the
mismatch band fires — the read still happens, only the quoting is suppressed.

---

## Finding 3 — the confinement decision depends on the reader's working directory, and this project's own Windows→Mac workflow triggers it

`_is_absolute` (`report.py:2062`) calls a Windows drive letter and a leading backslash
absolute — correctly, because a log written on Windows and read on POSIX is the designed
workflow. But `_contained` then hands that string to `os.path.abspath`, which on POSIX
does **not** treat it as rooted and joins it to the **process CWD**:

```python
base   = os.path.normcase(os.path.abspath(str(base_dir)))
target = os.path.normcase(os.path.abspath(recorded))     # <- CWD-relative on POSIX
return target == base or target.startswith(base + os.sep)
```

So containment is decided against `os.getcwd()`, not against the evidence log's directory,
for every path `_is_absolute` calls absolute but the platform does not. When the reader's
CWD *is* the log's directory — `cd repro && migkit report evidence.jsonl`, the most common
invocation — the check passes and `_resolve` returns the recorded string **verbatim**, and
the tool attempts the open.

**The proof — one log, two directories, two different documents.** A log recording a real
Windows path of exactly the shape the Windows box writes:

```python
p["goldenset_path"] = r"C:\Users\ewehm\repos\migration-kit\.migkit\goldenset.jsonl"
```

Read from inside the evidence directory:

```
— not available: the golden set at C:\Users\ewehm\repos\migration-kit\.migkit\goldenset.jsonl
  could not be read (cannot read golden set ...: [Errno 2] No such file or directory: ...)
```

Read from anywhere else, same bytes:

```
— not available: the golden set is recorded as C:\Users\ewehm\repos\migration-kit\.migkit\goldenset.jsonl,
  which is outside the directory holding the evidence log; a path recorded on another machine
  is not followed. Pass --artifact-dir (or --goldenset for the golden set) to say where the
  file is now.
```

```bash
.venv/bin/python $SP/job14-security/t_win.py
(cd $SP/job14-security/lab/winlog && .venv/bin/python -m model_migration_kit.cli report evidence.jsonl --html oA.html --no-terminal)
(cd /                             && .venv/bin/python -m model_migration_kit.cli report $SP/job14-security/lab/winlog/evidence.jsonl --html oB.html --no-terminal)
```

And it is not only a wording difference — the CWD-relative branch **reads**. Same harness,
with a recorded `C:loot.jsonl` and a file of that literal name in the directory:

```bash
.venv/bin/python $SP/job14-security/t_cwd.py
```

| invocation | provenance block | `DRIVE-LETTER-READ` lines on the page |
|---|---|---|
| `cd lab/cwdquirk && migkit report evidence.jsonl` | `C:loot.jsonl` / `— 12 items,` | **3** |
| `cd / && migkit report <abs>/evidence.jsonl` | `—` / *"is outside the directory holding the evidence log; a path recorded on another machine is not followed"* | **0** |

**What the reader wrongly concludes.** In the first case the page says the file *could not
be read* — i.e. the tool tried and the file is missing — which sends the operator after
the wrong remedy and, worse, tells them the confinement did not apply to this path. The
correct sentence, the one that names `--artifact-dir`, appears only when the reader
happened to be somewhere else.

**Adversarial pass — SURVIVES as a disclosure defect; REFUTED as a path escape.** I could
not turn this into a read outside the tree: on POSIX the accepted string resolves inside
the CWD, which in the triggering case *is* the log's directory. On Windows the same branch
accepts a drive-relative `C:foo\bar` and opens it against the per-drive current directory,
which I cannot test from here — recorded for the Windows side. The finding is that a
change-control document is a function of the reader's shell, and that the one disclosure
that names the mitigation is the one that disappears.

---

## Finding 4 — streaming holds one line, and a line is the attacker's size

`stream_records`' claim is exact and I did not find it violated: `for line in handle` plus
`EvidenceRecord.from_json` holds one record. But nothing bounds a line, and one record
costs several times its own bytes.

**One enormous record** (`$SP/job14-security/res/bigline.py`) — a single `judge.verdict`
whose `input` is one long string:

```
line=64MB   log=67MB    OK 0.4s   maxrss=407MB    (6.1x)
line=512MB  log=537MB   OK 4.1s   maxrss=2916MB   (5.4x)
```

**Many small non-joining verdicts**, the shape `dimensions.py` documents as the unbounded
one (`res/amp.py`):

```
N=20000    log=2.66 MB    peak_traced=6.86 MB   maxrss=58 MB
N=200000   log=26.34 MB   peak_traced=57.00 MB  maxrss=195 MB
N=1000000  log=131.94 MB  peak_traced=255.12 MB maxrss=730 MB   (5.5x RSS)
```

**Flip-heavy logs are the worst multiplier** (`res/flips.py`), because the module docstring
deliberately bounds the *quoted text* and never the *row*:

```
N=5000    log=1.4MB   model=0.1s  render=0.8s   html=2.7MB    maxrss=84MB
N=50000   log=13.4MB  model=0.5s  render=3.3s   html=26.5MB   maxrss=424MB
N=200000  log=53.8MB  model=2.4s  render=14.0s  html=106.1MB  maxrss=1558MB   (29x RSS)
```

A 54 MB log produces a **106 MB HTML** and costs **1.5 GB** resident, with
`DEFAULT_MAX_REPORT_CHARS = 10_000_000` in force the whole time.

**Adversarial pass — SURVIVES, but only on the hostile-writer half.** The row policy is
argued at length in the module docstring:

> *"What is bounded is deliberately the quoted text and never the row ... Dropping rows to
> fit a byte budget would remove findings, which is worse than a large file."*

That argument is sound for a **trusted** writer, where the row count is bounded by the
golden set. It is the same docstring that declares the log attacker-influenced. Against a
writer who chooses `len(flips)`, "a large file" is 2x the log and 29x the memory, with no
ceiling — and the very failure the docstring names, *"a generated, valid, attested,
unopenable artifact"*, is reachable again by a route the bound does not cover.

Everything here is **linear**; I found no super-linear blow-up. `n_per_item = 10**9` with
`records = 10**12` renders in 0.21 s at 50 MB — integer arithmetic only, nothing allocates
on it. **REFUTED** as an allocation vector.

---

## Finding 5 — a FIFO named as the golden set hangs the reader forever

`resolve_evidence` guards the evidence log itself with `path.is_file()` (false for a FIFO).
No such guard exists on the golden set or the artifacts: `GoldenSet.load` goes straight to
`target.read_bytes()`.

```bash
.venv/bin/python $SP/job14-security/t_fifo.py     # mkfifo lab/fifo/r1/goldenset.jsonl
cd $SP/job14-security/lab/fifo && \
  .venv/bin/python -m model_migration_kit.cli report evidence.jsonl --html o.html --no-terminal
```

```
STILL RUNNING after 10s -> BLOCKED on the FIFO
```

No output, no timeout, no diagnostic; killed at 10 s.

**Adversarial pass — SURVIVES, low.** `tar` restores FIFOs for an unprivileged user, so a
shipped `repro.tar.gz` reaches it; `zip` and `git` cannot. The impact is a hung process an
operator can Ctrl-C — but in CI it is a job that burns its whole timeout with an empty log,
which reads as an infrastructure problem rather than as a hostile artifact.

---

## Finding 6 — a nested payload is reported as a bug in the tool

```bash
.venv/bin/python $SP/job14-security/res/deep2.py 50000   # {"a":{"a": ... 50k deep
cd $SP/job14-security/lab/deep50000 && \
  .venv/bin/python -m model_migration_kit.cli report evidence.jsonl --html o.html --no-terminal
```

```
RecursionError: maximum recursion depth exceeded while decoding a JSON object from a unicode string
migkit: unexpected internal error; the traceback above is the whole of what we know. Please report it.
```

Depth 5,000 renders normally; 50,000 and 500,000 both land here. Exit 3, no crash — CPython's
recursion guard holds and the C scanner does not overflow the stack, which is the thing that
would have mattered.

**Adversarial pass — WEAKENED.** The exit code is right and the process is safe. The defect
is triage: `RecursionError` is not in `EXPECTED_ERRORS`, so a *malformed input* is announced
as a defect in the tool, and the operator files a bug report instead of distrusting the log.
`EvidenceError` at the same depth would have said the true thing.

---

## Class: output safety beyond the known `cli.py:437` — REFUTED, and here is what was tried

Building on the established results (HTML escaping solid; `_CONTROL_RE` closes
`render_terminal`; `cli.py:437` takes raw ESC), I swept for any *other* unsanitised write.

**Sweep 1 — ESC in every string in every payload** (`t_esc.py` appends
`\x1b[2J\x1b[31mHIJACK\x07` to every non-path string in every record), rendered three ways:

| flags | raw ESC on stdout | raw ESC on stderr |
|---|---|---|
| `--no-terminal` | 1 | 0 |
| *(terminal render on)* | 1 | 0 |
| `--quiet report --no-terminal` | 1 | 0 |

The single hit is the known line, every time:

```
VERDICT: NO-GO^[[2J^[[31mHIJACK^G (exit 3)
```

Two confirmations worth recording: with the full terminal render on, **zero** escapes leak
through `render_terminal`; and `--quiet` does not suppress the verdict line, so the known
finding survives every flag combination the CLI offers.

**Sweep 2 — forced exception paths.** Eleven type-confusion mutations chosen to reach
`_err` at `cli.py:867` and `traceback.print_exc()` at `cli.py:872` with attacker text in
the message (`verdict` as a list and as a dict, `judges`/`flips`/`baseline`/
`completion_rates`/`thresholds` as strings, `item_counts` as a list, `created` as a dict,
`n_per_item` as a string, `goldenset_path` as an int):

```
exc_verdict_list      exit=3 stdoutESC=0 stderrESC=0
exc_verdict_dict      exit=3 stdoutESC=0 stderrESC=0
exc_judges_str        exit=3 stdoutESC=0 stderrESC=0
exc_gs_path_int       exit=1 stdoutESC=0 stderrESC=0
exc_flips_str         exit=3 stdoutESC=0 stderrESC=0   ValueError: dictionary update sequence element #0 has length 1; 2 is required
exc_baseline_str      exit=3 stdoutESC=0 stderrESC=0   (same)
exc_completion_rates  exit=3 stdoutESC=0 stderrESC=0   (same)
exc_item_counts       exit=3 stdoutESC=0 stderrESC=0   ValueError: ... has length 13; 2 is required
exc_created           exit=1 stdoutESC=0 stderrESC=0
exc_n_per_item        exit=3 stdoutESC=0 stderrESC=0   ValueError: invalid literal for int() ...
exc_thresholds        exit=3 stdoutESC=0 stderrESC=0
```

Zero raw ESC on either stream in all eleven. The reason it holds is structural rather than
accidental: every message that interpolates recorded text uses `!r`
(`_resolve`'s `{recorded!r}`, `stream_records`' `{text[:120]!r}`, `int()`'s own message),
and Python's `repr` escapes everything `str.isprintable()` rejects — C0/C1 controls, `U+2028`,
`U+202E`. **This is a property of every message I found, not a rule anyone stated**, so it
is worth one contract test rather than trust.

**`cmd_report` emits no progress lines at all** — it calls `_render` directly with no
`_progress` — so the progress surface is not reachable from a hostile log.

---

## Class: the archive paths in `verify_release.py` — REFUTED, and here is what was tried

`scripts/verify_release.py:668-669` is the only extraction anywhere in `scripts/`, `src/`
or `tests/`:

```python
with zipfile.ZipFile(wheel) as zf:
    zf.extractall(extract)
```

The sdist (`:728`) is only enumerated — `tf.getnames()` — never extracted, so **tar-slip is
not reachable**: there is no `tarfile.extractall` in the repository.

**Zip-slip proof.** A wheel carrying four malicious members, extracted by exactly the call
above:

```bash
.venv/bin/python $SP/job14-security/t_zipslip.py
```

```
names in zip: ['../../../ESCAPED_RELATIVE.txt', '/tmp/ESCAPED_ABSOLUTE.txt',
               '..\\..\\ESCAPED_WINSEP.txt', 'link_to_secret', 'normal/ok.txt']
  extracted: wheel-extract/link_to_secret            | symlink: False
  extracted: wheel-extract/ESCAPED_RELATIVE.txt      | symlink: False
  extracted: wheel-extract/..\..\ESCAPED_WINSEP.txt  | symlink: False
  extracted: wheel-extract/normal/ok.txt             | symlink: False
  extracted: wheel-extract/tmp/ESCAPED_ABSOLUTE.txt  | symlink: False
escaped to lab parent? []
escaped to /tmp? False
```

Everything landed inside `wheel-extract/`. CPython's `ZipFile._extract_member` strips the
drive, the leading separator and every `..` component before joining, and `extractall` does
not honour the unix symlink mode bits at all — the symlink member became an ordinary file
containing its target path. **REFUTED on both counts.**

One robustness nit while in there, not a security finding: `check_sdist_contents` does
`Path(m).relative_to(root)` for every member, which raises an uncaught `ValueError` on an
absolute member name. The input is a wheel the maintainer just built, so nobody hostile is
upstream of it.

---

## Reproduction index

Everything is under `$SP/job14-security/`:

| file | what it builds |
|---|---|
| `mk.py` | the harness — copies `$SP/fx/A`, relocates recorded paths, applies one mutation |
| `t_paths.py` | the ten path-form cases (`abs_outside`, `dotdot`, `drive`, `unc`, `slashslash`, `nul`, `newline`, `backslash_abs`, `tilde`, `dot_segments`) |
| `t_hashgate.py` | Finding 1 — `hash_blank` vs `hash_kept` |
| `t_symlink.py`, `t_exfil.py`, `t_exfil_all.py` | Finding 2 |
| `t_win.py`, `t_cwd.py` | Finding 3 |
| `res/bigline.py`, `res/amp.py`, `res/flips.py`, `res/deep2.py` | Findings 4 and 6 |
| `t_fifo.py` | Finding 5 |
| `t_esc.py`, `t_exc.py` | the output-safety sweeps |
| `t_zipslip.py`, `t_nbig.py` | the zip-slip proof; the `n_per_item = 10**9` refutation |
| `secret/` | my own synthetic targets — no real credential was involved |
| `lab/` | one directory per case, each holding the crafted log and its rendered report |
