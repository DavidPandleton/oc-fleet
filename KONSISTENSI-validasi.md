# Konsistensi validasi dispatch di tiga lapisan

Dibuat 2026-09-22, setelah menemukan bahwa `fleet.py` dan `orchestrator.py`
tertinggal dari `web/dashboard.py` dalam hal validasi input.

---

## Temuan

Ada tiga jalan masuk ke `Fleet.dispatch()`, dan sebelum hari ini ketiganya
memeriksa hal yang **berbeda**:

| Lapisan | task kosong | workdir kosong | Respons tanpa id |
|---|---|---|---|
| `web/dashboard.py` `validate_dispatch` | ditolak | ditolak | ditangani (502) |
| `orchestrator.py` `validate()` | **lolos** | **lolos** | ditangani |
| `fleet.py` `dispatch()` | **lolos** | **lolos** | `KeyError: 'id'` |

Dashboard sudah benar sejak awal. Dua lapisan lain tertinggal.

---

## Kenapa dashboard sudah benar

`web/dashboard.py` punya fungsi terpisah:

```python
def validate_dispatch(data):
    if not isinstance(data, dict):
        raise ValueError("payload must be a JSON object")
    task = str(data.get("task") or "").strip()
    workdir = str(data.get("workdir") or "").strip()
    if not task:
        raise ValueError("task is required")
    if not workdir:
        raise ValueError("workdir is required")
    ...
```

Ini validasi sebagai **tahap tersendiri**, sebelum efek samping apa pun -
prinsip yang sama dengan `preflight/engine.py` di LintLang. Dan `do_POST`
memetakan setiap kegagalan ke status HTTP yang tepat: 400 untuk payload
buruk, 502 untuk kegagalan upstream.

Pola itulah yang sekarang dipakai di tiga lapisan.

---

## Yang diperbaiki

**`fleet.py` `dispatch()`** - menolak `task`/`workdir` kosong, dan menyebut
respons tanpa `id` sebagai `RuntimeError` yang menjelaskan panggilannya,
bukan `KeyError: 'id'`.

**`orchestrator.py` `validate()`** - memeriksa `prompt`/`workdir` setiap
Task di depan, supaya kegagalan muncul sebelum sesi apa pun jalan.

Perilakunya sekarang **identik** dengan `validate_dispatch`:

```
task kosong    -> ValueError
task spasi     -> ValueError
workdir kosong -> ValueError
workdir spasi  -> ValueError
normal         -> lolos
```

---

## Pelajaran

Perbaikan ini **bukan inovasi**. Standar yang benar sudah ada di repo,
di `web/dashboard.py`. Yang kurang adalah menyamakannya ke dua lapisan
lain.

Itu bentuk utang teknis yang sering luput: satu jalur diperketat, jalur
lain tidak, dan tidak ada yang membandingkan. Cara menemukannya bukan
membaca kode dengan cermat, tapi **menguji perilaku ketiganya berdampingan**.

Saran: kalau menambah jalan masuk ke `dispatch()` yang keempat, jalankan
tabel di atas lagi. Empat kasus, masing-masing satu baris.

---

## Catatan: dashboard sudah punya tes

`tests/test_dashboard.py` sudah menguji `validate_dispatch` untuk keempat
kasus penolakan, plus payload yang bukan objek sama sekali (`["task"]`).
Jadi lapisan dashboard lengkap: validasi benar **dan** teruji.

Yang kurang hanya kesamaan perilaku di dua lapisan lain. Itu sudah
ditambahkan, dengan tes di `test_fleet.py` dan `test_orchestrator.py`.
