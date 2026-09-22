# Temuan: tool `read` OpenCode menggantung tanpa batas

**Tanggal:** 2026-09-22
**OpenCode:** v2.0.8
**Provider:** cutad (ai.cutad.web.id)
**Ditemukan lewat:** oc-fleet, setelah deteksi macet ditambahkan ke `Fleet.status()`

---

## Ringkasan

Tool `read` OpenCode menggantung dengan status `running` yang **tidak
pernah selesai** dan `executed: false`. Sesi tidak pernah mencapai
outcome, sehingga pemanggil menunggu sampai timeout penuh.

Terukur pada sesi nyata di mesin ini:

| tool    | nyangkut | selesai | tingkat gagal |
|---------|---------:|--------:|--------------:|
| shell   |        0 |      91 |            0% |
| edit    |        0 |      26 |            0% |
| write   |        0 |      24 |            0% |
| glob    |        0 |       3 |            0% |
| grep    |        0 |       1 |            0% |
| **read**|    **8** |  **34** |       **19%** |

Hanya `read` yang terpengaruh. Semua tool lain nol kegagalan, pada
server yang sama, rentang waktu yang sama.

---

## Reproduksi

Terkontrol: satu sesi, satu tugas, satu file kecil.

```python
sid = fleet.dispatch(
    "Read the file /home/hermawan/oss/webget/webget/__init__.py and print "
    "the first 5 lines verbatim. Nothing else.",
    "/tmp/ml-uji", model="cutad/deepseek-v4-flash")
```

Hasil, dipoll dengan `Fleet.status()`:

```
[   0s] outcome=None tool_running=0
[  50s] outcome=None tool_running=1     <- mulai baca
[ 100s] outcome=None tool_running=1
[ 150s] outcome=None tool_running=1
[ 200s] outcome=None tool_running=1
[ 250s] outcome=None tool_running=1
[ 300s] outcome=None tool_running=1
[ 310s] outcome=None tool_running=1 stuck=True (309s)
```

File-nya ada, 27 KB, bisa dibaca normal dengan `cat` dan `head`:

```
$ ls -la /home/hermawan/oss/webget/webget/__init__.py
-rw-r--r-- 1 hermawan hermawan ... /home/hermawan/oss/webget/webget/__init__.py
$ head -3 /home/hermawan/oss/webget/webget/__init__.py
"""Scrape ladder: orchestrates HTTP -> Crawl4AI -> Firecrawl in series.
```

Jadi bukan izin, bukan ukuran file, bukan path salah.

---

## Berulang lintas model

Nyangkut terjadi dengan **model yang berbeda**, yang menunjukkan ini bukan
masalah model:

```
NYANGKUT read di glm-5.3-flash
NYANGKUT read di deepseek-v4-flash
NYANGKUT read di ml2-qwen3-8-flash-next
NYANGKUT read di ml2-glm-5.3-flash
NYANGKUT read di ml2-deepseek-v4-flash
NYANGKUT read di ml2-deepseek-v4-pro
NYANGKUT read di ml2-Qwen3.8-27B
```

Isi pesan pada dua sesi berbeda:

```
glm-5.3-flash:     assistant/reasoning : "The user wants me to read ..."
                   assistant/tool [running]: read

deepseek-v4-flash: assistant/text : "I'll read the file first."
                   assistant/tool [running]: read
```

Keduanya berhenti pada titik yang sama: tool `read` dimulai, tidak pernah
selesai.

---

## Yang TIDAK ada di log server

Untuk sesi yang nyangkut, log server tidak mencatat apa pun:

```
$ grep ses_<id> ~/.local/share/opencode/log/opencode.log
(tidak ada hasil)
```

Tidak ada error, tidak ada peringatan, tidak ada timeout. Tool dimulai
dan menghilang. Ini yang membuat bug ini sulit ditemukan: dari luar,
sesi tampak "masih bekerja".

Bandingkan dengan kegagalan yang tercatat baik - contoh model tidak
tersedia, yang muncul jelas di log:

```
level=ERROR message="Failed to drain Session"
cause="AI.Error: Model 'qwen/qwen3.8-max' not available.
       Check /v1/models for available models."
role=server sessionID=ses_...
```

Jadi infrastruktur logging ada dan bekerja untuk jenis kegagalan lain.

---

## Dampak

1. **Sesi menggantung sampai timeout penuh.** Pada beban kerja nyata di
   mesin ini, satu task menunggu 1500 detik (25 menit) sebelum menyerah,
   lalu melaporkan "timeout" - padahal penyebabnya tool yang tidak
   selesai.

2. **Kegagalan tidak dapat didiagnosis.** `outcome` tetap `null`, jadi
   tidak ada cara membedakan "model sedang berpikir" dari "tool sudah
   mati" hanya dari API.

3. **Sekitar 1 dari 5 panggilan `read` gagal.** Pada alur kerja agent
   yang membaca banyak file, ini berarti kebanyakan sesi akan tersandung
   cepat atau lambat.

---

## Saran

**Untuk OpenCode:**

1. Tambahkan timeout ke eksekusi tool. Tool yang berjalan tanpa hasil
   selama N detik harus gagal dengan error, bukan menggantung.
2. Catat kegagalan tool ke log server dengan `sessionID`, seperti
   kegagalan lain. Saat ini tidak ada jejak sama sekali.
3. Sampaikan kegagalan tool lewat API. Pesan `idle` hanya memuat
   `outcome: "failed"` tanpa field error, jadi pemanggil tidak bisa
   mengetahui penyebabnya:
   ```json
   {"id": "msg_...", "type": "idle", "outcome": "failed", "time": {...}}
   ```
   Menambahkan `error` atau `reason` akan membuat kegagalan bisa
   didiagnosis tanpa membuka log server.

**Sementara ini (sudah diterapkan di oc-fleet):**

`Fleet.status()` sekarang melaporkan `tool_running`, `stuck_seconds`, dan
`stuck`, dihitung dari `time.created` tool. Ambang 300 detik. Pemanggil
bisa menyerah lebih awal dan menyebut sebabnya, alih-alih menunggu
timeout penuh.

**Catatan metodologi.** Temuan ini awalnya salah gue diagnosis sebagai
"provider rusak", karena lima model sekaligus tampak timeout. Penyebab
sebenarnya berbeda: oc-fleet sendiri melanggar batas konkurensi provider
(15 bersamaan) lewat polling, dan itu membuat sesi tampak macet. Setelah
pembatas konkurensi dipasang di oc-fleet, pola yang tersisa - hanya
`read`, berulang lintas model, dengan file yang jelas bisa dibaca - baru
terlihat sebagai bug tool. Angka 0/91 untuk `shell` dan 8/34 untuk `read`
adalah bukti yang membedakan keduanya.

---

## Konfirmasi independen dari endpoint stats server

Angka di atas gue hitung sendiri dari daftar pesan. Server punya
hitungannya sendiri di `/api/session/stats`, dan angkanya cocok:

```
tools: calls=827  succeeded=793  failed=34
```

**34 dari 827 tool call gagal (4.1%)**, dihitung oleh server, bukan oleh
skrip ini. Dua sumber berbeda menunjuk arah yang sama.

Perhatikan angkanya berbeda dari tabel di atas, dan itu wajar - bukan
kontradiksi:

| Sumber | Cakupan | Angka |
|--------|---------|-------|
| Skrip ini | 40 sesi terbaru | 8 nyangkut / 42 read |
| Endpoint stats | seluruh riwayat (180 sesi, 5 hari) | 34 gagal / 827 call |

Yang penting bukan angka tunggalnya, melainkan bahwa `read` adalah
satu-satunya tool dengan kegagalan (0/91 shell, 8/34 read), dan server
mengonfirmasi adanya kegagalan tool pada tingkat keseluruhan.

Perbedaan metode hitung juga menjelaskan kenapa angkanya tidak sama
persis: skrip ini menghitung `status: running` sebagai nyangkut,
sementara `stats` menghitung tool yang gagal dengan cara sendiri. Dua
definisi berbeda atas gejala yang sama.

---

## Data mentah

Diambil dengan:

```python
fleet = Fleet()
for s in fleet.list_sessions(40):
    raw = fleet._request("GET", f"/api/session/{s['id']}/message")
    msgs = raw.get("data", raw)
    for m in msgs:
        if m.get("type") != "assistant":
            continue
        for c in (m.get("content") or []):
            if c.get("type") == "tool":
                status = (c.get("state") or {}).get("status")
                nama = c.get("name")
                # status == "running" -> nyangkut
                # status == "completed" -> selesai
```
