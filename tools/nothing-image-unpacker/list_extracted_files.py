#!/usr/bin/env python3
"""Print and save a complete inventory of extracted firmware files."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Firmware inspection output directory")
    parser.add_argument("--output", type=Path, help="Optional text inventory output path")
    args = parser.parse_args()

    root = args.root.resolve()
    unpacked = root / "unpacked"
    if not unpacked.is_dir():
        raise SystemExit(f"Unpacked directory not found: {unpacked}")

    entries: list[tuple[str, str, int | None]] = []
    symlinks: list[tuple[str, str]] = []
    special: list[tuple[str, str, int | None]] = []

    for path in sorted(unpacked.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_dir() and not path.is_symlink():
            entries.append(("D", rel + "/", None))
        elif path.is_file() and not path.is_symlink():
            entries.append(("F", rel, path.stat().st_size))

    # CPIO extractor intentionally doesn't create symlinks/special nodes.
    # Read manifests so those entries still appear in this inventory.
    for manifest_path in sorted(unpacked.rglob("_extraction_manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        base = manifest_path.parent
        for item in manifest.get("symlinks_not_created", []):
            symlinks.append((base.relative_to(root).as_posix() + "/" + item.get("path", ""),
                             item.get("target", "")))
        for item in manifest.get("special_files_not_created", []):
            special.append((base.relative_to(root).as_posix() + "/" + item.get("path", ""),
                            item.get("mode", "unknown"), item.get("size")))

    entries.sort(key=lambda item: item[1].lower())
    lines = [
        "========== EXTRACTED FILE INVENTORY ==========",
        f"Root: {root}",
        f"Regular files: {sum(1 for kind, _, _ in entries if kind == 'F')}",
        f"Directories: {sum(1 for kind, _, _ in entries if kind == 'D')}",
        f"Symlinks (not created; from manifests): {len(symlinks)}",
        f"Special entries (not created; from manifests): {len(special)}",
        "",
    ]
    for kind, path, size in entries:
        lines.append(f"[{kind}] {path}" + (f" ({size:,} bytes)" if size is not None else ""))
    if symlinks:
        lines.extend(["", "---------- SYMLINKS (NOT CREATED) ----------"])
        for path, target in sorted(symlinks):
            lines.append(f"[L] {path} -> {target}")
    if special:
        lines.extend(["", "---------- SPECIAL ENTRIES (NOT CREATED) ----------"])
        for path, mode, size in sorted(special):
            lines.append(f"[S] {path} mode={mode}" + (f" size={size}" if size is not None else ""))
    lines.extend(["", "============================================="])
    report = "\n".join(lines) + "\n"

    print(report, end="")
    if args.output:
        output = args.output
        if not output.is_absolute():
            output = root / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
        print(f"Inventory saved: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
