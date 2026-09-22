# Temuan review fleet.py

Hasil dua sesi review independen oleh OpenCode (via oc-fleet), plus verifikasi
manual. Semua temuan dibuktikan dengan eksekusi, bukan pembacaan kode.

Status: **`dispatch()` sudah diperbaiki. `cancel()` belum** - menunggu
keputusan desain.

---

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

## 2. `cancel()` - nilai balik ambigu (BELUM DIPERBAIKI)

```python
payload = self._unwrap(response)
if isinstance(payload, dict):
    return bool(payload.get("interrupted"))
return bool(payload)
```

Docstring: "Returns True when the server confirms."

Diuji terhadap server hidup. Bentuk respons nyata
`POST /api/session/{id}/interrupt`:

| Keadaan sesi | HTTP | Body | `cancel()` |
|---|---|---|---|
| Sedang jalan | 200 | `{"interrupted":true}` | `True` |
| Sudah diinterupsi | 200 | `{"interrupted":false}` | `False` |
| Belum pernah jalan | 200 | `{"interrupted":false}` | `False` |
| Sudah selesai | 200 | `{"interrupted":false}` | `False` |
| **Sesi tidak ada** | **404** | `{"_tag":"SessionNotFoundError"}` | `False` |
| Kredensial salah | 401 | (kosong) | `False` |
| Server mati | - | connection error | `False` |

Diverifikasi lewat `fleet.cancel()` langsung (bukan simulasi): satu nilai
`False` dipakai untuk **lima** keadaan berbeda. Hanya `True` yang tidak
ambigu.

Tiga dari lima itu kegagalan nyata: pemanggil yang membaca `False` lalu
melanjutkan retry bisa mengira sesinya sudah berhenti, padahal server tidak
pernah berhasil dihubungi. Untuk pemakaian di orchestrator (cancel lalu
retry), perbedaan ini penting.

`False` berarti lima hal berbeda:

1. server bilang "tidak ada yang perlu dihentikan" (sesi sudah selesai)
2. sesi tidak ada (404 - mungkin salah session ID)
3. autentikasi gagal (401)
4. server tidak bisa dihubungi sama sekali
5. (belum pernah jalan - sama saja dengan nomor 1 dari sisi pemanggil)

### Catatan status review

Sesi review kedua berhenti tanpa kesimpulan akhir - analisis intinya benar,
tapi keluar jalur dan tidak merangkum. Temuan kasus 404 diambil dari sana,
lalu diverifikasi ulang secara manual.

### Rekomendasi

`bool` tidak cukup memikul lima keadaan. Pertimbangkan mengembalikan hasil
yang bisa dibedakan (mis. `True` / `False` / `None`), atau lempar
pengecualian untuk kegagalan keras (404/401/tidak terhubung) sambil tetap
mengembalikan `False` untuk "sesi sudah selesai".

---

## Tes

| Berkas | Isi |
|---|---|
| `test_review_temuan.py` | 15 tes: perilaku baru `dispatch()` + bug `cancel()` yang belum diperbaiki |
| `test_fleet.py` | 2 tes lama disesuaikan dengan perilaku baru |

Suite penuh: **184 lulus**.

Tes `dispatch()` diuji mutasi 5/5 - setiap pengecekan dibalik satu per satu
dan tesnya gagal sesuai harapan:

| Mutasi | Hasil |
|---|---|
| strip spasi dibatalkan | 1 tes gagal |
| cek provider dihapus | 1 tes gagal |
| cek separator dihapus | 1 tes gagal |
| cek model id dihapus | 1 tes gagal |
| strip model dibatalkan | 1 tes gagal |

Tes `cancel()` juga diuji mutasi: memindahkan 404/401 menjadi exception
membuat 3 tes gagal, sementara jalur normal tetap lulus.

Diverifikasi juga terhadap server OpenCode hidup: model valid diterima,
tiga bentuk input rusak ditolak sebelum request dikirim, string kosong
tetap memakai model default.
