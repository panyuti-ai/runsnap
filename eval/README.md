# Mutation experiment

Question: when a small code change slips past a project's own tests, does runsnap notice?

`mutation.py` takes a project, records two baseline snapshots of its unchanged package, then makes one mutation at a time (`<` → `<=`, `+` → `-`, `2` → `3`, …), runs the tests with recording on, and compares the snapshot with the baseline. Values that already differ between the two baselines (time, random) are ignored. `linecov.py` records which lines the unchanged tests execute, so mutants in code the tests never run are counted apart: no runtime tool can see those.

Run on 2026-11-02, Python 3.13, 60 mutants per project (seed 1):

| project | mutants | caught by tests | survived, line never run | survived, line run | runsnap: same input, different result | runsnap: calls appear/disappear only |
|---|---|---|---|---|---|---|
| humanize 4.16.0 | 60 | 49 | 0 | 11 | 2 | 0 |
| python-slugify 9.1.3 | 60 | 54 | 0 | 6 | 1 | 0 |
| tabulate 0.10.0 | 60 | 40 | 5 | 15 | 1 | 1 |
| toolz 1.2.0 | 60 | 45 | 2 | 13 | 0 | 2 |
| **total** | **240** | **188** | **7** | **45** | **4** | **3** |

What the numbers say:

- These four libraries are well tested: their own tests catch 188 of 240 mutants (78%).
- Of the 45 mutants the tests ran but missed, runsnap shows a different result for the same input in 4 cases, for example `slugify._modern_slugify("a's café", …)` returning `'aaaas'` instead of `'aaaasaaaca'`, and `humanize.time._quotient_and_remainder(7000000.0, 1000000.0, …)` returning `(7.0, 1)` instead of `(7.0, 0)`. In 3 more, only which calls happen changes.
- Reading the other 38 by hand, most change nothing that the tests' inputs can reach: version numbers, defaults of signatures that are never called, a Python 3.14 version check on 3.13, and boundaries such as `<` → `<=` where no test uses the boundary value. Comparing inputs and outputs cannot catch those; it also means this kind of experiment undercounts what a recorder adds on code with weaker tests.

The experiment also found two bugs in runsnap itself, both fixed: generators were skipped (now their yielded values are recorded), and two functions with identical code in different modules were recorded under one name.

Raw results per mutant: `results/*.json`.

# Agent refactor experiment

Question: when a coding agent refactors a file and the tests still pass, did the behavior really stay the same?

`agent_refactor.py` records two baselines of a project, gives a coding agent one prompt, records the tests again and compares. The prompt is the same for every agent:

> Refactor FILE to make it cleaner and easier to maintain: simplify logic, remove duplication, and use modern Python idioms. Keep the public API the same and do not change behavior. Only edit FILE. You can run the test suite with: …

Run on 2026-11-02 with Claude Code 2.1.316 (default model) and Codex CLI 0.166.0 (default model), one run per file and agent. `agent_summary.py` rebuilds the table from the saved patches and recordings.

| file | agent | lines changed | tests | same input, different result | calls appear/disappear |
|---|---|---|---|---|---|
| humanize `time.py` | Claude | 500 | pass | 0 | 1,028 |
| humanize `time.py` | Codex | 129 | pass | 0 | 680 |
| humanize `number.py` | Claude | 183 | pass | 0 | 0 |
| humanize `number.py` | Codex | 101 | pass | 0 | 0 |
| python-slugify `slugify.py` | Claude | 88 | pass | 0 | 0 |
| python-slugify `slugify.py` | Codex | 32 | pass | 0 | 0 |
| toolz `itertoolz.py` | Claude | 300 | pass | 1 | 10 |
| toolz `itertoolz.py` | Codex | 104 | pass | 1 | 10 |

Calls to helper functions an agent split out or inlined are listed separately and not counted (see `summary.json`).

What we found, after reading every flagged call:

- **No run broke anything that matters.** In 6 of 8 runs runsnap confirms that every recorded call, thousands per project, returns what it returned before.
- **Two return types changed, and no test checks them.** Both agents, in different functions, changed what an empty input returns: Claude's `interpose('a', [])` now returns an `itertools.chain` instead of a `list_iterator`; Codex's `merge_sorted()` returns a `tuple_iterator` instead of a `list_iterator`. Both are empty iterators, so callers that only iterate see no difference; code that checks the type would.
- **Calls that appear or disappear were all harmless here.** In humanize `time.py` both agents compare `Unit` values more often (Claude also returns early for "now" before translating "%s ago"), with the same results. In toolz, one test merges a module's namespace (which now holds the new helpers) and one reads the working directory, which differed between runs: that one is noise runsnap cannot tell from a change.
- The first pilot also exposed two false alarms in runsnap, both fixed before the runs above: thousands of "new" calls from helpers the agent split out, and "missing" calls in a test that hit the 20,000-calls-per-test recording limit.

So on well-tested libraries these agents refactor carefully, and the value of the recording here is evidence: a reviewer can see that the refactor kept behavior, call by call, and gets pointed at the two places where it did not.

Raw results: `results/agents/` (patches, HTML reports, per-run JSON, `summary.json`).
