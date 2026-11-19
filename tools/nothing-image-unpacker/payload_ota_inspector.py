#!/usr/bin/env python3
"""Inspect a Full OTA and inventory recognizable files in recovery-related boot images."""
from __future__ import annotations
import argparse, hashlib, json, re, shutil, subprocess, sys, urllib.request
from pathlib import Path

MAX_DOWNLOAD = 8 * 1024**3
UA = "NOVA-Recovery-OTA-Inspector/0.4"

def sha256_file(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def download(url: str, dst: Path):
    if not url.lower().startswith("https://"):
        raise RuntimeError("OTA URL must use HTTPS.")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    h = hashlib.sha256()
    total = 0
    with urllib.request.urlopen(req, timeout=60) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_DOWNLOAD:
            raise RuntimeError("OTA exceeds 8 GiB download limit.")
        with dst.open("wb") as f:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD:
                    raise RuntimeError("OTA exceeds 8 GiB download limit.")
                h.update(chunk)
                f.write(chunk)
    return {"url": url, "bytes": total, "sha256": h.hexdigest(), "source": "download"}

def run(cmd):
    p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(p.stdout)
    if p.returncode:
        raise RuntimeError(f"Command failed (exit {p.returncode}): {' '.join(cmd)}")
    return p.stdout

def partition_names(listing: str):
    candidates = ["recovery", "vendor_boot", "init_boot", "boot"]
    found = []
    for name in candidates:
        if re.search(r"(?m)^\s*" + re.escape(name) + r"\s*(?:\(|$)", listing):
            found.append(name)
    if not found:
        found = [n for n in candidates if re.search(r"\b" + re.escape(n) + r"\b", listing)]
    return found

def inventory_ramdisk_files(images: Path, root: Path):
    """Recursively scan the extracted boot-related images and log extracted paths."""
    scan_root = root / "ramdisk-scan"
    scan_root.mkdir(parents=True, exist_ok=True)
    log_path = root / "ramdisk-file-inventory.txt"
    records = []
    with log_path.open("w", encoding="utf-8") as log:
        log.write("NOVA Recovery OTA Inspector - recursive boot-image inventory\n")
        log.write("Scope: recovery-related boot partition images only\n")
        log.write("This is a filename inventory of recognizable extracted content.\n\n")
        for image_path in sorted(images.rglob("*")):
            if not image_path.is_file() or image_path.suffix.lower() != ".img":
                continue
            dest = scan_root / image_path.stem
            dest.mkdir(parents=True, exist_ok=True)
            log.write(f"\n=== IMAGE: {image_path.name} ===\n")
            try:
                output = run(["binwalk", "-e", "-M", "-C", str(dest), str(image_path)])
                log.write(output)
            except Exception as exc:
                log.write(f"BINWALK ERROR: {exc}\n")
                records.append({"image": image_path.name, "error": str(exc)})
                continue
            extracted = []
            for p in sorted(dest.rglob("*")):
                rel = str(p.relative_to(dest))
                if p.is_file():
                    extracted.append({"path": rel, "size_bytes": p.stat().st_size})
                    log.write(f"FILE {rel} ({p.stat().st_size} bytes)\n")
                elif p.is_dir():
                    log.write(f"DIR  {rel}/\n")
            records.append({"image": image_path.name, "extracted_items": extracted})
            if not extracted:
                log.write("No extracted entries were produced by binwalk.\n")
    print(f"Ramdisk inventory log: {log_path}")
    print(f"Extracted file entries: {sum(len(x.get('extracted_items', [])) for x in records)}")
    shutil.rmtree(scan_root, ignore_errors=True)
    return records, log_path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--label", default="Galaxian-FullOTA")
    ap.add_argument("--payload-dumper", required=True)
    ap.add_argument("--output", default="out")
    ap.add_argument("--cache-file", default="")
    args = ap.parse_args()

    label = re.sub(r"[^A-Za-z0-9._-]+", "_", args.label).strip("._-")[:80] or "FullOTA"
    root = Path(args.output).resolve() / label
    root.mkdir(parents=True, exist_ok=True)

    if args.cache_file:
        ota = Path(args.cache_file).resolve()
        ota.parent.mkdir(parents=True, exist_ok=True)
        keep_ota = True
    else:
        ota = root / "full_ota.zip"
        keep_ota = False

    try:
        if ota.is_file() and ota.stat().st_size:
            if ota.stat().st_size > MAX_DOWNLOAD:
                raise RuntimeError("Cached OTA exceeds 8 GiB limit.")
            info = {"url": args.url, "bytes": ota.stat().st_size, "sha256": sha256_file(ota), "source": "cache"}
            print("Using cached OTA ZIP:", ota)
        else:
            info = download(args.url, ota)

        listing = run([args.payload_dumper, "-l", str(ota)])
        (root / "payload-partitions-list.txt").write_text(listing, encoding="utf-8")

        found = partition_names(listing)
        if not found:
            raise RuntimeError("No recovery-related boot partitions found in payload listing.")

        images = root / "recovery-boot-partitions"
        images.mkdir(exist_ok=True)
        run([args.payload_dumper, "-o", str(images), "-p", ",".join(found), str(ota)])

        image_inventory = []
        for path in sorted(images.rglob("*")):
            if path.is_file():
                image_inventory.append({
                    "path": str(path.relative_to(root)),
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                })

        ramdisk_records, ramdisk_log = inventory_ramdisk_files(images, root)
        report = {
            "tool": "NOVA Recovery OTA Inspector",
            "version": "0.4.0",
            "label": label,
            "download": info,
            "recovery_related_partitions": found,
            "partition_listing": listing,
            "extracted_inventory": image_inventory,
            "ramdisk_scan_inventory": ramdisk_records,
            "ramdisk_log": str(ramdisk_log.relative_to(root)),
            "note": "Scans recognizable embedded data recursively in recovery-related boot images only. No device is modified."
        }
        (root / "recovery-summary.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print("=== RAMDISK FILE INVENTORY ===")
        for rec in ramdisk_records:
            print(f"IMAGE {rec['image']}")
            for item in rec.get("extracted_items", []):
                print(f"FILE {item['path']} ({item['size_bytes']} bytes)")
            if rec.get("error"):
                print(f"ERROR {rec['error']}")
        print(json.dumps({"output": str(root), "partitions": found, "image_files": len(image_inventory), "ramdisk_scan_records": len(ramdisk_records), "ota_cache": str(ota)}, indent=2))
    finally:
        if not keep_ota and ota.exists():
            ota.unlink()

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR:", exc, file=sys.stderr)
        sys.exit(2)
