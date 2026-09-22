# Cross-Model Adversarial Review #2 - `orchestrator.py`

Repo: `/home/hermawan/oss/oc-fleet`
Commit saat review: `ebcb644` (branch `master`)
Python: 3.11.15
Suite: `python3 -m pytest -q` → **220 passed, 24 subtests passed** (2.84s)

## Metodologi

Setiap klaim di bawah **direproduksi dengan menjalankan kode Python**, bukan
dibaca saja. Skrip probe ada di `/tmp/advrev/` dan output mentahnya ditempel
apa adanya. Status tiap klaim:

- **DIBUKTIKAN** - ada output nyata dari eksekusi.
- **DIDUGA** - belum dijalankan (kalau ada, ditandai eksplisit).

Yang diuji berbeda dari `review.md` sebelumnya: review ini menekan **fuzz 4000
graf acak**, **injeksi `BaseException`**, **injeksi fault ke `Fleet` asli via
`urlopen`**, dan **integritas record lintas retry dengan waktu nyata** - sudut-sudut yang belum ditutup review pertama.

---

## 1. Race condition / counter `max_parallel`

**Verdict: AMAN - cap tidak bisa dilampaui. DIBUKTIKAN.**

`orchestrator.py` tidak memakai `threading` maupun `concurrent.futures` sama
sekali (dicek: tidak ada import semacam itu). Paralelisme adalah *sesi
konkuren di server fleet*, bukan konkurensi di proses Python, jadi tidak ada
data race. "Counter"-nya adalah `len(running)`, dan cap dipasang di
`_dispatch_ready` (`orchestrator.py:256`). Yang perlu dibuktikan adalah
apakah counter itu bisa salah hitung - khususnya saat **fail-lalu-retry**,
di mana sebuah task masuk dua kali ke `_dispatch_ready`.

Probe `p1_maxparallel.py` melacak sesi hidup (dispatch tapi belum selesai) dan
mencatat puncaknya:

```
== scenario_cap ==
max_parallel = 3, tasks = 8
peak concurrent sessions observed = 3
all dispatched = 8
statuses = {'a': 'succeeded', ..., 'h': 'succeeded'}
VERDICT: cap held (peak 3 <= 3)

== scenario_retry_slot ==
max_parallel = 2; a fails once then retries; b is slow
peak concurrent sessions observed = 2
a statuses: {'status': 'succeeded', 'session_id': 's-2', 'outcome': 'done', 'attempts': 2, ...}
all dispatched prompts = ['pa', 'pb', 'pa']
VERDICT: retry did not double-count a slot (peak 2 <= 2)

== scenario_zero ==
max_parallel=0 rejected: max_parallel must be >= 1, got 0
VERDICT: rejected loudly, good
```

Peak sesi hidup **tidak pernah** melewati `max_parallel`, termasuk saat slot
dipakai ulang oleh retry. `max_parallel=0` ditolak keras (`ValueError`), bukan
diam-diam di-clamp.

Probe tambahan `p9_order.py` (retry dengan `max_parallel=1` dan sibling yang
lambat) juga menunjukkan peak = 1:

```
=== A. retry with max_parallel=1 and a slow sibling ===
  dispatch order: ['pa', 'pa', 'pb']
  statuses: {'a': 'succeeded', 'b': 'succeeded'}
  attempts a = 2
```

Catatan minor (bukan cacat): retry disisipkan di `self._task_order.index(tid)`
(`orchestrator.py:356`), jadi task yang gagal **menyerobot** antrean di depan
sibling yang belum pernah jalan. Ini adil-atau-tidak, bukan bug korektnes;
tidak ada cap yang dilanggar.

---

## 2. Deteksi siklus - `validate()` dan `_find_cycle()`

**Verdict: BENAR untuk semua kasus yang diminta (self-loop, 2-cycle, 3-cycle,
unreachable) + lolos fuzz 4000 graf. DIBUKTIKAN.**

`p2_cycles.py` menguji semua kasus yang diminta plus varian tambahan:

```
self-loop _find_cycle() = ['a', 'a']
[self-loop] expect_cycle=True -> dependency cycle detected: a -> a  OK
[self-loop via add] rejected: task 'a' depends on unknown task 'a'
2-cycle _find_cycle() = ['a', 'b', 'a']
[2-cycle] expect_cycle=True -> dependency cycle detected: a -> b -> a  OK
3-cycle _find_cycle() = ['a', 'b', 'c', 'a']
[3-cycle] expect_cycle=True -> dependency cycle detected: a -> b -> c -> a  OK
5-cycle _find_cycle() = ['a', 'b', 'c', 'd', 'e', 'a']
[5-cycle] expect_cycle=True -> dependency cycle detected: a -> b -> c -> d -> e -> a  OK
unreachable-cycle _find_cycle() = ['p', 'q', 'p']
[unreachable-cycle] expect_cycle=True -> dependency cycle detected: p -> q -> p  OK
diamond _find_cycle() = None
[diamond-valid] expect_cycle=False -> NO CYCLE (validate passed)  OK
chain _find_cycle() = None
[chain-valid] expect_cycle=False -> NO CYCLE (validate passed)  OK
deep-self-loop _find_cycle() = ['b', 'b']
[deep-self-loop] expect_cycle=True -> dependency cycle detected: b -> b  OK

ALL CYCLE CASES MATCH EXPECTATION: True
```

**Self-loop** ditolak dua lapis: `add()` menolaknya lebih dulu
(`task 'a' depends on unknown task 'a'`), dan kalau disuntik lewat mutasi
pasca-`add`, `_find_cycle` tetap menangkap `['a', 'a']`.

**Node tak terjangkau**: karena DFS mengiterasi *setiap* task id sebagai root
(`orchestrator.py:140`), siklus `p<->q` yang terisolasi (tak terjangkau dari
dependen mana pun) tetap terdeteksi. Ini yang sering bocor di implementasi
lain, dan di sini benar.

Klaim reviewer sebelumnya bahwa `path.index(dep)` aman diverifikasi lewat fuzz
`p10_fuzz_cycle.py` - 4000 graf acak, hasilnya dicocokkan dengan detektor
siklus independen (algoritma Kahn):

```
fuzz runs: 4000
_find_cycle raised: never
returned a non-cycle path: never
disagreement with Kahn: none
VERDICT: find_cycle is correct on all random graphs
```

`_find_cycle` **tidak pernah** melempar exception (tidak ada `ValueError` dari
`path.index`), selalu mengembalikan path siklus yang valid, dan **tidak pernah
berbeda** dengan algoritma Kahn pada 4000 graf acak.

Probe `p2b_edge.py` juga memvalidasi bentuk path pada graf dengan cross-edge:

```
=== A. _find_cycle on a graph with cross edges ===
  path = ['b', 'c', 'b']
=== D. _find_cycle returns a genuinely valid cycle path ===
  case 0: path=['a', 'a'] valid=True
  case 1: path=['a', 'b', 'a'] valid=True
  case 2: path=['a', 'b', 'c', 'a'] valid=True
```

**Verdict: tidak ada cacat pada deteksi siklus.**

---

## 3. Skip dependen transitif

**Verdict: BENAR - propagasi transitif penuh, termasuk DAG bercabang.
DIBUKTIKAN.**

`p3_skip.py` menguji rantai `a→b→c→d` di mana `a` gagal terminal:

```
== chain a->b->c->d, a fails ==
  a: failed
  b: skipped
  c: skipped
  d: skipped
  dispatched prompts: ['pa']
  VERDICT: ALL TRANSITIVE SKIPS OK
```

**Semua** dependen transitif di-skip, dan **tidak satu pun** yang
di-dispatch (`dispatched prompts: ['pa']` saja).

DAG bercabang - `a` gagal, dengan dua sub-pohon di bawahnya plus cabang
mandiri `e`:

```
== branching DAG, a fails, e independent ==
  a: failed
  b: skipped
  c: skipped
  d: skipped
  e: succeeded
  f: skipped
  dispatched prompts: ['pa', 'pe']
  VERDICT: BRANCH SKIPS OK
  dependents dispatched (should be none): []
```

Cabang mandiri `e` tetap sukses (tidak ikut ter-skip), sedangkan kedua sub-pohon
`b→d` dan `c→f` semuanya di-skip dan tidak pernah di-dispatch.

Diamond dengan kegagalan parsial juga benar:

```
== diamond a->(b,c)->d, b fails ==
  a: succeeded
  b: failed
  c: succeeded
  d: skipped
  VERDICT: d skipped
```

`d` di-skip karena `b` (salah satu dari dua dependennya) gagal. **Tidak ada
cacat.**

---

## 4. Timeout dan penanganan error saat poll (waiter raise mid-run)

**Verdict: AMAN untuk semua tipe `Exception` nyata; ada satu lubang residual
`BaseException` (lihat §7). DIBUKTIKAN.**

`p4_waiter.py` menyuntikkan `status()` yang selalu raise, untuk 7 tipe
exception:

```
[status-raises: OSError]
  run() survived
  final statuses: {'a': 'failed', 'b': 'failed', 'c': 'failed'}
  stranded in 'running': []
[status-raises: ValueError]   ... survived, stranded=[]
[status-raises: KeyError]     ... survived, stranded=[]
[status-raises: RuntimeError] ... survived, stranded=[]
[status-raises: TypeError]    ... survived, stranded=[]
[status-raises: AttributeError] ... survived, stranded=[]
[status-raises: Exception]    ... survived, stranded=[]
```

**`run()` selamat di ketujuh tipe**, dan **tidak ada task yang ditinggal di
status `"running"`** - semuanya di-transisikan ke `failed` lewat deadline.
Ini konsisten dengan `RUN_ERRORS = Exception` (`orchestrator.py:39`).

Transient error yang **pulih** juga ditangani (poll gagal sekali, lalu sukses):

```
[recover OSError]
  run() survived
  statuses: {'a': 'succeeded', 'b': 'succeeded'}
```

`p4b_nondict.py` menguji respons status non-dict (None, list, string) - yang
dulu bisa menghapus `last_assistant_text` tersimpan:

```
[None-then-real-text-then-success] crashed=None status=succeeded outcome='succeeded' last_text='DONE'
[list-then-success] crashed=None status=succeeded outcome='succeeded' last_text='DONE'
[string-then-success] crashed=None status=succeeded outcome='succeeded' last_text='DONE'
[progress-then-None-forever] crashed=None status=failed outcome=None last_text='PROGRESS'

[success-before-deadline] status=succeeded outcome='succeeded' last_text='LATE-OK'
```

Teks terakhir yang diketahui (`PROGRESS`) **dipertahankan** meski poll
berikutnya mengembalikan non-dict/None. Sukses yang tiba sebelum deadline tidak
dibuang.

Untuk mengukur ketepatan timeout (klaim perbaikan agar tidak overshoot satu
interval penuh), `test_timeout_does_not_overshoot_by_a_poll_interval`
(interval 3.0s, timeout 0.3s) ada di suite dan lolos. Secara eksperimen:
`poll_interval` dipotong sampai deadline terdekat (`orchestrator.py:210-216`),
jadi overshoot ≤ 1 sweep, bukan 1 `poll_interval` penuh.

**Kesimpulan: run selamat dari waiter yang raise - untuk seluruh ruang
`Exception`. Tidak ada task yang terkunci di `running`.**

---

## 5. `Fleet.dispatch` raising exception dari tipe apa pun

**Verdict: AMAN untuk seluruh `Exception` (7 tipe diuji). Ada lubang residual
`BaseException`. DIBUKTIKAN.**

`p5_dispatch.py`, dispatch selalu raise, tanpa retry:

```
=== dispatch always raises; 3 tasks, no retries ===
  OSError        crashed=False statuses={'a': 'failed', 'b': 'failed', 'c': 'failed'} stranded=[]
  ValueError     crashed=False statuses={'a': 'failed', 'b': 'failed', 'c': 'failed'} stranded=[]
  KeyError       crashed=False statuses={'a': 'failed', 'b': 'failed', 'c': 'failed'} stranded=[]
  RuntimeError   crashed=False statuses={'a': 'failed', 'b': 'failed', 'c': 'failed'} stranded=[]
  TypeError      crashed=False statuses={'a': 'failed', 'b': 'failed', 'c': 'failed'} stranded=[]
  AttributeError crashed=False statuses={'a': 'failed', 'b': 'failed', 'c': 'failed'} stranded=[]
  Exception      crashed=False statuses={'a': 'failed', 'b': 'failed', 'c': 'failed'} stranded=[]
```

Selenjutnya, dispatch gagal sekali lalu sukses di retry (`retries=1`):

```
=== dispatch fails once then succeeds on retry (retries=1) ===
  OSError        crashed=False status=succeeded attempts=2 session_id=s-retry outcome=succeeded last_text='OK2'
  ValueError     ... status=succeeded attempts=2 session_id=s-retry outcome=succeeded
  KeyError       ... status=succeeded attempts=2 session_id=s-retry outcome=succeeded
  RuntimeError   ... status=succeeded attempts=2 session_id=s-retry outcome=succeeded
  TypeError      ... status=succeeded attempts=2 session_id=s-retry outcome=succeeded
  AttributeError ... status=succeeded attempts=2 session_id=s-retry outcome=succeeded
  Exception      ... status=succeeded attempts=2 session_id=s-retry outcome=succeeded
```

Untuk **ketujuh tipe**, task tidak pernah dibiarkan `running`; kegagalan
dispatch diintegrasikan dengan mesin retry dengan benar.

Untuk memastikan tipe exception mana yang **benar-benar** bisa keluar dari
`Fleet` asli, `p8_realfleet.py` menyuntikkan fault ke `urllib.request.urlopen`:

```
== real Fleet.dispatch under fault injection ==
  connection refused        -> raised URLError (OSError)
  HTTP 500                  -> raised HTTPError (OSError)
  HTTP 401                  -> raised HTTPError (OSError)
  empty body (None session) -> raised RuntimeError: POST /api/session did not return a session id; got NoneType None
  non-JSON body             -> raised JSONDecodeError (ValueError)
  JSON without id           -> raised RuntimeError: ... got dict {'title': 'x'}
  JSON wrong type (list)    -> raised RuntimeError: ... got list [1, 2, 3]
  timeout                   -> raised TimeoutError (OSError)

== real Fleet.status under fault injection ==
  connection refused        -> raised URLError (OSError)
  HTTP 500                  -> raised HTTPError (OSError)
  empty body                -> OK {'outcome': None, 'last_assistant_text': None}
  non-JSON body             -> raised JSONDecodeError (ValueError)
  HTTPException IncompleteRead -> raised IncompleteRead (Exception)
```

**Semua** exception nyata dari `Fleet` adalah subclass `Exception`
(`URLError`/`HTTPError`/`TimeoutError` → `OSError`; `JSONDecodeError` →
`ValueError`; `IncompleteRead` → `http.client.HTTPException`; sisanya
`RuntimeError`). Karena `RUN_ERRORS = Exception`, **seluruh permukaan error
nyata tertutup**.

**Verdict: tidak ada cacat untuk semua tipe `Exception`. Satu lubang residual
`BaseException` dibahas di §7.**

---

## 6. Integritas hasil setelah retry sukses di attempt ke-2

**Verdict: field konsisten; semantik `duration` mencampur skala-task dan
skala-attempt (terdokumentasi, bukan bug fungsional). DIBUKTIKAN.**

`p6_retry.py` - attempt 1 gagal, attempt 2 sukses, sesi berbeda:

```
== record after attempt-2 success ==
  status       = 'succeeded'
  session_id   = 'sess-2'
  outcome      = 'succeeded'
  attempts     = 2
  last_text    = 'second ok'
  duration     = 0.0
  session ids dispatched = ['sess-1', 'sess-2']

== consistency checks ==
  [PASS] attempts == 2
  [PASS] status == succeeded
  [PASS] outcome == succeeded
  [PASS] session_id == sess-2 (final attempt)
  [PASS] last_text == 'second ok'
  [PASS] duration is not None
  [PASS] duration >= 0
  [PASS] started_at set
  [PASS] finished_at >= started_at
  [PASS] duration == finished-started (rounded)
```

`attempts == 2`, `session_id` menunjuk sesi attempt terakhir (`sess-2`),
`outcome`/`status`/`last_text` konsisten. Untuk mengukur cakupan `duration`
dengan waktu nyata, `p6b_duration.py` membuat attempt 1 timeout (~0.15s) lalu
attempt 2 sukses instan:

```
== timeout-then-success, real time ==
  status       = 'succeeded'
  session_id   = 'sess-2'
  outcome      = 'succeeded'
  attempts     = 2
  duration     = 0.150
  timeout/attempt = 0.15s

  => duration INCLUDES attempt 1's timeout (whole-task span), while
     session_id/outcome describe ONLY the final attempt
```

**Ini temuan semantik (bukan korektnes):** `duration` mencakup seluruh task
(attempt 1 + retry), sedangkan `session_id`/`outcome` hanya attempt terakhir.
Satu baris `results()` mencampur dua skala waktu. `started_at` di-set sekali
di attempt pertama (`orchestrator.py:265-266`, `if rec["started_at"] is None`)
sehingga tidak pernah di-reset. Docstring `results()` (`orchestrator.py:421-430`)
sudah **mendokumentasikan** perilaku ini, jadi ini terdokumentasi - tapi tetap
jebakan bagi pemakai yang mengira `duration` = durasi attempt terakhir.

`p9_order.py` membuktikan konsistensi lebih lanjut: dependen `b` **tidak**
mulai di antara attempt 1 dan retry `a` - `b` menunggu sampai retry `a`
benar-benar sukses:

```
=== C. dependent waits for the RETRY to succeed (a retries=1 -> b) ===
  log: [('dispatch','pa'), ('status','s-0','failed'), ('dispatch','pa'),
        ('status','s-1','done'), ('dispatch','pb'), ('status','s-2','done')]
  a: succeeded attempts 2
  b: succeeded
  dispatch order: ['pa', 'pa', 'pb']
  VERDICT: b started only after a's retry
```

---

## 7. Temuan residual: `RUN_ERRORS = Exception` bocor pada `BaseException`

**Verdict: BUG RESIDUAL (severity rendah) - DIBUKTIKAN.**

`RUN_ERRORS = Exception` (`orchestrator.py:39`) menangkap semua `Exception`,
tetapi **tidak** menangkap subclass `BaseException` yang bukan `Exception`:
`KeyboardInterrupt`, `SystemExit`, `GeneratorExit`, dan `BaseException` kustom.
Probe `p7_baseexception.py`:

```
== dispatch raises BaseException subclass ==
dispatch:KeyboardInterrupt   crashed=True  stranded_running=['a'] statuses={'a':'running','b':'pending','c':'pending'}
dispatch:SystemExit          crashed=True  stranded_running=['a'] statuses={'a':'running','b':'pending','c':'pending'}
dispatch:GeneratorExit       crashed=True  stranded_running=['a'] statuses={'a':'running','b':'pending','c':'pending'}
dispatch:BaseException       crashed=True  stranded_running=['a'] statuses={'a':'running','b':'pending','c':'pending'}

== status raises BaseException subclass ==
status:KeyboardInterrupt     crashed=True  stranded_running=['a','b','c'] statuses={'a':'running','b':'running','c':'running'}
status:SystemExit            crashed=True  stranded_running=['a','b','c'] statuses={'a':'running','b':'running','c':'running'}
status:GeneratorExit         crashed=True  stranded_running=['a','b','c'] statuses={'a':'running','b':'running','c':'running'}
status:BaseException         crashed=True  stranded_running=['a','b','c'] statuses={'a':'running','b':'running','c':'running'}

== control: same shapes but Exception subclass -> must survive ==
dispatch:RuntimeError        crashed=False stranded_running=[] statuses={'a':'failed','b':'failed','c':'failed'}
status:RuntimeError          crashed=False stranded_running=[] statuses={'a':'failed','b':'failed','c':'failed'}
```

**Langkah reproduksi persis:**

```bash
cd /home/hermawan/oss/oc-fleet
python3 /tmp/advrev/p7_baseexception.py
```

Perhatikan baris `dispatch:KeyboardInterrupt` → `stranded_running=['a']` dan
`status:KeyboardInterrupt` → `stranded_running=['a','b','c']`.

**Analisis reachability:** `p8_realfleet.py` membuktikan `Fleet` asli **tidak
pernah** melempar `BaseException` - seluruh permukaan errornya adalah
`Exception`. Jadi bug ini hanya terpicu bila `fleet.dispatch`/`fleet.status`
melempar `KeyboardInterrupt`/`SystemExit` **langsung** dari dalam (mis. sinyal
SIGINT tiba saat kode sedang berada di dalam panggilan fleet), atau bila test
double mengganti fleet dengan yang melempar `BaseException`.

**Apakah ini harus diperbaiki?** Untuk `KeyboardInterrupt` dan
`SystemExit`, menangkapnya justru **tidak diinginkan** - Ctrl-C harus
membatalkan program, bukan ditelan jadi "satu attempt gagal". Jadi perilaku
sekarang (biarkan `BaseException` menembus) sebenarnya **benar secara
desain**. Yang tersisa hanyalah konsekuensi: kalau itu terjadi, task lain
ditinggalkan di `running`. Itu trade-off yang wajar untuk pembatalan
user-initiated.

**Rekomendasi (opsional):** dokumentasikan eksplisit di komentar `RUN_ERRORS`
bahwa `BaseException` (Ctrl-C/SystemExit) sengaja dibiarkan lewat, sehingga
pembaca berikutnya tidak "memperbaiki" jadi `except BaseException` dan
menelan Ctrl-C. Tidak perlu mengubah kode.

---

## Ringkasan

| # | Failure mode | Verdict | Status |
|---|---|---|---|
| 1 | Race condition / counter `max_parallel` | AMAN - cap tak bisa dilampaui, peak terukur = cap, retry reuse slot benar | DIBUKTIKAN |
| 2 | Deteksi siklus (`validate`/`_find_cycle`) | BENAR - self-loop, 2/3/5-cycle, unreachable semua tertangkap; fuzz 4000 graf cocok dgn Kahn | DIBUKTIKAN |
| 3 | Skip dependen transitif | BENAR - rantai & DAG bercabang, cabang mandiri tetap jalan | DIBUKTIKAN |
| 4 | Waiter raise saat poll | AMAN untuk semua `Exception`; tak ada task tertinggal `running`; non-dict aman | DIBUKTIKAN |
| 5 | `dispatch` raising | AMAN untuk seluruh `Exception` (7 tipe); semua error nyata `Fleet` tertutup | DIBUKTIKAN |
| 6 | Integritas hasil setelah retry attempt-2 | KONSISTEN (`attempts=2`, `session_id` final); `duration` mencakup seluruh task - didokumentasikan | DIBUKTIKAN |
| 7 | **Residual:** `RUN_ERRORS = Exception` bocor `BaseException` | **BUG RESIDUAL severity rendah** - `KeyboardInterrupt`/`SystemExit`/`GeneratorExit` meninggalkan task `running`; tapi menangkapnya justru tidak diinginkan untuk Ctrl-C | DIBUKTIKAN |

### Kesimpulan

Kelima failure mode pertama (1 - 5) **sudah ditangani dengan benar** dan
dibuktikan lewat eksekusi. Failure mode 6 (integritas hasil retry) **konsisten
secara field**, dengan satu catatan semantik (`duration` lintas-attempt) yang
sudah didokumentasikan di docstring.

Satu-satunya temuan baru adalah **§7**: `RUN_ERRORS = Exception` tidak
menangkap `BaseException`. Ini **bukan** regresi dari perbaikan sebelumnya - justru perbaikan `RUN_ERRORS = Exception` sudah menutup seluruh permukaan error
nyata dari `Fleet` (dibuktikan via fault injection ke `urlopen`). Lubang
`BaseException` adalah trade-off desain yang wajar (Ctrl-C harus tembus), jadi
rekomendasinya hanya **dokumentasi**, bukan perubahan kode.

**Tidak ada bug korektnes pada keenam failure mode yang diminta.**

Skrip probe (semua dapat dijalankan ulang):
`/tmp/advrev/p1_maxparallel.py`, `p2_cycles.py`, `p2b_edge.py`, `p3_skip.py`,
`p4_waiter.py`, `p4b_nondict.py`, `p5_dispatch.py`, `p6_retry.py`,
`p6b_duration.py`, `p7_baseexception.py`, `p8_realfleet.py`, `p9_order.py`,
`p10_fuzz_cycle.py`.

---

## Verifikasi independen atas review ini

Review ini tidak diterima begitu saja. Dua klaim terkuat diperiksa
ulang oleh penulis commit, dengan hasil berikut.

### Klaim "fuzz 4000 graf cocok dengan Kahn" - DIKONFIRMASI

Fuzz independen, 2000 graf acak (1-7 node, kepadatan 0.25), deteksi
siklus `validate()` dibandingkan dengan implementasi Kahn referensi:

```
2000 graf, 0 perbedaan
```

Catatan penting: percobaan pertama menghasilkan **776 perbedaan**, dan
itu **bukan** bug orchestrator. Skrip verifikasi pertama memakai
`o.add(Task(..., depends_on=[...]))`, yang menolak edge ke task yang
belum terdaftar dan diam-diam dilewati oleh `try/except`. Jadi graf yang
diuji berbeda dari yang dibandingkan.

Memperbaiki skripnya (daftarkan semua task dulu, baru set `depends_on`)
menghasilkan 0 perbedaan. Pelajaran: saat sebuah perbandingan melaporkan
ratusan perbedaan, periksa dulu alat ukurnya.

### Klaim §7 "BaseException bocor" - DIKONFIRMASI, dan ditindak

Diverifikasi: `issubclass(KeyboardInterrupt, RUN_ERRORS)` adalah False,
dan `KeyboardInterrupt` memang menembus `run()`. Itu benar, dan benar
juga bahwa itu **perilaku yang diinginkan**.

Rekomendasi review (dokumentasi, bukan perubahan kode) diikuti:
komentar di `RUN_ERRORS` sekarang menjelaskan kenapa bukan
`BaseException`, plus tiga tes yang mengunci keputusan itu
(`test_ctrl_c_is_not_swallowed_by_run`, `test_system_exit_is_not_swallowed_by_run`,
`test_run_errors_is_exception_not_base_exception`).

### Yang TIDAK diubah

Tidak ada kode yang diubah karena review ini. Kelima failure mode
pertama sudah benar, dan satu-satunya temuan baru adalah trade-off desain
yang memang disengaja.
