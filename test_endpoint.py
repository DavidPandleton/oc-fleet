"""Tes untuk endpoint.py.

Modul ini menemukan alamat dan password server OpenCode tanpa disuruh.
Dua kesalahan yang pernah terjadi di sini layak dikunci sebagai tes,
karena keduanya menyebabkan kegagalan yang tampak seperti masalah lain:

1. Password dari log serve dipakai lebih dulu daripada service.json.
   Log tertinggal satu restart, jadi setiap panggilan mendapat 401.
2. Verifikasi menerima endpoint yang MENJAWAB ANONIM. Proxy socat di
   port 8080 menjawab 200 untuk permintaan anonim, jadi `discover()`
   memilih port yang salah - lebih buruk daripada tidak menemukan apa pun.
"""

from __future__ import annotations

import base64
import json
import os
import unittest
from unittest import mock

import endpoint


def _konfigurasi(password):
    """Konteks mock: service.json berisi password tertentu."""
    return mock.patch.object(
        endpoint, "_SERVICE_CONFIG", "/tmp/__tes_service.json"
    ), mock.patch("builtins.open", mock.mock_open(read_data=json.dumps({"password": password})))


class PasswordOrderTest(unittest.TestCase):
    def test_service_config_beats_serve_log(self):
        """service.json lebih dipercaya daripada log serve yang basi.

        Ini regresi untuk bug nyata: di mesin tempat ini dikembangkan,
        serve.log menyimpan 'TeamE7ep...' (memberi 401) sementara
        service.json menyimpan 'gt9I8G_...' (memberi 200). Urutan lama
        memakai log lebih dulu, jadi setiap panggilan gagal sampai
        seseorang mengoper kredensial dengan tangan.
        """
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            endpoint, "_password_from_logs", return_value=("BASI", "/tmp/serve.log")
        ), mock.patch.object(
            endpoint,
            "open",
            mock.mock_open(read_data=json.dumps({"password": "SEGAR"})),
        ):
            kandidat = endpoint.password_candidates()
        self.assertEqual(kandidat[0][0], "SEGAR")
        # Yang basi tetap tersedia sebagai cadangan, bukan dibuang.
        self.assertIn("BASI", [pw for pw, _ in kandidat])

    def test_env_var_beats_everything(self):
        with mock.patch.dict(os.environ, {"OPENCODE_SERVER_PASSWORD": "DARI_ENV"}):
            kandidat = endpoint.password_candidates()
        self.assertEqual(kandidat[0][0], "DARI_ENV")

    def test_candidates_deduplicated_in_order(self):
        """Password yang sama dari dua sumber hanya muncul sekali."""
        with mock.patch.dict(os.environ, {"OPENCODE_SERVER_PASSWORD": "SAMA"}), \
             mock.patch.object(endpoint, "open", mock.mock_open(
                 read_data=json.dumps({"password": "SAMA"}))):
            kandidat = endpoint.password_candidates()
        self.assertEqual([pw for pw, _ in kandidat], ["SAMA"])


class RespondsAsOpencodeTest(unittest.TestCase):
    """Verifikasi harus menolak endpoint yang menjawab anonim."""

    def test_anonymous_200_is_rejected(self):
        """Proxy yang meneruskan apa saja BUKAN server opencode.

        socat di 8080 menjawab 200 untuk permintaan anonim. Versi pertama
        fungsi ini menganggap itu bukti server hidup, sehingga discover()
        mengembalikan port yang salah.
        """
        import urllib.error
        import urllib.request

        # Permintaan anonim berhasil (200): harus langsung menolak.
        with mock.patch.object(urllib.request, "urlopen") as urlopen:
            ctx = mock.MagicMock()
            ctx.__enter__ = lambda s: ctx
            ctx.__exit__ = lambda *a: None
            ctx.status = 200
            urlopen.return_value = ctx
            self.assertFalse(
                endpoint._responds_as_opencode("http://127.0.0.1:8080", "pw")
            )

    def test_anonymous_401_then_authed_200_is_accepted(self):
        """Server sebenarnya: 401 tanpa kredensial, 200 dengan kredensial."""
        import urllib.error
        import urllib.request

        ctx = mock.MagicMock()
        ctx.__enter__ = lambda s: ctx
        ctx.__exit__ = lambda *a: None
        ctx.status = 200

        def jawab(request, timeout=None):
            if "Authorization" in (request.headers or {}):
                return ctx
            raise urllib.error.HTTPError(request.full_url, 401, "no", {}, None)

        with mock.patch.object(urllib.request, "urlopen", side_effect=jawab):
            self.assertTrue(
                endpoint._responds_as_opencode("http://127.0.0.1:49374", "pw")
            )

    def test_anonymous_401_with_wrong_password_is_rejected(self):
        """401 anonim tapi kredensial ditolak: bukan endpoint yang kita mau."""
        import urllib.error
        import urllib.request

        def jawab(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 401, "no", {}, None)

        with mock.patch.object(urllib.request, "urlopen", side_effect=jawab):
            self.assertFalse(
                endpoint._responds_as_opencode("http://127.0.0.1:49374", "salah")
            )


class DiscoverTest(unittest.TestCase):
    def test_explicit_base_url_wins_and_skips_network(self):
        with mock.patch.object(endpoint, "_listening_ports") as ls:
            hasil = endpoint.discover(base_url="http://contoh:1234")
        self.assertEqual(hasil["base_url"], "http://contoh:1234")
        self.assertTrue(hasil["verified"])
        ls.assert_not_called()

    def test_env_base_url_is_honoured(self):
        """$OPENCODE_BASE_URL dihormati.

        Sebelumnya hanya password yang dibaca dari environment; alamat
        harus lewat --base-url. Itu tidak konsisten, dan membuat pemanggil
        yang menyetel env tetap terhubung ke port default yang salah.
        """
        with mock.patch.dict(os.environ, {"OPENCODE_BASE_URL": "http://env:9999"}):
            hasil = endpoint.discover()
        self.assertEqual(hasil["base_url"], "http://env:9999")
        self.assertEqual(hasil["source"], "$OPENCODE_BASE_URL")

    def test_discovers_listening_port_and_correct_password(self):
        """Port dari ss dipakai, dengan password yang benar-benar diterima."""
        dengan_salah = mock.patch.object(
            endpoint, "password_candidates",
            return_value=[("BASI", "log"), ("BENAR", "service.json")],
        )
        def terima(url, pw, timeout=None):
            return pw == "BENAR"

        with mock.patch.object(endpoint, "_listening_ports",
                               return_value=[(49374, 1)]), \
             mock.patch.object(endpoint, "_responds_as_opencode",
                               side_effect=terima), \
             dengan_salah:
            hasil = endpoint.discover()
        self.assertEqual(hasil["base_url"], "http://127.0.0.1:49374")
        self.assertEqual(hasil["password"], "BENAR")
        self.assertTrue(hasil["verified"])

    def test_unverified_when_nothing_answers(self):
        """Kalau tidak ada yang cocok, tandai TIDAK terverifikasi.

        Lebih penting: jangan berhenti di kandidat pertama yang kebetulan
        menjawab. Kalau tidak ada kombinasi yang benar, pemanggil harus
        tahu supaya bisa memberi pesan jelas, bukan 401 misterius.
        """
        with mock.patch.object(endpoint, "_listening_ports",
                               return_value=[(49374, 1)]), \
             mock.patch.object(endpoint, "password_candidates",
                               return_value=[("X", "y")]), \
             mock.patch.object(endpoint, "_responds_as_opencode",
                               return_value=False):
            hasil = endpoint.discover()
        self.assertFalse(hasil["verified"])
        self.assertIn("TIDAK TERVERIFIKASI", hasil["source"])

    def test_verify_false_skips_probing(self):
        with mock.patch.object(endpoint, "_listening_ports",
                               return_value=[(5000, 1)]), \
             mock.patch.object(endpoint, "_responds_as_opencode") as probe:
            hasil = endpoint.discover(verify=False)
        self.assertEqual(hasil["base_url"], "http://127.0.0.1:5000")
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
