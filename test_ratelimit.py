"""Tes untuk ratelimit.py.

Konstanta di sini berasal dari log server nyata, bukan karangan:

    RateLimit: Concurrency limit exceeded.
    You already have 15 active request(s); your plan allows 15.

    (batas 25 request per menit)

Terukur sebelum pembatas dipasang: 81 respons HTTP 429 dan 17 kegagalan
berpesan "Concurrency limit exceeded" ketika enam task berjalan
bersamaan dengan poll_interval 3 detik.
"""

from __future__ import annotations

import threading
import time
import unittest

from ratelimit import ConcurrencyLimiter, RateLimiter, RetryAfter


class RateLimiterTest(unittest.TestCase):
    def test_rejects_non_positive_rate(self):
        for buruk in (0, -1, -0.5):
            with self.subTest(rate=buruk):
                with self.assertRaises(ValueError):
                    RateLimiter(buruk)

    def test_first_burst_is_immediate(self):
        """Burst awal tidak menunggu: kapasitas tersedia sejak awal."""
        limiter = RateLimiter(rate=10, capacity=5)
        mulai = time.monotonic()
        for _ in range(5):
            limiter.acquire()
        self.assertLess(time.monotonic() - mulai, 0.05)

    def test_waits_once_capacity_is_spent(self):
        """Setelah burst habis, akuisisi berikutnya menunggu."""
        limiter = RateLimiter(rate=20, capacity=1)
        limiter.acquire()  # habiskan burst
        mulai = time.monotonic()
        limiter.acquire()
        jeda = time.monotonic() - mulai
        # rate=20/detik -> satu token sekitar 0.05s. Beri kelonggaran.
        self.assertGreaterEqual(jeda, 0.02)
        self.assertLess(jeda, 0.5)

    def test_acquire_more_than_capacity_raises(self):
        """Meminta lebih banyak dari kapasitas adalah bug pemanggil."""
        limiter = RateLimiter(rate=1, capacity=2)
        with self.assertRaises(ValueError):
            limiter.acquire(3)

    def test_thread_safe_under_contention(self):
        """Banyak thread tidak boleh melebihi laju secara kasar."""
        limiter = RateLimiter(rate=50, capacity=1)
        terpakai = []

        def pekerja():
            limiter.acquire()
            terpakai.append(time.monotonic())

        mulai = time.monotonic()
        threads = [threading.Thread(target=pekerja) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 5 token pada 50/detik: sekitar 0.08s minimum setelah burst.
        self.assertGreaterEqual(time.monotonic() - mulai, 0.05)
        self.assertEqual(len(terpakai), 5)


class ConcurrencyLimiterTest(unittest.TestCase):
    def test_rejects_non_positive_limit(self):
        with self.assertRaises(ValueError):
            ConcurrencyLimiter(0)

    def test_blocks_beyond_limit(self):
        """Pemanggil ke-limit+1 menunggu sampai ada yang melepas."""
        limiter = ConcurrencyLimiter(2)
        limiter.acquire()
        limiter.acquire()
        selesai = threading.Event()

        def pekerja():
            limiter.acquire()
            selesai.set()
            limiter.release()

        t = threading.Thread(target=pekerja)
        t.start()
        # Belum boleh lolos: dua slot masih dipegang.
        self.assertFalse(selesai.wait(0.15))
        limiter.release()
        self.assertTrue(selesai.wait(1.0))
        t.join()
        limiter.release()

    def test_context_manager_releases(self):
        limiter = ConcurrencyLimiter(1)
        with limiter:
            self.assertEqual(limiter.in_flight, 1)
        self.assertEqual(limiter.in_flight, 0)
        # Slot benar-benar dilepas: bisa dipakai lagi.
        with limiter:
            pass

    def test_releases_on_exception(self):
        """Slot dilepas walau blok melempar - kalau tidak, bocor permanen."""
        limiter = ConcurrencyLimiter(1)
        with self.assertRaises(RuntimeError):
            with limiter:
                raise RuntimeError("boom")
        self.assertEqual(limiter.in_flight, 0)

    def test_peak_is_tracked(self):
        limiter = ConcurrencyLimiter(4)
        with limiter:
            with limiter:
                self.assertEqual(limiter.peak, 2)
        self.assertEqual(limiter.in_flight, 0)


class RetryAfterTest(unittest.TestCase):
    def test_uses_retry_after_header_when_present(self):
        """Header Retry-After dari server lebih diutamakan daripada tebakan."""
        self.assertEqual(RetryAfter(base=1).delay(1, retry_after="7"), 7.0)

    def test_clamps_retry_after_to_maximum(self):
        self.assertEqual(RetryAfter(base=1, maximum=10).delay(1, retry_after="999"), 10.0)

    def test_ignores_unparseable_retry_after(self):
        """Header rusak tidak boleh melempar; mundur ke eksponensial."""
        d = RetryAfter(base=5, jitter=0).delay(1, retry_after="nanti")
        self.assertEqual(d, 5.0)

    def test_grows_exponentially(self):
        r = RetryAfter(base=2, factor=2, jitter=0)
        self.assertEqual(r.delay(1), 2.0)
        self.assertEqual(r.delay(2), 4.0)
        self.assertEqual(r.delay(3), 8.0)

    def test_stops_at_maximum(self):
        r = RetryAfter(base=10, factor=10, maximum=30, jitter=0)
        self.assertEqual(r.delay(5), 30.0)

    def test_jitter_spreads_retries(self):
        """Tanpa jitter, semua task yang kena batas mencoba lagi serentak.

        Itu membuat mereka langsung menabrak batas lagi - pola yang justru
        memperparah masalah.
        """
        r = RetryAfter(base=10, jitter=0.3)
        nilai = {round(r.delay(1), 4) for _ in range(30)}
        self.assertGreater(len(nilai), 1, "jitter tidak menghasilkan variasi")


if __name__ == "__main__":
    unittest.main()
