#!/usr/bin/env python3
"""Delete local Devin CLI sessions that have been inactive for a given period (default: 1 day).

Works like Claude Code's ``cleanupPeriodDays``, but runs on demand.
Dry-run by default; pass ``--apply`` to delete.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


def resolve_data_dir(override: str | None) -> Path:
    """Same lookup order as the devin binary: DEVIN_SESSIONS_DIR, XDG_DATA_HOME, ~/.local/share."""
    if override:
        return Path(override).expanduser()
    if os.environ.get("DEVIN_SESSIONS_DIR"):
        return Path(os.environ["DEVIN_SESSIONS_DIR"]).expanduser()
    if os.environ.get("XDG_DATA_HOME"):
        return Path(os.environ["XDG_DATA_HOME"]).expanduser() / "devin"
    return Path.home() / ".local" / "share" / "devin"


def desktop_cache_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Devin" / "User" / "acp-messages"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Devin" / "User" / "acp-messages"


def find_devin() -> str | None:
    found = shutil.which("devin")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "devin"
    return str(fallback) if fallback.exists() else None


def pid_is_devin(pid: int) -> bool:
    """True if pid is alive and looks like a devin process. Errs on the side of 'alive'."""
    if not pid_alive(pid):
        return False
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return True
    return "devin" in out.lower()


def live_locks(lock_dir: Path) -> dict[str, int]:
    """Session id -> pid for lock files whose pid is a running devin process."""
    live = {}
    for lock in lock_dir.glob("*.lock"):
        try:
            pid = int(lock.read_text().strip())
        except (OSError, ValueError):
            continue
        if pid_is_devin(pid):
            live[lock.stem] = pid
    return live


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def ancestor_pids() -> set[int]:
    """This process and its parents, so we never kill the agent that runs this script."""
    pids, pid = set(), os.getpid()
    while pid > 1 and pid not in pids:
        pids.add(pid)
        try:
            out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
            pid = int(out)
        except (OSError, subprocess.SubprocessError, ValueError):
            break
    return pids


def devin_processes() -> list[tuple[int, str, str]]:
    """Running Devin Desktop main processes and devin CLI processes as (pid, kind, command)."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    procs = []
    for line in out.splitlines():
        pid_str, _, cmd = line.strip().partition(" ")
        if not pid_str.isdigit() or int(pid_str) == os.getpid():
            continue
        if "Devin.app/Contents/MacOS/" in cmd:
            procs.append((int(pid_str), "desktop", cmd))
        elif cmd.split() and os.path.basename(cmd.split()[0]) == "devin":
            procs.append((int(pid_str), "cli", cmd))
    return procs


def wait_gone(pids: list[int], timeout: float) -> list[int]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        pids = [p for p in pids if pid_alive(p)]
        if not pids:
            break
        time.sleep(0.3)
    return pids


def send_signal(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def stop_devin(procs: list[tuple[int, str, str]]) -> list[int]:
    """Quit Devin Desktop gracefully, then SIGTERM (and finally SIGKILL) CLI processes. Returns survivors."""
    desktop = [pid for pid, kind, _ in procs if kind == "desktop"]
    cli = [pid for pid, kind, _ in procs if kind == "cli"]
    if desktop:
        if sys.platform == "darwin":
            subprocess.run(["osascript", "-e", 'quit app "Devin"'], capture_output=True, timeout=30)
        desktop = wait_gone(desktop, 15)
        for pid in desktop:
            send_signal(pid, signal.SIGTERM)
        desktop = wait_gone(desktop, 10)
    cli = [p for p in cli if pid_alive(p)]
    for pid in cli:
        send_signal(pid, signal.SIGTERM)
    cli = wait_gone(cli, 10)
    for pid in cli:
        send_signal(pid, signal.SIGKILL)
    return wait_gone(desktop + cli, 3)


def fmt_time(ts: int | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "-"


def fmt_period(hours: float) -> str:
    if hours >= 24 and hours % 24 == 0:
        return f"{hours / 24:g} day(s)"
    return f"{hours:g} hour(s)"


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n} B"


def file_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def old_files(directory: Path, pattern: str, cutoff: float) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob(pattern) if p.is_file() and p.stat().st_mtime < cutoff)


def delete_with_sql(db: Path, session_id: str) -> None:
    """Fallback that mirrors `devin rm`: drop the row from every table keyed by session_id."""
    con = sqlite3.connect(db, timeout=10)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND sql NOT LIKE 'CREATE VIRTUAL%'")]
        with con:
            for table in tables:
                cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
                if "session_id" in cols:
                    con.execute(f'DELETE FROM "{table}" WHERE session_id = ?', (session_id,))
            con.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    period = ap.add_mutually_exclusive_group()
    period.add_argument("--days", type=float,
                        help="delete sessions whose last activity is older than this many days (default: 1)")
    period.add_argument("--hours", type=float,
                        help="same as --days, in hours (e.g. --hours 6)")
    ap.add_argument("--apply", action="store_true", help="actually delete (default is dry-run)")
    ap.add_argument("--data-dir", help="Devin data dir (default: $DEVIN_SESSIONS_DIR or ~/.local/share/devin)")
    ap.add_argument("--sql", action="store_true", help="delete via SQLite directly instead of `devin rm`")
    ap.add_argument("--include-logs", action="store_true", help="also delete CLI logs older than the cutoff")
    ap.add_argument("--include-desktop-cache", action="store_true",
                    help="also delete Devin Desktop acp-messages cache files older than the cutoff")
    ap.add_argument("--no-stop", action="store_true",
                    help="do not stop running Devin processes; skip sessions they hold instead")
    ap.add_argument("--vacuum", action="store_true", help="VACUUM sessions.db afterwards to reclaim disk space")
    args = ap.parse_args()

    hours = args.hours if args.hours is not None else (args.days if args.days is not None else 1) * 24
    if hours < 0:
        ap.error("--days / --hours must be >= 0")

    data_dir = resolve_data_dir(args.data_dir)
    cli_dir = data_dir / "cli"
    db = cli_dir / "sessions.db"
    if not db.exists():
        print(f"No Devin session database at {db}", file=sys.stderr)
        return 1

    cutoff = time.time() - hours * 3600
    lock_dir = cli_dir / "session_locks"
    transcript_dir = cli_dir / "transcripts"

    con = sqlite3.connect(db, timeout=10)
    rows = con.execute(
        "SELECT id, title, working_directory, COALESCE(last_activity_at, created_at) AS last "
        "FROM sessions ORDER BY last").fetchall()
    con.close()
    known_ids = {r[0] for r in rows}
    expired = [r for r in rows if r[3] < cutoff]

    # Processes that hold sessions or the Desktop app. Our own ancestors are never stopped:
    # this script may be running inside a Devin session.
    protected = ancestor_pids()
    locks = live_locks(lock_dir)
    to_stop = {pid: (kind, cmd) for pid, kind, cmd in devin_processes() if pid not in protected}
    for pid in locks.values():
        if pid not in protected:
            to_stop.setdefault(pid, ("cli", f"(holds a session lock, pid {pid})"))
    if args.no_stop:
        to_stop = {}
    blocked = {sid for sid, pid in locks.items() if pid not in to_stop}

    skipped = [r for r in expired if r[0] in blocked]
    targets = [r for r in expired if r[0] not in blocked]
    orphans = [p for p in old_files(lock_dir, "*.lock", cutoff) + old_files(transcript_dir, "*.json", cutoff)
               if p.stem not in known_ids and p.stem not in blocked]
    logs = old_files(cli_dir / "logs", "*.log", cutoff) if args.include_logs else []
    desktop = old_files(desktop_cache_dir(), "*.db", cutoff) if args.include_desktop_cache else []
    has_work = bool(targets or orphans or logs or desktop or args.vacuum)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] data dir: {data_dir}")
    print(f"cutoff: last activity before {fmt_time(int(cutoff))} ({fmt_period(hours)})")
    print(f"sessions: {len(rows)} total, {len(targets)} to delete, {len(skipped)} skipped (in use)\n")

    for sid, title, cwd, last in targets:
        print(f"  {fmt_time(last)}  {sid:<28} {(title or '').strip()[:60]}")
        print(f"  {'':16}  {'':<28} {cwd}")
    for sid, title, _, last in skipped:
        print(f"  SKIP {fmt_time(last)}  {sid} (live lock) {(title or '')[:50]}")
    if orphans:
        print(f"\norphan lock/transcript files: {len(orphans)}")
    if args.include_logs:
        print(f"old CLI logs: {len(logs)} ({fmt_size(sum(map(file_size, logs)))})")
    if args.include_desktop_cache:
        print(f"old Desktop acp-messages cache files: {len(desktop)} "
              f"({fmt_size(sum(map(file_size, desktop)))})")
    if to_stop and has_work:
        print(f"\nrunning Devin processes to stop first: {len(to_stop)}")
        for pid, (kind, cmd) in sorted(to_stop.items()):
            print(f"  {pid:>7}  {kind:<7}  {cmd[:100]}")
    if any(pid in protected for pid in locks.values()):
        print("\nnote: this script runs inside a Devin session; that session is kept running and skipped.")

    if not args.apply:
        print("\nDry-run only. Re-run with --apply to delete.")
        return 0

    if to_stop and has_work:
        print("\nstopping Devin ...")
        survivors = stop_devin([(pid, kind, cmd) for pid, (kind, cmd) in to_stop.items()])
        if survivors:
            print(f"could not stop pid(s): {', '.join(map(str, survivors))}", file=sys.stderr)
        # Re-check: anything still holding a lock is skipped.
        still_locked = set(live_locks(lock_dir))
        for r in targets:
            if r[0] in still_locked:
                print(f"  SKIP {r[0]}: still locked", file=sys.stderr)
        targets = [r for r in targets if r[0] not in still_locked]

    if desktop and any(kind == "desktop" for _, kind, _ in devin_processes()):
        print("\nDevin Desktop is running; skipping its cache.", file=sys.stderr)
        desktop = []

    devin = None if args.sql else find_devin()
    if not args.sql and devin is None:
        print("devin binary not found; falling back to direct SQLite deletion.", file=sys.stderr)
    env = {**os.environ, "DEVIN_SESSIONS_DIR": str(data_dir)}

    failed = 0
    deleted = 0
    for sid, *_ in targets:
        try:
            if devin:
                res = subprocess.run([devin, "rm", "--force", sid], env=env,
                                     capture_output=True, text=True, timeout=60)
                if res.returncode != 0:
                    raise RuntimeError((res.stderr or res.stdout).strip())
            else:
                delete_with_sql(db, sid)
        except Exception as exc:  # keep going; report at the end
            failed += 1
            print(f"  FAILED {sid}: {exc}", file=sys.stderr)
            continue
        deleted += 1
        for extra in (lock_dir / f"{sid}.lock", transcript_dir / f"{sid}.json"):
            extra.unlink(missing_ok=True)

    freed = 0
    for p in orphans + logs + desktop:
        freed += file_size(p)
        p.unlink(missing_ok=True)
        if p.suffix == ".db":
            for side in ("-wal", "-shm", "-journal"):
                Path(str(p) + side).unlink(missing_ok=True)

    print(f"\ndeleted {deleted} session(s), {failed} failed; removed {len(orphans) + len(logs) + len(desktop)} "
          f"file(s) ({fmt_size(freed)})")

    if args.vacuum:
        if live_locks(lock_dir):
            print("A devin process holds a session lock; skipping VACUUM.", file=sys.stderr)
        else:
            before = file_size(db) + file_size(Path(str(db) + "-wal"))
            con = sqlite3.connect(db, timeout=10)
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con.execute("VACUUM")
            con.close()
            after = file_size(db) + file_size(Path(str(db) + "-wal"))
            print(f"VACUUM: {fmt_size(before)} -> {fmt_size(after)}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
