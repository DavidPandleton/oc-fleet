# Perbaikan: FLUFF-COT false positive

Dibuat 2026-09-22. Perbaikan bug yang ditemukan lewat uji head-to-head
melawan LintLang.

---

## Bug

`prompt_lint` tidak bisa membedakan prompt yang **melarang** fluff dari
prompt yang **memakai** fluff.

```
A. "Do not use chain of thought. Think step by step is forbidden."
B. "Think step by step and reason carefully."
```

Dua prompt dengan maksud **berlawanan** menghasilkan temuan `FLUFF-COT`
yang **identik**. Prompt A menolak chain-of-thought, tapi dilaporkan
memakai chain-of-thought.

Ini bukan bug kecil. Ini persis mode kegagalan yang jadi alasan `scope.py`
LintLang ada: linter yang salah baca **membalik maksud penulisnya**.

---

## Akar

`prompt_lint` menjalankan setiap aturan sebagai `re.search` polos pada
seluruh prompt, tanpa memperhatikan konteks:

```python
if re.search(pattern, prompt, ...):
    findings.append(...)
```

Tidak ada langkah yang bertanya: **apakah frasa ini diperintahkan, atau
dilarang?**

---

## Perbaikan

Ditambahkan `_is_negated(text, pattern)` dan gerbang di loop aturan:
aturan `FLUFF-*` dilewati kalau frasanya berada dalam larangan.

Bukan pengecualian per-kasus. Ini **mendefinisikan ulang kapan aturan
fluff berlaku** - sama seperti cara LintLang memperbaiki H6.

### Aturan yang dipakai

Negasi dikenali dari **dua sisi**, keduanya terikat:

**Sisi kiri** - negator dalam kalimat yang sama, dipisah klausa:
```
never, do not, don't, must not, should not, avoid, refrain from,
no need to, without, forbidden, prohibited,
is not / are not / isn't / aren't   (terikat kopula)
```

**Sisi kanan** - predikat larangan setelah frasa:
```
is/are not allowed, is/are not <kata>, is forbidden, is prohibited,
is unnecessary, is unwanted
```

Plus dua batas:
- **Batas kalimat**: `.!?;` dan baris baru
- **Batas klausa koordinatif**: `and`, `but`, `or`, `then`, `yet`

---

## Yang gue pelajari dari kegagalan gue sendiri

Gue butuh **tiga iterasi**, dan dua yang pertama salah. Ini catatannya,
karena nilainya lebih dari perbaikannya.

### Percobaan 1: window karakter

Gue batasi negator harus **persis menempel** di depan frasa (`\Z` anchor).
Hasil: **5/8**. Gagal karena:
- "Do not use chain of thought. **Think step by step** is forbidden."
  → negasinya di frasa lain
- "Never **ask for** step-by-step reasoning."
  → ada kata antara negator dan frasa

### Percobaan 2: cakupan per-kalimat

Gue ganti jadi: negator boleh di mana saja dalam kalimat. Hasil: **11/12**.
Gagal satu: "Think step by step **is forbidden**" - negasinya datang
**setelah** frasa, tidak terlihat oleh pencarian ke kiri.

### Percobaan 3: dua sisi + bentuk terikat

Tambah `_TRAILING_PROHIBITION` untuk sisi kanan. Hasil: **16/16**.

### Pelajaran yang sebenarnya

Gue hampir membuat dua kesalahan yang **sama jenisnya** dengan yang gue
kritik di LintLang:

**Kesalahan A.** Di percobaan 1, gue hampir menambahkan pengecualian
per-kasus: kalau gagal di "Never ask for", tambah string "never ask for".
Itu tambalan, bukan definisi. Yang benar: ubah **cakupan** dari "menempel"
ke "satu kalimat".

**Kesalahan B.** Yang paling berbahaya, dan gue hampir melakukannya:
ketika "This is not mission critical" gagal, refleks gue adalah menambah
**"not" polos** ke daftar negator.

Gue berhenti dan menguji dulu. Hasilnya:

```
"Think step by step, not just the answer."
```

Ini **memerintahkan** chain-of-thought. Kalau "not" jadi negator, aturan
fluff akan dilewati, dan **temuan yang benar hilang.**

Jadi menambah "not" polos bukan memperbaiki - itu **menukar false positive
dengan false negative**. Dan karena aturan ini `warn` (bukan memblokir),
arah kesalahannya harus condong **ke melaporkan**, bukan ke diam.

Yang benar: hanya "not" yang **terikat bentuk** (`is not`, `aren't`).

**Ini prinsip yang sama dengan `POLL_ERRORS = Exception` di orchestrator
lo.** Biaya salah tidak simetris, jadi condongkan ke sisi yang lebih murah.
Di sini: kelebihan laporan itu mengganggu, kekurangan laporan itu menghilang
tanpa jejak.

---

## Verifikasi

**Matriks yang harus BLOK (tidak dilaporkan):**
```
Do not use chain of thought. Think step by step is forbidden.   OK
Never ask for step-by-step reasoning.                           OK
Avoid chain of thought.                                         OK
Think step by step is unnecessary.                              OK
This is not mission critical.                                   OK
Do not act as a senior engineer.                                OK
```

**Matriks yang harus LAPOR (tetap dilaporkan):**
```
Think step by step and reason carefully.                        OK
Think step by step, not just the answer.                        OK
Do not skip tests. Think step by step.                          OK
Never commit secrets. Think step by step.                       OK
Do not retry, and think step by step.                           OK
This is mission critical production code.                       OK
You are a senior engineer, not a junior.                        OK
You are a senior engineer. Write the function.                  OK
```

**14/14 benar.**

**Suite:** 190 -> 200 lulus, tanpa regresi.

---

## Efek pada tes karakterisasi

`test_prompt_lint_karakterisasi.py` punya tes yang mencatat batas ini:

```python
def test_BATAS_fluff_cot_false_positive_pada_larangan():
    ...
```

Setelah perbaikan, tes itu **gagal** - dan itu memang tujuannya. Docstring-nya
sudah bilang: *"Kalau suatu hari gagal, berarti FLUFF-COT sudah diperbaiki -
hapus tes ini dan pindahkan kasusnya ke bagian 1."*

Gue melakukan persis itu: hapus tes batasnya, tambah tiga tes di bagian
"perilaku benar". Tes karakterisasi bekerja sesuai desain.

---

## Yang masih belum diperbaiki

`_is_negated` sekarang dipakai untuk tiga keluarga aturan:
`FLUFF-*`, `VAGUE-OUTPUT`, `DESTRUCTIVE-NO-GUARD`.

Belum diuji apakah punya false positive negasi serupa:

- `NO-ARTEFACT`, `NO-CONSTRAINT` - aturan "tidak ada X" ini membaca
  seluruh prompt sebagai bukti. Frasa ternegasi di satu tempat tidak
  boleh membungkamnya, jadi kemungkinan besar memang tidak perlu.
- `SPEC-*` - idem, dan sengaja tidak dimasukkan ke daftar.
- `UNBOUNDED-SCOPE` - diuji, ternyata bersih (nol false positive pada
  kasus yang dicoba).

Kandidat berikutnya untuk diperiksa. Tapi jangan tebak - **uji dulu**,
dengan prompt berlawanan maksud seperti di atas.

---

## Tambahan: dua aturan lain, dan satu bug di perbaikannya sendiri

Setelah `FLUFF-*` diperbaiki, gue uji aturan lain. Dua lagi kena:

```
"Do not say improve or enhance."   -> VAGUE-OUTPUT (salah)
"Do not delete anything."          -> DESTRUCTIVE-NO-GUARD (salah)
```

Keduanya prompt yang **melarang** hal yang aturannya cari. Bentuknya sama
persis. `_is_negated` diperluas ke tiga keluarga aturan, dengan cakupan
per-aturan (bukan global), supaya `SPEC-*` tidak ikut terbungkam.

### Bug di logika klausa

Perbaikan pertama gue **memecahkan** kasus koordinatif:

```
"Do not retry, and think step by step."
```

Ini **memerintahkan** chain-of-thought di klausa kedua. Tapi `_COORD_RE`
gue memotong di setiap konjungsi, sehingga "and think step by step"
kehilangan negatornya DAN jadi salah diblok.

Dua percobaan gagal:

1. **Butuh subjek setelah konjungsi.** "and think" tidak punya subjek,
   jadi tidak terdeteksi sebagai klausa baru. Gagal.
2. **Daftar akhiran kata.** Heuristik rapuh - pola `[a-z]+(?:e|y|t|k|...)`
   cocok dengan hampir semua kata. Itu bukan definisi, itu tebakan.

Yang benar, dan lebih sederhana: **hanya konjungsi yang didahului koma
yang memisah klausa.**

```
"do not say improve or enhance"        -> tanpa koma, satu objek list
"do not retry, and think step by step" -> ada koma, klausa baru
```

Itu perilaku yang sama dengan `_left_clause_start` di LintLang, yang
memanggil `_COORDINATED_CLAUSE` **hanya setelah menemukan koma**
(`patterns.py:292`). Gue sampai ke sana lewat dua kegagalan, bukan lewat
membaca, tapi hasilnya sama.

Diverifikasi langsung pada kode mereka:

```
_left_clause_start("Do not say improve or enhance", 0)      -> 0
    negasi mencakup seluruh kalimat
_left_clause_start("Do not retry, and think step by step", 0) -> 13
    negasi dipotong di koma, klausa kedua diperintahkan
```

Catatan: `_COORDINATED_CLAUSE` di LintLang terlihat seperti memisah di
setiap konjungsi (polanya `\s*(?:and|but|or|...)`), tapi pemanggilnya
hanya memakainya **setelah koma** ditemukan. Yang menentukan bukan pola
konjungsi, tapi fakta bahwa koma harus ada lebih dulu.

Pelajaran yang diulang: **kalau perbaikan mulai butuh daftar kata atau
pola yang makin rumit, itu tanda sedang menambal. Berhenti, cari definisi
yang lebih sederhana.**

