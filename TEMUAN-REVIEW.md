# Temuan review fleet.py

Hasil dua sesi review independen oleh OpenCode (via oc-fleet), plus verifikasi
manual. Semua temuan dibuktikan dengan eksekusi, bukan pembacaan kode.

Status: **kedua temuan sudah diperbaiki.**

---

## Ringkasan

| # | Temuan | Status |
|---|---|---|
| 1 | `dispatch()` mengirim model rusak, atau mengabaikannya diam-diam | **diperbaiki** |
| 2 | `cancel()` mengembalikan `False` untuk lima keadaan berbeda | **diperbaiki** |


## 1. `dispatch()` - parsing model (SUDAH DIPERBAIKI)

### Masalahnya

```python
provider, _, model_id = model.partition("/")
if _ and model_id:
    body["model"] = {"providerID": provider, "id": model_id}
```

`partition("/")` memotong di slash pertama. Kunci `_` (separator) hanya
mengecek "ada slash", `model_id` hanya mengecek "ada isi setelah slash".
**Provider kosong tidak pernah dicek.**

| Input | Yang terjadi (sebelum) | Sifat |
|---|---|---|
| `/foo` | body berisi `{"providerID": "", "id": "foo"}` | data rusak dikirim |
| `" cutad/qwen"` | `providerID = " cutad"` | data rusak dikirim |
| `"cutad /qwen"` | `providerID = "cutad "` | data rusak dikirim |
| `cutad/` | model diabaikan diam-diam | salah ketik tidak terdeteksi |
| `no-slash` | model diabaikan diam-diam | salah ketik tidak terdeteksi |

### Kenapa ini berbahaya

Diuji ke server OpenCode hidup. `POST /api/session` dengan `providerID: ""`
atau `providerID: " cutad"`:

- HTTP **200**, bukan 400
- body rusak **diterima dan disimpan** di sesi

Server tidak menolak. Sesi dibuat dengan provider yang tidak bisa
di-resolve, dan baru gagal **jauh di kemudian hari**, di tempat yang tidak
ada hubungannya dengan salah ketik model. Pengguna tidak akan pernah
menghubungkan kegagalan itu dengan `model="/foo"`.

`cli.py:160` dan `orchestrator.py:237` meneruskan `model` dari pengguna apa
adanya, jadi input salah ketik memang bisa sampai ke sini.

### Perbaikannya

Method baru `_parse_model()` (dipisah agar bisa diuji sendiri) dan
`dispatch()` memakainya:

- string kosong atau hanya spasi -> `None` (pakai model default, bukan error)
- di-strip: `" cutad/qwen "` -> `{"providerID": "cutad", "id": "qwen"}`
- tanpa separator -> `ValueError` "no '/' separator"
- provider kosong -> `ValueError` "missing a provider"
- model id kosong -> `ValueError` "missing a model id"

Ditolak **sebelum** request HTTP apa pun dikirim.

Perilaku diam untuk `no-slash` sengaja dihapus. Tes lama
`test_dispatch_unsplit_model_is_omitted` diganti menjadi
`test_dispatch_unsplit_model_is_rejected`.

### Bukan bug

Split di slash **pertama** tetap benar kalau nama model mengandung slash
(`a/b/c` -> `providerID="a"`, `id="b/c"`). OpenCode memutuskan ini bukan
masalah, dan itu penilaian yang tepat. Dikunci oleh tes.

---

## 2. `cancel()` - nilai balik ambigu (SUDAH DIPERBAIKI)

### Masalahnya

```python
payload = self._unwrap(response)
if isinstance(payload, dict):
    return bool(payload.get("interrupted"))
return bool(payload)
```

Docstring lama: "Returns True when the server confirms."

Diuji terhadap server hidup. Bentuk respons nyata
`POST /api/session/{id}/interrupt`:

| Keadaan sesi | HTTP | Body | `cancel()` (lama) |
|---|---|---|---|
| Sedang jalan | 200 | `{"interrupted":true}` | `True` |
| Sudah diinterupsi | 200 | `{"interrupted":false}` | `False` |
| Belum pernah jalan | 200 | `{"interrupted":false}` | `False` |
| Sudah selesai | 200 | `{"interrupted":false}` | `False` |
| **Sesi tidak ada** | **404** | `{"_tag":"SessionNotFoundError"}` | `False` |
| Kredensial salah | 401 | (kosong) | `False` |
| Server mati | - | connection error | `False` |

Satu nilai `False` dipakai untuk **lima** keadaan berbeda. Tiga di antaranya
kegagalan nyata, bukan "sudah selesai".

### Kenapa ini berbahaya

Bukan hipotetis. `orchestrator.py:321-323` menjelaskan alasannya sendiri:

> "Cancel before retrying. Without this the abandoned session keeps holding
> a fleet slot and keeps writing to the workdir while the retry writes to the
> same place, so 'retry' would mean 'two runs'."

Urutannya: `_cancel_session()` dipanggil, lalu task langsung di-retry. Tapi
`_cancel_session` hanya mencetak saat hasilnya truthy. Jadi kalau cancel
gagal karena server tidak terjangkau, hasilnya `False`, dan **diam** - retry
jalan berdampingan dengan sesi yang tidak pernah berhasil dihentikan. Persis
skenario yang komentar itu ada untuk mencegahnya.

### Perbaikannya

`cancel()` sekarang tri-state:

- `True` - server mengonfirmasi interrupt
- `False` - tidak ada yang perlu dihentikan (sesi sudah selesai / belum jalan)
- `None` - tidak ada keputusan: 404, 401, atau server tidak terjangkau

`None` bersifat falsy, jadi pemanggil lama yang hanya memakai `if stopped:`
tetap berjalan tanpa perubahan. `orchestrator._cancel_session` sekarang
mencetak peringatan saat hasilnya `None`, sehingga kegagalan tidak lagi
tersamar sebagai "sesi sudah selesai".

Diverifikasi terhadap server hidup: 404 -> `None`, sesi selesai -> `False`,
401 -> `None`, server mati -> `None`.

### Bug tambahan yang ditemukan saat memperbaiki

`fleet.py` memakai `urllib.error.HTTPError` tapi hanya mengimpor
`urllib.request`. Tanpa `import urllib.error`, cabang `except` itu akan
melempar `AttributeError` - dan hanya saat ada 404/401, yaitu saat sedang
menangani kegagalan. Sudah ditambahkan. Ditemukan oleh Pyright, bukan oleh
tes, karena tidak ada tes yang pernah menyentuh jalur HTTPError.

### Catatan status review

Sesi review kedua berhenti tanpa kesimpulan akhir - analisis intinya benar,
tapi keluar jalur dan tidak merangkum. Temuan kasus 404 diambil dari sana,
lalu diverifikasi ulang secara manual.

---

## Tes

| Berkas | Isi |
|---|---|
| `test_review_temuan.py` | 16 tes: `dispatch()` dan `cancel()` setelah diperbaiki |
| `test_fleet.py` | 2 tes lama disesuaikan dengan perilaku baru |
| `test_cancel.py` | 13 tes, termasuk 3 baru untuk peringatan verdict `None` |

Suite penuh: **190 lulus** (sebelumnya 169).

### Mutasi

Semua perbaikan diuji mutasi - setiap pengecekan dibalik satu per satu dan
tesnya harus gagal. Tes yang tidak mendeteksi perbaikan adalah dekorasi.

| Mutasi | Hasil |
|---|---|
| strip spasi `dispatch()` dibatalkan | 1 tes gagal |
| cek provider dihapus | 1 tes gagal |
| cek separator dihapus | 1 tes gagal |
| cek model id dihapus | 1 tes gagal |
| strip model dibatalkan | 1 tes gagal |
| penanganan `ValueError` di CLI dihapus | 1 tes gagal |
| `cancel()` dikembalikan ke bool lama | 8 tes gagal |
| peringatan verdict `None` dihapus | 1 tes gagal |

### Verifikasi terhadap server hidup

- model valid diterima, tiga bentuk input rusak ditolak sebelum request dikirim
- `cancel()`: 404 -> `None`, sesi selesai -> `False`, 401 -> `None`,
  server tidak nyala -> `None`

### Catatan gaya kode

Dua `print` baru memakai format `%`, bukan f-string, karena seluruh file
sekitarnya memakai `%` (24 kemunculan `UP031` di `orchestrator.py`, 15 di
`cli.py`).
Ruff menandai keduanya sebagai `UP031`, sama seperti kode di sekitarnya -
konsisten dengan gaya berkas, bukan kelalaian.

