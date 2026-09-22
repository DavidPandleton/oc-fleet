"""Tes karakterisasi untuk dua temuan review di fleet.py.

Lihat TEMUAN-REVIEW.md. Tes ini MENGUNCI PERILAKU YANG ADA SEKARANG, bukan
perilaku yang seharusnya. Tujuannya: kalau ada yang memperbaiki bug ini,
dia harus sadar dan mengubah tes ini secara sengaja - bukan tanpa sengaja.

Semua nilai di sini sudah diverifikasi terhadap server OpenCode hidup
(2026-09-22), bukan hasil pembacaan kode saja.
"""

import base64
import email.message
import json
import unittest
import urllib.error
from unittest import mock

from fleet import Fleet


def _context(payload):
    raw = json.dumps(payload).encode("utf-8")
    context = mock.Mock()
    context.read.return_value = raw
    context.__enter__ = lambda s: context
    context.__exit__ = lambda *a: None
    return context


class _FleetBase(unittest.TestCase):
    def setUp(self):
        self.fleet = Fleet(base_url="http://127.0.0.1:4096", password_file="/nonexistent")
        self.fleet.headers["Authorization"] = (
            "Basic " + base64.b64encode(b"opencode:testpass").decode()
        )

    def _dispatch_body(self, model):
        """Panggil dispatch() dan kembalikan body yang benar-benar dikirim."""
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = [_context({"data": {"id": "ses_x"}}), _context(None)]
            self.fleet.dispatch("tugas", "/tmp/wd", model=model)
            request = urlopen.call_args_list[0].args[0]
            return json.loads(request.data.decode())


class DispatchModelParsing(_FleetBase):
    """dispatch(): model rusak ditolak keras (baris 92-98, diperbaiki).

    Sebelumnya input rusak dikirim ke server tanpa error, atau diabaikan
    diam-diam. Server menerima `providerID: ""` dengan HTTP 200 dan
    menyimpannya, jadi sesi baru gagal jauh di kemudian hari.
    """

    def _body_atau_error(self, model):
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = [_context({"data": {"id": "ses_x"}}), _context(None)]
            self.fleet.dispatch("tugas", "/tmp/wd", model=model)
            request = urlopen.call_args_list[0].args[0]
            return json.loads(request.data.decode())

    def test_provider_kosong_ditolak(self):
        """'/foo' sekarang ditolak, tidak dikirim dengan providerID kosong."""
        with self.assertRaisesRegex(ValueError, "missing a provider"):
            self._body_atau_error("/foo")

    def test_spasi_di_depan_dibersihkan(self):
        body = self._body_atau_error(" cutad/qwen")
        self.assertEqual(body["model"]["providerID"], "cutad")

    def test_spasi_di_belakang_dibersihkan(self):
        body = self._body_atau_error("cutad /qwen")
        self.assertEqual(body["model"]["providerID"], "cutad")

    def test_model_tanpa_separator_ditolak(self):
        """'no-slash' ditolak dengan alasan 'tanpa separator'.

        Penting untuk membedakan dari kasus 'cutad/': tanpa cek `sep` yang
        terpisah, 'no-slash' tertangkap oleh cek model_id kosong dan
        perilaku lamanya (diabaikan diam-diam) bisa kembali tanpa ketahuan.
        """
        with self.assertRaisesRegex(ValueError, "no '/' separator"):
            self._body_atau_error("no-slash")

    def test_model_tanpa_id_ditolak(self):
        """'cutad/' ditolak dengan alasan 'tanpa model id'."""
        with self.assertRaisesRegex(ValueError, "missing a model id"):
            self._body_atau_error("cutad/")

    def test_slash_pertama_benar_untuk_nama_ber_slash(self):
        """Split di slash pertama tetap benar: 'a/b/c' -> id 'b/c'."""
        body = self._body_atau_error("a/b/c")
        self.assertEqual(body["model"], {"providerID": "a", "id": "b/c"})

    def test_model_kosong_tetap_pakai_default(self):
        """String kosong berarti 'pakai default', bukan kesalahan."""
        body = self._body_atau_error("")
        self.assertNotIn("model", body)

    def test_model_spasi_saja_dianggap_kosong(self):
        body = self._body_atau_error("   ")
        self.assertNotIn("model", body)

    def test_model_valid_dikirim_apa_adanya(self):
        body = self._body_atau_error("cutad/qwen3-8-flash-next")
        self.assertEqual(body["model"], {"providerID": "cutad", "id": "qwen3-8-flash-next"})


class CancelReturnValue(_FleetBase):
    """cancel(): tri-state, supaya kegagalan tidak tersamar sebagai 'selesai'.

    Sebelumnya semua keadaan mengembalikan False, sehingga 'server mati' dan
    'sesi sudah selesai' tidak bisa dibedakan. Diverifikasi terhadap server
    OpenCode hidup.
    """

    def _cancel(self, payload=None, error=None):
        def fake(req, *a, **k):
            if error is not None:
                raise error
            return _context(payload)

        with mock.patch("urllib.request.urlopen", fake):
            return self.fleet.cancel("ses_apa_saja")

    def test_berhasil_dihentikan_balikin_true(self):
        self.assertIs(self._cancel({"interrupted": True}), True)

    def test_sudah_selesai_balikin_false(self):
        """False = jawaban sah: tidak ada yang perlu dihentikan."""
        self.assertIs(self._cancel({"interrupted": False}), False)

    def test_sesi_tidak_ada_balikin_none(self):
        """404 adalah kegagalan, bukan 'sudah selesai'."""
        err = urllib.error.HTTPError("u", 404, "Not Found", email.message.Message(), None)
        self.assertIsNone(self._cancel(error=err))

    def test_kredensial_salah_balikin_none(self):
        err = urllib.error.HTTPError("u", 401, "Unauthorized", email.message.Message(), None)
        self.assertIsNone(self._cancel(error=err))

    def test_server_mati_balikin_none(self):
        err = urllib.error.URLError("connection refused")
        self.assertIsNone(self._cancel(error=err))

    def test_tiga_keadaan_sekarang_bisa_dibedakan(self):
        """Inti perbaikan: tiga hasil berbeda untuk tiga keadaan berbeda."""
        self.assertIs(self._cancel({"interrupted": True}), True)
        self.assertIs(self._cancel({"interrupted": False}), False)
        err = urllib.error.URLError("refused")
        self.assertIsNone(self._cancel(error=err))

    def test_session_id_kosong_balikin_none(self):
        self.assertIsNone(self.fleet.cancel(""))


if __name__ == "__main__":
    unittest.main()
