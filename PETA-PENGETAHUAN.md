# oc-fleet - peta pengetahuan

Dibuat 2026-09-22 dari membaca kode, bukan dari ingatan. Semua klaim di sini
dicek langsung ke berkasnya.

Tujuan: fleet agent OpenCode, dijalankan dari CLI. Bukan pengganti Hermes
delegation - ini spesifik OpenCode, lewat HTTP API-nya.

---

## 1. Bentuk keseluruhan

```
                          opencode serve  (127.0.0.1:4096, HTTP Basic)
                                    ^
                                    |  JSON over HTTP
        +---------------------------+---------------------------+
        |                           |                           |
   +---------+              +--------------+            +--------------+
   | fleet.py|              |orchestrator  |            | web/dashboard|
   |  (233)  |<-------------|    .py       |            |     .py      |
   |         |   dipakai    |    (411)     |            |    (698)     |
   | Fleet   |   oleh       | Orchestrator |            |  HTTP server |
   +---------+              | Task         |            |  + SSE       |
        ^                   +--------------+            +--------------+
        |                          ^                           |
        |                          |                           |
   +---------+              +--------------+            +--------------+
   | cli.py  |              |oc-fleet-wait |            | prompt_lint  |
   |  (348)  |              |    .py (117) |            |    .py (415) |
   +---------+              +--------------+            +--------------+
        |                          |                           |
        +--------------------------+---------------------------+
                    semua bergantung pada fleet.Fleet

Angka dalam kurung = jumlah baris. Total 2.131 baris kode
(di luar 2.300 baris tes).
```

Intinya: **satu client HTTP (`Fleet`), empat cara memakainya.**

---

## 2. Lima bagian, dan apa yang sebenarnya mereka lakukan

### `fleet.py` - client HTTP (254 baris)

Semua yang bicara ke server OpenCode ada di sini. Tidak ada yang lain yang
memanggil HTTP langsung.

```
Fleet
 |
 +-- _find_password()   cari password, berurutan:
 |                      env -> password_file -> /tmp/oc_serve.log ->
 |                      ~/.local/share/opencode/serve.log ->
 |                      ~/.config/opencode/service.json
 |
 +-- _request()         satu-satunya pintu HTTP. urllib, bukan requests.
 |                      Body kosong -> None (bukan error).
 |
 +-- _unwrap()          buka envelope {"data": ...} kalau ada.
 |                      PENTING: bentuk respons server TIDAK konsisten.
 |
 +-- _parse_model()     "provider/model" -> {"providerID","id"}
 |                      raise ValueError kalau salah format  <-- hasil sesi ini
 |
 +-- dispatch()         bikin session + kirim prompt. Balikin session_id.
 +-- status()           polling. Balikin {outcome, last_assistant_text}
 +-- cancel()           stop session. TRUE-TRISTATE  <-- hasil sesi ini
 +-- list_sessions()    daftar session
 +-- stats()            agregat
 +-- sanitize()         buang em-dash/en-dash dari output agent
```

**Yang paling penting dipahami: `cancel()` sekarang tri-state.**

| Nilai | Arti | Kenapa |
|---|---|---|
| `True` | server konfirmasi interrupt | - |
| `False` | tidak ada yang perlu dihentikan | sesi sudah selesai sendiri |
| `None` | **tidak ada keputusan** | 404, 401, atau server mati |

`None` itu falsy, jadi kode lama yang cuma `if stopped:` tetap jalan. Tapi
`orchestrator` sekarang bisa membedakan "aman" dari "tidak tahu".

### `cli.py` - lapisan argparse (348 baris)

Thin wrapper. Terjemahin argumen -> panggilan `Fleet`.

```
status | dispatch | watch | sessions | show | stats
```

`dispatch` punya penanganan khusus: `ValueError` dari model yang salah
format ditangkap dan dicetak sebagai pesan bersih, **bukan** lewat
`API_ERRORS` global - karena itu kesalahan pengguna, bukan kegagalan API.

### `orchestrator.py` - DAG runner (423 baris)

Ini bagian paling berisi. Dua kelas:

```
Task
  id, prompt, workdir, model, title,
  depends_on[], retries, timeout

Orchestrator(fleet, max_parallel=4, poll_interval=3.0)
  add(task)      -> ValueError kalau id duplikat / dependency tak dikenal
  validate()     -> deteksi dependency hilang + siklus
  run()          -> jalanin DAG
  results()      -> deep copy (dulu balikin dict internal, bisa dirusak)
```

**Cara kerjanya:**

```
   pending ──(semua dependency 'succeeded')──> running
      |                                          |
      |                                     poll status()
      |                                          |
      |                     +--------------------+--------------------+
      |                     |                    |                    |
      |                outcome ada          tidak ada           deadline
      |                     |                outcome             lewat
      |                     v                    |                    |
      |              _finish_attempt            |                    v
      |                     |               lanjut poll        timeout
      |         +-----------+-----------+                         |
      |         |           |           |                         |
      |     sukses     gagal,      gagal,                      sama
      |         |      masih ada   retry habis                 seperti
      |         |      retry          |                        gagal
      |         |        |            v
      |    dependents  cancel()    dependents
      |    jalan       lalu retry  -> skipped
      |                                   |
      +-----------------------------------+
```

**Dua hal yang mudah salah dipahami:**

1. **Task gagal tidak membunuh branch lain.** Dependent-nya di-skip, branch
   yang tidak berhubungan jalan terus.
2. **Pesan error dibedakan dengan hati-hati.** `dispatch` yang gagal sebelum
   dapat session_id **bukan** timeout - kodenya secara eksplisit tidak
   "mempercantik" kegagalan jadi timeout.

### `web/dashboard.py` - dashboard stdlib (698 baris)

Port default **8787** (`DEFAULT_PORT`), stdlib saja, tanpa
flask/npm.

```
do_GET   /             halaman
         /api/sessions  tabel session
         /api/events    SSE stream
do_POST  /api/dispatch  form dispatch dari browser
```

Refresh tabel tiap 5 detik, plus completion live lewat SSE.

### `prompt_lint.py` - linter prompt (415 baris)

Bukan sekadar cek gaya. Aturannya **berbasis pengukuran** dari 16 probe
terkontrol - jadi ini catatan empiris tentang bagaimana agent gagal.

```
RULES (umum)
  - fluff (chain-of-thought, urgency, role) yang terbukti tidak ngefek
  - scope tak terbatas
  - output tak terverifikasi
  - em-dash
  - destructive verb tanpa preservation constraint

RULES (SPEC-*, dari eksperimen delegasi 2026-09-21)
  SPEC-UNDEFINED-EDGE          enumerasi terbuka ("etc") -> agent ngarang case
  SPEC-TEST-ONLY-VALID         "verify examples above" = cuma happy path
  SPEC-NO-INVALID-CONTRACT     tidak nyebut perilaku input invalid
  SPEC-VERIFY-SELF-REFERENTIAL verifikasi mandiri, bukan bukti independen
```

Alasan `SPEC-*` ada: agent mengisi celah spec dengan **aturan karangan
sendiri yang terdengar masuk akal**. Contoh nyata: parser durasi diminta,
bentuk valid disebut, tapi `"1h1h"` tidak. Agent ngarang "unit harus urut
menurun", menolak input legal, dan **tesnya sendiri lulus** karena ditulis
dari asumsinya sendiri.

### `oc-fleet-wait.py` - waiter lepas (117 baris)

Dipakai saat `dispatch --detach`. Proses terpisah, polling session sendirian.

```
tulis   /tmp/oc_result_<epoch>.json   {session_id, outcome, last_assistant_text}
append  ~/.hermes/mailbox/opencode/alerts.log

exit 0 = selesai | 1 = timeout | 2 = error API
```

Catatan: docstring lama menyebut `.json`, kode sekarang juga `.json`.
File `.txt` di `/tmp` itu dari versi lama (20 Sep), sisa usang.

---

## 3. Cara semua bagian tersambung

```
   PENGGUNA
      |
      +--- terminal ---> cli.py ---+
      |                            |
      +--- browser ----> dashboard-+---> fleet.Fleet ---> opencode serve
      |                    .py     |         ^
      +--- python -----> orchestrator       |
      |                    .py -----+-------+
      +--- dispatch --detach --> oc-fleet-wait.py
      |
      +--- sebelum kirim ---> prompt_lint.py   (cek prompt, tidak kirim apa pun)

TES (~2.537 baris, 206 lulus)
  test_fleet.py 20 | test_cli.py 26 | test_cancel.py 13
  test_orchestrator.py 34 | test_orchestrator_adversarial.py 6
  test_prompt_lint.py 30 | test_prompt_lint_spec_gaps.py 19
    test_prompt_lint_karakterisasi.py 10
  test_review_minor.py 8 | test_review_temuan.py 16
  tests/test_dashboard.py 24
```

---

## 4. Hal-hal server yang di-encode di kode

Semua ini ditemukan dengan menembak server hidup, bukan dari dokumentasi:

1. **Envelope respons tidak konsisten.**
   `/api/session` -> `{"data": [...]}` tapi `/api/project` -> list telanjang.
   Karena itu ada `_unwrap()`.

2. **`Content-Type: application/json` wajib.** Tanpa itu, tiap POST dapat 415.

3. **Auth HTTP Basic** (`opencode:<password>`), bukan Bearer.

4. **Selesai ditandai event SSE `session.execution.succeeded`**,
   bukan pesan `idle`.

5. **Server menerima model yang salah** dengan HTTP 200 dan menyimpannya.
   Ini yang membuat `_parse_model()` raise - lebih baik gagal di depan
   daripada jadi session rusak yang meledak belakangan.

6. **`cancel()` pada session yang sudah selesai -> 200 `{"interrupted":false}`**,
   bukan error. Menghapus session -> `204 No Content`.

---

## 5. Jebakan yang sudah dibayar mahal (ada di komentar kode)

Ini bug nyata yang pernah kejadian, dan komentarnya sengaja ditinggal:

| Jebakan | Akibatnya | Penangkalnya sekarang |
|---|---|---|
| `http.client.HTTPException` bukan subclass `OSError` | satu error menumbangkan seluruh run, semua task tersangkut "running" | `API_ERRORS` diperluas + `POLL_ERRORS = Exception` |
| Poll balikin non-dict | `last_state` ke-reset `{}`, teks terakhir hilang | cek `isinstance(state, dict)` |
| Hasil balikin dict internal | pemanggil mengubah `results()["a"]`, state korup | `results()` balikin deep copy |
| `max_parallel=0` diam-diam jadi 1 | jalan serial tanpa alasan jelas | sekarang `ValueError` |
| `cancel()` gagal = `False` | retry jalan barengan session yang belum berhenti | tri-state `None` |
| `dispatch()` kirim model rusak | session dibuat, gagal jauh belakangan | `_parse_model()` raise |

---

## 6. Nilai sebenarnya tool ini

Dibangun dan di-debug sepenuhnya lewat OpenCode HTTP API sendiri (130+
session, 600+ tool call, ~97% sukses). Tool ini ada karena **mengemudikan N
agent paralel dari shell jadi tidak terkelola dengan cepat.**

Yang bikin dia berguna bukan "bisa jalanin agent", tapi:

- DAG + retry + skip dependent yang benar (bukan asal paralel)
- Semua keanehan server yang sudah dipetakan, jadi tidak perlu ditemukan ulang
- `prompt_lint` yang berisi **pengetahuan empiris tentang cara agent gagal**

---

## 7. Yang perlu diketahui tapi belum ada di dokumen

1. **`DESIGN.md` usang.** Isinya 24 baris, hanya menyebut `fleet.py` dan
   `cli.py`. Tidak menyebut `orchestrator.py`, `dashboard.py`,
   `prompt_lint.py`, atau `oc-fleet-wait.py` - padahal itu 1.700+ baris.
   README jauh lebih akurat.

2. **Tidak ada `pyproject.toml` / packaging.** Semua dijalankan langsung
   `python3 cli.py`. Tidak bisa di-install.

3. **Ruff melaporkan 120 error pra-eksisting** di seluruh repo, mayoritas
   `UP031` (gaya `%` format, 74 dari 120). Itu konvensi yang dipakai
   konsisten di seluruh berkas, bukan kelalaian - tapi kalau ada yang mau
   bersihin, harus disengaja, bukan sebagian-sebagian.

4. **`/tmp` menumpuk.** Waiter menulis file hasil ke `/tmp` dan tidak pernah
   dibersihkan. Ada 18 file sisa dari kerjaan sebelumnya.

5. **`DESIGN.md` bilang "gak manage opencode serve lifecycle"** - dan itu
   benar. Server harus dijalankan manual; seluruh tool ini akan gagal dengan
   `ConnectionError` kalau server mati. Tidak ada auto-start, tidak ada
   pengecekan kesehatan sebelum dispatch.
