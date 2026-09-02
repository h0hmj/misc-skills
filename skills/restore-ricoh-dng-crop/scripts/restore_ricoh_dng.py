#!/usr/bin/env python3
"""Restore Ricoh GR IV full-frame DNG crop metadata without touching RAW pixels."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

FULL_RAW = (6304, 4224)
FULL_CROP_ORIGIN = "28 24"
FULL_CROP_SIZE = "6192 4128"
FULL_CROP = (6192, 4128)
MODEL = "RICOH GR IV"


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def read_tags(path: Path) -> dict[str, object]:
    fields = [
        "Make",
        "Model",
        "SubIFD:ImageWidth",
        "SubIFD:ImageHeight",
        "SubIFD:DefaultCropOrigin",
        "SubIFD:DefaultCropSize",
        "SubIFD1:ImageWidth",
        "SubIFD1:ImageHeight",
        "ExifIFD:FocalLengthIn35mmFormat",
    ]
    result = run(["exiftool", "-j", *[f"-{field}" for field in fields], str(path)])
    try:
        return json.loads(result.stdout)[0]
    except (ValueError, IndexError) as exc:
        raise RuntimeError(f"failed to parse exiftool output: {exc}") from exc


def pair(value: object, field: str) -> tuple[int, int]:
    if not isinstance(value, str):
        raise RuntimeError(f"missing or unparseable {field}: {value!r}")
    parts = value.replace(",", " ").split()
    if len(parts) != 2:
        raise RuntimeError(f"unparseable {field}: {value!r}")
    try:
        return int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise RuntimeError(f"unparseable {field}: {value!r}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restore Ricoh GR IV full-frame DNG crop metadata on a new copy."
    )
    parser.add_argument("input", type=Path, help="source DNG; it is never overwritten")
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        help="output DNG (default: <input-stem>_restored.DNG)",
    )
    args = parser.parse_args()

    source = args.input.expanduser()
    if not source.is_file():
        print(f"error: file does not exist: {source}", file=sys.stderr)
        return 2
    if shutil.which("exiftool") is None:
        print("error: exiftool not found; please install it first.", file=sys.stderr)
        return 2

    output = (args.output.expanduser() if args.output else source.with_name(f"{source.stem}_restored{source.suffix}"))
    if source.resolve() == output.resolve():
        print("error: output path must differ from input path; keep the original DNG.", file=sys.stderr)
        return 2

    try:
        before = read_tags(source)
        make = str(before.get("Make", ""))
        model = str(before.get("Model", ""))
        raw = (int(before["ImageWidth"]), int(before["ImageHeight"]))
        crop = pair(before.get("DefaultCropSize"), "DefaultCropSize")
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if MODEL not in model or "RICOH" not in make.upper():
        print(f"error: not the expected Ricoh GR IV DNG (Make={make!r}, Model={model!r}).", file=sys.stderr)
        return 2
    if raw != FULL_RAW:
        print(f"error: RAW size is {raw[0]}x{raw[1]}, expected {FULL_RAW[0]}x{FULL_RAW[1]}.", file=sys.stderr)
        return 2
    if crop == FULL_CROP:
        print("no change needed: DefaultCropSize is already 6192x4128.")
        return 0
    if (
        any(value <= 0 for value in crop)
        or any(value > limit for value, limit in zip(crop, FULL_CROP))
    ):
        print(
            "error: DefaultCropSize must be positive and no larger than "
            f"{FULL_CROP[0]}x{FULL_CROP[1]} (got {crop[0]}x{crop[1]}).",
            file=sys.stderr,
        )
        return 2

    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)
    command = [
        "exiftool",
        "-overwrite_original",
        f"-SubIFD:DefaultCropOrigin={FULL_CROP_ORIGIN}",
        f"-SubIFD:DefaultCropSize={FULL_CROP_SIZE}",
        "-ExifIFD:FocalLengthIn35mmFormat=28",
        str(output),
    ]
    result = run(command, check=False)
    if result.returncode != 0:
        print(result.stderr or result.stdout, file=sys.stderr)
        return result.returncode or 1

    after = read_tags(output)
    restored_crop = pair(after.get("DefaultCropSize"), "DefaultCropSize")
    restored_origin = pair(after.get("DefaultCropOrigin"), "DefaultCropOrigin")
    if restored_crop != FULL_CROP or restored_origin != (28, 24):
        print("error: post-write verification failed.", file=sys.stderr)
        return 1

    validation = run(["exiftool", "-validate", "-warning", "-error", str(output)], check=False)
    if "Error" in validation.stdout or validation.returncode not in (0, 1):
        print(validation.stdout or validation.stderr, file=sys.stderr)
        return 1

    preview = f"{after.get('ImageWidth', '?')}x{after.get('ImageHeight', '?')} RAW"
    preview_ifd = f"{after.get('ImageWidth', '?')}x{after.get('ImageHeight', '?')}"
    print(f"created: {output}")
    print(f"RAW: {preview}; DefaultCropSize: {before.get('DefaultCropSize')} -> {after.get('DefaultCropSize')}")
    print(f"DefaultCropOrigin: {before.get('DefaultCropOrigin')} -> {after.get('DefaultCropOrigin')}")
    print("note: the embedded JPEG preview was not faked or rebuilt; decode the RAW separately if a full preview is needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
