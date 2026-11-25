# Splitting and validating APL code examples — design

Date: 2026-11-25. Status: approved.

## Intent

The `apl` code blocks in the documentation mix session input with the
interpreter's output. Input is marked only by the session's 6-space indent;
output is flush left. Readers cannot copy an example's input cleanly, and
nothing checks that the printed output is what Dyalog actually produces.

`tools/cutover/code-blocks.py` is a review tool. For each `apl` block it
proposes splitting session input from output, and it runs the examples
against Dyalog to flag those whose output is wrong. It never edits the
corpus: it writes a log for people to review entry by entry. Approved entries are applied
once, as a patch, during the cutover (`tools/cutover/cutover.py`), after
which `zensical/` is the authoring home. The September ⎕CSV rewrite
(commit 4abdc199, since discarded) is the precedent for the target format.

This round is about getting the tool right. Applying the log is a later
piece of work; the log's format must make it possible.

## Goals

1. Find every `apl` code block in the language reference guide
   (`zensical/docs/language-reference-guide/`, the converted tree the cutover
   patches).
2. For each block that is a session transcript, propose its split: input in
   an `apl` block without the 6-space indent, output in a `text` block
   following it.
3. Run each session example under `dyalogscript` and record how the actual
   output compares with the printed output.
4. Write a log that a reviewer can read, mark per entry, and that a later
   apply step can apply selectively and safely.

## Non-goals

- Modifying anything under `zensical/` or the source monorepo.
- Applying the log (designed for, built later).
- Guides other than the language reference guide (the tool takes the guide
  as a parameter so widening later is a flag, not a rewrite).
- Fixing wrong examples. The tool flags them; people fix them.
- Non-`apl` fences (`c`, `json`, `other`, bare fences).

## Anti-goals

- **Guessing.** A split that moves a line to the wrong side, drops a line or
  invents one is worse than no split. When a line's role is uncertain, the
  block is left alone and the log says why.
- **Noise.** A "differs" flag must mean the example ran here and Dyalog
  printed something else. Examples that cannot run on this machine (Windows
  GUI, .NET, files that do not exist, timing, randomness) must not be
  reported as differing.

## What the corpus looks like

Measured on the guide at 0afe7b461b:

- 1,861 `apl` blocks on 515 pages; no `text` blocks yet. 11 fences are
  indented (inside lists or admonitions).
- First lines: 882 blocks start at column 0 (syntax lines such as `R←⍳Y`,
  code listings, output-only blocks), 875 at the 6-space session indent, and
  about 100 at other indents (1–5, 7, 8, 10).
- Results appear in three display styles: plain, `]box on` (`┌─┬─┐`, 53
  pages) and `]box on -style=max` (`┌→`, `↓`, `∊` markers, 22 pages).
- 55 pages contain `∇`, i.e. traditional functions defined in the session
  editor notation.
- Examples with output that depends on the machine or the moment: `⎕AI`,
  `⎕TS`, `?`, file paths such as `c:\Dyalog16.0\sales.csv`, `⎕NA`, `⎕WC`.

## Approaches

1. **A small APL harness driven by Python (chosen).** Python parses pages
   and blocks; one `dyalogscript` run per page evaluates the input lines in
   order, so definitions made in earlier blocks are available to later ones
   as a reader would expect. Each line is evaluated the way the session does
   it, with shy results suppressed and errors displayed session-style, and
   results are rendered in the display style the printed output uses. Python
   compares and writes the log. Execution also confirms splits.
2. **Plain script mode.** Feed input lines as a script and parse stdout.
   Simpler, but script mode does not display bare expressions, output cannot
   be tied to its line, and shy results and errors behave differently. Too
   unreliable to separate "wrong example" from "harness artefact".
3. **A real session over RIDE (the Tauride MCP).** Exact session behaviour,
   but needs a running interpreter and a protocol client. Kept as the
   fallback if `dyalogscript` proves to need dirty hacks; the spike below
   says it does not.

Option 1 was picked.

## Feasibility (spike, Dyalog 21.0 on macOS)

- `1(85⌶)'expr'` returns the result of a non-shy expression and signals
  error 85 for a shy one, so the harness can tell what the session would
  display.
- Errors are available via `⎕DMX` (`EN`, `DM`, `Message`) to rebuild the
  session's three-line error display.
- Passing `DYALOG_INITSESSION=1` before the script name loads the full
  session (`⎕SE.UCMD`, `⎕SE.Dyalog.Utils`), so `]box` works, including
  `-style=max`, and applies to `⎕←` output.
- `dyalogscript` ends lines with CR; Python normalises to LF.
- Script mode starts with `⎕PW` 32767; the harness sets it explicitly.

## How it works

### Finding blocks

Python reads each page and finds fenced blocks whose info string is `apl`.
Each block is identified by page path (relative to `zensical/docs/`), the
line number of its opening fence, and the SHA-256 of its exact text from
opening to closing fence. Indented (nested) fences are recorded and skipped
in this round, with reason `nested`.

### Classifying lines and blocks

A line indented exactly 6 spaces is a candidate input line; any other
non-blank line is a candidate output line. Three refinements:

- **NBSP indents.** 430 lines on 54 pages are indented with non-breaking
  spaces (typically five U+00A0 and a space). An indent of exactly six
  characters drawn from space and U+00A0 counts as the session indent; the
  split strips it like spaces, and the entry records `nbsp: true` so the
  normalisation is visible in review. *(Builder's call.)*
- **Error displays.** The session shows an error as three lines: the
  message at column 0 (`DOMAIN ERROR…`), the failing line repeated at the
  6-space indent, and a caret line (`∧` or `^`) indented 6 or more. When a
  line ending in `ERROR` (optionally followed by `: …`) is followed by such
  a repeat and caret, all three are output: the repeat is not run again,
  and the three lines go verbatim into the `text` block. *(Builder's call:
  no line is dropped, consistent with the anti-goals.)*
- **Explicit display commands.** Input lines such as `]box on`,
  `]Boxing on` or `]Display …` are run as user commands, and an explicit
  `]box` setting overrides the style inferred from printed output for the
  rest of the page. *(Builder's call.)*

A block is:

- **syntax / listing**: no candidate input lines. Not split, not run. Logged
  as a summary count only (no per-block entry), so the log is not flooded.
- **session**: at least one input line. Considered for splitting and run.
- **ambiguous**: the split cannot be decided without guessing. Logged with
  the reason, not split. There are two kinds:
  - *Structural*, never run: a `∇` definition in session-editor notation
    (`∇` opener, `[n]` numbered body lines, closing `∇`), including
    multi-line dfns written that way (reason `del-definition`; decided by
    the user: leave these alone for now and flag them); a `:Class`,
    `:Namespace` or `:Interface` script in the same block as session lines
    (reason `script`); input lines at an odd indent (reason `indent`).
    *(`script` and `indent` are the builder's call.)* `∇` appearing in
    output, such as a `⎕VR` listing, is ordinary output and not a reason.
  - *Indent*, run: an output line indented 6 or more spaces (a
    right-aligned number looks like input). These are run as if the
    6-space lines were input; if the output matches, the split is
    confirmed and the block is no longer ambiguous.

  Later blocks on a page that depend on a structural-ambiguous block will
  typically fail and get `not-run` (reason `dependency`). That loss of
  coverage is accepted for now.

### Running a page

One `dyalogscript DYALOG_INITSESSION=1` process per page, under a hard
timeout (the `~/.claude/scripts/dyalog-run.sh` pattern: kill the process
group). The page's session blocks are passed in order; state persists across
the blocks of a page and is fresh for each page (`⎕IO←1`, `⎕ML←1`,
`⎕PW←80` unless the page sets them).

For each input line the harness:

1. Chooses the display style for the block from the printed output: `┌→`,
   `↓` or `∊` markers mean `]box on -style=max`, other box-drawing means
   `]box on`, none means `]box off`.
2. Evaluates the line with `85⌶`. Output written during evaluation (`⎕←`,
   `⍞←`) is captured in order with the displayed result.
3. On error, produces the session's error display from `⎕DMX`.
4. Writes a sentinel after each line so Python can attribute output to the
   line that produced it.

### Comparing

Before comparison, trailing whitespace is removed from every line and
trailing blank lines from each line's output; nothing else is normalised.
Each block gets one verdict:

- `match`: every input line's output equals the printed output.
- `differs`: the example ran without error here and the output differs, or
  the printed output shows an error and the actual one differs. This is
  the flag the reviewer acts on.
- `not-run`: the example could not run meaningfully here, with a reason:
  an error that the printed output does not show (missing file, Windows-only
  facility, .NET), the use of a volatile facility (`?`, `⎕AI`, `⎕TS`, `⎕DL`,
  `⎕WA` and similar, from a list in the tool), or a dependency on an
  earlier block that did not run.
- `timeout`: the page timed out; blocks not yet reached are not judged.

### Proposing splits

A session block is split when either:

- **confirmed**: it ran, and taking the 6-space lines as input reproduced
  the remaining lines as their output (verdict `match`); or
- **unambiguous**: it did not run or differs, but no output line could be
  mistaken for input (no output line indented 6 or more spaces) and no line
  is of uncertain role.

The split writes each run of consecutive input lines, without the 6-space
indent, as an `apl` block, and the output that follows it as a `text` block.
Inputs with no output stay together in one `apl` block. A split never drops
or adds a non-blank line; Python checks this before writing the proposal.
A block whose verdict is `differs` still gets its split proposed (from the
printed output, not the actual output); the two are separate decisions for
the reviewer.

## The log

One YAML file (`pyyaml` is already a dependency), one entry per session or
ambiguous block, ordered by page and line:

```yaml
- page: language-reference-guide/primitive-functions/index-generator.md
  line: 24
  sha256: 3f9a…
  verdict: match            # match | differs | not-run | timeout
  reason: null              # for not-run, timeout, ambiguous
  split: confirmed          # confirmed | unambiguous | ambiguous | none
  decision: pending         # reviewer sets apply | skip
  original: |
    ```apl
          ⍳5
    1 2 3 4 5
    ```
  proposed: |
    ```apl
    ⍳5
    ```

    ```text
    1 2 3 4 5
    ```
  actual: null              # actual output, for differs
```

The reviewer changes `decision`. The later apply step replaces `original`
with `proposed` for each `apply` entry, after checking `sha256` against the
page, and refuses an entry whose block has changed. Reruns of the tool carry
existing decisions forward by `page` and `sha256`, so review work survives
a rerun, but only while `proposed` and `split` are unchanged: if a rerun
proposes something different for the same block, its decision resets to
`pending` so no unreviewed change is applied. *(Builder's call.)*

YAML literal blocks keep multi-line examples readable; text that YAML cannot
hold as a literal (for example lines with trailing spaces) may be written
quoted. Fidelity wins over readability: the tool reads the log back after
writing and checks every `original` and `proposed` round-trips exactly.

A short summary (counts per verdict and split kind, per page and overall)
is printed to stdout.

## Interface

```
uv run python tools/cutover/code-blocks.py [--guide language-reference-guide]
    [--log code-blocks.yml] [--page GLOB] [--no-run] [--timeout SECONDS]
```

`--no-run` skips execution (splits only where unambiguous). `--page`
restricts to matching pages for iteration. `dyalogscript` is found on
`PATH` or via `DYALOGSCRIPT`; without it the tool runs as `--no-run` and
says so.

## Constraints

- Python 3.14, run with `uv`, as the rest of `tools/`.
- Must not write under `zensical/` or the source tree.
- Dyalog 21.0 via `dyalogscript`; results are as this platform (macOS)
  produces them, and the log records the Dyalog version and platform.
- Leaves no interpreter running: every `dyalogscript` run is under a hard
  timeout that kills the process group.

## Testing

Unit tests (pytest, `tools/cutover/test_code_blocks.py`) for block finding,
classification, the split rules, the no-lost-lines check and YAML
round-tripping, using small fixture pages. Harness tests that need
`dyalogscript` are skipped when it is not installed. The tool is then run
over the whole guide, and a sample of each verdict and split kind is
checked by hand.

## Left to the builder

- The harness's sentinel format and how Python parses it.
- The exact list of volatile facilities, kept as a constant in the tool.
- How the harness reconstructs the session's error display from `⎕DMX`.
- How `code-blocks.py` (a hyphenated name) is imported by its tests.
