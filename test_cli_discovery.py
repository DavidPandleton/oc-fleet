"""Tes untuk penemuan endpoint di cli.py.

Regresi untuk dua default yang membuat CLI tidak bisa dipakai tanpa
konfigurasi manual:

1. `--base-url` default `http://127.0.0.1:4096`. Server `serve --service`
   memakai port acak, jadi default itu hampir selalu salah DAN menutupi
   penemuan otomatis - nilai default yang salah lebih buruk daripada
   tidak ada default.
2. `--password-file` default `/tmp/oc_serve.log`, file yang di mesin ini
   berisi password BASI (memberi 401) sementara service.json berisi yang
   benar.

Diverifikasi pada server nyata: sebelum perbaikan `cli.py show <id>`
selalu "api/connection failure"; sesudahnya keluar outcome dan teks.
"""

from __future__ import annotations

import io
import unittest
from unittest import mock

import cli
import endpoint


class BaseUrlDiscoveryTest(unittest.TestCase):
    def test_base_url_defaults_to_none_so_discovery_runs(self):
        """--base-url tidak boleh punya default yang menutupi penemuan."""
        args = cli.build_parser().parse_args(["sessions"])
        self.assertIsNone(args.base_url)

    def test_password_file_defaults_to_none_so_search_runs(self):
        args = cli.build_parser().parse_args(["sessions"])
        self.assertIsNone(args.password_file)

    def test_make_fleet_passes_none_through(self):
        """Fleet() dipanggil tanpa base_url, sehingga penemuan berjalan."""
        args = cli.build_parser().parse_args(["sessions"])
        with mock.patch.object(cli, "Fleet") as fleet_cls:
            cli.make_fleet(args)
        fleet_cls.assert_called_once_with(base_url=None, password_file=None)

    def test_explicit_base_url_is_still_honoured(self):
        """Yang eksplisit tetap menang - penemuan hanya cadangan."""
        args = cli.build_parser().parse_args(
            ["--base-url", "http://contoh:1234", "sessions"]
        )
        self.assertEqual(args.base_url, "http://contoh:1234")


class ShowOutputTest(unittest.TestCase):
    """`show` harus menampilkan jawaban agent.

    Sebelum perbaikan _assistant_text, `last_assistant_text` selalu None
    (server mengirim `type: "assistant"`, kode menuntut `type: "message"`),
    jadi baris ini selalu "(none)".
    """

    def test_show_prints_the_assistant_text(self):
        args = cli.build_parser().parse_args(["show", "ses_x"])
        fleet = mock.MagicMock()
        fleet.status.return_value = {
            "outcome": "succeeded",
            "last_assistant_text": "Ini jawabannya.",
        }
        with mock.patch.object(cli, "make_fleet", return_value=fleet), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = cli.cmd_show(args)
        keluaran = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("Ini jawabannya.", keluaran)
        self.assertNotIn("(none)", keluaran)


if __name__ == "__main__":
    unittest.main()
