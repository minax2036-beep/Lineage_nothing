#!/usr/bin/env python3
"""Collect Recovery/ADB/USB-related files from a fully unpacked firmware tree."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

NAME_PATTERNS = [
    re.compile(r"(^|/)(init[^/]*\\.rc|ueventd[^/]*\\.rc|fstab[^/]*|adbd|minadbd|recovery[^/]*|.*usb.*\\.rc)$", re.I),
    re.compile(r"(^|/)(.*recovery.*|.*adbd.*|.*usb.*|.*fstab.*)$", re.I),
]
CONTENT_TERMS = (
    b"adbd", b"minadbd", b"sys.usb", b"configfs", b"usb.config",
    b"recovery", b"init.recovery", b"ro.debuggable", b"ueventd",
)
TEXT_SUFFIXES = {".rc", ".prop", ".conf", ".xml", ".fstab", ".txt", ".te", ".cil", ".mk", ".json"}
MAX_TEXT_SCAN = 4 * 1024 * 1024


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True, help="Full inspection output directory")
    ap.add_argument("--output", type=Path, required=True, help="Focused report directory")
    args = ap.parse_args()

    root = args.root.resolve()
    out = args.output.resolve()
    unpacked = root / "unpacked"
    if not unpacked.is_dir():
        raise SystemExit(f"unpacked directory not found: {unpacked}")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    copied = []
    scanned = 0
    for path in sorted(unpacked.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        name_match = any(p.search(rel) for p in NAME_PATTERNS)
        content_hits = []
        if path.suffix.lower() in TEXT_SUFFIXES and path.stat().st_size <= MAX_TEXT_SCAN:
            scanned += 1
            try:
                data = path.read_bytes()
                lower = data.lower()
                content_hits = [term.decode() for term in CONTENT_TERMS if term in lower]
            except OSError:
                pass
        if not name_match and not content_hits:
            continue

        destination = out / "files" / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        copied.append({
            "source": rel,
            "collected": destination.relative_to(out).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
            "matched_by_name": name_match,
            "content_matches": content_hits,
        })

    report = {
        "purpose": "Read-only collection of Recovery, init, ADB, USB, fstab and related files",
        "source_root": str(root),
        "text_files_scanned": scanned,
        "collected_file_count": len(copied),
        "files": copied,
        "important": [
            "Files are copied for inspection only; nothing is flashed or modified on a device.",
            "A filename/content match is a candidate, not proof that the file is active at runtime.",
            "Symlinks are recorded in the original ramdisk extraction manifests and are not followed.",
        ],
    }
    (out / "recovery-file-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    lines = [
        "# Recovery-related file collection",
        "",
        f"- Scanned text files: {scanned}",
        f"- Collected files: {len(copied)}",
        "",
    ]
    for item in copied:
        hits = ", ".join(item["content_matches"]) or "filename match"
        lines.append(f"- `{item['source']}` ({item['bytes']} bytes; {hits})")
    (out / "recovery-file-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Collected {len(copied)} candidate files into {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
