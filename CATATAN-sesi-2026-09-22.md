# Catatan sesi 2026-09-22

Ringkasan kerja pada `oc-fleet`, supaya sesi berikutnya bisa langsung
lanjut tanpa mengulang penelusuran.

---

## Yang ditemukan dan diperbaiki

### Satu pola bug, muncul lima kali

Semua temuan hari ini adalah variasi dari satu pola: **kegagalan yang
tersamar, atau penjagaan yang terlalu sempit/lebar.**

| # | Lokasi | Masalah | Perbaikan |
|---|---|---|---|
| 1 | `prompt_lint.py` FLUFF-* | Prompt yang MELARANG dilapor seolah MEMINTA | `_is_negated` |
| 2 | `prompt_lint.py` VAGUE-OUTPUT, DESTRUCTIVE-NO-GUARD | Sama, dua aturan lain | `_is_negated` diperluas |
| 3 | `fleet.py` `dispatch()` | `KeyError: 'id'` tanpa konteks; workdir kosong lolos | Validasi di depan |
| 4 | `orchestrator.py` `validate()` | prompt/workdir kosong tidak diperiksa | Diperiksa di depan |
| 5 | `orchestrator.py` `_dispatch_ready` | Exception di luar API_ERRORS bunuh run | `RUN_ERRORS = Exception` |
| 6 | `oc-fleet-wait.py` | Sama seperti #5, di waiter detached | `POLL_ERRORS = Exception` |
| 7 | `orchestrator.py` timeout | Overshoot sampai 6x (interval penuh) | Tidur sampai deadline |

### Kenapa #5, #6, #7 penting

Ketiganya **tidak terlihat** dari membaca kode dengan santai:

- #5 dan #6 hanya muncul kalau server mengembalikan **respons rusak**.
  Tidak ada yang mencoba itu.
- #7 hanya muncul dengan `timeout` **lebih pendek** dari `poll_interval`.
  Konfigurasi normal tidak memicunya.

Ketiganya ditemukan dengan **menguji batas**, bukan membaca.

---

## Yang TIDAK diubah, dan kenapa

Bagian ini sama pentingnya. Menghindari perubahan yang salah.

- **`cli.py` `API_ERRORS` tetap sempit.** `main()` adalah batas teratas
  program interaktif; exception di luar daftar berarti bug oc-fleet
  sendiri, dan traceback itu sinyal berguna. Proses latar berlawanan.
  Alasan ini ditulis sebagai komentar di `cli.py` supaya tidak ada yang
  "memperbaiki" secara keliru demi konsistensi.

- **`duration`/`session_id` tidak diubah**, hanya didokumentasikan.
  Tidak ada pemakai yang membaca `duration` selain tes.

- **Output SARIF tidak ditambahkan.** Repo belum punya CI, jadi belum ada
  gunanya.

- **Skip transitive tidak disentuh.** Review menyebutnya "latency, not
  correctness", dan pengujian membenarkan: rantai a→b→c→d→e dan DAG
  bercabang keduanya benar.

---

## Pelajaran yang terulang

**1. Analisis statis menyesatkan.** Analisis AST bilang `_dispatch_ready`
dan `do_GET`/`do_POST` "belum diuji". Keduanya sebenarnya tercakup lewat
`run()` dan server loopback nyata. Mencari nama fungsi di berkas tes
bukan ukuran cakupan.

**2. Kalau perbaikan butuh daftar kata yang makin panjang, itu tambalan.**
Dua percobaan `_COORD_RE` gagal:
- butuh subjek setelah konjungsi → "do not retry, and think step by step"
  jadi salah
- daftar akhiran kata → hampir semua kata cocok

Yang benar lebih sederhana: hanya konjungsi **ber-koma** yang memisah
klausa. Sama dengan `_left_clause_start` LintLang, ditemukan lewat dua
kegagalan.

**3. Memperbaiki bisa memasukkan bug yang lebih buruk.** Setelah
`dispatch()` mulai raise `RuntimeError`, exception itu lolos dari
`API_ERRORS` dan membunuh run - persis cacat yang baru diperbaiki. Ini
kenapa setiap perbaikan harus diuji, bukan diasumsikan benar.

**4. Konsistensi lintas lapisan bukan inovasi.** `web/dashboard.py`
sudah punya `validate_dispatch` yang benar dan teruji sejak awal.
`fleet.py` dan `orchestrator.py` yang tertinggal. Lihat
`KONSISTENSI-validasi.md`.

---

## Status akhir

```
suite    : 216 lulus, 17 subtes
commit   : 22 sejak 39ffed5
worktree : bersih
sinkron  : origin/master
```

Tidak ada pekerjaan yang menggantung. Semua ter-push.

---

## Kalau lanjut

Kandidat, dalam urutan nilai:

1. **`review.md` ditulis pada commit `c11c9b7`, suite 28 tes.** Sekarang
   216. Banyak temuan sudah tertutup. Menjalankan ulang cross-model
   review akan menemukan hal yang berbeda - kode sudah berubah banyak.

2. **`SPEC-*` belum diuji untuk false-positive negasi.** Sengaja tidak
   dimasukkan ke `_is_negated` karena aturan itu membaca seluruh prompt
   sebagai bukti. Tapi belum pernah diuji dengan prompt berlawanan maksud.

3. **Tidak ada CI.** Semua tes jalan manual. `prompt_lint.py` sudah bisa
   jadi langkah pertama CI kalau ditambahkan.

---

## Tambahan: CI ditambahkan, dan langsung membuktikan sesuatu

`.github/workflows/test.yml`, matrix 3.9 / 3.11 / 3.12.

Hasil run pertama, ketiganya hijau:

```
test (3.9)   completed  success
test (3.11)  completed  success
test (3.12)  completed  success
```

Yang penting: **3.9 sukses**, padahal mesin lokal tidak punya 3.9 dan gue
tidak bisa mengujinya di sana.

### Bug yang ditemukan saat menulis workflow

`orchestrator.py` memakai `list[str]` di field dataclass **tanpa**
`from __future__ import annotations`. Di 3.9 anotasi dataclass dievaluasi
saat kelas dibuat, jadi itu `TypeError: 'type' object is not subscriptable`
- modulnya tidak bisa di-import sama sekali.

Jadi menulis matrix CI **menemukan bug kompatibilitas nyata**, sebelum CI
sempat jalan. Pelajaran: mendefinisikan lingkungan yang didukung memaksa
memeriksa asumsi yang sebelumnya tidak pernah diuji.

### Yang diverifikasi vs yang diklaim

Dipisah dengan sengaja:
- 3.11 - suite penuh, lokal
- 3.14 - import semua modul, lokal (lebih ketat dari 3.11)
- 3.9 - tidak ada lokal, **tidak diuji langsung saat itu**. Diprediksi
  benar setelah celah `list[str]` diperbaiki, lalu **dibuktikan CI**.

Kedua baris terakhir itu penting: prediksi yang benar bukan pengganti
bukti, dan CI-lah yang mengubah prediksi jadi bukti.

---

## Tambahan kedua: pola "perbaiki di satu tempat, bukan di setiap pemakai"

Dua bug lagi, keduanya bentuk yang sama: aturan/penjagaan ada, tapi
diterapkan di tempat yang salah.

### `Fleet.sanitize` hanya dipanggil di satu dari tiga jalur

Fungsi itu ada khusus untuk mencegah em-dash keluar. Tapi hanya
`cli.py` `cmd_show` yang memanggilnya:

| jalur | dulu | sekarang |
|---|---|---|
| `oc-fleet show` | aman (memanggil sanitize) | aman |
| `oc-fleet-wait.py` result JSON | **em-dash bocor** | aman |
| `orchestrator` `last_text` | **em-dash bocor** | aman |

Perbaikan: sanitasi dipindah ke `status()`, di batas tempat teks masuk.
Setiap pemakai mewarisi. Kalau ditambah jalur keempat nanti, dia ikut
aman tanpa perlu diingat.

### Kerapuhan bentuk respons hanya dijaga di satu fungsi

`list_sessions` dan `stats` sudah memakai `isinstance` sejak awal.
`status()` tidak. Enam bentuk respons rusak melempar AttributeError atau
TypeError keluar dari sana.

Dampaknya beda per pemanggil, dan itu yang membuatnya tidak terlihat:
- orchestrator dan waiter **selamat** (`RUN_ERRORS = Exception`)
- `oc-fleet show` dan dashboard **tidak selamat** (menangkap lebih sempit)

Jadi menguji lewat orchestrator saja tidak akan menemukannya.

### Pelajaran yang sama, dua kali

Keduanya diperbaiki di **sumber**, bukan di setiap pemanggil. Mengingat
untuk memanggil penjagaan di tiap tempat baru adalah cara bug ini muncul
pertama kali.

---

## Angka akhir sesi

```
suite    : 224 lulus, 24 subtes
commit   : 32 sejak 39ffed5
CI       : GitHub Actions, matrix 3.9 / 3.11 / 3.12, hijau
worktree : bersih
```
