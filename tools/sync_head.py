#!/usr/bin/env python3
"""Rewrite the SEO head block (title, description, Open Graph, JSON-LD) and the
no-JavaScript fallback in index.html from data/site.json.

Run after editing data/site.json:  python3 tools/sync_head.py
"""
import sys

from sitegen import INDEX, load, render_index


def main():
    old = INDEX.read_text(encoding="utf-8")
    new = render_index(old, load())
    if new == old:
        print("index.html already in sync with data/site.json")
    else:
        INDEX.write_text(new, encoding="utf-8")
        print("index.html updated from data/site.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
