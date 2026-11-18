#!/usr/bin/env python3
"""Download and inspect Android boot-image archives from Nothing Archive.

This tool extracts files for offline inspection. It never flashes a device.
"""
from __future__ import annotations

import argparse
import base64
import bz2
import gzip
import hashlib
import json
import lzma
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

AOSP_UNPACK_BOOTIMG_URL = (
    "https://android.googlesource.com/platform/system/tools/mkbootimg/"
    "+/refs/heads/main/unpack_bootimg.py?format=TEXT"
)
USER_AGENT = "Nothing-Image-Unpacker/0.1 (+https://github.com/minax2036-beep/Lineage_nothing)"
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024 * 1024  # 8 GiB download cap
MAX_EXTRACTED_FILES = 100_000
MAX_CPIO_TOTAL_BYTES = 4 * 1024 * 1024 * 1024  # 4 GiB per ramdisk


class ToolError(RuntimeError):
    pass


def run_checked(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except FileNotFoundError as exc:
        raise ToolError(f"必要なコマンドが見つかりません: {command[0]}") from exc
    if result.returncode != 0:
        raise ToolError(
            f"コマンドが失敗しました (exit={result.returncode}): {' '.join(command)}\n"
            f"{result.stdout[-6000:]}"
        )
    return result


def safe_label(value: str) -> str:
    label = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    label = label.strip("._-")
    return label[:80] or "firmware"


def download_archive(url: str, destination: Path) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ToolError("ダウンロードURLには https:// の完全なURLを指定してください。")

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    downloaded = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_ARCHIVE_BYTES:
                raise ToolError("アーカイブが上限 (8 GiB) を超えています。")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > MAX_ARCHIVE_BYTES:
                    raise ToolError("ダウンロードが上限 (8 GiB) を超えたため中止しました。")
                digest.update(chunk)
                output.write(chunk)
    except urllib.error.HTTPError as exc:
        raise ToolError(f"ダウンロードに失敗しました: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"ダウンロードに失敗しました: {exc.reason}") from exc
    return {"url": url, "bytes": downloaded, "sha256": digest.hexdigest()}


def validate_7z_members(archive_path: Path) -> None:
    listing = run_checked(["7z", "l", "-slt", str(archive_path)]).stdout
    members = 0
    for line in listing.splitlines():
        if not line.startswith("Path = "):
            continue
        name = line[len("Path = "):].strip()
        # 7z prints the archive path itself as the first Path entry.
        if Path(name).resolve() == archive_path.resolve():
            continue
        member = PurePosixPath(name.replace("\\", "/"))
        if member.is_absolute() or any(part in ("..", "") for part in member.parts):
            raise ToolError(f"安全でないアーカイブ内パスを検出しました: {name}")
        if re.match(r"^[A-Za-z]:", name):
            raise ToolError(f"ドライブ指定を含むパスを検出しました: {name}")
        members += 1
        if members > MAX_EXTRACTED_FILES:
            raise ToolError("アーカイブ内のファイル数が多すぎます。")


def extract_7z(archive_path: Path, destination: Path) -> None:
    validate_7z_members(archive_path)
    destination.mkdir(parents=True, exist_ok=True)
    run_checked(["7z", "x", "-y", f"-o{destination}", str(archive_path)])


def download_aosp_unpacker(destination: Path) -> Path:
    request = urllib.request.Request(AOSP_UNPACK_BOOTIMG_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            encoded = response.read().replace(b"\n", b"").replace(b"\r", b"").strip()
        source = base64.b64decode(encoded, validate=True)
    except Exception as exc:  # network and base64 failures should produce one clear diagnostic
        raise ToolError(f"AOSP unpack_bootimg.py を取得できませんでした: {exc}") from exc
    if b"def unpack_vendor_boot_image" not in source or b"def unpack_boot_image" not in source:
        raise ToolError("取得したAOSP unpack_bootimg.pyが想定した内容ではありません。")
    destination.write_bytes(source)
    destination.chmod(0o755)
    return destination


def decompress_ramdisk(data: bytes) -> tuple[bytes, str]:
    if data.startswith((b"070701", b"070702", b"070707")):
        return data, "uncompressed cpio"
    if data.startswith(b"\x1f\x8b"):
        return gzip.decompress(data), "gzip"
    if data.startswith(b"\xfd7zXZ\x00"):
        return lzma.decompress(data), "xz"
    if data.startswith(b"BZh"):
        return bz2.decompress(data), "bzip2"
    if data.startswith(b"\x28\xb5\x2f\xfd"):
        try:
            import zstandard  # type: ignore
        except ImportError as exc:
            raise ToolError("zstd Ramdiskです。requirements.txt の依存関係をインストールしてください。") from exc
        return zstandard.ZstdDecompressor().decompress(data), "zstd"
    if data.startswith((b"\\x04\\x22\\x4d\\x18", b"\\x02\\x21\\x4c\\x18")):
        # Modern frames use the Python module; legacy Android frames use the CLI.
        if data.startswith(b"\\x04\\x22\\x4d\\x18"):
            try:
                import lz4.frame  # type: ignore
                return lz4.frame.decompress(data), "lz4 frame"
            except Exception:
                pass
        lz4_binary = shutil.which("lz4")
        if not lz4_binary:
            raise ToolError("Legacy LZ4の展開には lz4 コマンドが必要です。")
        with tempfile.TemporaryDirectory(prefix="ramdisk-lz4-") as temp_dir:
            source = Path(temp_dir) / "ramdisk.lz4"
            target = Path(temp_dir) / "ramdisk.cpio"
            source.write_bytes(data)
            run_checked([lz4_binary, "-d", "-f", str(source), str(target)])
            if not target.is_file():
                raise ToolError("lz4コマンドが展開ファイルを生成しませんでした。")
            return target.read_bytes(), "lz4 legacy"
    raise ToolError(f"未対応のRamdisk圧縮形式です (先頭: {data[:8].hex()})")


def safe_cpio_path(root: Path, member_name: str) -> Path:
    normalized = member_name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ToolError(f"Ramdisk内に安全でないパスがあります: {member_name!r}")
    clean_parts = [part for part in path.parts if part not in ("", ".")]
    target = root.joinpath(*clean_parts)
    # No symlinks are created while extracting, so this check also blocks traversal.
    if os.path.commonpath([str(root.resolve()), str(target.resolve())]) != str(root.resolve()):
        raise ToolError(f"Ramdisk内パスが出力先から外れています: {member_name!r}")
    return target


def extract_newc_cpio(data: bytes, destination: Path) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    offset = 0
    files = 0
    total_bytes = 0
    symlinks: list[dict[str, str]] = []
    special: list[dict[str, Any]] = []

    while offset < len(data):
        if len(data) - offset < 110:
            raise ToolError("CPIOヘッダーが途中で切れています。")
        magic = data[offset:offset + 6]
        if magic not in (b"070701", b"070702"):
            if magic == b"070707":
                raise ToolError("旧形式のbinary CPIO (070707) はまだ対応していません。")
            raise ToolError(f"CPIOのmagicが不正です: {magic!r}")
        fields = [int(data[offset + 6 + i * 8:offset + 14 + i * 8], 16) for i in range(13)]
        mode = fields[1]
        file_size = fields[6]
        name_size = fields[11]
        name_start = offset + 110
        name_end = name_start + name_size
        if name_size < 1 or name_end > len(data):
            raise ToolError("CPIOファイル名の範囲が不正です。")
        name = data[name_start:name_end - 1].decode("utf-8", errors="replace")
        data_start = (name_end + 3) & ~3
        data_end = data_start + file_size
        if data_end > len(data):
            raise ToolError(f"CPIOファイルのデータが途中で切れています: {name}")
        next_offset = (data_end + 3) & ~3
        if name == "TRAILER!!!":
            break

        files += 1
        total_bytes += file_size
        if files > MAX_EXTRACTED_FILES:
            raise ToolError("Ramdisk内のファイル数が上限を超えています。")
        if total_bytes > MAX_CPIO_TOTAL_BYTES:
            raise ToolError("Ramdiskの展開サイズが上限 (4 GiB) を超えています。")

        target = safe_cpio_path(destination, name)
        file_type = stat.S_IFMT(mode)
        payload = data[data_start:data_end]

        if file_type == stat.S_IFDIR:
            target.mkdir(parents=True, exist_ok=True)
        elif file_type == stat.S_IFREG:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            try:
                target.chmod(mode & 0o777)
            except OSError:
                pass
        elif file_type == stat.S_IFLNK:
            symlinks.append({"path": name, "target": payload.decode("utf-8", errors="replace")})
        else:
            special.append({"path": name, "mode": oct(mode), "size": file_size})

        offset = next_offset

    manifest = {
        "format": "newc CPIO",
        "files_and_entries": files,
        "regular_payload_bytes": total_bytes,
        "symlinks_not_created": symlinks,
        "special_files_not_created": special,
    }
    (destination / "_extraction_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def find_ramdisk_files(unpacked_dir: Path) -> list[Path]:
    found: list[Path] = []
    for path in unpacked_dir.rglob("*"):
        if path.is_file() and not path.is_symlink():
            name = path.name.lower()
            if name in {"ramdisk", "vendor_ramdisk"} or name.startswith(("ramdisk_", "vendor_ramdisk")):
                found.append(path)
    return sorted(found)


def find_recovery_ramdisk_files(unpacked_dir: Path, image_info: str) -> list[Path]:
    candidates = find_ramdisk_files(unpacked_dir)
    selected: set[Path] = set()

    for path in candidates:
        if "recovery" in path.name.lower():
            selected.add(path)

    for line in image_info.splitlines():
        match = re.search(
            r"vendor ramdisk\s+(\d+):.*?(?:type=RECOVERY|name=recovery)",
            line,
            re.IGNORECASE,
        )
        if match:
            index = int(match.group(1))
            for path in candidates:
                if path.name.lower() in {f"vendor_ramdisk{index:02d}", f"vendor_ramdisk{index}"}:
                    selected.add(path)
    return sorted(selected)


def process_image(
    image_path: Path, image_root: Path, unpacker: Path, recovery_only: bool = False
) -> dict[str, Any]:
    relative = image_path.relative_to(image_root)
    partition_name = re.sub(r"[^A-Za-z0-9._-]+", "_", image_path.stem)
    target_dir = image_root.parent / "unpacked" / partition_name
    target_dir.mkdir(parents=True, exist_ok=True)

    with image_path.open("rb") as file:
        magic = file.read(8)

    result: dict[str, Any] = {
        "image": str(relative),
        "bytes": image_path.stat().st_size,
        "magic": magic.decode("ascii", errors="replace"),
        "status": "skipped",
        "output": str(target_dir.relative_to(image_root.parent)),
    }
    if magic not in (b"ANDROID!", b"VNDRBOOT"):
        result["note"] = "AOSP boot/vendor_boot imageではないため、元のIMGを保存して解析をスキップしました。"
        return result

    command = [sys.executable, str(unpacker), "--boot_img", str(image_path), "--out", str(target_dir)]
    unpack_result = run_checked(command)
    (target_dir / "image-info.txt").write_text(unpack_result.stdout, encoding="utf-8")
    result["status"] = "unpacked"
    result["ramdisks"] = []
    image_info = unpack_result.stdout
    ramdisk_files = (
        find_recovery_ramdisk_files(target_dir, image_info)
        if recovery_only else find_ramdisk_files(target_dir)
    )
    if recovery_only and not ramdisk_files:
        result["status"] = "recovery_ramdisk_not_found"
        result["note"] = "RecoveryタイプのRamdiskを特定できませんでした。image-info.txtを確認してください。"
        return result

    for ramdisk in ramdisk_files:
        try:
            raw = ramdisk.read_bytes()
            cpio_data, compression = decompress_ramdisk(raw)
            root_name = re.sub(r"[^A-Za-z0-9._-]+", "_", ramdisk.stem) + "_root"
            root_dir = ramdisk.parent / root_name
            manifest = extract_newc_cpio(cpio_data, root_dir)
            result["ramdisks"].append({
                "file": str(ramdisk.relative_to(image_root.parent)),
                "compression": compression,
                "root": str(root_dir.relative_to(image_root.parent)),
                "manifest": manifest,
            })
        except Exception as exc:
            result["ramdisks"].append({
                "file": str(ramdisk.relative_to(image_root.parent)),
                "status": "kept_unpacked_blob",
                "note": str(exc),
            })

    if recovery_only:
        keep = {target_dir / "image-info.txt", *ramdisk_files}
        for entry in result["ramdisks"]:
            root_value = entry.get("root")
            if root_value:
                keep.add(image_root.parent / root_value)
        for child in list(target_dir.iterdir()):
            if child in keep:
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink(missing_ok=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NothingArchiveの7zイメージを取得し、Android boot/vendor_bootを展開します。"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive-url", help="NothingArchiveの直接ダウンロードURL (HTTPS)")
    source.add_argument("--archive", type=Path, help="手元にある .7z ファイル")
    parser.add_argument("--output", type=Path, default=Path("out"), help="出力ディレクトリ (default: out)")
    parser.add_argument("--label", default="firmware", help="出力フォルダ名に使うビルドラベル")
    parser.add_argument("--recovery-only", action="store_true",
                        help="vendor_boot.img内のRecoveryタイプRamdiskだけを展開・保存します。")
    args = parser.parse_args()

    output_dir = args.output / safe_label(args.label)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ToolError(f"出力先が空ではありません。別の --label を指定してください: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    images_dir = output_dir / "images"
    images_dir.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="nothing-image-unpacker-") as temporary:
        temp_dir = Path(temporary)
        if args.archive_url:
            archive_path = temp_dir / "firmware.7z"
            download_info = download_archive(args.archive_url, archive_path)
        else:
            archive_path = args.archive.expanduser().resolve()
            if not archive_path.is_file():
                raise ToolError(f"アーカイブが見つかりません: {archive_path}")
            if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
                raise ToolError("アーカイブが上限 (8 GiB) を超えています。")
            digest_obj = hashlib.sha256()
            with archive_path.open("rb") as local_file:
                for chunk in iter(lambda: local_file.read(1024 * 1024), b""):
                    digest_obj.update(chunk)
            download_info = {"local_file": str(archive_path), "bytes": archive_path.stat().st_size, "sha256": digest_obj.hexdigest()}

        if archive_path.suffix.lower() != ".7z":
            raise ToolError("初版はNothingArchiveの .7z イメージアーカイブに対応しています。")
        extracted = temp_dir / "archive_contents"
        extract_7z(archive_path, extracted)

        image_paths = sorted(p for p in extracted.rglob("*.img") if p.is_file() and not p.is_symlink())
        if args.recovery_only:
            image_paths = [p for p in image_paths if p.name.lower() == "vendor_boot.img"]
        if not image_paths:
            expected = "vendor_boot.img" if args.recovery_only else ".img"
            raise ToolError(f"アーカイブ内に必要な {expected} が見つかりませんでした。")

        for image in image_paths:
            relative = image.relative_to(extracted)
            destination = images_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(image, destination)

    images = sorted(p for p in images_dir.rglob("*.img") if p.is_file())
    with tempfile.TemporaryDirectory(prefix="aosp-unpack-tool-") as tool_temp:
        aosp_tool = download_aosp_unpacker(Path(tool_temp) / "unpack_bootimg.py")
        image_results = [
            process_image(image, images_dir, aosp_tool, recovery_only=args.recovery_only)
            for image in images
        ]

    if args.recovery_only:
        shutil.rmtree(images_dir, ignore_errors=True)

    summary = {
        "tool": "Nothing Image Unpacker",
        "version": "0.2.0",
        "mode": "recovery-only" if args.recovery_only else "full-image-inspection",
        "device_focus": "Nothing Phone (3a) Lite / Galaxian",
        "download": download_info,
        "image_count": len(images),
        "images": image_results,
        "important_note": (
            "Recovery-only mode keeps the Recovery ramdisk, extracted files, image header report, and summary. "
            "It does not recreate original C/C++ source code. No flashing is performed."
        ),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(output_dir), "image_count": len(images), "summary": str(output_dir / "summary.json")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ToolError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
