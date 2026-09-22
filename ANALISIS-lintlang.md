# LintLang: apa yang sebenarnya terjadi di dalam

Dibuat 2026-09-22. Hasil membaca kode langsung: `patterns.py` (1.613),
`detectors/h1.py` (834), `preflight/scope.py`, `report.py`, `semantics
sarif.py`, `baseline.py`. Bukan dari README.

Ini catatan pemahaman, bukan ringkasan fitur.

---

## 1. Arsitekturnya tiga lapis, bukan satu

Gue salah di analisis sebelumnya. Gue kira LintLang = 7 aturan. Ternyata
ada **tiga mesin berbeda** di dalam satu repo:

```
lapis 1  H1-H7 + P1 P2      patterns.py, detectors/h1.py
         aturan struktural  "bentuk kalimat ini salah"

lapis 2  PF001 - PF005      preflight/engine.py (1.393 baris)
         preflight prompt   classifier scope + keputusan ALLOW/HOLD

lapis 3  HERM v1.1          herm.py
         skor hermeneutik   6 dimensi, skor numerik
```

Lapis 3 itu yang paling beda konsepnya: dia **nggak bilang salah atau
benar**. Dia kasih **skor** di 6 dimensi:

- HERM-1 Interpretive Ambiguity
- HERM-2 User-Intent Misalignment Risk
- HERM-3 Input-Driven Misinterpretation Surface
- HERM-4 Instruction Conflict/Polysemy
- HERM-5 Pragmatic Drift Risk
- HERM-6 Adversarial Reframing Susceptibility

Kenapa ini penting: **ambiguity itu bukan biner.** H1-H7 bilang "ada
masalah"; HERM bilang "seberapa ambigu, dan di dimensi apa". Dua pertanyaan
berbeda, dan menjawab yang kedua butuh pendekatan yang berbeda.

---

## 2. Scope classifier - ini yang paling berharga, dan lo nggak punya

`preflight/scope.py`. Ini **bukan** regex pencari pola. Ini classifier
berbasis **posisi karakter**: setiap indeks di prompt diberi satu label.

```
DIRECT         perintah sungguhan ("kirim email ke X")
QUOTED         di dalam tanda kutip ("...")
CODE           di dalam `backtick` atau ```fence```
HYPOTHETICAL   "misalkan...", "seandainya..."
NEGATED        "jangan pernah bilang X"
METALINGUISTIC "analisis frasa X"  (X dibicarakan, bukan diperintahkan)
```

Efeknya: prompt seperti ini

```
Jangan pernah bilang "saya tidak bisa membantu".
Kalau user minta tolong, bilang "saya tidak bisa membantu".
```

Kalimat pertama → `NEGATED`, **tidak dilaporkan**. Kalimat kedua → `DIRECT`,
dilaporkan. Regex biasa akan menandai keduanya dan hasilnya false positive.

**Ini akar kenapa linter yang naif tidak dipercaya.** Dan lo belum punya ini.

### Detail cerdas yang gue temukan

**a. Tolak menganalisis kalau delimiter tidak seimbang.**

Kalau ada ``` fence ``` yang tidak ditutup, `analyze_scope` **berhenti dan
melaporkan alasan** (`"unclosed fenced-code delimiter"`), bukan menebak.
Hasilnya `ScopeAnalysis` dengan `unavailable_reason` terisi.

Alasannya jelas begitu dibaca: kalau delimiter tidak jelas, **tidak ada**
cara tahu mana DIRECT dan mana QUOTED. Menebak di situ = bikin temuan
palsu. Mereka memilih diam.

**b. Offset tidak digeser.**

Docstring: *"Classify scope without normalizing or changing original
offsets."* Setiap label dipetakan ke posisi karakter di teks **asli**.
Kalau mereka menormalkan teks dulu (misal collapse whitespace), nomor baris
jadi salah dan temuan menunjuk ke tempat yang keliru.

**c. Apostrof kurung bukan penutup kutip.**

```
if closing_char == "’" and index > 0 and text[index - 1].isalnum():
    continue
```

Supaya `don't` di dalam teks tidak dianggap penutup kutip tunggal. Kalau
ini tidak ada, seluruh sisa prompt jadi salah label gara-gara satu
apostrof.

**d. Nesting divalidasi terpisah.**

`_validate_nesting` melacak `()[]{}` dengan stack, dan hanya menghitung
delimiter yang ada di `DIRECT`. Jadi kurung di dalam string tidak
mengacaukan hitungan.

### Kenapa mereka sampai sejauh ini

Karena kalau salah, **temuannya berisik**. Dan linter yang berisik akan
dimatikan orang. Semua kompleksitas di `scope.py` itu ada demi satu hal:
**jangan lapor yang salah.** Itu pelajaran yang lo sendiri sudah temukan
di tempat lain (lihat bagian 5).

---

## 3. Parser negasi H2 - linguistik serius

Di `patterns.py` baris 163-265, kira-kira 100 baris cuma untuk menjawab
satu pertanyaan: **apakah "do not continue indefinitely" itu perintah
tak terbatas, atau justru perintah untuk berhenti?**

Kalimat itu **menyatakan batas**. Tapi kalau lo cari pola "continue",
lo akan melaporkannya sebagai tak terbatas - dan membalik maksud
penulisnya.

Mereka membangun ini untuk menanganinya:

| Konstanta | Menangani |
|---|---|
| `_NEG_GAP` | jarak boleh spasi atau satu baris baru (prosa yang di-wrap) |
| `_NEG_APOSTROPHE` | `don't` dan `don’t` dan `dont` |
| `_NEGATION_ADVERBS` | daftar tertutup, bukan `\w+ly` |
| `_ADJACENT_NEGATOR` | negator harus di sebelah perilaku, maks 2 kata keterangan |
| `_EARLIER_NEGATIVE` | negasi ganda ("tidak benar bahwa kamu tidak boleh") |
| `_LICENSING_EXCEPTION` | "kecuali" memberi izin, di mana pun posisinya |
| `_PROHIBITION_DEFEATER` | larangan bersyarat/pertanyaan bukan menyatakan batas |
| `_RIGHT_CLAUSE_BOUNDARY` | kondisi di klausa terpisah = batas punya penulis |
| `_TRAILING_CONDITION` | bedakan "kecuali kalau" dari "(lihat runbook)" |
| `_LEFT_CLAUSE_COMMA` | koma hanya mulai klausa baru kalau koordinatif |

### Dua keputusan yang gue kagumi

**a. Daftar adverb tertutup, bukan `\w+ly`.** Komentarnya:

> *"'reply', 'apply', 'rely', 'comply', and 'supply' end in the same letters
> and are verbs."*

Kalau mereka pakai `\w+ly`, kata "apply" akan dianggap adverb. Sepele, tapi
ini jenis bug yang bikin hasil salah tanpa ada yang sadar.

**b. Adverb restriktif sengaja tidak dimasukkan.** "only", "merely",
"solely", "just" **tidak** ada di daftar. Alasannya:

> *"'do not merely retry until...' asks for more than the retry, not for none."*

Jadi mereka paham bedanya "jangan **hanya** retry" (minta lebih) dengan
"jangan retry" (minta berhenti). Itu pemahaman bahasa, bukan pattern
matching.

**c. Semua aturan ditulis condong ke satu sisi:**

> *"H2 findings are CRITICAL, so a missed unbounded instruction costs more
> than a prohibition that stays reported. Every rule below is therefore
> written to fail towards reporting: anything not positively recognized is
> not a prohibition."*

**Ini prinsip yang persis sama dengan punya lo.**

`orchestrator.py:29-32` punya lo:

> *"the cost of a wrong guess is asymmetric: a crashed run strands live
> sessions, whereas a wrongly-tolerated error only marks one attempt
> failed."*

Dua penulis, dua proyek, domain beda, kesimpulan sama: **kalau biaya salah
tidak simetris, condongkan ke sisi yang lebih murah, dan tulis alasan itu
di komentar.** Ini bukan kebetulan - ini cara berpikir yang benar kalau lo
sadar lo nggak bisa sempurna.

---

## 4. H1.6 - dan logika berbeda antara dua aturan yang mirip

H1 punya 9 sub-kode. Yang paling menarik pasangan **H1.5 vs H1.6**.

Komentar baris 28-37:

> *"A description says what a tool is. A diagnosis says what distinguishes
> it from its nearest neighbour. A description can be entirely accurate and
> still fail as a diagnosis - which is precisely when a model picks the
> wrong tool."*

- **H1.5** tanya: *"apakah dua deskripsi ini ditulis mirip?"* (overlap kata)
- **H1.6** tanya: *"apakah ada kata di sini yang bikin pembaca bisa memilih
  yang satu daripada yang lain?"*

Dan kelemahan H1.5 yang mereka sadari:

> *"two descriptions can share almost no vocabulary and still be perfectly
> interchangeable, because their differing words are synonyms."*

### Aturan anti-duplikasi

Baris 626-628:

> *"A pair reported by H1.5 is not also reported by H1.6 - same defect."*

**Satu kecacatan, satu laporan.** Ini yang bikin LintLang nggak berisik.
Punya lo: kalau satu prompt melanggar 4 aturan, lo dapat 4 temuan. Itu
mungkin benar, tapi mungkin juga bikin orang berhenti baca.

### Bug yang mereka bayar mahal

Baris 53-56, soal kata **"store"**:

> *"'store' belongs here, not with the container nouns: as a container it
> would canonicalize to 'system' and be dropped as low-information, which
> silently deletes the only verb in a name like `fidelis_store` and makes it
> look dominated by `fidelis_recall`."*

Mereka menemukan kasus di mana **klasifikasi yang salah** bikin satu tool
kelihatan "didominasi" tool lain. Itu false positive yang tidak kelihatan
di tes - cuma kelihatan kalau lo perhatikan contoh nyata.

---

## 5. Kejujuran epistemik - dan ini yang paling lo kenal

Tiga tempat di mana mereka menolak berpura-pura tahu:

**a. `scanner.py:220`** - field untuk "kenapa tidak ada yang diperiksa":

> *"Why nothing was inspected, when nothing was (never a PASS)."*

**b. `scanner.py:413`** - file yang di-skip:

> *"is SKIPPED, which is visible and is never a PASS"*

**c. `baseline.py` docstring:**

> *"HERM scores and input errors are never changed by a baseline."*

Artinya: skor dan error **tidak boleh** dibungkam oleh baseline. Baseline
cuma menyembunyikan temuan struktural yang memang sudah lama ada; dia tidak
boleh menyembunyikan ketidakmampuan mengukur.

Lo kenal pola ini. Ingat `POLL_ERRORS` di orchestrator lo, dan ingat
percakapan kita soal `cancel()`? Nilai balik `None` itu buat membedakan
**"saya tahu aman"** dari **"saya nggak tahu"**. Itu prinsip yang sama
persis.

**Dan ini menjelaskan kenapa `SPEC-VERIFY-SELF-REFERENTIAL` lo begitu
penting buat mereka.** LintLang punya obsesi memisahkan "terukur" dari
"tidak terukur". Aturan lo tentang verifikasi mandiri itu contoh paling
tajam dari masalah yang sama: **tes yang lulus bukan bukti, kalau tesnya
ditulis dari asumsi yang mau dibuktikan.**

---

## 6. Infrastruktur produk yang belum lo punya

| Berkas | Isi | Untuk apa |
|---|---|---|
| `sarif.py` (261) | output SARIF 2.1.0 | GitHub Code Scanning |
| `baseline.py` (198) | baseline hash-based | sembunyikan temuan lama, kelihatan yang baru |
| `github_init.py` (193) | tulis workflow | pasang otomatis di CI |
| `report.py` (441) | verdict ERROR/FAIL/REVIEW/PASS/SKIPPED | keputusan satu kata |
| `extractors.py` (546) | cari prompt **di dalam source Python** | P1, P2 |

### `compute_verdict` - urutan prioritas yang eksplisit

```
ERROR    ada input error          (paling tinggi)
SKIPPED  tidak ada yang diperiksa (bukan PASS!)
FAIL     ada CRITICAL atau HIGH
REVIEW   ada MEDIUM
PASS     cuma LOW/INFO atau kosong
```

Perhatikan: **input error menang atas segalanya.** Kalau inputnya sendiri
gagal dibaca, tidak ada temuan yang bermakna - jadi jangan lapor PASS atau
FAIL, lapor ERROR.

### `baseline.py` - cara kerjanya

- simpan hash + lokasi + pesan temuan
- hash relatif (path sumber saja), bukan absolut
- "detector atau source berubah → tetap kelihatan" (konservatif)
- disimpan ke file JSON, ditulis atomic (pakai tempfile)

Kenapa hash konservatif: kalau pesannya berubah sedikit saja, temuan lama
muncul lagi. Itu **sengaja** - lebih baik muncul lagi daripada diam-diam
hilang.

---

## 7. Yang gue akui belum paham penuh

Jujur, ada tiga tempat yang gue belum baca sampai habis:

1. **`preflight/engine.py`, 1.393 baris.** Gue baru lihat `models.py` dan
   `scope.py`. Mesin keputusan yang sebenarnya (bagaimana scope + aturan →
   ALLOW/HOLD) belum gue baca.
2. **`herm.py` (204 baris).** Gue baca daftar 6 dimensinya, belum cara
   skornya dihitung atau bagaimana "coverage/confidence" bekerja.
3. **Delapan sub-kode H1 lain.** Gue cuma baca H1.5, H1.6, dan sebagian
   H1.1/H1.9.

Gue **tidak** akan merangkum yang belum gue baca. Analisis gue sebelumnya
sudah salah sekali karena terlalu cepat menyimpulkan dari nama aturan.

---

## 8. Yang gue paham sekarang, dan implikasinya

### Kenapa LintLang jauh lebih dalam dari perkiraan gue awal

Bukan karena jumlah aturannya (9, bukan 7). Tapi karena **lapisan bawahnya**:

- classifier scope per karakter (bukan regex)
- parser negasi dengan clause boundary
- skor hermeneutik 6 dimensi
- aturan anti-duplikasi antar-atruran
- infrastruktur CI lengkap

### Yang harus lo ambil (urutan prioritas baru)

**Prioritas 1 - scope classifier.** Ini bukan fitur, ini **fondasi**. Tanpa
ini, setiap aturan lo bisa salah baca "jangan bilang X" sebagai "bilang X".
Kalau lo ambil satu hal saja dari LintLang, ambil ini. Bukan kodenya
(189 baris) - **idenya**.

**Prioritas 2 - aturan anti-duplikasi.** Satu kecacatan, satu laporan.
Bikin hasil lo lebih mungkin dibaca manusia.

**Prioritas 3 - output SARIF.** Untuk masuk CI.

**Prioritas 4 - integrasi `pre_verify`.** Jalan otomatis, bukan manual.

**Prioritas 5 - aturan H/PF yang belum lo punya.** H1.6 (differentia),
H6 (format campur), P1 (kalibrasi).

### Yang makin jelas: jangan serahkan `prompt_lint` ke LintLang

Setelah baca dalam-dalam, alasan gue makin kuat, tapi **arahnya berubah**.

Alasannya **bukan** "aturan lo lebih bagus". Alasannya:

**LintLang mengukur bentuk. Lo mengukur hasil. Dan keduanya butuh
dipisah.**

Bukti paling kuat ada di lapis 3 mereka sendiri: HERM ngasih **skor**
justru karena mereka sadar "ambigu atau tidak" bukan pertanyaan ya/tidak.
Aturan lo (`SPEC-VERIFY-SELF-REFERENTIAL`) menanyakan hal yang berbeda
lagi: **apakah buktinya sah?** Itu pertanyaan tentang epistemologi, dan
LintLang tidak punya dimensi untuk itu.

Jadi:

```
LintLang          -> bentuk bahasa, skor dimensi, CI, SARIF
prompt_lint lo    -> kesahan bukti (bukti mandiri = bukan bukti)
scope classifier  -> ambil idenya, taruh di prompt_lint lo
```

---

## 8b. H6 - pelajaran paling langsung buat lo

H6 (format campur) punya catatan yang harus lo baca, karena ini **persis
masalah yang lo akan hadapi**.

Kutipan dari `patterns.py` sekitar baris 1395:

> *"H6's competing-contract finding **used to be true** whenever the words
> "JSON", "Markdown", or "XML" appeared twice over, so a document that
> merely listed the file types a tool accepts was reported as a format
> contract violation."*

Baca itu dua kali. Aturan mereka dulu **salah**, dan mereka tahu, dan
mereka **menulisnya di kode**.

Versi lamanya: kata "JSON" muncul dua kali → pelanggaran.
Kenyataannya: dokumen yang cuma mendaftar tipe file yang diterima tool →
kena lapor padahal tidak salah apa-apa.

Perbaikannya bukan "tambah pengecualian". Mereka mendefinisikan ulang:
**format hanya dihitung kalau prompt memberikannya sebagai instruksi
output.** Dan mereka mendaftar bentuk yang sah:

- kata kerja respons yang menunjuk padanya ("write your reply as friendly
  Markdown")
- "use X for/when"
- elipsis dispatch di awal kalimat ("XML for configs.")
- pernyataan keberterimaan eksplisit
- deklarasi "output format:"
- tuntutan "X output only"
- klausa konformansi schema

### Kenapa ini penting buat lo

Gue **menguji** ini, bukan menduga. Hasilnya:

**`FLUFF-COT`: false positive terbukti.** Dua prompt berlawanan maksud:

```
A. "Do not use chain of thought. Think step by step is forbidden."
B. "Think step by step and reason carefully."
```

Keduanya menghasilkan temuan **identik**:

```
[WARN] FLUFF-COT: Chain-of-thought prompting had no measurable effect...
```

Prompt yang **melarang** chain-of-thought dilaporkan **memakai**
chain-of-thought. Ini persis masalah yang `scope.py` LintLang ada untuk
mencegah, dan lo belum punya penangkalnya.

**`EM-DASH`: prediksi gue salah.** Gue duga prompt yang membahas em-dash
akan kena aturan em-dash. Ternyata tidak - aturan itu memeriksa karakter
em-dash itu sendiri, bukan kata "em-dash". Itu desain yang benar.

**Tapi muncul temuan lain di uji yang sama:** prompt "Jangan pakai
em-dash..." dapat `NO-CONSTRAINT` ("tidak ada constraint"), padahal kata
"jangan" jelas ada. Sebabnya: `NO-CONSTRAINT` hanya mengenali kata Inggris
(`must`, `never`, `exactly`, `only`).

Ini **bukan bug** - itu batas bahasa. Tapi berguna diketahui: `prompt_lint`
dirancang untuk prompt Inggris. Kalau lo menulis prompt Indonesia, beberapa
aturan akan buta.

### Pelajaran yang lebih berguna

LintLang sudah membayar harga untuk tahu bahwa **aturan lexical selalu
punya false positive**, dan cara mereka memperbaiki H6 bukan menambal
pengecualian - tapi **mendefinisikan ulang kapan aturan itu berlaku**.

Untuk `FLUFF-COT` lo, perbaikan yang sejalan: jangan cari kata
"step by step". Cari kata itu **yang berada dalam scope DIRECT** - yaitu
diperintahkan, bukan dilarang atau dibahas. Itu jawaban yang sama dengan
cara LintLang memperbaiki H6.

Dan lo baru saja mengalami versi lain masalah yang sama di repo ini:
`cancel()` yang tidak bisa membedakan lima keadaan.

---

## 9. Satu hal yang gue salah pahami, dan gue betulkan

Di analisis sebelumnya gue bilang:

> "LintLang mengkatalogkan bentuk bahasa yang biasanya salah. prompt_lint
> mengkatalogkan mode kegagalan yang diukur."

Itu **terlalu meremehkan mereka.** LintLang juga sampai ke insight empiris:

- H1.6 datang dari **pengamatan** bahwa model memilih tool yang salah
  ketika deskripsinya akurat tapi tidak membedakan
- `_SYNONYM_GROUPS` punya komentar "Retrieval splits three ways **on
  purpose**. `list X` / `search X` / `get X` are genuinely different
  operations"
- "store" masuk grup persistence setelah menemukan kasus `fidelis_store`

Jadi mereka **juga** belajar dari kasus nyata. Bedanya: mereka belajar dari
**kasus kegagalan seleksi tool**, sedangkan lo belajar dari **eksperimen
prompt yang dikontrol**.

Bedanya bukan "diukur vs tidak diukur". Bedanya: **apa yang diukur.**

Dan itu artinya **aturan lo bisa diajukan ke mereka** - bukan sebagai
"lebih baik", tapi sebagai **dimensi yang belum mereka ukur**:
kesahan verifikasi.

---

## Catatan

Pemahaman ini didasari pembacaan: `patterns.py` (bagian H2 penuh, H3-H7
sekitar), `detectors/h1.py` (H1.5, H1.6, sebagian H1.1/H1.9),
`preflight/scope.py` (seluruh 189 baris), `report.py` (compute_verdict),
`baseline.py` (docstring + struktur), `sarif.py` (struktur),
`extractors.py` (P1, P2), `models.py`, `__init__.py`, `herm.py` (daftar
dimensi).

Belum dibaca: `preflight/engine.py` penuh, `herm.py` cara skor, 8 sub-kode
H1 sisanya, `cli.py`, `ingestion.py`, `parsers.py`.
