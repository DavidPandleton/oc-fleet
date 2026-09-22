"""Tes karakterisasi prompt_lint: batas cakupan yang diketahui.

Tes ini mengunci perilaku prompt_lint SEBAGAI BUKTI, bukan sebagai
kebenaran. Kalau salah satu tes di sini gagal setelah perubahan, itu
artinya perilaku tool berubah - dan lo harus memutuskan apakah perubahan
itu perbaikan atau regresi.

Ada dua jenis penegasan di sini:

1. PERILAKU BENAR yang harus dijaga (misal: VAGUE-OUTPUT menangkap scope
   tak terbatas).
2. BATAS DIKETAHUI yang dicatat apa adanya (misal: FLUFF-COT kena false
   positive pada prompt yang melarang chain-of-thought).

Jenis kedua sengaja ditulis sebagai tes yang LULUS. Tujuannya supaya
batasnya TERLIHAT. Kalau suatu hari batasnya diperbaiki, tes akan gagal
dan memaksa seseorang memperbarui catatan ini - itu memang yang
diinginkan.

Ditemukan lewat uji head-to-head melawan LintLang v0.6.0 pada 2026-09-22.
Lihat UJI-lintlang.md.
"""

import pytest

from prompt_lint import lint


def rules(prompt):
    """Kembalikan id aturan yang dilaporkan untuk sebuah prompt."""
    return {f.rule_id for f in lint(prompt)}


# ---------------------------------------------------------------------------
# 1. Perilaku benar yang harus dijaga
# ---------------------------------------------------------------------------

def test_vague_output_menangkap_scope_tak_terbatas():
    """Scope tak terbatas harus jadi ERROR, bukan didiamkan.

    LintLang PASS di prompt ini (H2 mencari loop, bukan scope). prompt_lint
    menangkapnya. Ini kekuatan yang harus dijaga.
    """
    result = rules("Improve the codebase. Make it better where needed and fix any issues you find.")
    assert "VAGUE-OUTPUT" in result


def test_prompt_jelas_tidak_kena_vague_output():
    """Kalau salah satu tes di sini gagal setelah perubahan, itu
    artinya perilaku tool berubah. Ini kebalikan dari tes pertama: prompt
    dengan artefak konkret tidak boleh dianggap kabur.
    """
    result = rules(
        "Write the function parse_duration into /repo/src/duration.py. "
        "Run pytest and report the exit code."
    )
    assert "VAGUE-OUTPUT" not in result


def test_spec_no_invalid_contract_tersedia():
    """Aturan ini tidak ada padanannya di LintLang. Pastikan hidup."""
    result = rules("Implement parse_duration(s) that handles the valid forms 1h, 30m, 1h30m.")
    assert "SPEC-NO-INVALID-CONTRACT" in result


def test_em_dash_memeriksa_karakter_bukan_kata():
    """Desain yang benar: yang diperiksa karakter em-dash, bukan kata
    'em-dash'. Prompt yang membahas aturannya tidak boleh kena.
    """
    result = rules("Jangan pakai em-dash dalam output apapun.")
    assert "EM-DASH" not in result

    result = rules("Write the summary \u2014 keep it short.")
    assert "EM-DASH" in result


def test_fluff_cot_tidak_kena_prompt_yang_melarang():
    """Prompt yang MELARANG chain-of-thought bukan memakai chain-of-thought.

    Dulu ini false positive (tercatat di commit sebelumnya, ditemukan lewat
    uji head-to-head melawan LintLang). Sekarang diperbaiki lewat
    `_is_negated`, yang membatasi cakupan aturan fluff ke kalimatnya
    sendiri.
    """
    assert "FLUFF-COT" not in rules(
        "Do not use chain of thought. Think step by step is forbidden."
    )
    assert "FLUFF-COT" not in rules("Never ask for step-by-step reasoning.")
    assert "FLUFF-COT" not in rules("Avoid chain of thought.")
    assert "FLUFF-COT" not in rules("Think step by step is unnecessary.")


def test_fluff_cot_masih_menangkap_yang_memang_memakai():
    """Sisi lain: perbaikan tidak boleh mematikan temuan yang benar.

    Ini penjaga utama. Kalau `_is_negated` terlalu luas, tes ini gagal.
    """
    assert "FLUFF-COT" in rules("Think step by step and reason carefully.")
    assert "FLUFF-COT" in rules("Please think step by step.")
    assert "FLUFF-COT" in rules("This is critical production code. Think step by step.")
    # Negator di kalimat lain tidak boleh menular ke kalimat berikutnya.
    assert "FLUFF-COT" in rules("Never commit secrets. Think step by step.")
    # Negator di klausa koordinatif sebelumnya tidak menular.
    assert "FLUFF-COT" in rules("Do not retry, and think step by step.")


def test_fluff_aturan_lain_juga_dijaga_negasi():
    """Perbaikan berlaku untuk semua aturan FLUFF-*, bukan cuma COT."""
    assert "FLUFF-ROLE" not in rules("Do not act as a senior engineer.")
    assert "FLUFF-ROLE" in rules("You are a senior engineer. Write the function.")
    assert "FLUFF-URGENCY" not in rules("This is not mission critical.")
    assert "FLUFF-URGENCY" in rules("This is mission critical production code.")


def test_vague_output_tidak_kena_prompt_yang_mengutuk_kata_kabur():
    """Prompt yang MELARANG kata kabur bukan prompt yang kabur.

    "Do not say improve or enhance" menyebut kata-kata itu untuk
    dilarang, bukan memakainya sebagai perintah.
    """
    assert "VAGUE-OUTPUT" not in rules(
        "Do not say improve or enhance. Write the parser into /tmp/p.py."
    )
    assert "VAGUE-OUTPUT" not in rules(
        "Avoid vague words like 'make it better'. Fix index.js at line 12."
    )
    assert "VAGUE-OUTPUT" not in rules("Do not clean up. Fix the parser at line 4.")
    # Sisi lain: yang memang memakai kata kabur tetap dilaporkan.
    assert "VAGUE-OUTPUT" in rules("Improve the codebase. Make it better.")
    assert "VAGUE-OUTPUT" in rules("Clean up the mess and fix everything you find.")


def test_destructive_no_guard_tidak_kena_prompt_yang_melarang():
    """Prompt yang MELARANG tindakan destruktif bukan tindakan destruktif."""
    assert "DESTRUCTIVE-NO-GUARD" not in rules(
        "Do not delete anything. Read /tmp/a.py and report."
    )
    assert "DESTRUCTIVE-NO-GUARD" not in rules(
        "Never force push. Commit to /tmp/repo on branch main."
    )
    # Sisi lain: yang memang destruktif tetap dilaporkan.
    assert "DESTRUCTIVE-NO-GUARD" in rules("Delete all branches and force push.")
    assert "DESTRUCTIVE-NO-GUARD" in rules("Drop the production database.")


def test_konjungsi_tanpa_koma_bukan_klausa_baru():
    """Hanya konjungsi ber-koma yang memisah klausa.

    "do not say improve or enhance" menghubungkan dua objek dari satu
    verba, jadi negasinya mencakup keduanya. "do not retry, and think step
    by step" punya koma, jadi klausa kedua diperintahkan.
    """
    assert "VAGUE-OUTPUT" not in rules("Do not say improve or enhance.")
    assert "FLUFF-COT" in rules("Do not retry, and think step by step.")
    assert "FLUFF-COT" not in rules("Do not think step by step or reason carefully.")


# ---------------------------------------------------------------------------
# 2. Batas yang diketahui (sengaja LULUS, supaya terlihat)
# ---------------------------------------------------------------------------


def test_BATAS_tidak_ada_deteksi_loop_tak_terbatas():
    """BATAS DIKETAHUI: tidak ada aturan untuk loop tak terbatas.

    LintLang menangkap ini sebagai CRITICAL (H2). prompt_lint diam.
    Kalau suatu hari prompt_lint menangkapnya, tes ini akan gagal dan
    itu kabar baik - perbarui catatannya.
    """
    result = rules("Keep trying until it works.")
    aturan_loop = {r for r in result if "LOOP" in r or "UNBOUNDED" in r.upper()}
    assert aturan_loop == set(), (
        "Kalau ini gagal, prompt_lint sudah punya deteksi loop tak "
        "terbatas. Bagus. Hapus tes ini dan tambahkan ke bagian 1."
    )


def test_BATAS_negasi_dikenali_hanya_untuk_constraint():
    """BATAS DIKETAHUI: negasi dikenali sebagian, tidak merata.

    Awalnya gue duga tidak ada penangkal negasi sama sekali. Ternyata
    SALAH: NO-CONSTRAINT mengenali kata "never" - prompt yang melarang
    ("Never keep trying...") justru TIDAK dilaporkan kurang constraint,
    sedangkan yang memakai ("Keep trying...") dilaporkan.

    Jadi: negasi dikenali untuk constraint, tapi TIDAK untuk loop.
    Tidak ada aturan yang membedakan loop terlarang dari loop diperintah.
    """
    melarang = rules("Never keep trying until it works. Stop after two attempts.")
    memakai = rules("Keep trying until it works.")

    # NO-CONSTRAINT kenal "never": ini yang benar.
    assert "NO-CONSTRAINT" not in melarang
    assert "NO-CONSTRAINT" in memakai

    # Tapi tidak ada aturan yang membedakan loop-nya sendiri.
    loop_melarang = {r for r in melarang if "LOOP" in r or "UNBOUNDED" in r.upper()}
    loop_memakai = {r for r in memakai if "LOOP" in r or "UNBOUNDED" in r.upper()}
    assert loop_melarang == loop_memakai == set()


def test_BATAS_hanya_mengenali_constraint_bahasa_inggris():
    """BATAS DIKETAHUI: NO-CONSTRAINT buta pada kata Indonesia.

    Prompt Indonesia yang jelas punya constraint dilaporkan tidak punya.
    Bukan bug logika - ini batas bahasa yang perlu diketahui.
    """
    indo = rules("Harus memakai Python 3. Jangan pakai library eksternal.")
    assert "NO-CONSTRAINT" in indo, (
        "Kalau ini gagal, NO-CONSTRAINT sudah mengenali kata Indonesia. "
        "Bagus - perbarui catatannya."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
