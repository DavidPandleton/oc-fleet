"""Kumpulkan data mentah temuan tool `read` menggantung.

Skrip ini hanya MENGUMPULKAN dan MENCETAK, tidak memutuskan. Ambang
keputusan (misalnya "19% itu buruk") tidak ada di sini supaya datanya
bisa dipakai ulang untuk pertanyaan lain.

Pelajaran dari skrip uji model sebelumnya: kegagalan yang hanya dicetak
sebagai teks membuat exit code tetap 0, sehingga otomatisasi menyimpulkan
"sukses". Di sini exit code dibuat bermakna:

    0 = data terkumpul, setidaknya ada satu tool call terlihat
    1 = tidak ada tool call sama sekali (server mati / kredensial salah)
    2 = kesalahan saat membaca

Dengan begitu, pemanggil otomatis tidak bisa salah menyimpulkan.
"""

from __future__ import annotations

import json
import sys
from collections import Counter

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fleet import Fleet  # noqa: E402

BATAS_SESI = 40


def kumpulkan(batas=BATAS_SESI):
    """Hitung tool call per status, per nama tool, dari sesi terbaru."""
    fleet = Fleet()
    nyangkut = Counter()
    selesai = Counter()
    status_lain = Counter()
    sesi_dibaca = 0
    sesi_gagal_dibaca = 0

    for sesi in fleet.list_sessions(batas):
        sid = sesi.get("id") if isinstance(sesi, dict) else None
        if not sid:
            continue
        try:
            mentah = fleet._request("GET", f"/api/session/{sid}/message")
        except Exception:
            sesi_gagal_dibaca += 1
            continue
        sesi_dibaca += 1
        pesan = mentah.get("data", mentah) if isinstance(mentah, dict) else mentah
        if not isinstance(pesan, list):
            continue
        for m in pesan:
            if not isinstance(m, dict) or m.get("type") != "assistant":
                continue
            isi = m.get("content")
            if not isinstance(isi, list):
                continue
            for c in isi:
                if not isinstance(c, dict) or c.get("type") != "tool":
                    continue
                nama = c.get("name") or "?"
                state = c.get("state")
                status = state.get("status") if isinstance(state, dict) else None
                if status == "running":
                    nyangkut[nama] += 1
                elif status == "completed":
                    selesai[nama] += 1
                else:
                    status_lain[(nama, status)] += 1

    return {
        "sesi_dibaca": sesi_dibaca,
        "sesi_gagal_dibaca": sesi_gagal_dibaca,
        "nyangkut": dict(nyangkut),
        "selesai": dict(selesai),
        "status_lain": {"%s/%s" % k: v for k, v in status_lain.items()},
    }


def main():
    try:
        data = kumpulkan()
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, indent=1))
        return 2

    semua_tool = sorted(set(data["nyangkut"]) | set(data["selesai"]))
    baris = []
    for nama in semua_tool:
        n = data["nyangkut"].get(nama, 0)
        s = data["selesai"].get(nama, 0)
        total = n + s
        baris.append({
            "tool": nama,
            "nyangkut": n,
            "selesai": s,
            "total": total,
            "persen_nyangkut": round(100.0 * n / total, 1) if total else None,
        })
    baris.sort(key=lambda b: (-(b["nyangkut"] or 0), b["tool"]))

    keluaran = dict(data)
    keluaran["per_tool"] = baris
    print(json.dumps(keluaran, indent=1, sort_keys=True))

    # Exit code bermakna. Tanpa ini, kegagalan hanya jadi teks dan
    # otomatisasi menyimpulkan sukses.
    return 0 if semua_tool else 1


if __name__ == "__main__":
    sys.exit(main())
