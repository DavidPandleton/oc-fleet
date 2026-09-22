"""Tes untuk keluaran `--json` di cli.py.

Keluaran ini ditujukan untuk pemanggil program (agen, skrip). Tabel
berkolom rapuh: lebarnya berubah mengikuti judul, dan judul bisa berisi
spasi serta tanda hubung. JSON punya bentuk yang stabil.

Alasan praktisnya: tanpa ini, satu-satunya cara membaca hasil adalah
mengurai teks berformat, dan itu rusak setiap kali tampilan diubah.
"""

from __future__ import annotations

import io
import json
import unittest
from unittest import mock

import cli


def _jalankan(func, args, fleet):
    with mock.patch.object(cli, "make_fleet", return_value=fleet), \
         mock.patch("sys.stdout", new_callable=io.StringIO) as out:
        code = func(args)
    return code, out.getvalue()


class ShowJsonTest(unittest.TestCase):
    def _fleet(self, state):
        fleet = mock.MagicMock()
        fleet.status.return_value = state
        return fleet

    def _args(self):
        return cli.build_parser().parse_args(["show", "ses_x", "--json"])

    def test_json_contains_outcome_and_text(self):
        fleet = self._fleet({
            "outcome": "succeeded",
            "last_assistant_text": "jawabannya",
            "tool_running": 0,
            "stuck_seconds": None,
            "stuck": False,
        })
        code, keluaran = _jalankan(cli.cmd_show, self._args(), fleet)
        data = json.loads(keluaran)
        self.assertEqual(code, 0)
        self.assertEqual(data["outcome"], "succeeded")
        self.assertEqual(data["last_assistant_text"], "jawabannya")
        self.assertEqual(data["session_id"], "ses_x")

    def test_json_includes_liveness_keys(self):
        """Pemanggil program perlu tahu sesi macet, bukan hanya outcome."""
        fleet = self._fleet({
            "outcome": None,
            "last_assistant_text": None,
            "tool_running": 3,
            "stuck_seconds": 1244.0,
            "stuck": True,
        })
        code, keluaran = _jalankan(cli.cmd_show, self._args(), fleet)
        data = json.loads(keluaran)
        self.assertTrue(data["stuck"])
        self.assertEqual(data["tool_running"], 3)
        self.assertAlmostEqual(data["stuck_seconds"], 1244.0, places=1)
        # Sesi macet adalah kegagalan: kalau keluar 0, pemanggil akan
        # menyimpulkan "masih jalan, tunggu saja" - kesalahan yang sama
        # yang membuat orchestrator menunggu 25 menit.
        self.assertEqual(code, 1)

    def test_failed_outcome_exits_one(self):
        fleet = self._fleet({"outcome": "failed", "last_assistant_text": None})
        code, _ = _jalankan(cli.cmd_show, self._args(), fleet)
        self.assertEqual(code, 1)

    def test_json_is_ascii_safe(self):
        """Keluaran tetap ASCII walau agen menulis karakter eksotis."""
        fleet = self._fleet({
            "outcome": "succeeded",
            "last_assistant_text": "bagus\u2014tapi perlu fix \u4e2d\u6587",
        })
        _, keluaran = _jalankan(cli.cmd_show, self._args(), fleet)
        keluaran.encode("ascii")  # melempar kalau tidak ASCII
        data = json.loads(keluaran)
        self.assertIn("bagus", data["last_assistant_text"])


class SessionsJsonTest(unittest.TestCase):
    def test_sessions_json_is_a_list_of_dicts(self):
        fleet = mock.MagicMock()
        fleet.list_sessions.return_value = [
            {"id": "s-1", "title": "satu", "time": {"created": 1_790_000_000_000}},
            {"id": "s-2", "title": "dua", "time": {"updated": 1_790_000_001_000}},
        ]
        args = cli.build_parser().parse_args(["sessions", "5", "--json"])
        code, keluaran = _jalankan(cli.cmd_sessions, args, fleet)
        data = json.loads(keluaran)
        self.assertEqual(code, 0)
        self.assertIsInstance(data, list)
        self.assertEqual([d["id"] for d in data], ["s-1", "s-2"])
        self.assertEqual(data[0]["title"], "satu")

    def test_empty_sessions_json_is_an_empty_list(self):
        """Kosong harus jadi [] (list), bukan "(no sessions)"."""
        fleet = mock.MagicMock()
        fleet.list_sessions.return_value = []
        args = cli.build_parser().parse_args(["sessions", "--json"])
        code, keluaran = _jalankan(cli.cmd_sessions, args, fleet)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(keluaran), [])

    def test_malformed_session_entry_does_not_crash(self):
        """Entri rusak tidak boleh menjatuhkan seluruh keluaran."""
        fleet = mock.MagicMock()
        fleet.list_sessions.return_value = [None, "bukan dict", {"id": "s-1"}]
        args = cli.build_parser().parse_args(["sessions", "--json"])
        code, keluaran = _jalankan(cli.cmd_sessions, args, fleet)
        self.assertEqual(code, 0)
        data = json.loads(keluaran)
        self.assertEqual(len(data), 3)


if __name__ == "__main__":
    unittest.main()
