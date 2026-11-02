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
