# Engine preflight LintLang: apa yang gue paham

Dibuat 2026-09-22. Hasil membaca `preflight/engine.py` (1.393 baris).

Ini melanjutkan `ANALISIS-lintlang.md`. Fokusnya satu berkas: mesin
keputusan preflight.

---

## PF001-PF005 bukan aturan kualitas prompt

Gue salah duga. Gue kira PF001-005 itu aturan lanjutan untuk menilai
prompt. Ternyata **bukan** - ini tentang **pertanyaan yang menyesatkan**.

| Kode | Label | Menyasar |
|---|---|---|
| PF001 | validation-seeking-frame | "isn't it true that...", ", right?" |
| PF002 | presupposed-causality | "why does X cause Y" (mengandaikan kausalitas) |
| PF003 | unresolved-context-reference | "our usual format", "as before" |
| PF004 | missing-required-context | context wajib tidak ada |
| PF005 | explicit-instruction-conflict | dua instruksi tidak bisa dipenuhi bersama |

Masing-masing punya: `label`, `risk`, `explanation`, `suggestion`,
`confidence`, `maturity`, `enforcement`, `related_output_modes`.

Contoh PF001:

```
label:       validation-seeking-frame
risk:        The wording frames confirmation as the conversational default.
explanation: This input-side steer is not evidence that the proposition is
             true or false.
suggestion:  Ask for evidence for and against the proposition.
```

Perhatikan `explanation`-nya. Mereka **menjelaskan batas alat**: "ini bukan
bukti benar atau salah". Linter yang jujur soal apa yang **tidak** dia
buktikan.

PF003 bahkan lebih tegas:

> *"History is outside LintLang's boundary; the caller must supply the
> value."*

## Confidence dan enforcement - dua sumbu terpisah

```
Confidence:   EXACT | HEURISTIC
Enforcement:  HOLD_ELIGIBLE | NOTICE_ONLY
```

PF001 dan PF002: HEURISTIC + NOTICE_ONLY (tidak pernah menahan).
PF003: EXACT + NOTICE_ONLY.
PF004, PF005: EXACT + HOLD_ELIGIBLE (bisa menahan).

Dan keputusan akhirnya cuma satu baris:

```python
if any(f.confidence is EXACT and f.enforcement is HOLD_ELIGIBLE for f in findings):
    status = HOLD
elif findings:
    status = NOTICE
else:
    status = ALLOW
```

**Harus EXACT **dan** HOLD_ELIGIBLE baru menahan.** Temuan heuristik tidak
pernah memblokir apa pun, sekuat apa pun keyakinan penulisnya.

Ini pemisahan yang bersih: **seberapa yakin** (confidence) dipisah dari
**apa akibatnya** (enforcement). Banyak tool mencampur keduanya.

---

## Temuan terbesar: 11 gerbang validasi sebelum satu aturan jalan

`_preflight()` punya **11 `return _early(...)`** sebelum `_detect()` pernah
dipanggil. Lebih dari 20 kode error berbeda di seluruh berkas.

Urutannya:

```
1.  request bukan PreflightRequest        -> INVALID_REQUEST
2.  policy tidak valid                    -> INVALID_POLICY
3.  prompt bukan string                   -> INVALID_PROMPT
4.  prompt bukan UTF-8 valid              -> INVALID_UTF8
5.  prompt kosong                         -> EMPTY_INPUT
6.  prompt > 32 KiB                       -> INPUT_TOO_LARGE
7.  language tidak valid                  -> INVALID_LANGUAGE
8.  context tidak valid                   -> INVALID_CONTEXT (120 baris validator)
9.  tidak ada rule aktif                  -> ALLOW (eksplisit, bukan lolos)
10. bahasa bukan 'en'                     -> UNAVAILABLE
11. scope tidak aman                      -> UNAVAILABLE
--- baru di sini ---
    _detect() dipanggil
```

### Kenapa ini penting

**Langkah 10 dan 11 adalah keputusan desain yang berani.** Kalau prompt
bukan bahasa Inggris, mereka **menolak memberi verdict**. Bukan PASS,
bukan FAIL - `UNAVAILABLE`. Sama kalau delimiter scope tidak seimbang.

Mereka memilih **tidak menjawab** daripada menjawab dengan dasar yang tidak
bisa dipercaya. Itu kejujuran epistemik yang diwujudkan jadi alur kode.

### Bandingkan dengan `fleet.py` di oc-fleet

`dispatch()` di `fleet.py` (sebelum perbaikan) **tidak punya gerbang sama
sekali**. Itu sebabnya server bisa menerima model rusak dengan HTTP 200
dan menyimpannya. Perbaikannya (`_parse_model` raise) itu benar, tapi
bersifat tambalan untuk satu bidang - bukan satu prinsip.

LintLang menunjukkan bentuk lain: **validasi sebagai tahap tersendiri**,
sebelum logika apa pun, dengan kode error sendiri per bidang.

Itu pelajaran arsitektur, dan lebih berguna dari daftar aturan mana yang
tumpang tindih.

---

## Setiap detektor lewat gerbang scope

Pola yang muncul di setiap aturan:

```python
for match in pattern.finditer(prompt):
    if not scope.is_direct(match.start(), match.end()):
        continue
    ...
```

Tidak ada satu pun detektor yang langsung melapor. Semua harus lolos
`is_direct` dulu. Itu sebabnya *"jangan pernah bilang X"* tidak dilaporkan
sebagai "bilang X".

## Penawaran perbaikan, bukan cuma keluhan

`_FindingSeed` membawa `edits`: daftar `(start, end, replacement)`. Contoh
nyata dari PF001:

- pola 0: `"is it true that ..."` -> `"What evidence supports or refutes whether ..."`
- pola 1: `"wouldn't you agree ..."` -> `"Assess whether ..."`
- pola 2: `", right?"` -> `"?"`

Jadi temuan bukan sekadar "ini salah". Dia membawa **teks pengganti yang
siap dipakai**. Itu kenapa ada `corrections` di samping `findings`, dengan
`correction_id` yang ditautkan ke `finding_id`.

## `_materialize` - memasang metadata ke temuan

Alur: `_FindingSeed` (temuan mentah) -> `_materialize` (isi metadata dari
`_RULE_META`) -> `Finding` (lengkap).

`finding_id` deterministik dari seed + sha256 prompt:

```
finding_id = "pf_" + sha20(seed, prompt_sha256)
```

Artinya temuan yang sama pada prompt yang sama **selalu punya id yang
sama**. Itu yang membuat override dan baseline bisa bekerja.

---

## Yang harus lo ambil dari engine.py

**Prioritas 1 - validasi sebagai tahap tersendiri.** Gerbang input sebelum
logika, kode error per bidang. Ini akan memperbaiki kelas bug yang sama di
`fleet.py`, `orchestrator.py`, dan `dashboard.py` sekaligus.

**Prioritas 2 - pisahkan confidence dari enforcement.** "Seberapa yakin"
dan "apa akibatnya" itu dua pertanyaan berbeda. Punya lo: `severity` saja.
Pemisahan ini bikin aturan heuristik bisa hidup tanpa memblokir orang.

**Prioritas 3 - temuan yang membawa perbaikan.** `Finding` lo punya
`suggestion`. Naikkan jadi `edit` yang siap diterapkan, dengan id
deterministik.

**Prioritas 4 - status UNAVAILABLE.** Untuk kasus di mana lo **tidak bisa
menilai**. Ini konsekuensi dari Prioritas 1 dan 2, dan merupakan bentuk
paling jujur dari "tidak tahu".

---

## Yang gue masih belum paham

- `_validate_context` (120 baris) - gue baca strukturnya, belum detail
  setiap pemeriksaannya.
- `_validate_policy` (90 baris) - idem.
- `herm.py` cara skor 6 dimensi dihitung.
- 8 sub-kode H1 yang belum gue baca.

Gue tidak akan merangkum yang belum gue baca.

---

## Angka terverifikasi

- `engine.py`: 1.393 baris
- `return _early(...)`: 11 kemunculan
- kode error unik: lebih dari 20 (dihitung dari string `[A-Z_]{8,}`)
- `_validate_policy`: 90 baris
- `_validate_context`: 120 baris
- `EXIT_CODES`: ALLOW=0, NOTICE=0, HOLD=1, ERROR=2, UNAVAILABLE=3
