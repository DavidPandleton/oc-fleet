"""Tests for oc-fleet config file (no server needed)."""

import json
import unittest

from config import DEFAULTS, default_config, load


class ConfigTest(unittest.TestCase):
    def test_default_config_punya_peran(self):
        cfg = default_config()
        self.assertTrue(cfg["models"]["scaffold"])
        self.assertTrue(cfg["models"]["logic"])
        self.assertTrue(cfg["models"]["docs"])
        self.assertTrue(cfg["models"]["review"])

    def test_default_tidak_sama_dengan_referensi(self):
        a = default_config()
        a["models"]["logic"] = "diubah"
        self.assertNotEqual(default_config()["models"]["logic"], "diubah")

    def test_load_tanpa_file_kembali_default(self):
        cfg = load("/tmp/oc-fleet-config-tidak-ada.json")
        self.assertEqual(cfg, default_config())

    def test_load_merge_parsial(self):
        p = "/tmp/oc-fleet-config-test.json"
        with open(p, "w") as fh:
            json.dump({"models": {"logic": "cutad/model-x"}, "max_parallel": 5}, fh)
        cfg = load(p)
        self.assertEqual(cfg["models"]["logic"], "cutad/model-x")
        self.assertEqual(cfg["models"]["scaffold"], DEFAULTS["models"]["scaffold"])
        self.assertEqual(cfg["max_parallel"], 5)

    def test_load_file_rusak_kembali_default(self):
        p = "/tmp/oc-fleet-config-rusak.json"
        with open(p, "w") as fh:
            fh.write("{bukan json")
        self.assertEqual(load(p), default_config())


if __name__ == "__main__":
    unittest.main()
