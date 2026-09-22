"""Pembatas laju dan konkurensi untuk panggilan ke server OpenCode.

Kenapa ini ada
--------------
Provider `cutad` punya dua batas, keduanya terbaca di log server nyata:

    RateLimit: Concurrency limit exceeded.
    You already have 15 active request(s); your plan allows 15.

plus batas 25 request per menit.

oc-fleet melanggarnya tanpa sadar. `poll_interval` default 3 detik per
task: dengan 6 task paralel itu sekitar 120 request per menit, hampir 5x
batas. Terukur di mesin ini: 81 respons HTTP 429 di log server, dan 17
kegagalan berpesan "Concurrency limit exceeded".

Yang membuat ini lebih buruk daripada sekadar 429: tool call agent juga
memakai slot konkurensi. Jadi mempoll lebih sering BUKAN hanya kena
batas - ia mengambil slot yang dibutuhkan agent untuk bekerja. Polling
berlebihan memperlambat pekerjaan yang sedang dipolling.

Gejalanya menyesatkan: sesi tampak "macet" atau kehabisan waktu, padahal
hanya kena batas. Itu yang terjadi saat enam model diuji sekaligus di
mesin ini - lima dilaporkan "timeout"/"macet", padahal model dan
servernya sehat.

Rancangan
---------
`RateLimiter` adalah token bucket: `capacity` token, diisi ulang
`rate` per detik, dan tiap `acquire()` mengambil satu. Ini membatasi LAJU.

`ConcurrencyLimiter` membatasi jumlah panggilan yang berjalan BERSAMAAN.
Ini yang sebenarnya dibutuhkan provider ini, karena batasnya konkurensi,
bukan laju.

Keduanya memakai `time.monotonic()` supaya tidak terpengaruh perubahan
jam sistem.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    """Token bucket: paling banyak `rate` akuisisi per detik, burst `capacity`.

    Thread-safe: kunci dipegang hanya saat menghitung token, tidak pernah
    saat tidur, supaya satu pemanggil yang menunggu tidak memblokir yang lain.
    """

    def __init__(self, rate, capacity=None):
        if rate <= 0:
            raise ValueError("rate must be positive, got %r" % (rate,))
        self.rate = float(rate)
        self.capacity = float(capacity if capacity is not None else max(1.0, rate))
        if self.capacity <= 0:
            raise ValueError("capacity must be positive, got %r" % (capacity,))
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self):
        sekarang = time.monotonic()
        lalu = sekarang - self._updated
        if lalu > 0:
            self._tokens = min(self.capacity, self._tokens + lalu * self.rate)
            self._updated = sekarang

    def acquire(self, tokens=1.0):
        """Ambil token, tidur kalau perlu. Mengembalikan detik menunggu."""
        tokens = float(tokens)
        if tokens > self.capacity:
            raise ValueError(
                "cannot acquire %r tokens, capacity is %r" % (tokens, self.capacity)
            )
        ditunggu = 0.0
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return ditunggu
                kurang = tokens - self._tokens
                tidur = kurang / self.rate
            # Tidur di luar kunci: pemanggil lain tetap bisa lanjut.
            time.sleep(tidur)
            ditunggu += tidur


class ConcurrencyLimiter:
    """Batasi jumlah panggilan yang berjalan bersamaan.

    Dipakai untuk batas konkurensi provider ini (15). Berbeda dari
    RateLimiter yang membatasi laju, ini membatasi jumlah slot yang
    dipegang pada satu waktu.
    """

    def __init__(self, limit):
        if limit <= 0:
            raise ValueError("limit must be positive, got %r" % (limit,))
        self.limit = int(limit)
        self._sem = threading.Semaphore(self.limit)
        self.in_flight = 0
        self.peak = 0
        self._lock = threading.Lock()

    def acquire(self):
        self._sem.acquire()
        with self._lock:
            self.in_flight += 1
            if self.in_flight > self.peak:
                self.peak = self.in_flight

    def release(self):
        with self._lock:
            self.in_flight -= 1
        self._sem.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


class RetryAfter:
    """Jeda eksponensial untuk percobaan ulang setelah 429.

    Menghormati header `Retry-After` kalau ada, dan mundur secara
    eksponensial dengan jitter kalau tidak. Jitter penting: tanpa itu,
    semua task yang kena batas pada saat yang sama akan mencoba lagi pada
    saat yang sama pula, dan langsung menabrak batas lagi.
    """

    def __init__(self, base=5.0, factor=2.0, maximum=120.0, jitter=0.3):
        self.base = float(base)
        self.factor = float(factor)
        self.maximum = float(maximum)
        self.jitter = float(jitter)

    def delay(self, attempt, retry_after=None):
        """Detik untuk menunggu sebelum percobaan ke-`attempt` (mulai 1)."""
        if retry_after is not None:
            try:
                diminta = float(retry_after)
            except (TypeError, ValueError):
                diminta = None
            if diminta is not None and diminta >= 0:
                return min(diminta, self.maximum)
        mentah = self.base * (self.factor ** max(0, int(attempt) - 1))
        mentah = min(mentah, self.maximum)
        if self.jitter:
            # Jitter deterministik-per-pemanggil tidak perlu; yang penting
            # nilainya berbeda antar pemanggil.
            import random

            mentah *= 1.0 + random.uniform(-self.jitter, self.jitter)
        return max(0.0, mentah)
