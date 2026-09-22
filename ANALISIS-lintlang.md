# Analisis: LintLang vs prompt_lint oc-fleet

Dibuat 2026-09-22. Diverifikasi langsung ke kode LintLang (clone repo,
grep isi `src/`), bukan dari README atau nama aturan.

---

## Apa yang LintLang sebenarnya kerjakan

`hermes-labs-ai/lintlang` - 9.488 baris Python (src). Apache-2.0.

**Yang gue temukan: lebih banyak dari 7 aturan H.** Ada juga aturan `P1`
dan `P2` di `extractors.py`, dan H1 punya 9 sub-kode (H1.1 - H1.9).

Daftar lengkap `pattern_name` yang unik di seluruh src:

| Kode | Nama | Menyasar |
|---|---|---|
| H1 | Tool Description Ambiguity | deskripsi tool kabur (9 sub-kode) |
| H2 | Missing Constraint Scaffolding | constraint hilang, loop tak terbatas |
| H3 | Schema-Intent Mismatch | schema vs maksud tidak cocok |
| H4 | Context Boundary Erosion | risiko batas konteks |
| H5 | Implicit Instruction Failure | instruksi negatif tanpa scaffold |
| H6 | Template Format Contract Violation | format output campur |
| H7 | Role Confusion | persona bertabrakan |
| **P1** | **Uncalibrated Threshold** | **magic number tanpa komentar kalibrasi** |
| **P2** | **Embedded Scaffold** | **prompt besar di dalam source Python** |
| PF001-PF005 | preflight | prompt: scope, negasi, meta-linguistik |

Ada **integrasi resmi Hermes** (`integrations/hermes_agent.py`, 85 baris).
Nyala di gate `pre_verify`, cuma file yang terdeteksi permukaan instruksi.
REVIEW = saran, FAIL/ERROR = menahan turn.

---

## P1 dan P2 - ini yang paling menarik

### P1 Uncalibrated Threshold

```
description: "Threshold 'T = 0.7' has no calibration comment.
              Magic numbers in LLM pipelines cause silent drift."
suggestion:  "Add a comment explaining how 'T' was calibrated:
              distribution analysis, ablation study, or empirical
              testing. Example: '# Calibrated on 470-question dev set,
              fires on ~10% of queries'."
```

Ini **filosofinya sama dengan lo**. LintLang keberatan pada angka yang
tidak punya jejak asal - sama seperti lo keberatan pada aturan prompt yang
tidak punya bukti pengukuran.

Mereka bahkan menolak komentar yang kabur (`todo`, `arbitrary`, `guess`,
`maybe`, `probably`). Itu standar yang tinggi.

### P2 Embedded Scaffold

```
description: "Large prompt (N chars, M lines) embedded in Python source."
suggestion:  "Externalize to a .prompt or .txt file, loaded at runtime."
```

Ini **secara langsung menunjuk ke lo**. Andaikan LintLang dijalankan ke
`oc-fleet/prompt_lint.py`, dia akan bilang: aturan lo ada di dalam source,
pindahkan ke file terpisah.

Severity-nya sengaja LOW, dan komentarnya jujur:

> "keeping a prompt in source is a common, deliberate choice, not a defect.
> At MEDIUM this made every Python file that holds a real prompt a REVIEW
> on length alone."

Sikap itu bagus. Mereka tahu kapan harus diam.

---

## Perbandingan 18 aturan lo melawan LintLang

| Aturan lo | Padanan LintLang | Penilaian |
|---|---|---|
| `UNBOUNDED-SCOPE` | H2 | **tumpang tindih** |
| `NO-CONSTRAINT` | H2 | **tumpang tindih** |
| `VAGUE-OUTPUT` | H6 / H1 | **tumpang tindih** |
| `NO-VERIFY-TARGET` | H5 (sebagian) | **tumpang tindih** |
| `NO-ARTEFACT` | H5 (sebagian) | sebagian |
| `DESTRUCTIVE-NO-GUARD` | H5 (negasi) | sebagian |
| `MULTI-STEP-UNORDERED` | **tidak ada** (grep 0) | **milik lo** |
| `HINT-ONLY` | praktis tidak ada (grep 2) | **milik lo** |
| `SENTENCE-CASE-QUESTION` | praktis tidak ada (grep 1) | **milik lo** |
| `FLUFF-COT` | **tidak ada** | **milik lo** |
| `FLUFF-URGENCY` | **tidak ada** | **milik lo** |
| `FLUFF-PLEASANTRY` | **tidak ada** | **milik lo** |
| `FLUFF-ROLE` | H7 (sebagian) | sebagian |
| `EM-DASH` | **tidak ada** | **milik lo** (khas) |
| `SPEC-UNDEFINED-EDGE` | H2 (maksud sama) | **tumpang tindih** |
| `SPEC-NO-INVALID-CONTRACT` | H5 (maksud sama) | **tumpang tindih** |
| `SPEC-TEST-ONLY-VALID` | **tidak ada** | **milik lo** |
| `SPEC-VERIFY-SELF-REFERENTIAL` | **tidak ada** | **milik lo** |

### Konfirmasi grep (dijalankan ke seluruh src/ LintLang)

Semua **kosong**:

```
self.referen | verify.*self | own test | wrote the test | self.verif  -> kosong
happy path | valid example | only valid | test only                   -> kosong
invalid input | invalid contract                                      -> kosong
chain.of.thought | step.by.step                                       -> kosong
urgent | hurry | asap                                                -> kosong
em.dash | em dash                                                    -> kosong
```

Artinya: **9 dari 18 aturan lo tidak ada padanannya** - termasuk dua yang
paling gue puji (`SPEC-TEST-ONLY-VALID`, `SPEC-VERIFY-SELF-REFERENTIAL`)
dan tiga aturan fluff yang lo buktikan lewat probe (`FLUFF-COT`,
`FLUFF-URGENCY`, `FLUFF-PLEASANTRY`).

Sembilan yang tumpang tindih pun tidak seluruhnya sama: `NO-ARTEFACT` dan
`DESTRUCTIVE-NO-GUARD` cuma bersinggungan sebagian dengan H5, bukan sama.

---

## Perbedaan pendekatan yang mendasar

| | LintLang | oc-fleet `prompt_lint` |
|---|---|---|
| **Sumber aturan** | katalog tetap, ditulis manual | 16 probe terkontrol |
| **Cara deteksi** | regex + kata kunci | regex |
| **Yang jadi sasaran** | bentuk bahasa | **hasil pengukuran** |
| **Skala** | 9.488 baris, 9+ aturan | 324 baris, 18 aturan |
| **Distribusi** | PyPI, CI, pre-commit, plugin | satu file, lokal |
| **Cakupan** | tool desc, schema, role, format, kode | prompt task + hasil eksperimen |

**Perbedaan paling penting.**

LintLang mengkatalogkan **bentuk bahasa yang biasanya salah**. Contoh H2:
cari kata kunci `max_retries`, `timeout`, `budget`; kalau tidak ada padahal
ada tools, itu temuan.

`prompt_lint` mengkatalogkan **mode kegagalan agent yang diukur**. Contoh
asal `SPEC-*`: parser durasi diminta, bentuk valid disebut, `"1h1h"` tidak.
Agent ngarang aturan "unit harus urut menurun", menolak input legal, dan
**tesnya sendiri lulus** karena ditulis dari asumsi sendiri.

LintLang tidak bisa menangkap itu. Bukan karena kurang pintar - karena itu
**bukan properti kalimat**. Itu properti **hasil**.

Yang lebih halus lagi: aturan FLUFF lo (`FLUFF-COT`, `FLUFF-URGENCY`,
`FLUFF-PLEASANTRY`) itu **aneh** kalau dilihat sebagai linting. Menandai
"think step by step" sebagai masalah hanya masuk akal kalau lo sudah
**mengukur** bahwa itu tidak menolong. LintLang tidak punya aturan itu,
karena LintLang tidak mengukur - dia mengkatalogkan.

**Itu inti kekuatan lo, dan sekaligus alasan lo tidak boleh menyerahkannya.**

---

## Yang harus diambil

### 1. Integrasi plugin Hermes - ambil, segera

`integrations/hermes_agent.py` menunjukkan caranya:

- entry-point group `hermes_agent.plugins`
- nyala di gate `pre_verify`
- cuma file yang terdeteksi permukaan instruksi

Ini persis cara memasang `prompt_lint` supaya jalan **otomatis**, bukan
dipanggil manual.

### 2. P1 Uncalibrated Threshold - ambil idenya

Ada logika yang bisa lo curi: **tolak komentar kalibrasi yang kabur**.
Daftar mereka: `todo`, `fixme`, `arbitrary`, `guess`, `probably`, `maybe`.

Punya lo sudah punya semangat yang sama, tapi belum menolak kalibrasi
palsu. Ini peningkatan nyata.

### 3. P2 Embedded Scaffold - perhatikan, tapi jangan buru-buru

Mereka akan menyarankan lo memindahkan 18 aturan keluar dari
`prompt_lint.py`. **Jangan langsung turuti.** Severity mereka sendiri LOW,
dan alasannya jujur. Satu file 324 baris yang bisa dibaca sekali duduk
punya nilainya sendiri. Putuskan berdasarkan apakah lo butuh A/B testing,
bukan karena linter bilang.

### 4. Output machine-readable - ambil idenya

LintLang keluarkan **SARIF** untuk GitHub Code Scanning. Punya lo `Finding`
dataclass tapi cuma print ke terminal. Kalau lo mau masuk CI, butuh JSON
atau SARIF.

### 5. Sub-kode H1.1-H1.9 - pelajari sebagai taksonomi

H1 punya 9 sub-kode. Itu cara memecah satu kategori besar jadi temuan
spesifik. Aturan lo belum punya tingkat detail itu.

---

## Yang harus dihindari

### 1. Jangan ganti aturan lo dengan aturan mereka

**9 dari 18 aturan lo tidak ada padanannya.** Kalau lo pindah ke LintLang
sepenuhnya, lo kehilangan setengah aturan lo:

- `SPEC-TEST-ONLY-VALID` dan `SPEC-VERIFY-SELF-REFERENTIAL` (dua temuan
  paling mahal di repo lo)
- `FLUFF-COT`, `FLUFF-URGENCY`, `FLUFF-PLEASANTRY` (berbasis probe)
- `EM-DASH` (khas lo, dan jadi konvensi repo)
- `MULTI-STEP-UNORDERED`, `HINT-ONLY`, `SENTENCE-CASE-QUESTION`

### 2. Jangan kunci ke `hermes_agent.py` sebagai satu-satunya jalan

Integrasi itu cuma mengenali `_EXACT_NAMES` tertentu (`.cursorrules`,
`agents.md`, `claude.md`, `skill.md`, `system.md`, sudah dicek). Kalau lo
simpan prompt di lokasi lain, hook-nya tidak nyala. Pakai sebagai contoh,
bukan sebagai satu-satunya pintu.

### 3. Jangan tiru katalog beku sebagai sumber kebenaran

LintLang punya `ALL_RULE_IDS` yang **frozen**
(`RULE_BUNDLE_VERSION = "preflight-rules.v1"`). Masuk akal untuk produk.
Tapi kekuatan `prompt_lint` justru **kemampuan menambah aturan setiap kali
sebuah eksperimen menunjukkan mode gagal baru**. Kalau dibekukan, lo
matikan sumber nilainya.

### 4. Jangan bangun ulang 9.488 baris infrastruktur

Parser, SARIF, GitHub Action, pre-commit, plugin Claude Code/Gemini,
MegaLinter, Gentoo ebuild. Itu semua sudah dikerjakan.

---

## Rekomendasi akhir

**Jangan pilih salah satu. Bagi peran.**

```
prompt_lint.py          ->  tetap punya lo, tetap diukur dari eksperimen
                            fokus: mode kegagalan VERIFIKASI + fluff hasil probe
                            7 aturan yang tidak ada di LintLang

lintlang (pipa mereka)  ->  pakai untuk sisanya
                            H1-H7 + P1 + P2 + PF001-PF005
                            dapat CI, SARIF, plugin, gratis
```

**Kontribusi nyata:** sembilan aturan lo sebagai usulan ke katalog LintLang.
Bukan "lebih bagus" - mereka **tidak punya** itu, dan dua di antaranya
menyasar masalah yang paling sulit ditangkis di dunia agent.

**Kandidat terkuat untuk diajukan lebih dulu:**
`SPEC-VERIFY-SELF-REFERENTIAL`. Karena LintLang punya P1 yang
mempersoalkan angka tanpa kalibrasi - jadi mereka **jelas menghargai bukti
yang bisa dilacak**. Aturan verifikasi mandiri lo adalah versi itu untuk
dunia prompt. Kemungkinan besar mereka mau.

---

## Catatan kejujuran

Gue belum membaca 9.488 baris itu seluruhnya. Klaim "tidak ada padanan"
berdasar:

1. daftar lengkap `pattern_name` unik (9 nama, sudah diambil semua)
2. grep pola kata kunci (semua kosong untuk 7 aturan itu)

Itu bukti kuat, bukan bukti mutlak. Grep bisa meleset kalau mereka memakai
istilah lain. Sebelum lo mengajukan aturan sebagai kontribusi, **cek sendiri
ke repo mereka** - jangan bergantung pada ringkasan gue.

Yang gue belum baca: `detect_h3`, `detect_h7` sampai selesai, dan isi
`detectors/h1.py` (834 baris, tempat 9 sub-kode H1).
