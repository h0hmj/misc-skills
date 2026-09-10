#!/usr/bin/env python3
"""Free QQ for Mac disk space while keeping the logged-in account."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

FORBIDDEN_CONTAINER_DIRS = frozenset(
    {"Pictures", "Movies", "Desktop", "Downloads", "Music", "Public"}
)
ELECTRON_PROFILE_NAMES = frozenset(
    {
        "Partitions",
        "Local Storage",
        "Session Storage",
        "IndexedDB",
        "Cookies",
        "GPUCache",
        "Code Cache",
        "Cache",
        "blob_storage",
        "Service Worker",
        "Crashpad",
        "DawnGraphiteCache",
        "DawnWebGPUCache",
        "Shared Dictionary",
        "WebStorage",
        "Network Persistent State",
    }
)
KEEP_QQ_ROOT_NAMES = frozenset({"auth", "versions", "global"})


class CleanupError(Exception):
    """User-facing failure."""


def human_bytes(num: int) -> str:
    value = float(num)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{num} B"


def path_size(path: Path) -> int:
    """Directory size without following directory symlinks."""
    if not path.exists() and not path.is_symlink():
        return 0
    if path.is_symlink() or path.is_file():
        try:
            return path.lstat().st_size
        except OSError:
            return 0
    total = 0
    for root, dirnames, filenames in os.walk(path, followlinks=False):
        root_path = Path(root)
        # Drop directory symlinks so we never walk into Photos / Movies.
        dirnames[:] = [name for name in dirnames if not (root_path / name).is_symlink()]
        try:
            total += root_path.lstat().st_size
        except OSError:
            pass
        for name in filenames:
            child = root_path / name
            try:
                if child.is_symlink():
                    total += child.lstat().st_size
                else:
                    total += child.lstat().st_size
            except OSError:
                continue
    return total


def running_qq_pids() -> list[str]:
    try:
        result = subprocess.run(
            ["pgrep", "-x", "QQ"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    pids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return pids


def is_forbidden_container_path(path: Path) -> bool:
    parts = path.parts
    try:
        idx = parts.index("Containers")
    except ValueError:
        return False
    # .../Containers/com.tencent.qq/Data/<child>/
    if len(parts) > idx + 3 and parts[idx + 2] == "Data":
        return parts[idx + 3] in FORBIDDEN_CONTAINER_DIRS
    return False


def allowed_roots(home: Path) -> list[Path]:
    container = home / "Library" / "Containers" / "com.tencent.qq" / "Data"
    return [
        container / "Library",
        container / "Documents" / "TPReportPluginCache",
        container / "tmp",
        home / "Library" / "Application Support" / "QQ",
        home / "Library" / "Caches" / "com.tencent.qq",
    ]


def _is_same_or_child(path: Path, root: Path) -> bool:
    candidate = str(path.expanduser())
    base = str(root.expanduser())
    return candidate == base or candidate.startswith(base + os.sep)


def is_resolved_under_allowed(path: Path, home: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in allowed_roots(home):
        try:
            resolved.relative_to(root.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def is_safe_target(path: Path, home: Path) -> bool:
    if is_forbidden_container_path(path):
        return False
    if not any(_is_same_or_child(path, root) for root in allowed_roots(home)):
        return False
    # Symlinks are unlinked, not walked. The link itself must sit under an
    # allowed root; the target may be anywhere and is never followed.
    if path.is_symlink():
        return True
    if not path.exists():
        return True
    return is_resolved_under_allowed(path, home)


def qq_roots(home: Path) -> list[Path]:
    sandbox = (
        home
        / "Library"
        / "Containers"
        / "com.tencent.qq"
        / "Data"
        / "Library"
        / "Application Support"
        / "QQ"
    )
    unsandboxed = home / "Library" / "Application Support" / "QQ"
    roots = []
    if sandbox.is_dir():
        roots.append(sandbox)
    if unsandboxed.is_dir():
        roots.append(unsandboxed)
    return roots


def extra_cache_targets(home: Path) -> list[Path]:
    container = home / "Library" / "Containers" / "com.tencent.qq" / "Data"
    return [
        home / "Library" / "Caches" / "com.tencent.qq",
        container / "Library" / "Caches" / "com.tencent.qq",
        container / "Documents" / "TPReportPluginCache",
        container / "tmp",
    ]


def is_electron_profile_name(name: str) -> bool:
    return name in ELECTRON_PROFILE_NAMES


def classify_qq_root(qq_root: Path, keep_electron: bool) -> tuple[list[Path], list[Path]]:
    keep: list[Path] = []
    delete: list[Path] = []
    if not qq_root.is_dir():
        return keep, delete

    keep.append(qq_root / "auth")
    keep.append(qq_root / "versions")
    keep.append(qq_root / "global" / "nt_db")

    global_dir = qq_root / "global"
    if global_dir.is_dir():
        for child in sorted(global_dir.iterdir(), key=lambda p: p.name.lower()):
            if child.name == "nt_db":
                continue
            delete.append(child)

    for child in sorted(qq_root.iterdir(), key=lambda p: p.name.lower()):
        if child.name in KEEP_QQ_ROOT_NAMES:
            continue
        if keep_electron and is_electron_profile_name(child.name):
            keep.append(child)
            continue
        delete.append(child)
    return keep, delete


def collect_targets(home: Path, keep_electron: bool) -> tuple[list[Path], list[Path], list[Path]]:
    roots = qq_roots(home)
    keep: list[Path] = []
    delete: list[Path] = []
    for qq_root in roots:
        k, d = classify_qq_root(qq_root, keep_electron)
        keep.extend(k)
        delete.extend(d)
    for extra in extra_cache_targets(home):
        if extra.exists() or extra.is_symlink():
            delete.append(extra)
    # Deduplicate while preserving order.
    keep = _unique(keep)
    delete = _unique(delete)
    return roots, keep, delete


def _unique(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def print_table(rows: list[tuple[str, Path, int]]) -> None:
    print(f"{'ROLE':<8} {'SIZE':>10}  PATH")
    print("-" * 72)
    for role, path, size in rows:
        print(f"{role:<8} {human_bytes(size):>10}  {path}")


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.is_dir():
        shutil.rmtree(path)
        return
    if path.exists():
        path.unlink()


def apply_deletes(delete: list[Path], home: Path) -> list[Path]:
    failed: list[Path] = []
    for path in delete:
        if not path.exists() and not path.is_symlink():
            continue
        if not is_safe_target(path, home):
            raise CleanupError(f"refusing to delete unsafe path: {path}")
        if is_forbidden_container_path(path):
            raise CleanupError(f"refusing to delete container user-folder: {path}")
        try:
            remove_path(path)
        except OSError as exc:
            print(f"warning: could not delete {path}: {exc}", file=sys.stderr)
            failed.append(path)
    return failed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete QQ for Mac caches while keeping login state. Dry-run by default."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete. Default is dry-run.",
    )
    parser.add_argument(
        "--keep-electron-profile",
        action="store_true",
        help="Keep Partitions/, Local Storage/, and other Chromium profile dirs.",
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=None,
        help="Override home directory (tests).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    home = (args.home or Path.home()).expanduser()
    # --home is for fixtures; never inspect the live QQ process in that mode.
    pids = [] if args.home is not None else running_qq_pids()
    if pids:
        msg = (
            "QQ is running (pids: "
            + ", ".join(pids)
            + "). Quit it first:\n  osascript -e 'quit app \"QQ\"'"
        )
        if args.apply:
            print(msg, file=sys.stderr)
            return 2
        print("warning: " + msg, file=sys.stderr)

    roots, keep, delete = collect_targets(home, args.keep_electron_profile)
    if not roots:
        print("No QQ data directory found.", file=sys.stderr)
        return 1

    print("QQ data roots:")
    for root in roots:
        print(f"  {root}  ({human_bytes(path_size(root))})")
    print()

    rows: list[tuple[str, Path, int]] = []
    keep_total = 0
    delete_total = 0
    for path in keep:
        size = path_size(path) if path.exists() or path.is_symlink() else 0
        keep_total += size
        rows.append(("keep", path, size))
    for path in delete:
        size = path_size(path)
        delete_total += size
        rows.append(("delete", path, size))
    print_table(rows)
    print("-" * 72)
    print(f"{'keep':<8} {human_bytes(keep_total):>10}  total")
    print(f"{'delete':<8} {human_bytes(delete_total):>10}  total")
    print()

    unsafe = [path for path in delete if path.exists() and not is_safe_target(path, home)]
    if unsafe:
        print("Refusing unsafe delete targets:", file=sys.stderr)
        for path in unsafe:
            print(f"  {path}", file=sys.stderr)
        return 1

    if not args.apply:
        print("Dry-run only. Re-run with --apply to delete.")
        return 0

    failed = apply_deletes(delete, home)
    missing = [path for path in keep if not path.exists()]
    print()
    print("After apply:")
    for root in roots:
        print(f"  remaining {root}: {human_bytes(path_size(root))}")
    for path in keep:
        status = "ok" if path.exists() else "MISSING"
        print(f"  {status:8} {path}")
    if missing:
        print("warning: some keep paths are missing after cleanup.", file=sys.stderr)
    if failed:
        print(f"warning: {len(failed)} path(s) could not be deleted.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CleanupError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
