"""Load documents exported from the tracker artifact into the bot's database.

Usage: python -m scripts.seed seed/ [--force]
Each file seed/<collection>/<id>.json becomes the document <collection>/<id>.
Existing documents are kept unless --force is given.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from coach.store import Store  # noqa: E402


def main() -> None:
    root = sys.argv[1] if len(sys.argv) > 1 else "seed"
    force = "--force" in sys.argv
    store = Store(os.environ.get("DB_PATH", "coach.db"))
    n = 0
    for dirpath, _, files in os.walk(root):
        for name in sorted(files):
            if not name.endswith(".json"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            path = rel[:-5].replace(os.sep, "/")
            if store.get(path) is not None and not force:
                print(f"skip {path} (exists)")
                continue
            with open(os.path.join(dirpath, name), encoding="utf-8") as f:
                store.set(path, json.load(f))
            print(f"set {path}")
            n += 1
    print(f"{n} documents written")


if __name__ == "__main__":
    main()
