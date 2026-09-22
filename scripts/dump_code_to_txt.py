#!/usr/bin/env python3
"""Copy project source files into one text dump, labeled by relative path.

Usage:
  python scripts/dump_code_to_txt.py
  python scripts/dump_code_to_txt.py --out dumps/code.txt
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SKIP_DIRS = {
    ".git",
    ".next",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "dist",
    "data",
    ".cursor",
    "mcps",
}

SKIP_FILES = {".env", ".env.local"}

CODE_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".css",
    ".scss",
    ".html",
    ".json",
    ".toml",
    ".yml",
    ".yaml",
    ".md",
    ".sql",
    ".sh",
    ".csv",
}

EXTRA_NAMES = {
    "Dockerfile",
    "Makefile",
    ".gitignore",
    ".env.example",
}

MAX_FILE_BYTES = 1_000_000


def should_skip_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.startswith(".")


def is_code_file(path: Path) -> bool:
    if path.name in SKIP_FILES:
        return False
    if path.name in EXTRA_NAMES:
        return True
    return path.suffix.lower() in CODE_SUFFIXES


def iter_code_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not should_skip_dir(d))
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if is_code_file(path):
                files.append(path)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="Dump source files into one .txt with filenames.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Project root (default: repo root)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output .txt path (default: <root>/code_dump.txt)",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    out = (args.out or (root / "code_dump.txt")).resolve()

    files = iter_code_files(root)
    # Do not dump the dump file into itself if it already exists with a code suffix.
    files = [p for p in files if p.resolve() != out]

    out.parent.mkdir(parents=True, exist_ok=True)
    skipped: list[str] = []
    written = 0

    with out.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"# Code dump\n# root: {root}\n# files: {len(files)}\n\n")
        for path in files:
            rel = path.relative_to(root).as_posix()
            try:
                size = path.stat().st_size
            except OSError as exc:
                skipped.append(f"{rel} ({exc})")
                continue
            if size > MAX_FILE_BYTES:
                skipped.append(f"{rel} (too large: {size} bytes)")
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                skipped.append(f"{rel} (not utf-8)")
                continue
            fh.write("=" * 80 + "\n")
            fh.write(f"FILE: {rel}\n")
            fh.write("=" * 80 + "\n")
            fh.write(text)
            if not text.endswith("\n"):
                fh.write("\n")
            fh.write("\n")
            written += 1

        if skipped:
            fh.write("=" * 80 + "\n")
            fh.write("SKIPPED\n")
            fh.write("=" * 80 + "\n")
            for item in skipped:
                fh.write(f"- {item}\n")

    print(f"Wrote {written} files to {out}")
    if skipped:
        print(f"Skipped {len(skipped)} files", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
