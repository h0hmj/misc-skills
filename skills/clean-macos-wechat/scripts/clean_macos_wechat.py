#!/usr/bin/env python3
"""Free WeChat for Mac disk space while keeping the logged-in account."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

BUNDLE_ID = "com.tencent.xinWeChat"
FORBIDDEN_CONTAINER_DIRS = frozenset(
    {"Pictures", "Movies", "Desktop", "Downloads", "Music", "Public"}
)
FORBIDDEN_LIBRARY_DIRS = frozenset({"Keychains"})
XWECHAT_SHARED_KEEP = frozenset({"all_users"})
XWECHAT_SHARED_DELETE = frozenset({"Backup"})
KEEP_ACCOUNT_CHILDREN = frozenset({"config", "login"})
KEEP_DB_STORAGE = frozenset({"general", "MMKV"})
KEEP_APP_DATA_NAMES = frozenset({"login", "config"})
BULKY_3X_NAMES = frozenset(
    {
        "FileStorage",
        "Message",
        "Cache",
        "Caches",
        "predownload",
        "mmxpt",
        "Video",
        "Image",
        "Files",
        "RevokeMsg",
        "MessageTemp",
    }
)
WECHAT_PROCESS_NAMES = ("WeChat", "WeChatAppEx")


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
        dirnames[:] = [name for name in dirnames if not (root_path / name).is_symlink()]
        try:
            total += root_path.lstat().st_size
        except OSError:
            pass
        for name in filenames:
            child = root_path / name
            try:
                total += child.lstat().st_size
            except OSError:
                continue
    return total


def running_wechat_pids() -> list[str]:
    pids: list[str] = []
    for name in WECHAT_PROCESS_NAMES:
        try:
            result = subprocess.run(
                ["pgrep", "-x", name],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return []
        for line in result.stdout.splitlines():
            pid = line.strip()
            if pid:
                pids.append(pid)
    return pids


def container_data(home: Path) -> Path:
    return home / "Library" / "Containers" / BUNDLE_ID / "Data"


def xwechat_files_dir(home: Path) -> Path:
    return container_data(home) / "Documents" / "xwechat_files"


def app_data_dir(home: Path) -> Path:
    return container_data(home) / "Documents" / "app_data"


def legacy_3x_dir(home: Path) -> Path:
    return (
        container_data(home)
        / "Library"
        / "Application Support"
        / BUNDLE_ID
    )


def preferences_plist(home: Path) -> Path:
    return (
        container_data(home)
        / "Library"
        / "Preferences"
        / f"{BUNDLE_ID}.plist"
    )


def extra_cache_targets(home: Path) -> list[Path]:
    data = container_data(home)
    return [
        data / "Library" / "Caches",
        data / "Library" / "HTTPStorages",
        data / "Library" / "Logs",
        data / "tmp",
    ]


def allowed_roots(home: Path) -> list[Path]:
    data = container_data(home)
    roots = [
        data / "Documents",
        data / "Library" / "Caches",
        data / "Library" / "HTTPStorages",
        data / "Library" / "Logs",
        data / "tmp",
    ]
    legacy = legacy_3x_dir(home)
    if legacy.is_dir() and not legacy.is_symlink():
        roots.append(legacy)
    return roots


def _is_same_or_child(path: Path, root: Path) -> bool:
    candidate = str(path.expanduser())
    base = str(root.expanduser())
    return candidate == base or candidate.startswith(base + os.sep)


def is_forbidden_container_path(path: Path) -> bool:
    parts = path.parts
    try:
        idx = parts.index("Containers")
    except ValueError:
        return False
    if len(parts) <= idx + 3 or parts[idx + 1] != BUNDLE_ID or parts[idx + 2] != "Data":
        return False
    child = parts[idx + 3]
    if child in FORBIDDEN_CONTAINER_DIRS:
        return True
    if (
        child == "Library"
        and len(parts) > idx + 4
        and parts[idx + 4] in FORBIDDEN_LIBRARY_DIRS
    ):
        return True
    return False


def forbidden_resolved_bases(home: Path) -> list[Path]:
    return [
        home / "Library" / "Keychains",
        home / "Desktop",
        home / "Downloads",
        home / "Pictures",
        home / "Movies",
        home / "Music",
        home / "Public",
    ]


def is_forbidden_resolved(path: Path, home: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return True
    for base in forbidden_resolved_bases(home):
        try:
            resolved.relative_to(base.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


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
    if is_forbidden_resolved(path, home):
        return False
    if path.is_symlink():
        return True
    if not path.exists():
        return True
    return is_resolved_under_allowed(path, home)


def redact_path(path: Path, home: Path) -> str:
    """Hide wxid / account folder names in printed paths."""
    xfiles = xwechat_files_dir(home)
    try:
        rel = path.relative_to(xfiles)
    except ValueError:
        rel = None
    if rel is not None and rel.parts:
        top = rel.parts[0]
        if top not in XWECHAT_SHARED_KEEP and top not in XWECHAT_SHARED_DELETE:
            rest = Path(*rel.parts[1:]) if len(rel.parts) > 1 else Path()
            shown = xfiles / "<account>"
            if rest.parts:
                shown = shown.joinpath(*rest.parts)
            path = shown
    parts: list[str] = []
    for part in path.parts:
        if part.startswith("wxid_"):
            parts.append("<account>")
        else:
            parts.append(part)
    if not parts:
        return str(path)
    if parts[0] == os.sep:
        return str(Path(os.sep).joinpath(*parts[1:])) if len(parts) > 1 else os.sep
    return str(Path(*parts))


def _iterdir_sorted(path: Path) -> list[Path]:
    if not path.is_dir() or path.is_symlink():
        return []
    return sorted(path.iterdir(), key=lambda p: p.name.lower())


def classify_account(account: Path) -> tuple[list[Path], list[Path]]:
    keep: list[Path] = []
    delete: list[Path] = []
    keep.append(account / "config")
    login = account / "login"
    if login.exists() or login.is_symlink():
        keep.append(login)
    db_storage = account / "db_storage"
    if db_storage.is_dir() and not db_storage.is_symlink():
        for child in _iterdir_sorted(db_storage):
            if child.name in KEEP_DB_STORAGE:
                keep.append(child)
            else:
                delete.append(child)
    elif db_storage.exists() or db_storage.is_symlink():
        delete.append(db_storage)
    else:
        keep.append(db_storage / "general")
        keep.append(db_storage / "MMKV")
    if db_storage.is_dir() and not db_storage.is_symlink():
        for name in KEEP_DB_STORAGE:
            child = db_storage / name
            if child not in keep:
                keep.append(child)
    for child in _iterdir_sorted(account):
        if child.name in KEEP_ACCOUNT_CHILDREN or child.name == "db_storage":
            continue
        delete.append(child)
    return keep, delete


def classify_xwechat_files(xfiles: Path) -> tuple[list[Path], list[Path]]:
    keep: list[Path] = []
    delete: list[Path] = []
    if not xfiles.is_dir():
        return keep, delete
    keep.append(xfiles / "all_users")
    for child in _iterdir_sorted(xfiles):
        if child.name in XWECHAT_SHARED_KEEP:
            continue
        if child.name in XWECHAT_SHARED_DELETE:
            delete.append(child)
            continue
        if child.is_dir() and not child.is_symlink():
            k, d = classify_account(child)
            keep.extend(k)
            delete.extend(d)
            continue
        delete.append(child)
    return keep, delete


def classify_app_data(app_data: Path) -> tuple[list[Path], list[Path]]:
    keep: list[Path] = []
    delete: list[Path] = []
    if not app_data.is_dir():
        return keep, delete
    keep.append(app_data / "login")
    keep.append(app_data / "config")
    for child in _iterdir_sorted(app_data):
        if child.name in KEEP_APP_DATA_NAMES:
            continue
        if child.name.startswith("wxid_"):
            keep.append(child)
            continue
        if child.name == "radium":
            keep.append(child / "device_uuid_0")
            if child.is_dir() and not child.is_symlink():
                for sub in _iterdir_sorted(child):
                    if sub.name == "device_uuid_0":
                        continue
                    delete.append(sub)
            elif child.exists() or child.is_symlink():
                delete.append(child)
            continue
        delete.append(child)
    return keep, delete


def classify_documents(home: Path) -> tuple[list[Path], list[Path]]:
    keep: list[Path] = []
    delete: list[Path] = []
    documents = container_data(home) / "Documents"
    if not documents.is_dir():
        return keep, delete
    k, d = classify_xwechat_files(xwechat_files_dir(home))
    keep.extend(k)
    delete.extend(d)
    k, d = classify_app_data(app_data_dir(home))
    keep.extend(k)
    delete.extend(d)
    for child in _iterdir_sorted(documents):
        if child.name in {"xwechat_files", "app_data"}:
            continue
        delete.append(child)
    keep.append(preferences_plist(home))
    return keep, delete


def find_3x_bulky(app_support: Path) -> list[Path]:
    found: list[Path] = []
    if not app_support.is_dir() or app_support.is_symlink():
        return found
    for root, dirnames, _filenames in os.walk(app_support, followlinks=False):
        root_path = Path(root)
        keep_descending: list[str] = []
        for name in dirnames:
            child = root_path / name
            if name in BULKY_3X_NAMES:
                found.append(child)
                continue
            if child.is_symlink():
                continue
            keep_descending.append(name)
        dirnames[:] = keep_descending
    return found


def wechat_roots(home: Path) -> list[Path]:
    roots: list[Path] = []
    xfiles = xwechat_files_dir(home)
    if xfiles.is_dir():
        roots.append(xfiles)
    app_data = app_data_dir(home)
    if app_data.is_dir():
        roots.append(app_data)
    legacy = legacy_3x_dir(home)
    if legacy.is_dir() and not legacy.is_symlink():
        roots.append(legacy)
    return roots


def collect_targets(home: Path) -> tuple[list[Path], list[Path], list[Path]]:
    roots = wechat_roots(home)
    keep, delete = classify_documents(home)
    for bulky in find_3x_bulky(legacy_3x_dir(home)):
        delete.append(bulky)
    for extra in extra_cache_targets(home):
        if extra.is_symlink():
            continue
        if extra.exists():
            delete.append(extra)
    keep = _unique(keep)
    delete = _unique(delete)
    delete = [path for path in delete if path not in set(keep)]
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


def delete_overlaps_keep(delete: Path, keep: list[Path]) -> bool:
    delete_str = str(delete)
    for kept in keep:
        kept_str = str(kept)
        if kept_str == delete_str:
            return True
        if kept_str.startswith(delete_str + os.sep):
            return True
    return False


def print_table(rows: list[tuple[str, Path, int]], home: Path) -> None:
    print(f"{'ROLE':<8} {'SIZE':>10}  PATH")
    print("-" * 72)
    for role, path, size in rows:
        print(f"{role:<8} {human_bytes(size):>10}  {redact_path(path, home)}")


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
            raise CleanupError(
                f"refusing to delete unsafe path: {redact_path(path, home)}"
            )
        if is_forbidden_container_path(path):
            raise CleanupError(
                f"refusing to delete container user-folder: {redact_path(path, home)}"
            )
        try:
            remove_path(path)
        except OSError as exc:
            print(
                f"warning: could not delete {redact_path(path, home)}: {exc}",
                file=sys.stderr,
            )
            failed.append(path)
    return failed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Delete WeChat for Mac caches while keeping login state. "
            "Dry-run by default. Does not touch WeCom."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete. Default is dry-run.",
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
    pids = [] if args.home is not None else running_wechat_pids()
    if pids:
        msg = (
            "WeChat is running (pids: "
            + ", ".join(pids)
            + "). Quit it first:\n  osascript -e 'quit app \"WeChat\"'"
        )
        if args.apply:
            print(msg, file=sys.stderr)
            return 2
        print("warning: " + msg, file=sys.stderr)

    roots, keep, delete = collect_targets(home)
    if not roots:
        print("No WeChat data directory found.", file=sys.stderr)
        return 1

    overlapping = [path for path in delete if delete_overlaps_keep(path, keep)]
    if overlapping:
        print("Refusing delete targets that overlap keep paths:", file=sys.stderr)
        for path in overlapping:
            print(f"  {redact_path(path, home)}", file=sys.stderr)
        return 1

    print("WeChat data roots:")
    for root in roots:
        print(f"  {redact_path(root, home)}  ({human_bytes(path_size(root))})")
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
    print_table(rows, home)
    print("-" * 72)
    print(f"{'keep':<8} {human_bytes(keep_total):>10}  total")
    print(f"{'delete':<8} {human_bytes(delete_total):>10}  total")
    print()

    unsafe = [path for path in delete if path.exists() and not is_safe_target(path, home)]
    if unsafe:
        print("Refusing unsafe delete targets:", file=sys.stderr)
        for path in unsafe:
            print(f"  {redact_path(path, home)}", file=sys.stderr)
        return 1

    if not args.apply:
        print("Dry-run only. Re-run with --apply to delete.")
        return 0

    keep_existed = [path for path in keep if path.exists() or path.is_symlink()]
    failed = apply_deletes(delete, home)
    print()
    print("After apply:")
    for root in roots:
        print(
            f"  remaining {redact_path(root, home)}: {human_bytes(path_size(root))}"
        )
    missing = []
    for path in keep:
        exists = path.exists() or path.is_symlink()
        status = "ok" if exists else "MISSING"
        print(f"  {status:8} {redact_path(path, home)}")
        if path in keep_existed and not exists:
            missing.append(path)
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
