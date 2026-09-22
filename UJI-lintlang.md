# LintLang vs prompt_lint: hasil uji head-to-head

Dibuat 2026-09-22. **Ini hasil menjalankan kedua alat pada prompt yang
sama**, bukan analisis dari kode saja.

LintLang v0.6.0 dijalankan dari source:
`PYTHONPATH=src python3 -m lintlang scan FILE --format terminal`

prompt_lint: `python3 prompt_lint.py "PROMPT"`

---

## Kenapa uji ini penting

Analisis analisis sebelumnya (baca kode) sudah menghasilkan satu koreksi
karena gue terlalu cepat menyimpulkan dari nama aturan. Uji langsung ini
adalah lapis pembuktian berikutnya: **menjalankan alat yang sebenarnya.**

Dan hasilnya membalik beberapa kesimpulan gue.

---

## UJI 1: negasi loop tak terbatas

Dua prompt berlawanan maksud:

```
A. "Never keep trying until it works. Stop after two attempts."
B. "Keep trying until it works."
```

| | LintLang | prompt_lint |
|---|---|---|
| A (melarang) | PASS (benar) | cuma NO-ARTEFACT |
| B (memakai) | **FAIL, 1 CRITICAL (H2)** | cuma NO-ARTEFACT |

**Hasil: LintLang menang jelas di kasus ini.**

LintLang membedakan A dari B dengan benar. Dia tahu "never keep trying"
bukan "keep trying" - itu `_is_negated_prohibition` yang bekerja.

Punya lo: **nol temuan di keduanya.** Lo tidak punya aturan yang menangkap
loop tak terbatas dalam bentuk ini sama sekali. Jadi lo tidak bisa
membedakan, karena lo tidak mendeteksi apa pun.

Ini **melengkapi** temuan FLUFF-COT: benar bahwa LintLang tidak punya
FLUFF-COT, tapi juga benar bahwa lo tidak punya penangkal negasi. Dua-duanya
punya lubang, di tempat yang berbeda.

## UJI 2: chain-of-thought (FLUFF-COT)

```
A. "Do not use chain of thought. Think step by step is forbidden."
B. "Think step by step and reason carefully."
```

| | LintLang | prompt_lint |
|---|---|---|
| A (melarang) | PASS | **FLUFF-COT (salah)** |
| B (memakai) | PASS | FLUFF-COT (benar) |

**Hasil: dua-duanya gagal, dengan cara berbeda.**

- LintLang: tidak punya aturan chain-of-thought sama sekali. Diam di keduanya.
- prompt_lint: **false positive terbukti.** Prompt A melarang
  chain-of-thought, tapi dilaporkan memakai chain-of-thought. Temuannya
  identik dengan prompt B.

**Gue salah di analisis sebelumnya.** Gue dugaan LintLang akan menangkap ini
lewat H5. Ternyata tidak - LintLang PASS di keduanya.

## UJI 3: format campur (H6)

```
A. "Respond in JSON and also include a friendly Markdown summary."
B. "Respond in JSON only."
```

| | LintLang | prompt_lint |
|---|---|---|
| A (campur) | PASS | cuma NO-ARTEFACT |
| B (bersih) | PASS | cuma NO-ARTEFACT |

**Hasil: dua-duanya diam. H6 lebih sempit dari dugaan gue.**

Gue kira H6 akan menangkap "JSON dan Markdown". Ternyata aturan itu mencari
pola format-output yang lebih spesifik (kata kerja respons + nilai format,
dalam scope DIRECT). Ini persis perbaikan yang mereka jelaskan di komentar
H6 - mereka sudah mempersempitnya agar dokumen yang cuma **menyebut** tipe
file tidak jadi korban.

Efek sampingnya: kasus seperti di atas juga lolos. **Itu harga dari
mempersempit.**

## UJI 4: scope tak terbatas (H2 vs UNBOUNDED-SCOPE)

```
A. "Improve the codebase. Make it better where needed and fix any issues."
B. "Write the function parse_duration into /repo/src/duration.py. Run
    pytest and report the exit code."
```

| | LintLang | prompt_lint |
|---|---|---|
| A (tak terbatas) | PASS | **VAGUE-OUTPUT (ERROR)** + NO-ARTEFACT |
| B (jelas) | PASS | SPEC-NO-INVALID-CONTRACT (INFO) |

**Hasil: prompt_lint menang jelas. Dan ini membalik klaim gue.**

Gue klaim di dokumen sebelumnya bahwa `UNBOUNDED-SCOPE` tumpang tindih
dengan H2. **Salah.** H2 tidak menyala pada prompt scope-tak-terbatas.
H2 mencari **loop tak terbatas** ("keep trying until"), bukan **scope
tak terbatas** ("improve everything"). Itu dua hal berbeda.

prompt_lint menangkap yang H2 lewatkan, dan menandainya ERROR.

---

## Ringkasan skor

| Uji | Pemenang |
|---|---|
| 1. Negasi loop | **LintLang** |
| 2. Chain-of-thought | seri (dua-duanya gagal, cara beda) |
| 3. Format campur | seri (dua-duanya diam) |
| 4. Scope tak terbatas | **prompt_lint** |

**2 menang, 1 seri-gagal, 1 seri-diam.**

Bukan "salah satu lebih baik". Mereka **punya cakupan yang berbeda**, dan
uji ini menunjukkannya dengan angka, bukan opini.

---

## Koreksi terhadap analisis sebelumnya

Dua klaim di **analisis versi pertama** (commit `5628d75`, sudah
tergantikan oleh `ANALISIS-lintlang.md` versi sekarang) **terbukti salah**
dari uji ini:

1. **"`UNBOUNDED-SCOPE` tumpang tindih dengan H2"** - SALAH. H2 soal loop,
   bukan scope. prompt_lint menangkap scope-tak-terbatas, LintLang tidak.
2. **"`VAGUE-OUTPUT` tumpang tindih dengan H6"** - SALAH. H6 soal format
   output, bukan kejelasan tujuan. Uji 3 dan uji 4 menunjukkan keduanya
   diam di H6, tapi VAGUE-OUTPUT ERROR di uji 4.

Catatan: tabel perbandingan 18 aturan yang memuat klaim-klaim itu ada di
versi pertama, dan sudah tidak ada di `ANALISIS-lintlang.md` sekarang
(ditimpa saat versi dalam-dalam ditulis). Klaim di atas merujuk ke isi
commit `5628d75`, bukan ke file yang ada sekarang.

Yang **tetap benar**:
- `SPEC-TEST-ONLY-VALID` dan `SPEC-VERIFY-SELF-REFERENTIAL` tidak ada di
  LintLang (dikonfirmasi grep)
- `FLUFF-COT` false positive (dikonfirmasi uji)
- LintLang menangani negasi dengan benar (dikonfirmasi uji)

---

## Pelajaran yang lebih berguna dari skor

### 1. Jangan percaya tabel tumpang-tindih dari nama aturan

Gue bikin tabel 18 aturan vs LintLang dari **nama** dan **grep**. Dua baris
di antaranya salah. Yang membongkarnya cuma satu: **menjalankan alatnya.**

Pelajaran: kalau mau tahu apakah dua linter tumpang tindih, **jalankan
keduanya pada prompt yang sama.** Jangan bandingkan daftar aturan.

### 2. Mengukur cakupan sendiri itu mahal

Uji 1-4 ini gue bisa lakukan karena kedua alat jalan. Kalau lo mau tahu
di mana `prompt_lint` buta, lo butuh **set prompt yang diketahui
jawabannya** - dan lo sudah punya dasarnya: 16 probe terkontrol.

**Rekomendasi konkret:** jadikan uji 1-4 ini sebagai **tes karakterisasi**
di `oc-fleet`. Prompt A/B yang berlawanan maksud, dengan keluaran yang
diharapkan. Itu akan menangkap false positive seperti FLUFF-COT secara
otomatis, selamanya.

### 3. "Tidak melaporkan apa pun" bukan "aman"

Di uji 1 dan 4, LintLang PASS pada prompt yang jelas bermasalah. Di uji 2
dan 3, prompt_lint diam pada prompt yang jelas bermasalah.

Kalau lo cuma lihat "PASS", lo akan pikir tidak ada masalah. Padahal
artinya cuma "alat ini tidak punya aturan untuknya".

LintLang **tahu** ini - itu sebabnya mereka punya status `UNAVAILABLE` dan
`SKIPPED` yang bukan PASS. Tapi `PASS` yang keluar dari uji di atas tetap
terbaca sebagai "aman" oleh mata manusia.

Ini alasan lain kenapa lo butuh tes karakterisasi: supaya batas cakupan lo
**tertulis**, bukan cuma tidak terlihat.

---

## Angka yang diverifikasi

- LintLang v0.6.0, dijalankan dari source
- 4 uji, 8 prompt
- prompt_lint: `prompt_lint.py` di repo oc-fleet
- Semua keluaran di dokumen ini disalin dari terminal, bukan ditulis ulang
