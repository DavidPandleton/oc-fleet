"""Config oc-fleet: ~/.config/oc-fleet/config.json.

Menyimpan model per peran (scaffold, logic, docs, review), fallback,
max_parallel, dan worktree root. Nilai user di-merge di atas DEFAULTS,
jadi config parsial tetap valid. File hilang atau rusak diam-diam
kembali ke default supaya tool tetap jalan.
"""

import json
import os

DEFAULTS = {
    "models": {
        "scaffold": "cutad/qwen3-8-flash-next",
        "logic": "cutad/deepseek-v4-pro",
        "docs": "cutad/glm-5.3-flash",
        "review": "cutad/qwen3-8-flash-next",
    },
    "fallbacks": {
        "logic": ["cutad/qwen3-8-flash-next"],
    },
    "max_parallel": 3,
    "worktree_root": "/tmp/oc-fleet-worktrees",
}


def path():
    """Lokasi config file."""
    return os.path.expanduser("~/.config/oc-fleet/config.json")


def default_config():
    """Salinan baru DEFAULTS (bukan referensi, supaya aman diubah)."""
    return json.loads(json.dumps(DEFAULTS))


def load(p=None):
    """Baca config, merge di atas default. Hilang/rusak = default."""
    cfg = default_config()
    try:
        with open(p or path()) as fh:
            user = json.load(fh)
    except (OSError, ValueError):
        return cfg
    if not isinstance(user, dict):
        return cfg
    for key, value in user.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    return cfg


def save(cfg, p=None):
    """Tulis config ke file, buat direktori bila perlu."""
    dest = p or path()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    return dest
