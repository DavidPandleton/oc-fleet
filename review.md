# Cross-model review: `orchestrator.py` / `test_orchestrator.py`

> **Status per 2026-09-22.** Dokumen ini adalah snapshot review pada saat
> ditulis, dan sengaja tidak ditimpa - temuan aslinya berharga sebagai
> catatan. Tiga hal di bawah sudah diperbaiki setelahnya, jadi baca
> verdict-nya dengan koreksi ini:
>
> - **#4 (crash + abandoned work): SUDAH DIPERBAIKI.** `dispatch` dan
>   `status` sekarang memakai satu konstanta `RUN_ERRORS = Exception`.
>   Sebelumnya `dispatch` pakai `API_ERRORS` yang lebih sempit dan bocor.
>   Direproduksi dan diverifikasi tertutup. Test gap-nya juga ditutup
>   (RuntimeError, TypeError, AttributeError, Exception generik).
> - **Cancel sebelum retry: SUDAH ADA.** `_cancel_session` dipanggil di
>   `_finish_attempt` sebelum retry (orchestrator.py:342).
> - **"fleet.py exposes no cancel API at all": SUDAH TIDAK BENAR.**
>   `Fleet.cancel` ada di fleet.py:175, tri-state True/False/None.
> - **"Deadline dicek sebelum status baru dibaca": SUDAH TIDAK BENAR.**
>   `_poll_running` memeriksa `outcome` lebih dulu (baris 312), dan baru
>   memeriksa deadline kalau outcome masih None (baris 315). Sesi yang
>   selesai pada poll yang melewati deadline tetap tercatat sukses.
> - **"Timeout precision is poll-granular": SUDAH DIPERBAIKI.** Loop
>   tidur sampai deadline terdekat, bukan satu `poll_interval` penuh.
>   Terverifikasi: timeout=0.5 dengan interval 3.0 selesai di 0.50s,
>   overshoot 0.00s (sebelumnya sampai 3s, 6x lipat).
>
> Masih terbuka: tidak ada riwayat per-attempt di `results()`.

**Verdict asli di bawah ini dipertahankan apa adanya.**

Scope: the six failure modes requested. Every claim below was checked against the
source and, where marked **(probe)**, reproduced by executing the code against the
real `orchestrator.py` (commit `c11c9b7`, branch `master`; suite: 28 tests, all pass).

---

## 1. Race conditions / `max_parallel`

**Verdict: handled correctly. No defect.**

There are no threads, no `threading`, no `concurrent.futures` anywhere in the file.
`run()` (orchestrator.py:151-170) is a single-threaded cooperative poll loop; the
parallelism is concurrent *sessions on the fleet server*, not concurrency in the
Python process, so there is no data race by construction.

- Cap enforced at orchestrator.py:209-210 (`if len(running) >= self.max_parallel: break`).
- The "counter" is `len(running)`; entries are removed only by the `running.pop(tid)`
  at orchestrator.py:237, which happens *before* `_finish_attempt` decides
  fail/retry. A task that fails mid-flight therefore frees its slot, and a retried
  task re-acquires a slot only on a later sweep, with `attempts` incremented exactly
  once per dispatch (orchestrator.py:217). No double count, no leak.
- **(probe)** fail-then-retry with `max_parallel=2`: attempts=2, slot reused, no
  duplicate entry for the same task id.

Test coverage: `test_parallel_branch_execution_up_to_max_parallel`
(test_orchestrator.py:237-261) asserts the cap held at first status poll
(`dispatch_count_at_first_status == 2`) and that dispatch overlapped. Would catch a
cap regression. The fail-mid-flight slot accounting is covered indirectly by
`test_dependents_skipped_when_dependency_fails` (b succeeds while a is failing).

Minor (not defects): freed slots are only reusable on the *next* sweep because
`_dispatch_ready` (line 163) runs before `_poll_running` (line 167) — one
`poll_interval` of latency per wave; and `max_parallel=0` is silently clamped to 1
(line 61) rather than rejected.

## 2. Cycle detection

**Verdict: handled correctly for all three asked cases. One test gap.**

- **Self-loop (A depends_on A):** cannot even be *added* — `add()` checks deps
  against already-added tasks (orchestrator.py:82-84) and the task itself is not in
  `self._tasks` until line 85, so `Task(id="a", depends_on=["a"])` raises
  `ValueError: task 'a' depends on unknown task 'a'`. If `depends_on` is mutated
  after `add()` (`orch._tasks["a"].depends_on = ["a"]`), `validate()` still catches
  it via `_find_cycle`: **(probe)** raises `dependency cycle detected: a -> a`
  (orchestrator.py:95-97, 113-114).
- **3-node cycle:** caught — test_orchestrator.py:138-149 asserts
  `a -> b -> c -> a`; **(probe)** confirms.
- **Cycle reachable only from a node not added to the graph:** deps on never-added
  ids are impossible to create through `add()` (lines 82-84) and are re-checked in
  `validate()` (lines 91-94); post-add mutation introducing such a dep is also
  caught. Unreachable-from-any-root cycles are impossible to miss because the DFS
  iterates *every* task id as a root (line 103). **(probe)** a mutated `x <-> y`
  cycle is caught: `dependency cycle detected: x -> y -> x`.

`_find_cycle` itself (lines 99-126) is correct: GRAY = on current path, the
iterator is resumed where it left off (line 110 keeps the iterator in the stack
frame), BLACK deps are skipped, and `path.index(dep)` is safe because a GRAY node is
always on `path`.

Test gap (would *not* be caught by the suite if it regressed): there is **no
self-loop test**. The 2-node and 3-node cycle tests (lines 127-149, 167-175) exist,
so a regression limited to the `dep == root` self-edge case (line 113) or to the
`add()` guard (lines 82-84) would pass CI.

## 3. Dependent skipping (transitive)

**Verdict: handled correctly. Not one-level-only.**

`_skip_blocked` (orchestrator.py:184-199) marks direct dependents skipped, and
because `"skipped"` is itself a blocking status (line 189), the skip propagates one
level per sweep until the whole downstream subgraph is skipped.
**(probe)** chain a→b→c→d with `a` terminally failed: results
`{a: failed, b: skipped, c: skipped, d: skipped}`, and only `s-0` was ever
dispatched.

Test coverage: `test_dependents_skipped_when_dependency_fails`
(test_orchestrator.py:316-340) covers two levels (c direct, d transitive) and
asserts neither was dispatched. Would catch a one-level-only regression for the
tested shape.

Minor (latency, not correctness): the skip is observed on the sweep *after* the
failure, since `run()` calls `_skip_blocked` (line 162) before `_poll_running`
(line 167) — each downstream level costs one extra `poll_interval`.

## 4. Timeout: waiter raises while other tasks run

**Verdict: DEFECT — crash + abandoned work (not a hang).**

`API_ERRORS = (OSError, ValueError, KeyError)` (orchestrator.py:22) is the only
guard, at orchestrator.py:242-245. Real `fleet.status()` goes through
`urllib.request.urlopen` (fleet.py:73-83), whose failure set is larger than that
tuple:

- `urllib.error.HTTPError` (incl. 401), `URLError`, `socket.timeout`,
  `RemoteDisconnected` → all `OSError` subclasses → caught. A mid-run 401 degrades
  to keep-polling → eventual timeout. OK.
- **`http.client.HTTPException` family (`IncompleteRead`, `BadStatusLine`, …) and
  `json.JSONDecodeError`** (JSONDecodeError happens to be a `ValueError`, but the
  HTTPException family is not) → **not caught**.

**(probe)** `fleet.status` raising `http.client.IncompleteRead` with two tasks
in flight: `run()` crashed with `IncompleteRead(7 bytes read)` and results left at
`{'a': 'running', 'b': 'running'}`. Consequences: remaining tasks are never
polled, never marked failed/skipped, never skipped downstream, and their sessions
are orphaned on the server. Answering the question directly: the remaining tasks do
not hang the process — the process crashes — but anything that catches the
exception and inspects `results()` sees tasks stuck in `running` forever.

The same hole exists on the dispatch side: `pending.remove(tid)` and the
`running` bookkeeping (orchestrator.py:213-216) happen *before* the guarded
`dispatch` call (lines 220-227), so a non-`API_ERRORS` exception from dispatch also
abandons a task marked `running`.

Test coverage: **none.** `test_dispatch_error_marks_task_failed`
(test_orchestrator.py:516-524) only exercises `OSError`. No test feeds a
non-`(OSError, ValueError, KeyError)` exception into `status()`/`dispatch()`, so
the suite would not catch this.

Related timeout findings (minor):

- Deadline is checked *before* the fresh status is read (orchestrator.py:239-250),
  so a session that completes successfully on the very poll that expires the
  deadline is recorded as `failed (timed out)` and its success is discarded.
- Timeout precision is poll-granular: actual runtime ∈ [timeout, timeout +
  poll_interval]. With the default 3s interval and `timeout=1`, overshoot is 3x.
- **Timed-out / retried-over sessions are never cancelled** — `fleet.py` exposes no
  cancel API at all. After a timeout with `retries > 0`, the old session keeps
  consuming a fleet slot and may keep writing to the shared `workdir` while the
  retry's new session does the same — duplicate concurrent side effects. No test
  covers this (it is a design gap, but a real operational defect).

## 5. `Fleet.dispatch` raising

**Verdict: handled for the declared error types; same blast-radius gap as #4.**

For `OSError`/`ValueError`/`KeyError` the orchestrator does not crash:
orchestrator.py:224-227 sets `session_id = None` and records
`last_text = "dispatch failed: …"`, the task enters `running` (lines 228-232), and
the next sweep's `_poll_running` pops it (session_id is None, line 239) and routes
it into `_finish_attempt`, i.e. fail or retry. **(probe)** dispatch raises
`OSError("401 unauthorized")` once with `retries=1`: attempt 2 dispatches
successfully, final state `succeeded, attempts=2, session_id=s-0`. A 401 during
dispatch therefore marks the task failed and the run continues — exactly as asked.

Test coverage: `test_dispatch_error_marks_task_failed` (lines 516-524) covers the
no-retry `OSError` case (status `failed`, `session_id None`, last_text). It does
*not* cover retry-after-dispatch-failure, and it does not cover non-`API_ERRORS`
exceptions (which crash, per finding #4).

Cosmetic defect: when a dispatch failure exhausts retries, `_finish_attempt`
prints `failed: a (timed out after 1800s)` (orchestrator.py:277-278) because
`outcome is None` — the task failed in microseconds, not 1800s. **(probe)**
confirms the misleading line. The record itself is correct (`last_text` says
`dispatch failed: …`).

## 6. Result integrity after a retry that succeeded on attempt 2

**Verdict: `attempts` handled correctly. Two semantic caveats worth knowing.**

`attempts` is incremented exactly once per dispatch (orchestrator.py:217) and is
never reset, so attempt-2 success reports `attempts == 2`.
**(probe)** and `test_retry_on_failure_then_success` (test_orchestrator.py:282-302,
assertion at line 298) both confirm; `test_retry_exhaustion…` (lines 304-314)
confirms `attempts == 3` for `retries=2`. The suite would catch a regression here.

Caveats (arguably intended, but undocumented in `results()`'s docstring):

- `session_id` (line 227) and `last_text`/`outcome` (lines 258-260) always reflect
  the **final attempt only**; the session id of the failed attempt is discarded —
  there is no per-attempt history, which hurts post-mortem debugging of fleet runs.
- `started_at` is set only on the first attempt (lines 218-219: `if rec["started_at"]
  is None`), so `duration` spans all attempts including the retry wait, while
  `session_id`/`outcome` describe only the last one — a mixed semantic in one row.

Also minor: `results()` returns the live internal dict (line 287), so a caller
mutating it corrupts orchestrator state; and `_poll_running` line 243
(`self.fleet.status(session_id) or {}`) plus line 248 means a transient `None`
status response replaces `last_state` with `{}`, silently dropping the cached
`last_assistant_text` (an outcome can never be lost this way, because a poll that
returns an outcome finishes the task immediately).

---

## Summary

| # | Failure mode | Verdict |
|---|---|---|
| 1 | Race conditions / max_parallel counter | Handled; no threads, counter correct |
| 2 | Cycle detection (self-loop, 3-cycle, unreachable) | Handled; suite lacks a self-loop test |
| 3 | Transitive dependent skipping | Handled (one level per sweep) |
| 4 | Waiter raises mid-run | **Defect**: non-`API_ERRORS` exceptions crash `run()`, tasks abandoned as `running`; untested |
| 5 | `dispatch` raises | Handled for OSError/ValueError/KeyError (incl. 401); non-API errors crash (#4); misleading "timed out" log |
| 6 | `attempts` after successful retry | Handled; session_id/duration reflect final attempt vs whole task (mixed semantics) |

Top fixes, in order: widen the guarded exception set to include
`http.client.HTTPException` (or catch `Exception` around `fleet.status`/`dispatch`
and treat it as a failed poll/attempt), add session cancellation on timeout/retry,
and add a self-loop test.
