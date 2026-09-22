# Temuan review fleet.py

Hasil dua sesi review independen oleh OpenCode (via oc-fleet), plus verifikasi
manual. Semua temuan sudah dibuktikan dengan eksekusi, bukan pembacaan kode.

Status: **belum diperbaiki.** Menunggu keputusan desain pemilik repo.

---

## 1. `dispatch()` - parsing model (baris 95-98)

```python
if model:
    provider, _, model_id = model.partition("/")
    if _ and model_id:
        body["model"] = {"providerID": provider, "id": model_id}
```

`partition("/")` memotong di slash pertama. Kunci `_` (separator) hanya
mengecek "ada slash", dan `model_id` hanya mengecek "ada isi setelah slash".
**Provider kosong tidak pernah dicek.**

### Input yang salah

| Input | Yang terjadi | Sifat |
|---|---|---|
| `/foo` | body berisi `{"providerID": "", "id": "foo"}` | **data rusak dikirim** |
| `" cutad/qwen"` | `providerID = " cutad"` (spasi depan ikut) | **data rusak dikirim** |
| `"cutad /qwen"` | `providerID = "cutad "` (spasi belakang ikut) | **data rusak dikirim** |
| `cutad/` | model diabaikan diam-diam | ambigu |
| `no-slash` | model diabaikan diam-diam | ambigu, **sengaja** |
| `""` | model diabaikan, benar (pakai default) | benar |

`/foo` dan spasi adalah bug jelas: kode mengirim body yang melanggar
kontrak "provider/model-id" ke server. Tidak ada error, tidak ada peringatan.

`cutad/` dan `no-slash` ambigu karena masuk akal untuk menolaknya sebagai
input tidak lengkap. Perilaku diam untuk `no-slash` **sengaja dikunci** oleh
`test_fleet.py::test_dispatch_unsplit_model_is_omitted` (baris 83-89), dan
`test_fleet.py:52` memakai `model="deepseek"` sebagai input normal.

### Kenapa didiamkan itu berbahaya

`cutad/` adalah salah ketik yang wajar (lupa model ID). Pemanggil mengira
modelnya dipakai, padahal sesi jalan dengan model default. Tidak ada cara
bagi pemanggil untuk tahu.

### Catatan verifikasi

Bukan bug: split di slash **pertama** justru benar kalau nama model
mengandung slash (`a/b/c` -> `providerID="a"`, `id="b/c"`). OpenCode
memutuskan ini bukan masalah dan itu penilaian yang tepat.

---

## 2. `cancel()` - nilai balik ambigu (baris 121-147)

```python
payload = self._unwrap(response)
if isinstance(payload, dict):
    return bool(payload.get("interrupted"))
return bool(payload)
```

Docstring: "Returns True when the server confirms."

Diuji terhadap server hidup (bukan asumsi). Bentuk respons nyata endpoint
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

`False` berarti minimal empat hal berbeda:

1. server bilang "tidak ada yang perlu dihentikan" (sesi sudah selesai)
2. sesi tidak ada (404 - mungkin salah session ID)
3. autentikasi gagal (401)
4. server tidak bisa dihubungi sama sekali

Pemanggil tidak punya cara membedakannya. Untuk pemakaian di orchestrator
(cancel lalu retry), ini penting: `False` karena server sedang mati berbeda
konsekuensinya dengan `False` karena sesi sudah selesai sendiri.

### Catatan status review ini

Sesi review kedua berhenti tanpa kesimpulan akhir - analisis intinya benar
dan lebih dalam dari yang diperkirakan (nemu kasus 404), tapi keluar jalur
dan tidak merangkum. Perlu diperiksa ulang sebelum dijadikan dasar.

---

## Rekomendasi

**`dispatch()`**: perlakukan sebagai kesalahan keras. `provider` kosong, atau
input punya slash tapi model ID kosong, itu salah ketik - lebih baik
dikatakan daripada didiamkan. Perlu keputusan: apakah `no-slash` tetap
didiamkan (tes lama) atau ikut ditolak.

**`cancel()`**: `bool` tidak cukup untuk memikul empat keadaan. Pertimbangkan
mengembalikan hasil yang bisa dibedakan (mis. `True` / `False` / `None`),
atau lempar pengecualian untuk kegagalan keras (404/401/tidak terhubung)
sambil tetap mengembalikan `False` untuk "sesi sudah selesai".

Kedua masalah belum diperbaiki. Kode tidak diubah.
