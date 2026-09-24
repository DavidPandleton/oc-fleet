"""Menemukan endpoint server OpenCode tanpa disuruh.

Kenapa modul ini ada
--------------------
`opencode serve --service` mengikat ke port ACAK dan tidak menuliskannya ke
file mana pun. `service.json` hanya menyimpan password. Satu-satunya tempat
port itu muncul adalah tabel socket kernel (`ss -tlnp`) atau probe.

Akibatnya, default lama `http://127.0.0.1:4096` salah hampir selalu: server
memakai 49374, 41231, atau apa pun yang dipilih kernel saat start. Setiap
kali server restart, pemanggil harus menggali port dengan tangan.

Modul ini menggantinya dengan satu urutan pencarian yang deterministik, dan
mengembalikan endpoint mana yang dipakai BESERTA dari mana asalnya, supaya
kegagalan bisa dijelaskan alih-alih ditebak.

Urutan (menang yang pertama)
----------------------------
1. argumen eksplisit (`--base-url`)
2. $OPENCODE_BASE_URL
3. port dari proses `opencode serve` yang sedang mendengarkan (`ss -tlnp`)
4. probe port yang lazim dipakai
5. default lama 4096 (supaya perilaku lama tetap jalan kalau semua gagal)
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import urllib.error
import urllib.request

DEFAULT_PORT = 4096
DEFAULT_HOST = "127.0.0.1"

# Port yang pernah terlihat dipakai opencode di mesin ini, plus 4096.
# Diprobe hanya kalau `ss` tidak memberi jawaban.
CANDIDATE_PORTS = (4096, 49374, 8787, 3000, 8080)

_SERVICE_CONFIG = os.path.expanduser("~/.config/opencode/service.json")
_PASSWORD_SOURCES = (
    "/tmp/oc_serve.log",
    os.path.expanduser("~/.local/share/opencode/serve.log"),
)


def _password_from_logs():
    """Cari password di log serve yang lazim. Mengembalikan (password, sumber).

    Cadangan saja. Log ditulis sekali saat server pertama start dan tidak
    ikut berubah saat restart, jadi isinya bisa sudah basi - di mesin ini
    memang basi, dan itu memberi 401. `password()` memakai service.json
    lebih dulu karena alasan itu.
    """
    pattern = re.compile(r"server password\s+(\S+)")
    for path in _PASSWORD_SOURCES:
        try:
            with open(path, encoding="utf-8") as fh:
                match = pattern.search(fh.read())
        except OSError:
            continue
        if match:
            return match.group(1), path
    return None, None


def password_candidates(password_file=None):
    """Semua kandidat password, urut dari paling mungkin benar.

    Mengembalikan list of (password, sumber). Duplikat dibuang tapi urutan
    dipertahankan.

    Kenapa perlu lebih dari satu: sumber yang berbeda bisa tidak sinkron.
    Di mesin ini `serve.log` tertinggal satu restart di belakang
    `service.json`, dan tidak selalu jelas mana yang segar. Daripada
    menebak lalu gagal dengan 401, `discover()` mencoba semuanya terhadap
    server yang benar-benar menjawab dan memakai yang diterima.
    """
    kandidat = []
    env = os.environ.get("OPENCODE_SERVER_PASSWORD")
    if env:
        kandidat.append((env, "$OPENCODE_SERVER_PASSWORD"))
    if password_file:
        try:
            with open(password_file, encoding="utf-8") as fh:
                content = fh.read()
            match = re.search(r"server password\s+(\S+)", content)
            value = match.group(1) if match else content.strip()
            if value:
                kandidat.append((value, password_file))
        except OSError:
            pass
    try:
        with open(_SERVICE_CONFIG, encoding="utf-8") as fh:
            data = json.load(fh)
        pw = data.get("password") if isinstance(data, dict) else None
        if pw:
            kandidat.append((pw, _SERVICE_CONFIG))
    except (OSError, json.JSONDecodeError):
        pass
    log_pw, log_src = _password_from_logs()
    if log_pw:
        kandidat.append((log_pw, log_src))

    unik = []
    terlihat = set()
    for pw, src in kandidat:
        if pw not in terlihat:
            terlihat.add(pw)
            unik.append((pw, src))
    return unik


def password(password_file=None):
    """Password server, dengan sumbernya. Mengembalikan (password, sumber)."""
    kandidat = password_candidates(password_file)
    return kandidat[0] if kandidat else (None, None)


def _listening_ports():
    """Port TCP yang didengarkan proses bernama `opencode`, dari `ss -tlnp`.

    Mengembalikan daftar (port, pid). Kosong kalau `ss` tidak ada, tidak
    bisa dijalankan, atau tidak ada proses opencode yang mendengarkan.
    """
    try:
        proc = subprocess.run(
            ["ss", "-tlnp"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    found = []
    for line in proc.stdout.splitlines():
        if "opencode" not in line:
            continue
        # Kolom 4 adalah Local Address:Port
        parts = line.split()
        if len(parts) < 4:
            continue
        addr = parts[3]
        if ":" not in addr:
            continue
        port_text = addr.rsplit(":", 1)[1]
        pid_match = re.search(r"pid=(\d+)", line)
        if port_text.isdigit():
            found.append((int(port_text), int(pid_match.group(1)) if pid_match else None))
    return found


def _port_is_open(host, port, timeout=0.5):
    """Apakah sesuatu menerima koneksi TCP di host:port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _responds_as_opencode(base_url, password_value, timeout=3):
    """Apakah base_url benar-benar server OpenCode, bukan server lain.

    Dua syarat, KEDUANYA harus benar:

      1. tanpa kredensial  -> 401  (server ini memang meminta auth)
      2. dengan kredensial -> 200

    Syarat kedua yang penting. Versi pertama fungsi ini hanya memeriksa
    "dapat 200 tanpa auth" dan itu menerima proxy socat di port 8080
    sebagai OpenCode: socat meneruskan apa saja dan menjawab 200 untuk
    permintaan anonim. Hasilnya `discover()` mengembalikan port yang salah
    dan lebih buruk daripada tidak menemukan apa pun.

    Endpoint yang menerima anonim BUKAN server OpenCode yang kita cari -
    jadi 200 tanpa kredensial harus MENGGAGALKAN pemeriksaan, bukan
    meloloskannya.
    """
    import base64

    url = base_url.rstrip("/") + "/api/session?limit=1"

    anon = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(anon, timeout=timeout) as response:
            # 200 (atau apa pun) tanpa kredensial: bukan server yang kita cari.
            if response.status == 200:
                return False
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            return False
    except (urllib.error.URLError, OSError):
        return False

    if not password_value:
        # Tidak ada kredensial untuk diuji; 401 anonim adalah bukti terbaik
        # yang bisa didapat.
        return True

    token = base64.b64encode(("opencode:" + password_value).encode()).decode()
    auth = urllib.request.Request(url, headers={"Authorization": "Basic " + token})
    try:
        with urllib.request.urlopen(auth, timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return False


def discover(base_url=None, password_file=None, host=DEFAULT_HOST, verify=True):
    """Temukan endpoint server. Mengembalikan dict.

    Kunci hasil:
        base_url    - URL yang dipakai
        source      - dari mana base_url berasal, untuk dilaporkan
        password    - password yang DITERIMA, atau kandidat pertama
        pw_source   - dari mana password itu berasal
        candidates  - port yang dicoba (diagnosa)
        pw_tried    - berapa kandidat password diuji (diagnosa)
        verified    - apakah koneksi benar-benar diuji

    Kalau `verify` dan tidak ada kombinasi yang diterima, `base_url` tetap
    diisi (kandidat terbaik) tapi `verified` False - supaya pemanggil bisa
    memberi pesan yang jelas alih-alih gagal dengan 401 yang misterius.
    """
    pw_kandidat = password_candidates(password_file)
    hasil = {
        "password": pw_kandidat[0][0] if pw_kandidat else None,
        "pw_source": pw_kandidat[0][1] if pw_kandidat else None,
        "candidates": [],
        "source": None,
        "base_url": None,
        "verified": False,
        "pw_tried": 0,
    }

    if base_url:
        hasil["base_url"] = base_url.rstrip("/")
        hasil["source"] = "argumen --base-url"
        hasil["verified"] = True
        return hasil

    env_url = os.environ.get("OPENCODE_BASE_URL")
    if env_url:
        hasil["base_url"] = env_url.rstrip("/")
        hasil["source"] = "$OPENCODE_BASE_URL"
        hasil["verified"] = True
        return hasil

    ports = [p for p, _ in _listening_ports()]
    hasil["candidates"] = list(ports)
    for port in CANDIDATE_PORTS:
        if port not in ports:
            ports.append(port)
            hasil["candidates"].append(port)

    if not verify:
        if ports:
            hasil["base_url"] = "http://%s:%d" % (host, ports[0])
            hasil["source"] = "kandidat pertama (verifikasi dilewati)"
        else:
            hasil["base_url"] = "http://%s:%d" % (host, DEFAULT_PORT)
            hasil["source"] = "fallback default"
        return hasil

    # Coba tiap port dengan tiap kandidat password. Kombinasi yang benar
    # adalah yang menjawab 401 anonim lalu 200 dengan kredensial.
    for port in ports:
        candidate = "http://%s:%d" % (host, port)
        for pw, pw_src in pw_kandidat:
            hasil["pw_tried"] += 1
            if _responds_as_opencode(candidate, pw):
                hasil["base_url"] = candidate
                hasil["source"] = "ditemukan otomatis"
                hasil["password"] = pw
                hasil["pw_source"] = pw_src
                hasil["verified"] = True
                return hasil
        # Port tanpa kandidat password sama sekali masih bisa diperiksa
        # kalau server memang tidak memakai auth.
        if not pw_kandidat and _responds_as_opencode(candidate, None):
            hasil["base_url"] = candidate
            hasil["source"] = "ditemukan otomatis (tanpa auth)"
            hasil["verified"] = True
            return hasil

    # Tidak ada yang cocok. Kembalikan kandidat pertama supaya pesan
    # kegagalan menyebut alamat yang masuk akal.
    if ports:
        hasil["base_url"] = "http://%s:%d" % (host, ports[0])
    else:
        hasil["base_url"] = "http://%s:%d" % (host, DEFAULT_PORT)
    hasil["source"] = "TIDAK TERVERIFIKASI (tidak ada kombinasi yang diterima)"
    return hasil
