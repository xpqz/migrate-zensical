# Code-blocks Review Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `tools/cutover/code-blocks.py` finds the `apl` blocks in one guide, proposes input/output splits, validates session examples under `dyalogscript`, and writes a reviewable YAML log, without touching the corpus.

**Architecture:** Pure-Python parsing, classification and splitting; one `dyalogscript DYALOG_INITSESSION=1` run per page drives an APL harness (`tools/cutover/code-blocks-harness.apls`) through a JSON request file and sentinel-delimited stdout; Python judges each block and writes the log.

**Tech Stack:** Python 3.14, uv, pytest 9.2.0, PyYAML, Dyalog 21.0 `dyalogscript`.

**Spec:** `design/superpowers/specs/2026-11-25-code-blocks-design.md`

## Global Constraints

- Never write under `zensical/` or the source monorepo; the only output is the log file and stdout.
- Scope: `zensical/docs/<guide>/`, guide default `language-reference-guide`.
- Only fences whose info string is exactly `apl`; indented fences are skipped with reason `nested`.
- Session indent: exactly six characters drawn from U+0020 and U+00A0.
- `dyalogscript` from `$DYALOGSCRIPT`, else `PATH`; launched as `dyalogscript DYALOG_INITSESSION=1 <harness> <request.json>` with `stdin=DEVNULL`, `start_new_session=True`, and the whole process group killed on timeout (default 60 s per page). No interpreter may outlive the tool.
- Per page: fresh interpreter, `⎕IO←1`, `⎕ML←1`, `⎕PW←80`.
- Normalisation before comparing: strip trailing whitespace per line, drop trailing blank lines per input line's output. Nothing else.
- A split never drops or adds a non-blank line.
- Verdicts: `match | differs | not-run | timeout`; splits: `confirmed | unambiguous | ambiguous | none`; decisions: `pending | apply | skip`.
- Commit messages never mention Claude, AI or carry Co-Authored-By trailers.

## Review Focus

1. **An example that ends the interpreter** (`⎕OFF` on `system-functions/off.md`, or `)OFF`): the rest of the page must still be judged, not the whole page lost. Harness treats `⎕OFF`/`)` system commands as not-run (reason `system-command`). Test in Task 4.
2. **An example that waits for input** (`⍞`, `⎕` input): must not hang the tool; stdin is `/dev/null` and the page timeout applies. Test in Task 4.
3. **Output lines with trailing spaces or whitespace-only lines**: the log must round-trip them exactly. Test in Task 6.
4. **A character-matrix result whose rows begin with six spaces**: must be indent-ambiguous, run, and split only if confirmed. Test in Task 2 and Task 5.
5. **An unclosed `apl` fence at end of file, or a fence closed by a longer fence**: the unclosed one is ignored; the longer close is accepted. Test in Task 1.

---

### Task 1: Finding blocks

**Files:**
- Create: `tools/cutover/code-blocks.py`
- Create: `tools/cutover/test_code_blocks.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Block: page: str; line: int; sha256: str; text: str; body: list[str]; nested: bool` — `line` is the 1-based line of the opening fence; `text` runs from opening to closing fence inclusive, newline-terminated; `body` excludes the fences; `sha256` is hex of `text` UTF-8.
  - `find_blocks(page: str, markdown: str) -> list[Block]`
  - Tests import the module with `importlib.util.spec_from_file_location("code_blocks", <path>)` in a module-level fixture `cb`.

- [ ] **Step 1: Write failing tests** `test_find_blocks_returns_apl_blocks_only` (a page with ```` ```apl ````, ```` ```c ```` and a bare fence yields one Block with `line`, `body` and `sha256 == hashlib.sha256(text.encode()).hexdigest()`), `test_find_blocks_marks_indented_fences_nested`, `test_find_blocks_ignores_unclosed_fence`, `test_find_blocks_accepts_longer_closing_fence`.
- [ ] **Step 2: Run** `uv run pytest tools/cutover/test_code_blocks.py -q` — expect FAIL (module missing).
- [ ] **Step 3: Implement** `Block` and `find_blocks` in `code-blocks.py` (module docstring states the tool's purpose and that it never writes the corpus).
- [ ] **Step 4: Run** the tests — expect PASS.
- [ ] **Step 5: Commit** `git commit -m "Find apl code blocks for the code-blocks review tool"`

### Task 2: Classifying lines and blocks

**Files:**
- Modify: `tools/cutover/code-blocks.py`
- Modify: `tools/cutover/test_code_blocks.py`

**Interfaces:**
- Consumes: `Block`.
- Produces:
  - `Role = Literal["input", "output", "blank"]`
  - `@dataclass class Classification: kind: Literal["listing", "session", "ambiguous"]; reason: str | None; roles: list[Role]; nbsp: bool; runnable: bool; style: Literal["max", "min", "off"]`
  - `classify(block: Block) -> Classification`
  - `strip_indent(line: str) -> str` — removes the six-character session indent.
- Rules (from the spec): a line with the session indent is `input`, except the repeat line of an error triple (line matching `^[A-Z ]*ERROR\b` at column 0, followed by a session-indented line, followed by a line indented ≥6 containing only spaces and `∧`/`^`), whose repeat and caret are `output`. Reasons and `runnable`: `del-definition` (any input line whose stripped text starts with `∇`, or a `[n]` line at column 0 after an input line ending in `{` or starting with `∇`) → ambiguous, not runnable; `script` (a column-0 line starting `:Class`, `:Namespace` or `:Interface` in a block with input) → ambiguous, not runnable; `indent` (a non-blank line indented 1–5 or 7+ characters before the first output, i.e. an input at an odd indent) → ambiguous, not runnable; `indented-output` (an output line indented ≥6) → ambiguous, runnable. No input lines → `listing`. `style`: `max` if output contains `┌→`, `┌↓` or a line starting `└∊`/`↓`; `min` if output contains any of `┌┬┐├┼┤└┴┘`; else `off`.

- [ ] **Step 1: Write failing tests**, each asserting `kind`, `reason`, `runnable` and, where relevant, `roles`/`style`/`nbsp`: `test_classify_syntax_line_is_listing` (`R←⍳Y`), `test_classify_session_example` (`      ⍳5` / `1 2 3 4 5` → roles `["input","output"]`), `test_classify_nbsp_indent_counts_as_input` (`" "*5+" ⍳5"` → input, `nbsp=True`), `test_classify_error_triple_is_output` (`      1÷0` / `DOMAIN ERROR: Divide by zero` / `      1÷0` / `       ∧` → roles `["input","output","output","output"]`), `test_classify_del_definition_is_ambiguous` (`      ∇ R←foo Y` / `[1] R←Y` / `      ∇`), `test_classify_dfn_with_line_numbers_is_ambiguous` (`      f←{` / `[1] ⍵` / `[2] }`), `test_classify_del_in_output_is_not_a_reason` (`      ⎕VR 'foo'` / `     ∇ R←foo` / `[1]  R←1` / `     ∇` → session), `test_classify_script_is_ambiguous`, `test_classify_odd_indent_is_ambiguous`, `test_classify_char_matrix_with_six_space_rows_is_indented_output` (`      ↑'      ab' 'cd'` / `      ab` / `cd` → ambiguous, `indented-output`, runnable), `test_classify_style_max_min_off`.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement** `classify` and `strip_indent`.
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `git commit -m "Classify code-block lines as input or output"`

### Task 3: Proposing splits

**Files:**
- Modify: `tools/cutover/code-blocks.py`
- Modify: `tools/cutover/test_code_blocks.py`

**Interfaces:**
- Consumes: `Block`, `Classification`, `strip_indent`.
- Produces:
  - `split_block(block: Block, cls: Classification) -> str` — the replacement Markdown: each run of consecutive input lines (indent stripped, blank lines between them dropped) as an ```` ```apl ```` block, the output following it as a ```` ```text ```` block, blocks separated by one blank line, trailing blank lines of each output run dropped, newline-terminated.
  - `check_no_lost_lines(original: Block, proposed: str) -> None` — raises `SplitError(ValueError)` unless the multiset of non-blank lines (inputs compared after `strip_indent`) is equal.
  - `split_kind(cls: Classification, verdict: str | None) -> Literal["confirmed", "unambiguous", "ambiguous", "none"]` — `none` for listings; `ambiguous` for not-runnable ambiguous blocks, and for `indented-output` blocks whose verdict is not `match`; `confirmed` when verdict is `match`; otherwise `unambiguous`.

- [ ] **Step 1: Write failing tests** `test_split_simple_example` (exact expected string for `      ⍳5` / `1 2 3 4 5`), `test_split_groups_consecutive_inputs`, `test_split_keeps_error_triple_in_text_block`, `test_split_strips_nbsp_indent`, `test_split_drops_trailing_blank_output_lines`, `test_check_no_lost_lines_rejects_dropped_line`, `test_split_kind_table` (parametrised over the cases above).
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement** the three functions and `SplitError`.
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `git commit -m "Propose apl/text splits for session examples"`

### Task 4: The APL harness and page runner

**Files:**
- Create: `tools/cutover/code-blocks-harness.apls`
- Modify: `tools/cutover/code-blocks.py`
- Modify: `tools/cutover/test_code_blocks.py`

**Interfaces:**
- Produces:
  - Request file (JSON, UTF-8): `{"pw": 80, "blocks": [{"id": int, "style": "max"|"min"|"off"|null, "lines": [str]}]}` — `lines` are input lines with the indent stripped; `style: null` means leave the current `]box` setting.
  - Harness stdout protocol: before each line `⍝CB-BEGIN <id> <index>`, after it `⍝CB-END`; Python converts CR to LF.
  - `@dataclass class LineResult: output: list[str]; error: bool; skipped: str | None`
  - `@dataclass class RunBlock: id: int; style: str | None; lines: list[str]`
  - `run_page(blocks: list[RunBlock], dyalogscript: str, timeout: float = 60.0) -> tuple[dict[int, list[LineResult]], bool]` — results keyed by block id (blocks not reached are absent); the bool is True if the run timed out or the interpreter exited early.
  - `find_dyalogscript() -> str | None`; `dyalog_version(dyalogscript: str) -> str`.
- Harness behaviour: its own names live in `⎕SE.CB`; user lines execute in `#` with `⎕IO←1`, `⎕ML←1`, `⎕PW←pw` set once. Per block, set `]box` per `style` (`max` → `box on -style=max`, `min` → `box on -style=min`, `off` → `box off`). Per line: a line starting `]` runs via `⎕SE.UCMD` and displays a non-empty result; a line starting `)` or containing `⎕OFF` is skipped with `skipped="system-command"`; otherwise `1(85⌶)line` displays a non-shy result with `⎕←`, error 85 means shy (no display), and any other error prints the session display: `EM` (plus `: ` and `Message` when non-empty), the line at six-space indent, and the caret line with `∧` under the error position.
- Python side: write the request to a temp file, run with the Global Constraints' launch rules, parse sentinels, kill the process group on timeout.

- [ ] **Step 1: Write failing tests** (skipped via `pytest.mark.skipif(find_dyalogscript() is None)`): `test_run_page_displays_non_shy_result` (`2+3` → `["5"]`), `test_run_page_suppresses_shy_result` (`x←5` → `[]`, then `x` → `["5"]` in the same block, proving state), `test_run_page_state_carries_across_blocks`, `test_run_page_error_display` (`1÷0` → `["DOMAIN ERROR: Divide by zero", "      1÷0", "       ∧"]`, `error=True`), `test_run_page_box_max` (`2 2⍴1 'ab' (1 2) 3` with style `max` → first line `┌→───────────┐`), `test_run_page_user_command` (`]box on` output non-empty), `test_run_page_skips_off` (`⎕OFF` then `1+1` → first skipped `system-command`, second `["2"]`), `test_run_page_input_does_not_hang` (`⍞` with `timeout=10` returns within 15 s), `test_run_page_timeout_kills_process` (`{∇⍵}0`-style loop with `timeout=5` → timed-out flag True, and no `dyalog` child left: `os.killpg` target group no longer exists).
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement** the harness and `run_page`, `find_dyalogscript`, `dyalog_version`.
- [ ] **Step 4: Run** — expect PASS. Also run `pgrep -fl 'dyalog.*-script'` — expect no output.
- [ ] **Step 5: Commit** `git commit -m "Run code-block examples through a dyalogscript harness"`

### Task 5: Judging blocks

**Files:**
- Modify: `tools/cutover/code-blocks.py`
- Modify: `tools/cutover/test_code_blocks.py`

**Interfaces:**
- Consumes: `Block`, `Classification`, `LineResult`.
- Produces:
  - `VOLATILE: frozenset[str]` — initially `{"?", "⎕AI", "⎕TS", "⎕DL", "⎕WA", "⎕RL", "⎕SI", "⎕LC", "⎕TID", "⎕TNUMS", "⎕DMX"}`; matched in input lines outside quotes and comments.
  - `expected_outputs(block: Block, cls: Classification) -> list[list[str]]` — printed output per input line.
  - `judge(block: Block, cls: Classification, results: list[LineResult] | None, prior_unrun: bool, timed_out: bool) -> tuple[str, str | None, list[str] | None]` — `(verdict, reason, actual)`. Rules from the spec: no results and `timed_out` → `timeout`; not runnable → `not-run` with the classification's reason; any `skipped` → `not-run` reason `system-command`; volatile facility → `not-run` reason `volatile`; an error the printed output does not show → `not-run`, reason `dependency` if `prior_unrun` else `error`; all outputs equal after normalisation → `match`; else `differs`. `actual` is the joined actual output for `differs` and `not-run` with reason `error`/`dependency`, else `None`.

- [ ] **Step 1: Write failing tests** with hand-built `LineResult`s (no interpreter): `test_judge_match`, `test_judge_differs`, `test_judge_trailing_whitespace_is_not_a_difference`, `test_judge_unexpected_error_is_not_run`, `test_judge_unexpected_error_after_unrun_block_is_dependency`, `test_judge_expected_error_matching_is_match`, `test_judge_volatile_is_not_run` (`?6` and `⎕TS`, but not `'?'` in a string or `⍝ ?` in a comment), `test_judge_timeout`, `test_judge_indented_output_confirmed_by_match` (Review Focus 4: the char-matrix case with matching results gives `match`, and `split_kind` gives `confirmed`).
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement** `VOLATILE`, `expected_outputs`, `judge`.
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `git commit -m "Judge code-block results against the printed output"`

### Task 6: The log

**Files:**
- Modify: `tools/cutover/code-blocks.py`
- Modify: `tools/cutover/test_code_blocks.py`

**Interfaces:**
- Produces:
  - Log shape: `{"meta": {"guide", "dyalog", "platform", "generated"}, "entries": [entry]}`; entry keys in order `page, line, sha256, verdict, reason, split, nbsp, decision, original, proposed, actual`. `proposed` is `None` unless split is `confirmed` or `unambiguous`.
  - `write_log(path: Path, meta: dict, entries: list[dict]) -> None` — multi-line strings as YAML literal blocks where PyYAML allows, quoted otherwise; reads the file back and raises `LogError` if any entry's `original`/`proposed`/`actual` differs.
  - `read_log(path: Path) -> tuple[dict, list[dict]]`
  - `carry_decisions(old: list[dict], new: list[dict]) -> list[dict]` — copies `decision` from an old entry with the same `page` and `sha256` only when `proposed` and `split` are equal; otherwise `pending`.

- [ ] **Step 1: Write failing tests** `test_log_round_trips_multiline_text`, `test_log_round_trips_trailing_spaces_and_blank_lines` (Review Focus 3: `"   \n1 2  \n\n"` survives), `test_log_uses_literal_blocks_for_plain_text` (file contains `original: |`), `test_carry_decisions_keeps_matching`, `test_carry_decisions_resets_when_proposal_changed`, `test_carry_decisions_drops_vanished_blocks`.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement** `write_log`, `read_log`, `carry_decisions`, `LogError`.
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `git commit -m "Write and carry forward the code-blocks review log"`

### Task 7: Command line and a run over the guide

**Files:**
- Modify: `tools/cutover/code-blocks.py`
- Modify: `tools/cutover/test_code_blocks.py`
- Modify: `.gitignore` (add `/code-blocks.yml` only if the log is written to the repo root by default — see Step 3)

**Interfaces:**
- Consumes: everything above.
- Produces: `main(argv: list[str] | None = None) -> int` with options `--guide` (default `language-reference-guide`), `--log` (default `code-blocks.yml` in the current directory), `--page GLOB` (matched against the page path relative to the guide), `--no-run`, `--timeout SECONDS` (default 60), `--docs` (default `zensical/docs`, for tests). Without `dyalogscript`, behaves as `--no-run` and prints a note to stderr. Per page: find, classify, run runnable blocks in order (`RunBlock.style` is `None` once an earlier input line on the page started `]box` or `]boxing`, case-insensitive), judge (with `prior_unrun` True once an earlier block on the page was ambiguous-not-runnable, nested or `not-run`), split, emit entries for session and ambiguous blocks and for nested `apl` blocks (verdict `not-run`, reason `nested`, split `none`); carry decisions from an existing log; write the log; print counts per verdict and split kind to stdout. Exit 0 on success.

- [ ] **Step 1: Write failing tests** with a fixture docs tree in `tmp_path`: `test_main_no_run_writes_log_and_leaves_docs_untouched` (snapshot every file's bytes before and after), `test_main_listing_blocks_are_counted_not_logged`, `test_main_logs_nested_blocks_as_not_run`, `test_main_carries_decisions_on_rerun`, and (skipif no dyalogscript) `test_main_end_to_end_match_and_differs` (a page with one correct and one wrong example → verdicts `match` and `differs`).
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement** `main` and the `if __name__ == "__main__": sys.exit(main())` guard.
- [ ] **Step 4: Run** `uv run pytest tools/cutover/test_code_blocks.py -q` — expect PASS; then `uv run pytest -q` — expect the whole suite PASS.
- [ ] **Step 5: Run over the guide** `uv run python tools/cutover/code-blocks.py --log "$SCRATCH/code-blocks.yml"` — expect it to finish, `git status --short zensical` to print nothing, and `pgrep -fl 'dyalog.*-script'` to print nothing. Sample by hand at least three entries of each verdict and split kind; record surprises and fix any tool bug found (with a test) before committing.
- [ ] **Step 6: Commit** `git commit -m "Add the code-blocks review tool's command line"`
