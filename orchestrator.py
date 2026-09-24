"""DAG-based task orchestration on top of fleet.Fleet.

A Task is one unit of work: a prompt dispatched to an OpenCode session.
The Orchestrator executes a DAG of Tasks where a task only starts once
every task in depends_on has succeeded. Independent tasks run in
parallel with at most max_parallel sessions in flight. A failed task is
retried up to Task.retries times; once it ultimately fails, its
dependents are marked skipped while unrelated branches keep running.

Waiting on a session follows the same pattern as oc-fleet-wait.py:
poll fleet.status(session_id) every poll_interval seconds (3s by
default) until an outcome is set or the per-task timeout expires, so
the runner never blocks forever.
"""

from __future__ import annotations

import time
import inspect
from dataclasses import dataclass, field
from shlex import split as shell_split
import os
import subprocess

from contracts import VerificationResult
from failures import classify_failure
from fleet import Fleet
from ratelimit import ConcurrencyLimiter, RateLimiter



def run_verification(commands, workdir, timeout=300.0):
    """Run independent verification commands and capture bounded evidence.

    Commands are tokenized with ``shlex`` and executed without a shell. This
    intentionally does not support shell operators unless a future explicit
    opt-in is added; verification must be auditable, not an invisible agent
    prompt.
    """
    import subprocess

    commands = list(commands or [])
    result = VerificationResult(required=bool(commands), passed=True)
    for command in commands:
        entry = {
            "command": command,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
        }
        try:
            argv = shell_split(command)
            if not argv:
                raise ValueError("verification command is empty")
            completed = subprocess.run(
                argv,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            entry["returncode"] = completed.returncode
            entry["stdout"] = completed.stdout[-8000:]
            entry["stderr"] = completed.stderr[-8000:]
        except subprocess.TimeoutExpired as exc:
            entry["timed_out"] = True
            entry["stdout"] = (exc.stdout or "")[-8000:]
            entry["stderr"] = (exc.stderr or "")[-8000:]
        except (OSError, ValueError) as exc:
            entry["error"] = str(exc)[:2000]
        result.commands.append(entry)
        if entry.get("timed_out") or entry.get("returncode") != 0:
            result.passed = False
            break
    return result


FAILED_OUTCOMES = {"failed", "crashed", "error", "cancelled", "canceled"}


# Both call sites around a running session catch everything, for the same
# reason. The cost of a wrong guess is asymmetric: a crashed run strands
# live sessions in "running" that nothing will ever poll again, whereas a
# wrongly-tolerated error only marks one attempt failed.
#
# The network failure space is too wide to enumerate. ConnectionError and
# HTTPError are OSError subclasses, but http.client.HTTPException
# (IncompleteRead, BadStatusLine) is not - and a malformed response now
# leaves `dispatch` as a RuntimeError. Each of those, caught narrowly, used
# to take the whole run down.
#
# This was two narrower constants (API_ERRORS for dispatch, POLL_ERRORS for
# status). The dispatch one was the leak: it missed RuntimeError, so a bad
# server response killed a run and abandoned siblings as "running". One
# constant removes the chance of the two drifting apart again.
#
# Deliberately `Exception`, NOT `BaseException`. Ctrl-C raises
# KeyboardInterrupt and `sys.exit()` raises SystemExit, both BaseException
# subclasses; catching those would swallow a user's cancellation and turn
# it into "one attempt failed". Letting them through is correct - the cost
# is that a run interrupted this way leaves its live tasks marked
# "running", which is the right trade for a user-initiated stop.
#
# Do not widen this to BaseException.
RUN_ERRORS = Exception
# Kept as the name the polling path reads; identical by construction.
POLL_ERRORS = RUN_ERRORS
DISPATCH_ERRORS = RUN_ERRORS
# Interval polling. 3.0 dulu, dan itu terlalu agresif untuk provider
# yang membatasi konkurensi: dengan 4 task paralel, 3 detik berarti
# sekitar 80 request per menit hanya untuk polling - dan tiap request
# memakai slot konkurensi yang dibutuhkan agent untuk bekerja.
# Terukur di mesin ini: 81 respons 429 dan 17 kegagalan "Concurrency
# limit exceeded" ketika enam task berjalan bersamaan.
#
# 10 detik memberi ruang bernapas tanpa membuat respons terasa lambat:
# task yang benar-benar bekerja jarang berubah dalam 10 detik, dan
# deteksi macet tetap menangkap sesi berhenti jauh sebelum deadline.
POLL_INTERVAL = 10.0
# Seberapa sering mencetak satu baris progres untuk task yang masih jalan.
# Nilainya sengaja lebih besar dari POLL_INTERVAL: heartbeat ada untuk
# memberi tahu bahwa run masih hidup, bukan untuk menambah kebisingan tiap
# poll. Satu menit cukup untuk membedakan "masih bekerja" dari "menggantung"
# tanpa membanjiri log run yang panjang.
HEARTBEAT_INTERVAL = 60.0
# Batas konkurensi provider cutad. Ditemukan dari pesan 429 di log
# server: "You already have 15 active request(s); your plan allows 15".
# Disisakan satu slot supaya panggilan di luar oc-fleet tidak langsung
# ditolak.
#
# INI pembatas yang penting. Provider membatasi KONKURENSI, bukan
# laju: 15 request bersamaan. Membatasi konkurensi di sini menyerang
# penyebabnya langsung.
PROVIDER_CONCURRENCY = 14
# Batas 25 request/menit juga ada, tapi JANGAN dipakai sebagai
# throttle ketat. Diukur: membatasi 24 request pada 25/menit membuat
# operasi 0.1 detik menjadi 43 detik - 400x lebih lambat - karena
# 25/menit sama dengan 0.42/detik, dan itu menghukum pemakaian wajar.
#
# Batas laju hanya dijadikan jaring pengaman untuk lonjakan
# (burst), dengan kapasitas kecil supaya dispatch awal tidak
# menghabiskan kuota sekaligus. Nilai None mematikannya.
PROVIDER_RATE_PER_MINUTE = None
PROVIDER_BURST = 10


@dataclass
class Task:
    """One unit of work: a prompt dispatched to an OpenCode session."""

    id: str
    prompt: str
    workdir: str = "."
    model: str = "cutad/qwen3-8-flash-next"
    title: str = ""
    depends_on: list[str] = field(default_factory=list)
    retries: int = 0
    timeout: float = 1800
    # Model pengganti dipakai saat retry. Attempt 1 selalu memakai
    # `model`; attempt N>1 memakai `fallbacks[N-2]` bila ada, kalau
    # tidak memakai `model` lagi. Motivasi dari demo mandor 2026-09-24:
    # prompt yang sama gagal 2x di deepseek-v4-flash
    # (provider.invalid-request, content kosong) lalu sukses sekali
    # jalan di qwen3-8-flash-next. Retry buta dengan model yang sama
    # membuang waktu bila errornya berasal dari provider, bukan prompt.
    fallbacks: list[str] = field(default_factory=list)
    # Independent verification commands run after the agent succeeds.
    verify: list[str] = field(default_factory=list)
    verify_timeout: float = 300.0
    # Inject a bounded, typed summary of successful dependencies into prompt.
    handoff: bool = False
    # Explicit environment and local lifecycle commands.
    env: dict[str, str] = field(default_factory=dict)
    setup: list[str] = field(default_factory=list)
    teardown: list[str] = field(default_factory=list)
    # Bila True, orchestrator membuat git worktree baru dari `repo`
    # sebelum dispatch pertama dan memakai path itu sebagai workdir,
    # supaya agent paralel tidak menulis ke direktori yang sama.
    isolate: bool = False
    repo: str = ""
    # Wilayah tulis yang boleh disentuh agent, sebagai pola glob relatif
    # terhadap workdir, misalnya ["backend/**"]. Kosong berarti tidak ada
    # batasan (perilaku lama). Diperiksa setelah agent selesai: file yang
    # berubah di luar daftar ini dianggap pelanggaran boundary.
    #
    # Ini yang membuat kerja paralel benar-benar harmonis. Prompt bisa
    # meminta agent untuk "hanya sentuh backend", tapi tidak ada yang
    # menjaminnya; `owns` mengubah permintaan itu menjadi pemeriksaan
    # yang dilakukan fleet pada file yang benar-benar berubah.
    owns: list[str] = field(default_factory=list)


def model_for_attempt(task, attempt):
    """Model yang dipakai attempt ke-`attempt` (1-based).

    Attempt 1 selalu model utama. Attempt berikutnya memakai
    fallbacks berurutan; bila fallbacks habis, kembali ke model utama.
    """
    if attempt > 1 and task.fallbacks:
        idx = min(attempt - 2, len(task.fallbacks) - 1)
        return task.fallbacks[idx]
    return task.model


def exit_code_for_statuses(statuses):
    """Return the exit code for a set of task statuses.

    One implementation, used by both the live ``Orchestrator.run_status()``
    and the MCP adapter that reads statuses back from the store. The two
    used to carry separate copies of this rule and drifted: an empty run
    read as success in the MCP copy while the orchestrator's docstring
    said it must not.

    * 1 when nothing ran, or an agent failed, timed out, or was skipped;
    * 2 when any task failed verification (outranks 1);
    * 0 only when at least one task ran and every task met its bar.
    """
    seen = list(statuses)
    if not seen:
        return 1
    if any(status == "verification_failed" for status in seen):
        return 2
    unfinished = {
        "failed", "timed_out", "setup_failed", "skipped", "pending", "running",
    }
    return 1 if any(status in unfinished for status in seen) else 0


def _new_record():
    return {
        "status": "pending",
        "agent_status": None,
        "verification_status": None,
        "session_id": None,
        "outcome": None,
        "attempts": 0,
        "last_text": None,
        "started_at": None,
        "finished_at": None,
        "duration": None,
        "verification": {
            "required": False,
            "passed": None,
            "commands": [],
        },
        "artifacts": None,
        "boundary": None,
        "failure_class": None,
        "attempts_detail": [],
    }


def check_boundary(paths, owns):
    """Periksa apakah `paths` semuanya berada di dalam pola `owns`.

    `owns` kosong berarti tidak ada batasan. Pola dibandingkan memakai
    fnmatch terhadap path relatif yang dinormalisasi ke depan. Pola
    seperti "backend/**" juga dicocokkan sebagai prefiks direktori,
    karena fnmatch tidak memahami "**" secara khusus.

    Mengembalikan dict {"owns", "violations", "verdict"}; verdict bernilai
    None bila tidak ada pelanggaran.
    """
    from fnmatch import fnmatch

    if not owns:
        return {"owns": [], "violations": [], "verdict": None}

    def allowed(path):
        normalized = path.replace(os.sep, "/").lstrip("./")
        for pattern in owns:
            cleaned = pattern.replace(os.sep, "/")
            if fnmatch(normalized, cleaned):
                return True
            # "backend/**" harus mengizinkan "backend/a/b.py"; fnmatch
            # sendiri tidak menyeberangi "/", jadi bandingkan prefiksnya.
            prefix = cleaned[:-3] if cleaned.endswith("/**") else cleaned
            if normalized == prefix or normalized.startswith(prefix.rstrip("/") + "/"):
                return True
        return False

    violations = [p for p in paths if not allowed(p)]
    return {
        "owns": list(owns),
        "violations": violations,
        "verdict": "boundary_violation" if violations else None,
    }


def patterns_overlap(first, second):
    """Apakah dua pola glob `owns` bisa menyentuh file yang sama?

    Perbandingan glob secara umum tidak bisa diputuskan dengan tepat,
    jadi fungsi ini sengaja konservatif: lebih baik menolak dua pola
    yang sebenarnya disjoint daripada meloloskan tabrakan nyata.

    Aturannya, setelah normalisasi ke bentuk kanonik:

    - pola yang sama persis selalu tumpang-tindih;
    - satu pola yang merupakan prefiks direktori pola lain (setelah
      melepas "**" dan "/*") tumpang-tindih, mis. "src/**" vs "src/db.py";
    - selebihnya dianggap disjoint.
    """

    def canonical(pattern):
        cleaned = pattern.replace(os.sep, "/").strip()
        while cleaned.startswith("./"):
            cleaned = cleaned[2:]
        cleaned = cleaned.rstrip("/")
        for suffix in ("/**", "/*", "**", "*"):
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
                break
        return cleaned.rstrip("/")

    if first == second:
        return True
    a, b = canonical(first), canonical(second)
    if not a or not b:
        # Pola kosong (setelah normalisasi) berarti "semuanya", sehingga
        # bertabrakan dengan apa pun.
        return True
    if a == b:
        return True
    return a.startswith(b + "/") or b.startswith(a + "/")


class Orchestrator:
    """Run a DAG of Tasks over a Fleet, with retries and parallel branches.

    Statuses: pending, running, succeeded, failed, skipped.
    """

    def __init__(self, fleet=None, max_parallel=4, poll_interval=POLL_INTERVAL,
                 concurrency_limit=PROVIDER_CONCURRENCY,
                 rate_per_minute=PROVIDER_RATE_PER_MINUTE,
                 burst=PROVIDER_BURST, store=None, run_id=None,
                 event_sink=None, heartbeat_interval=HEARTBEAT_INTERVAL,
                 tool_timeout=None, prices=None, worktree_root=None):
        if fleet is None:
            fleet = Fleet()
        if int(max_parallel) < 1:
            raise ValueError("max_parallel must be >= 1, got %r" % (max_parallel,))
        self.max_parallel = int(max_parallel)
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        # Ambang macet per-tool untuk seluruh run. None = pakai default
        # Fleet.STUCK_AFTER_SECONDS. Task yang memang punya langkah lambat
        # bisa menaikkannya, task yang seharusnya cepat bisa menurunkannya.
        self.tool_timeout = None if tool_timeout is None else float(tool_timeout)
        # Cache hasil introspeksi dukungan `stuck_after` pada fleet ini.
        self._status_accepts_stuck = None
        # Lewat lambda supaya test yang mem-patch time.monotonic (pola lama)
        # tetap berlaku, dan test yang mengganti orch._clock langsung juga
        # bisa. Menugaskan `time.monotonic` mentah membekukan referensi lama.
        self._clock = lambda: time.monotonic()
        self._tasks = {}
        self._task_order = []
        self._results = {}
        self._fleet = fleet
        self._store = store
        self._run_id = run_id
        self._event_sink = event_sink
        self._preflight_failed = False
        # Task ids adopted from the store on a resumed run; never dispatched.
        self._resumed = set()
        # Root untuk git worktree isolasi. None = default worktree.py.
        self.worktree_root = worktree_root
        # Tabel harga opsional. Tanpa ini, biaya tetap None dan itu
        # memang jawaban yang benar: model tak dikenal tidak sama dengan
        # biaya nol. Diteruskan apa adanya ke pricing.estimate_cost.
        self._prices = prices

        # Pasang pembatas pada klien kalau belum ada. Tanpa ini, oc-fleet
        # membanjiri server dengan pollingnya sendiri dan mendapat 429 -
        # yang tampak seperti model rusak atau sesi macet.
        #
        # Konkurensi lebih dulu dan selalu: itu batas yang benar-benar
        # ditegakkan provider (15 bersamaan).
        if getattr(fleet, "concurrency_limiter", None) is None and concurrency_limit:
            try:
                fleet.concurrency_limiter = ConcurrencyLimiter(concurrency_limit)
            except (AttributeError, ValueError):
                pass
        # Pembatas laju hanya kalau diminta. Default None: membatasi pada
        # 25/menit terbukti menghukum pemakaian wajar (24 request berubah
        # dari 0.1s menjadi 43s) tanpa memberi manfaat - yang ditolak
        # provider adalah konkurensi.
        if (getattr(fleet, "rate_limiter", None) is None
                and rate_per_minute and burst):
            try:
                fleet.rate_limiter = RateLimiter(
                    rate=rate_per_minute / 60.0,
                    capacity=max(1.0, min(burst, rate_per_minute / 2.0)),
                )
            except (AttributeError, ValueError):
                pass

    @property
    def fleet(self):
        """The fleet in use; a real Fleet is created lazily if none was passed in."""
        if self._fleet is None:
            self._fleet = Fleet()
        return self._fleet

    # -- graph construction --------------------------------------------------

    def add(self, task):
        """Register a Task. Raises ValueError on duplicate id or unknown dependency."""
        if not isinstance(task, Task):
            raise TypeError("add() expects a Task, got %s" % type(task).__name__)
        if task.id in self._tasks:
            raise ValueError("duplicate task id: %r" % task.id)
        for dep in task.depends_on:
            if dep not in self._tasks:
                raise ValueError("task %r depends on unknown task %r" % (task.id, dep))
        self._tasks[task.id] = task
        self._task_order.append(task.id)
        self._results[task.id] = _new_record()

    def validate(self):
        """Raise ValueError on a malformed task, missing dependency, or cycle.

        The per-task checks mirror what ``Fleet.dispatch`` now rejects, and
        exist so the failure lands here, before anything is dispatched. A
        bad ``workdir`` caught mid-run aborts one session while its
        siblings are already writing to the shared tree, which is far
        harder to reason about than a refusal up front.
        """
        for tid in self._task_order:
            task = self._tasks[tid]
            if not isinstance(task.prompt, str) or not task.prompt.strip():
                raise ValueError("task %r has an empty prompt" % tid)
            if not isinstance(task.workdir, str) or not task.workdir.strip():
                raise ValueError("task %r has an empty workdir" % tid)
            self._validate_task_contract(task, tid)
            for dep in task.depends_on:
                if dep not in self._tasks:
                    raise ValueError("task %r depends on unknown task %r" % (tid, dep))
        cycle = self._find_cycle()
        if cycle:
            raise ValueError("dependency cycle detected: %s" % " -> ".join(cycle))
        self._reject_ownership_overlap()

    def _validate_task_contract(self, task, tid):
        """Tolak task yang secara struktural tidak bisa berhasil.

        Semua hal di sini bisa diketahui sebelum dispatch. Menangkapnya
        di sini menghabiskan satu baris output; menangkapnya di tengah run
        menghabiskan satu slot, satu sesi, dan kejernihan pohon bersama
        yang sudah setengah tertulis.
        """
        # Angka yang tidak bisa berarti apa-apa.
        if task.retries < 0:
            raise ValueError("task %r has negative retries: %r" % (tid, task.retries))
        if not isinstance(task.timeout, (int, float)) or task.timeout <= 0:
            raise ValueError(
                "task %r needs a positive timeout, got %r" % (tid, task.timeout)
            )
        if task.verify and (
            not isinstance(task.verify_timeout, (int, float))
            or task.verify_timeout <= 0
        ):
            raise ValueError(
                "task %r needs a positive verify_timeout, got %r"
                % (tid, task.verify_timeout)
            )
        # Fallback yang mengulang model yang sama membuang satu attempt
        # tanpa harapan berhasil; kalau model itu penyebab gagalnya, ini
        # cuma memperlambat kegagalan yang sama.
        seen_models = {task.model}
        for fb in task.fallbacks:
            if not isinstance(fb, str) or not fb.strip():
                raise ValueError("task %r has an empty fallback model" % tid)
            if fb in seen_models:
                raise ValueError(
                    "task %r fallback %r repeats an earlier model; a retry "
                    "on the same model cannot help" % (tid, fb)
                )
            seen_models.add(fb)
        # `owns` adalah pagar; pola yang keluar dari workdir melubangi pagar.
        for pattern in task.owns:
            if not isinstance(pattern, str) or not pattern.strip():
                raise ValueError("task %r has an empty owns pattern" % tid)
            if os.path.isabs(pattern) or pattern.startswith("/"):
                raise ValueError(
                    "task %r owns pattern %r is absolute; owns patterns are "
                    "relative to the workdir" % (tid, pattern)
                )
            escaped = pattern.replace(os.sep, "/")
            if escaped == ".." or escaped.startswith("../") or "/../" in escaped:
                raise ValueError(
                    "task %r owns pattern %r escapes the workdir" % (tid, pattern)
                )
        # Perintah verifikasi harus benar-benar bisa dijalankan.
        for command in task.verify:
            if not isinstance(command, str) or not command.strip():
                raise ValueError(
                    "task %r has an empty verify command" % tid
                )

    def _reject_ownership_overlap(self):
        """Tolak dua task di workdir sama yang wilayah tulisnya bertabrakan.

        Boundary hanya melindungi kalau lane-nya benar-benar terpisah.
        `src/**` dan `src/db.py` sama-sama mengklaim `src/db.py`, jadi
        tak ada satu agent pun yang bisa dituduh bersalah dengan adil.
        Kesalahan seperti ini jauh lebih murah ditangkap di sini,
        sebelum ada sesi yang jalan, daripada setelah file bertabrakan.
        """
        for i, first_id in enumerate(self._task_order):
            first = self._tasks[first_id]
            if not first.owns:
                continue
            for second_id in self._task_order[i + 1:]:
                second = self._tasks[second_id]
                if not second.owns or second.workdir != first.workdir:
                    continue
                for first_pattern in first.owns:
                    for second_pattern in second.owns:
                        if patterns_overlap(first_pattern, second_pattern):
                            raise ValueError(
                                "ownership overlap: tasks %r and %r both claim "
                                "matching paths (%r vs %r) in workdir %r"
                                % (first_id, second_id, first_pattern,
                                   second_pattern, first.workdir)
                            )

    def _find_cycle(self):
        """Iterative DFS over dependency edges; return the cycle path or None."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {tid: WHITE for tid in self._task_order}
        for root in self._task_order:
            if color[root] != WHITE:
                continue
            color[root] = GRAY
            stack = [(root, iter(self._tasks[root].depends_on))]
            path = [root]
            while stack:
                _, deps = stack[-1]
                next_node = None
                for dep in deps:
                    if color[dep] == GRAY:
                        return path[path.index(dep):] + [dep]
                    if color[dep] == WHITE:
                        next_node = dep
                        break
                if next_node is not None:
                    color[next_node] = GRAY
                    stack.append((next_node, iter(self._tasks[next_node].depends_on)))
                    path.append(next_node)
                else:
                    color[stack[-1][0]] = BLACK
                    stack.pop()
                    path.pop()
        return None

    def topological_order(self):
        """Return task ids in dependency order (Kahn's algorithm, insertion
        order among the currently ready tasks)."""
        self.validate()
        indegree = {tid: len(self._tasks[tid].depends_on) for tid in self._task_order}
        children = {tid: [] for tid in self._task_order}
        for tid in self._task_order:
            for dep in self._tasks[tid].depends_on:
                children[dep].append(tid)
        ready = [tid for tid in self._task_order if indegree[tid] == 0]
        order = []
        while ready:
            ready.sort(key=self._task_order.index)
            node = ready.pop(0)
            order.append(node)
            for child in children[node]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        return order

    def plan(self):
        """Build and validate an execution plan without dispatching anything.

        The plan is the preflight contract for a run: graph order, parallel
        waves, per-task execution settings, and operator-visible risks.
        """
        self.validate()
        order = self.topological_order()
        levels = {}
        for tid in order:
            deps = self._tasks[tid].depends_on
            levels[tid] = 0 if not deps else max(levels[dep] + 1 for dep in deps)
        waves = []
        for tid in order:
            level = levels[tid]
            while len(waves) <= level:
                waves.append([])
            waves[level].append(tid)
        tasks = {}
        risks = []
        for tid in order:
            task = self._tasks[tid]
            tasks[tid] = {
                "depends_on": list(task.depends_on),
                "workdir": task.workdir,
                "model": task.model,
                "fallbacks": list(task.fallbacks),
                "retries": task.retries,
                "timeout": task.timeout,
                "setup": list(task.setup),
                "teardown": list(task.teardown),
                "verification": list(task.verify),
                "isolate": task.isolate,
                "owns": list(task.owns),
            }
            if not task.verify:
                risks.append({"task_id": tid, "kind": "no_verification"})
            if task.retries and not task.fallbacks:
                risks.append({"task_id": tid, "kind": "retry_same_model"})
            if not task.isolate and sum(
                    1 for other in self._tasks.values()
                    if other.workdir == task.workdir) > 1:
                risks.append({"task_id": tid, "kind": "shared_workdir"})
        return {
            "order": order,
            "waves": waves,
            "max_parallel": self.max_parallel,
            "tasks": tasks,
            "risks": risks,
        }

    # -- execution ------------------------------------------------------------

    def run(self, dry_run=False, resume=False):
        """Execute the DAG.

        Returns the topological order (dry_run) or the results() dict.

        With ``resume=True`` and a store attached, a task already recorded
        as finished is adopted instead of dispatched again. Opt-in on
        purpose: a plain ``run()`` still repeats every task, because
        silently doing nothing on a second call would be more surprising
        than repeating work the caller asked to repeat.
        """
        try:
            self.validate()
            plan = self.plan()
        except ValueError:
            # Preflight refused the run. Record it so run_status() can
            # report code 3 to a caller instead of pretending nothing
            # happened; then let the error surface to the caller.
            self._preflight_failed = True
            raise
        if dry_run:
            return self._print_plan(plan)
        if resume:
            self._apply_resume()
        self._persist_run("running")
        pending = list(self._task_order)
        running = {}
        while pending or running:
            self._skip_blocked(pending)
            self._skip_resumed(pending)
            self._dispatch_ready(pending, running)
            if not running and pending:
                # A validated DAG should never reach this state. Do not mark a
                # partially executed run completed if it does happen.
                self._persist_run("aborted")
                raise RuntimeError("orchestrator stalled with pending tasks: %s" % pending)
            self._poll_running(pending, running)
            if running:
                # Sleep until the next poll, but never past the nearest
                # deadline. Sleeping the full interval regardless meant a
                # task with timeout=1 and the default interval overshot by
                # up to 3s - a 3x error on the deadline the caller asked for.
                sleep_for = self.poll_interval
                nearest = min(info["deadline"] for info in running.values())
                remaining = nearest - self._clock()
                if remaining < sleep_for:
                    sleep_for = max(remaining, 0.0)
                if sleep_for:
                    time.sleep(sleep_for)
        self._persist_run("completed")
        return self.results()

    def _persist_run(self, status):
        if self._store is None or self._run_id is None:
            return
        self._store.upsert_run(self._run_id, {
            "status": status,
            "tasks": len(self._task_order),
        })
        for tid, record in self._results.items():
            self._store.upsert_task(self._run_id, tid, self._record_for_store(tid, record))

    def _record_for_store(self, tid, record):
        """A task record plus the fields the store needs to be useful.

        `workdir` lives on the Task, but anything reading the store back
        (diff, review, resume diagnostics) needs it on the record. Without
        this, `oc_fleet_diff` failed on every real run with "no readable
        workdir", because the field was never written.
        """
        stored = dict(record)
        task = self._tasks.get(tid)
        if task is not None:
            stored["workdir"] = task.workdir
        return stored

    def _persist_event(self, task_id, event_type, status, record):
        event = {
            "event_type": event_type,
            "task_id": task_id,
            "status": status,
            "session_id": record.get("session_id"),
            "attempt": record.get("attempts"),
        }
        if self._event_sink is not None:
            self._event_sink.emit({"run_id": self._run_id, **event})
        if self._store is None or self._run_id is None:
            return
        self._store.append_event(self._run_id, event)
        self._store.upsert_task(self._run_id, task_id, self._record_for_store(task_id, record))
        if record.get("artifacts") is not None:
            self._store.upsert_artifact(
                self._run_id, task_id, record["artifacts"]
            )

    def _print_plan(self, plan=None):
        plan = plan or self.plan()
        order = plan["order"]
        print("dry run: %d task(s), max_parallel=%d" % (len(order), self.max_parallel))
        print("waves: %s" % " -> ".join("[" + ", ".join(wave) + "]" for wave in plan["waves"]))
        if plan["risks"]:
            print("risks:")
            for risk in plan["risks"]:
                print("  %s: %s" % (risk["task_id"], risk["kind"]))
        else:
            print("risks: none")
        for index, tid in enumerate(order, 1):
            task = self._tasks[tid]
            deps = ", ".join(task.depends_on) if task.depends_on else "-"
            print(
                "  %d. %s  deps: %s  model: %s  workdir: %s"
                % (index, tid, deps, task.model, task.workdir)
            )
        return order

    def _apply_resume(self):
        """Adopt tasks a previous run already finished.

        Reads task records back from the store and, for any task this run
        also contains that finished successfully, copies the record into
        this run so it is never dispatched again. A task left running or
        pending is ignored: it did not finish, so it runs.
        """
        if self._store is None or self._run_id is None:
            return
        for tid in self._task_order:
            try:
                previous = self._store.get_task(self._run_id, tid)
            except Exception:
                # A store that cannot be read must not abort a resume; the
                # worst case is that we re-run a task, which is the
                # pre-resume behaviour anyway.
                continue
            if not isinstance(previous, dict):
                continue
            if previous.get("status") in ("succeeded", "verification_passed"):
                self._results[tid] = previous
                self._resumed.add(tid)
                print("resumed: %s (already %s)" % (tid, previous["status"]))

    def _skip_resumed(self, pending):
        for tid in list(pending):
            if tid in self._resumed:
                pending.remove(tid)

    def _skip_blocked(self, pending):
        for tid in list(pending):
            blocked_by = [
                dep
                for dep in self._tasks[tid].depends_on
                if self._results[dep]["status"] in (
                    "failed",
                    "verification_failed",
                    "setup_failed",
                    "skipped",
                )
            ]
            if not blocked_by:
                continue
            pending.remove(tid)
            self._results[tid]["status"] = "skipped"
            dep = blocked_by[0]
            print(
                "skipped: %s (dependency %s is %s)"
                % (tid, dep, self._results[dep]["status"])
            )

    def _ready(self, tid):
        return all(
            self._is_success(self._results[dep])
            for dep in self._tasks[tid].depends_on
        )

    @staticmethod
    def _is_success(record):
        return record.get("status") in ("succeeded", "verification_passed")

    def _run_hooks(self, commands, workdir, env, phase="setup"):
        merged = os.environ.copy()
        for key, value in env.items():
            if not isinstance(key, str) or not isinstance(value, str):
                return "%s failed: environment keys and values must be strings" % phase
            merged[key] = value
        for command in commands:
            try:
                argv = shell_split(command)
                if not argv:
                    return "%s failed: empty hook command" % phase
                completed = subprocess.run(
                    argv, cwd=workdir, env=merged,
                    capture_output=True, text=True, check=False,
                )
            except (OSError, ValueError) as exc:
                return "%s failed: %s" % (phase, str(exc)[:2000])
            if completed.returncode:
                return "%s failed (%s): %s" % (
                    phase,
                    completed.returncode,
                    (completed.stderr or completed.stdout).strip()[:2000],
                )
        return None

    def _prompt_for_task(self, task, rec=None):
        """Rakit prompt untuk sebuah attempt.

        Attempt 1 adalah prompt asli apa adanya. Attempt berikutnya
        menambahkan ringkasan kegagalan sebelumnya: agent yang mengulang
        tanpa tahu apa yang gagal akan mengulang kesalahan yang sama.
        Blok ini dibatasi supaya transkrip raksasa tidak menenggelamkan
        instruksi sebenarnya.
        """
        base = task.prompt
        if task.handoff and task.depends_on:
            lines = [base, "", "Upstream task evidence:"]
            for dep in task.depends_on:
                record = self._results[dep]
                artifacts = record.get("artifacts") or {}
                verification = record.get("verification") or {}
                lines.extend([
                    "Upstream task: %s" % dep,
                    "status: %s" % record.get("status"),
                    "files_changed: %s" % ", ".join(artifacts.get("files_changed", [])),
                    "verification: %s" % verification,
                ])
            base = "\n".join(lines)
        base = base[:12000]

        context = self._retry_context(task, rec)
        if context:
            return "%s\n\n%s" % (base, context)[:12000]
        return base

    def _retry_context(self, task, rec):
        """Blok 'previous attempt failed' untuk attempt N>1, atau ''.

        Sumber kebenaran ada di record: `attempts_detail` menyimpan model
        tiap attempt, `failure_class` kategori kegagalannya, `last_text`
        kata-kata terakhir agent, dan `verification` keluaran perintah
        verifikasi. Semua itu yang paling berguna untuk attempt berikutnya.
        """
        if not rec or rec.get("attempts", 1) <= 1:
            return ""
        previous = rec["attempts"] - 1
        detail = (rec.get("attempts_detail") or [])
        failed_model = None
        failed_class = None
        for entry in reversed(detail):
            if entry.get("attempt") == previous:
                failed_model = entry.get("model")
                failed_class = entry.get("failure_class")
                break
        if failed_model is None:
            failed_model = model_for_attempt(task, previous)
        if failed_class is None:
            failed_class = rec.get("failure_class")

        lines = [
            "Previous attempt %d of this task FAILED. Do not repeat it." % previous,
            "failed_model: %s" % (failed_model or "unknown"),
            "failure_class: %s" % (failed_class or "unknown"),
        ]
        agent_status = rec.get("agent_status")
        if agent_status:
            lines.append("agent_status: %s" % agent_status)
        last_text = (rec.get("last_text") or "").strip()
        if last_text:
            lines.append("agent_last_message:")
            lines.append(last_text[:4000])
        verification = rec.get("verification")
        if isinstance(verification, dict) and verification.get("passed") is False:
            lines.append("verification_failed:")
            for command in verification.get("commands") or []:
                if not isinstance(command, dict):
                    continue
                # Per-command dict tidak punya `passed`; kegagalan tampak
                # dari returncode != 0 atau timeout.
                if command.get("returncode") in (0, None) and not command.get("timed_out"):
                    continue
                output = (command.get("stderr") or command.get("stdout") or "").strip()
                lines.append("- %s" % command.get("command", ""))
                if output:
                    lines.append("  %s" % output[:1500])
        lines.append("Address the failure above in this attempt.")
        return "\n".join(lines)

    def _dispatch_ready(self, pending, running):
        for tid in list(pending):
            if len(running) >= self.max_parallel:
                break
            if not self._ready(tid):
                continue
            pending.remove(tid)
            task = self._tasks[tid]
            rec = self._results[tid]
            rec["status"] = "running"
            rec["attempts"] += 1
            if rec["started_at"] is None:
                rec["started_at"] = time.monotonic()
            if task.isolate and rec["attempts"] == 1:
                self._ensure_worktree(task, tid)
            elif task.owns and rec["attempts"] == 1:
                # A shared workdir is a shared baseline, but only boundary
                # enforcement needs a clean one. Task B would otherwise see
                # task A's dirty files and be blamed for crossing a lane it
                # never touched. Tasks without `owns` keep the old behaviour:
                # their workdir is left exactly as the caller set it up.
                self._refresh_baseline(task.workdir, tid)
            setup_error = self._run_hooks(task.setup, task.workdir, task.env)
            if setup_error:
                rec["status"] = "setup_failed"
                rec["last_text"] = setup_error
                self._persist_event(tid, "task_finished", rec["status"], rec)
                continue
            self._persist_event(tid, "task_started", "running", rec)
            try:
                session_id = self.fleet.dispatch(
                    self._prompt_for_task(task, rec), task.workdir, title=task.title or tid,
                    model=(
                        task.model
                        if rec.pop("_retry_same_model", False)
                        else model_for_attempt(task, rec["attempts"])
                    ),
                )
            except DISPATCH_ERRORS as exc:
                session_id = None
                rec["last_text"] = "dispatch failed: %s" % exc
            rec["session_id"] = session_id
            running[tid] = {
                "session_id": session_id,
                "deadline": self._clock() + max(float(task.timeout), 0.0),
                "started_at": self._clock(),
                "last_heartbeat": self._clock(),
                "last_state": {"outcome": None, "last_assistant_text": None},
            }
            print("started: %s (attempt %d, session %s)" % (tid, rec["attempts"], session_id or "-"))

    def _supports_stuck_after(self):
        """Apakah fleet ini menerima `stuck_after`? Dihitung sekali saja.

        Versi lama `Fleet.status` hanya punya `session_id`, dan test double
        lazim mengikuti bentuk itu. Menebak lewat `except TypeError` salah:
        TypeError yang berasal dari DALAM status juga akan tertangkap dan
        ditelan, sehingga poll yang gagal tampak seperti poll biasa. Jadi
        kita periksa tanda tangan fungsi secara eksplisit.
        """
        if self._status_accepts_stuck is None:
            try:
                params = inspect.signature(self.fleet.status).parameters
            except (TypeError, ValueError):
                params = {}
            self._status_accepts_stuck = (
                "stuck_after" in params
                or any(p.kind == p.VAR_KEYWORD for p in params.values())
            )
        return self._status_accepts_stuck

    def _call_status(self, session_id):
        """Panggil fleet.status, setel ambang macet hanya bila didukung."""
        if self.tool_timeout is not None and self._supports_stuck_after():
            return self.fleet.status(session_id, stuck_after=self.tool_timeout)
        return self.fleet.status(session_id)

    def _poll_running(self, pending, running):
        for tid in list(running):
            info = running.pop(tid)
            session_id = info["session_id"]
            if session_id is None:
                # Dispatch never produced a session: this attempt failed before
                # it started. Do not dress it up as a timeout.
                self._finish_attempt(tid, info["last_state"], pending)
                continue
            try:
                state = self._call_status(session_id)
            except POLL_ERRORS as exc:
                # A bad poll is a poll failure, not a task failure: keep the
                # last known state and let the deadline decide. Never let it
                # escape and strand the other tasks in this run.
                info["last_state"] = dict(info["last_state"] or {})
                info["last_state"]["last_assistant_text"] = (
                    info["last_state"].get("last_assistant_text")
                    or "poll failed: %s" % exc
                )
                if self._clock() >= info["deadline"]:
                    self._finish_attempt(tid, info["last_state"], pending, timed_out=True)
                else:
                    self._maybe_heartbeat(tid, info)
                    running[tid] = info
                continue
            if not isinstance(state, dict):
                # A falsy / non-dict status must not wipe the cached text: the
                # old `status(...) or {}` replaced last_state with {} on a
                # transient None, silently losing the last assistant text.
                state = info["last_state"]
            elif state.get("last_assistant_text") is None and info["last_state"].get(
                "last_assistant_text"
            ):
                # The server reports the outcome and the text separately, and a
                # later poll can legitimately carry outcome=None with text=None
                # while an earlier poll already saw real progress. Keep the
                # newest KNOWN text so a timeout report says what the task had
                # produced rather than nothing.
                merged = dict(state)
                merged["last_assistant_text"] = info["last_state"]["last_assistant_text"]
                state = merged
            info["last_state"] = state
            if state.get("outcome") is not None:
                self._finish_attempt(tid, state, pending)
                continue
            if state.get("stuck"):
                # Sesi berhenti: tool call berstatus "running" yang tidak
                # pernah selesai dan sudah terlalu lama. Tanpa cabang ini,
                # `outcome` tetap None dan task menunggu sampai deadline
                # penuh - 25 menit pada kegagalan nyata di mesin ini - tanpa
                # ada cara membedakan "masih bekerja" dari "sudah mati".
                #
                # Diperlakukan sebagai timeout, bukan sukses: hasilnya
                # memang tidak ada. Menandai `timed_out` membuat jalur
                # retry/skip yang sudah ada ikut berlaku.
                detik = state.get("stuck_seconds")
                detail = (
                    "stuck: %d tool berjalan, paling tua %.0f detik"
                    % (state.get("tool_running", 0), detik)
                    if isinstance(detik, (int, float))
                    else "stuck: tool tidak selesai"
                )
                if state.get("last_assistant_text") is None:
                    state = dict(state)
                    state["last_assistant_text"] = detail
                print("stuck: %s (%s)" % (tid, detail))
                self._finish_attempt(tid, state, pending, timed_out=True)
                continue
            if self._clock() >= info["deadline"]:
                # Deadline expired with no outcome: only NOW is it a timeout.
                self._finish_attempt(tid, state, pending, timed_out=True)
                continue
            self._maybe_heartbeat(tid, info)
            running[tid] = info

    def _maybe_heartbeat(self, tid, info):
        """Cetak satu baris progres untuk task yang lama berjalan.

        Murni observability: tidak mengubah alur, tidak menyentuh hasil.
        Berguna persis pada kasus yang memicunya - run coffee-catalog yang
        diam 18 menit antara "started" dan verdict akhir. Tanpa baris ini,
        run yang sehat dan run yang menggantung terlihat sama dari luar.
        """
        if self.heartbeat_interval is None:
            return
        now = self._clock()
        # Toleran terhadap info yang dibuat sebelum heartbeat ada (mis. di
        # test yang menyuntik `running` langsung). Fallback ke sekarang =
        # tidak ada heartbeat sampai interval berikutnya.
        last = info.get("last_heartbeat")
        if last is None:
            info["last_heartbeat"] = now
            last = now
        if now - last < self.heartbeat_interval:
            return
        info["last_heartbeat"] = now
        elapsed = now - info.get("started_at", now)
        state = info.get("last_state") or {}
        running = state.get("tool_running")
        detail = ""
        if isinstance(running, int) and running > 0:
            oldest = state.get("stuck_seconds")
            if isinstance(oldest, (int, float)):
                detail = ", %d tool berjalan (paling tua %.0f detik)" % (running, oldest)
            else:
                detail = ", %d tool berjalan" % running
        print("heartbeat: %s (%s berjalan%s)" % (tid, self._fmt_duration(elapsed), detail))

    def _finish_attempt(self, tid, state, pending, timed_out=False):
        task = self._tasks[tid]
        rec = self._results[tid]
        outcome = state.get("outcome") if isinstance(state, dict) else None
        rec["outcome"] = outcome
        attempt_failure = classify_failure(
            outcome=outcome,
            timed_out=timed_out,
        ) if outcome in FAILED_OUTCOMES or timed_out else None
        rec["attempts_detail"].append({
            "attempt": rec["attempts"],
            "model": model_for_attempt(task, rec["attempts"]),
            "session_id": rec.get("session_id"),
            "failure_class": attempt_failure,
        })
        if attempt_failure:
            rec["failure_class"] = attempt_failure
        if isinstance(state, dict) and state.get("last_assistant_text") is not None:
            rec["last_text"] = state["last_assistant_text"]
        rec["finished_at"] = time.monotonic()
        if rec["started_at"] is not None:
            rec["duration"] = round(rec["finished_at"] - rec["started_at"], 3)

        # Token and cost attribution. `normalize_stats` and
        # `estimate_cost` existed and were tested, but nothing called
        # them, so a finished task carried no usage at all. Read the
        # counters straight off the status payload, and only compute a
        # cost when a price table was supplied - an unknown model is
        # unknown, not free.
        from accounting import normalize_stats
        from pricing import estimate_cost

        stats = normalize_stats(state) if isinstance(state, dict) else {}
        stats.pop("attribution", None)
        if stats:
            rec["stats"] = stats
        model_used = (
            state.get("model") if isinstance(state, dict) else None
        ) or task.model
        rec["model_used"] = model_used
        rec["estimated_cost"] = estimate_cost(
            model_used, stats.get("input", 0), stats.get("output", 0),
            self._prices,
        ) if stats else None

        # Verification is about the ARTIFACT, not about the agent. The coffee
        # run showed why this distinction matters: the QA agent timed out, so
        # verification was skipped entirely, even though a build+test pass was
        # possible the whole time. Run verification whenever the agent had a
        # chance to write to the workdir - success, failure, or timeout alike.
        agent_failed = outcome is None or outcome in FAILED_OUTCOMES
        rec["agent_status"] = (
            "timed_out" if (timed_out and outcome is None)
            else ("failed" if agent_failed else "succeeded")
        )
        verification = None
        if task.verify:
            verification = run_verification(
                task.verify,
                task.workdir,
                timeout=task.verify_timeout,
            )
            rec["verification"] = verification.to_dict()
            rec["verification_status"] = (
                "passed" if verification.passed else "failed"
            )
        else:
            rec["verification_status"] = "not_required"

        # The artifact manifest describes what the agent left in the workdir.
        # It is collected regardless of verification: an agent that wrote real
        # files and then failed still produced evidence worth handing off.
        # It is also required whenever `owns` is set, because boundary
        # enforcement reads the changed files from it.
        if not agent_failed or task.verify or task.owns:
            from artifacts import collect_manifest

            rec["artifacts"] = collect_manifest(task.workdir).to_dict()

        # Boundary enforcement. A prompt can ask an agent to stay in its lane;
        # only this check proves it. Violations are recorded against the files
        # the agent actually changed, so a lane breach is evidence, not a
        # guess - and it is checked before any merge, not after.
        if task.owns:
            changed = (rec.get("artifacts") or {}).get("files_changed", [])
            rec["boundary"] = check_boundary(changed, task.owns)

        if not agent_failed and outcome is not None:
            if rec["boundary"] and rec["boundary"]["verdict"]:
                rec["failure_class"] = classify_failure(verification_failed=True)
                rec["status"] = "verification_failed"
                self._persist_event(tid, "task_finished", rec["status"], rec)
                print(
                    "boundary violation: %s wrote outside %s (%s)"
                    % (tid, task.owns, ", ".join(rec["boundary"]["violations"]))
                )
                return
            if verification is not None and verification.required and not verification.passed:
                rec["failure_class"] = classify_failure(verification_failed=True)
                rec["status"] = "verification_failed"
                if rec["attempts"] <= task.retries:
                    rec["_retry_same_model"] = True
                    rec["status"] = "pending"
                    pending.insert(self._task_order.index(tid), tid)
                    print("retrying: %s after verification failure" % tid)
                else:
                    self._persist_event(tid, "task_finished", rec["status"], rec)
                    print("verification failed: %s" % tid)
                return
            rec["status"] = "verification_passed" if verification is not None and verification.required else "succeeded"
            teardown_error = self._run_hooks(
                task.teardown, task.workdir, task.env, phase="teardown"
            )
            if teardown_error:
                rec["last_text"] = (rec.get("last_text") or "") + "\n" + teardown_error
            self._persist_event(tid, "task_finished", rec["status"], rec)
            print(
                "finished: %s (outcome %s, %s)"
                % (tid, outcome, self._fmt_duration(rec["duration"]))
            )
            return
        if rec["attempts"] <= task.retries:
            # Cancel before retrying. Without this the abandoned session keeps
            # holding a fleet slot and keeps writing to the workdir while the
            # retry writes to the same place, so "retry" would mean "two runs".
            self._cancel_session(rec.get("session_id"), tid)
            rec["status"] = "pending"
            pending.insert(self._task_order.index(tid), tid)
            print("retrying: %s (attempt %d of %d)" % (tid, rec["attempts"] + 1, task.retries + 1))
            return

        # The agent failed and we are out of retries. Before calling it a
        # plain agent failure, check whether the artifact contract also
        # failed. Both are true; the status reports the more severe one and
        # `agent_status` keeps the agent's own verdict, so neither fact is
        # lost. Reporting only "failed" here would bury a boundary violation
        # or a failing verification under a generic agent failure.
        boundary_broken = bool(rec.get("boundary") and rec["boundary"].get("verdict"))
        artifact_broken = (
            verification is not None and verification.required and not verification.passed
        )
        if boundary_broken or artifact_broken:
            rec["failure_class"] = classify_failure(verification_failed=True)
            rec["status"] = "verification_failed"
            self._persist_event(tid, "task_finished", rec["status"], rec)
            if boundary_broken:
                print(
                    "boundary violation: %s wrote outside %s (%s)"
                    % (tid, task.owns, ", ".join(rec["boundary"]["violations"]))
                )
            else:
                print("verification failed: %s (agent also %s)"
                      % (tid, rec["agent_status"]))
            return

        rec["status"] = "failed"
        if outcome is None:
            if timed_out:
                print("failed: %s (timed out after %ss)" % (tid, task.timeout))
            else:
                # Prefer the recorded reason. A dispatch failure leaves
                # `last_text` as "dispatch failed: ..." while `state` is the
                # empty placeholder, so reading `state` alone printed
                # "failed: a (None)" - the useful line was in the record the
                # whole time.
                reason = None
                if isinstance(state, dict):
                    reason = state.get("last_assistant_text")
                reason = reason or rec.get("last_text") or "no outcome"
                print("failed: %s (%s)" % (tid, reason))
        else:
            print("failed: %s (outcome %s)" % (tid, outcome))

    def _ensure_worktree(self, task, tid):
        """Buat git worktree untuk task isolasi, arahkan workdir ke sana.

        Gagal = biarkan exception naik supaya run berhenti sebelum
        dispatch: lebih baik gagal awal yang jelas daripada agent
        menulis ke workdir yang salah.
        """
        import worktree

        root = getattr(self, "worktree_root", None) or "/tmp/oc-fleet-worktrees"
        task.workdir = worktree.create(task.repo or ".", tid, root=root)
        print("worktree: %s -> %s" % (tid, task.workdir))

    def _refresh_baseline(self, workdir, tid):
        """Commit sisa perubahan sebelum task non-isolasi berikutnya jalan.

        Saat beberapa task berbagi satu workdir, tugas yang berjalan
        belakangan akan melihat file milik tugas sebelumnya sebagai
        'berubah' dan dituduh melanggar boundary milik orang lain.
        Baseline bersih membuat atribusi perubahan kembali ke agent
        yang benar. Kegagalan di sini tidak fatal: hanya tidak
        memperbarui baseline.
        """
        import subprocess

        commit = [
            "git", "-C", workdir,
            "-c", "user.name=oc-fleet", "-c", "user.email=fleet@localhost",
            "commit", "--allow-empty", "-q", "-m",
            "oc-fleet baseline before %s" % tid,
        ]
        try:
            subprocess.run(
                ["git", "-C", workdir, "add", "-A"],
                capture_output=True, text=True, check=False,
            )
            subprocess.run(commit, capture_output=True, text=True, check=False)
        except OSError:
            return

    def _cancel_session(self, session_id, tid):
        """Best-effort cancel of a session we are about to abandon.

        Never raises and never blocks the run: a session that already finished
        will simply refuse the interrupt. Fleet objects that do not implement
        cancel (test doubles) are tolerated.
        """
        if not session_id:
            return
        cancel = getattr(self.fleet, "cancel", None)
        if cancel is None:
            return
        try:
            stopped = cancel(session_id)
        except Exception as exc:  # noqa: BLE001 - cancel must not break the run
            print("warning: could not cancel session %s (%s): %s" % (tid, session_id, exc))
            return
        if stopped:
            print("stopped: %s (session %s cancelled)" % (tid, session_id))
        else:
            # False means the server had nothing to stop (already finished),
            # which is fine. None means the cancel never got an answer - the
            # session may still be running and writing to the workdir while the
            # retry writes to the same place. Say so instead of staying silent.
            if stopped is None:
                print(
                    "warning: could not confirm session %s (%s) stopped - "
                    "a retry may run alongside it" % (tid, session_id)
                )

    # -- reporting --------------------------------------------------------------

    def results(self):
        """Return the results dict: task_id -> {status, session_id, outcome,
        attempts, last_text, started_at, finished_at, duration}.

        Returns a copy. This used to hand back the live internal dict, so a
        caller doing `results()["a"]["status"] = "done"` silently corrupted
        orchestrator state and the next sweep's decisions were made on a
        doctored record. A per-row `dict(rec)` is enough because every field
        is a primitive (str/int/float/None), so there is nothing nested to
        share; the copy isolates the rows, which is the property that
        matters.

        Two fields span the whole task, not one attempt, which is worth
        knowing when reading a row from a retried task:

        * ``duration`` is measured from the *first* attempt's start
          (``started_at`` is set once, and never reset), so it includes the
          backoff between attempts.
        * ``session_id`` and ``outcome`` describe only the *final* attempt.
          The session id of a superseded attempt is not recorded, so there
          is no per-attempt history to reconstruct what earlier attempts
          did.
        """
        return {tid: dict(rec) for tid, rec in self._results.items()}

    def run_status(self):
        """Ringkas hasil run jadi satu kode keluar untuk pemanggil program.

        Kontraknya:

        * 3 - preflight menolak run (validate/plan melempar sebelum ada
          dispatch); tidak ada agent yang jalan;
        * 2 - ada verification yang gagal (termasuk pelanggaran boundary);
        * 1 - ada agent yang gagal, timeout, atau setup gagal, tetapi
          tidak ada verification yang gagal;
        * 0 - semua task sukses dan semua verification yang diwajibkan
          lolos.

        Kalau run belum pernah dijalankan, hasil task masih "pending"
        dan dianggap belum sukses, sehingga kode 1 - bukan 0. Pemanggil
        tidak boleh menyimpulkan sukses dari run yang tak menghasilkan
        apa pun.

        Kode 2 sengaja di atas 1: verification gagal berarti artefak
        tidak memenuhi kontrak, terlepas dari agent-nya sukses atau
        tidak, dan itu kondisi yang lebih parah untuk dilaporkan.
        """
        if self._preflight_failed:
            return 3
        return exit_code_for_statuses(
            self._results[tid]["status"] for tid in self._task_order
        )

    def summary(self):
        """Return a human readable multi-line status summary."""
        lines = []
        for tid in self._task_order:
            rec = self._results[tid]
            lines.append(
                "%-24s %-10s attempts=%-2d outcome=%-8s time=%s"
                % (
                    tid,
                    rec["status"],
                    rec["attempts"],
                    rec["outcome"] if rec["outcome"] is not None else "-",
                    self._fmt_duration(rec["duration"]),
                )
            )
        counts = {}
        for tid in self._task_order:
            status = self._results[tid]["status"]
            counts[status] = counts.get(status, 0) + 1
        order = ("succeeded", "failed", "skipped", "running", "pending")
        parts = ", ".join("%d %s" % (counts[s], s) for s in order if counts.get(s))
        lines.append("totals: %d task(s) [%s]" % (len(self._task_order), parts or "none"))
        return "\n".join(lines)

    @staticmethod
    def _fmt_duration(seconds):
        if seconds is None:
            return "-"
        return "%.2fs" % seconds
