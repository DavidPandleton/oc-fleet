# Benchmark fixture repository

A tiny, real Python project used by `benchmarks/run_benchmark.py` when a
scenario needs a Git repository to create worktrees from or a verify
command with something to run. It is a `src` layout with one module and
one test file, kept deliberately small so the measured cost is the
orchestrator's, not the fixture's.

The tests pass as shipped:

```bash
cd benchmarks/fixture_repo
python3 -m pytest -q
```

Do not add anything here that the benchmark does not need. A fixture that
grows becomes a second project to maintain.
