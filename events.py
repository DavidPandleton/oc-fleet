"""Append-only local JSONL event sink."""

import json
import os


class JsonlEventSink:
    def __init__(self, path):
        self.path = path

    def emit(self, event):
        if not isinstance(event, dict):
            raise TypeError("event must be a dict")
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        line = json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
