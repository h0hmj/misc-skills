---
name: clean-devin-sessions
description: Delete local Devin CLI / Devin Desktop sessions that have been inactive for more than N days or hours (default 1 day; stopping running Devin first), like Claude Code's cleanupPeriodDays but run on demand. Use when the user wants to prune old Devin session history or reclaim space used by ~/.local/share/devin.
---

# Clean Devin Sessions

Delete local Devin sessions whose last activity is older than a given number of days or hours. The default is 1 day (24 hours). Devin has no retention setting, so this skill does the cleanup on demand.

## When to Use

- The user asks to clean, prune, or expire old Devin sessions, history, or transcripts.
- The user gives a retention period ("keep 30 days", "delete sessions older than 6 hours").
- The user wants to shrink `~/.local/share/devin/cli/sessions.db`.

Do not use it for Devin **Cloud** sessions. Those live on the server.

## Storage Layout

The Devin CLI keeps all local sessions in one SQLite store. Devin Desktop runs local agent sessions through `devin acp`, so they end up in the same store.

```
$DEVIN_SESSIONS_DIR            # default: ${XDG_DATA_HOME:-~/.local/share}/devin
└── cli/
    ├── sessions.db (+ -wal, -shm)   # sessions, message_nodes, tool_call_state, prompt_history, FTS index, ...
    ├── session_locks/<id>.lock      # contains the PID of the devin process that opened the session
    ├── transcripts/<id>.json        # exported trajectories (optional)
    └── logs/devin_*.log             # CLI debug logs
```

Devin Desktop also keeps a render cache, one SQLite file per conversation:

```
macOS: ~/Library/Application Support/Devin/User/acp-messages/<hash>.db
Linux: ~/.config/Devin/User/acp-messages/<hash>.db
```

## How It Works

The helper `scripts/clean_devin_sessions.py` (Python 3.9+, stdlib only) does the following:

1. It resolves the data dir: `--data-dir`, then `$DEVIN_SESSIONS_DIR`, then `$XDG_DATA_HOME/devin`, then `~/.local/share/devin`.
2. It selects sessions where `last_activity_at` (or `created_at` if that is missing) is older than `now - period`. The period comes from `--days N` or `--hours N` (mutually exclusive), and defaults to 1 day.
3. If any Devin process is running, it plans to stop it first. That covers the Desktop main app, every `devin` CLI process, and any process whose PID is in a session lock. The script's own ancestor processes are protected, because the agent running this skill may itself be a Devin session. A session held by a protected process is kept and skipped.
4. With `--apply`, it stops those processes, but only if there is something to clean. Devin Desktop is asked to quit (`osascript quit app "Devin"` on macOS), then gets `SIGTERM`. CLI processes get `SIGTERM`, then `SIGKILL` after 10 s. Afterwards the locks are checked again, and any session that is still locked is skipped.
5. It deletes each remaining session with the official `devin rm --force <id>`. `DEVIN_SESSIONS_DIR` points at the same store. If `devin` is not installed, or `--sql` is passed, it instead deletes rows from every table that has a `session_id` column, plus `sessions`, in one transaction. FTS triggers keep the search index in sync.
6. It removes `session_locks/<id>.lock` and `transcripts/<id>.json` for each deleted session. Lock and transcript files that belong to no session and are older than the cutoff also go.
7. Optional extras: old CLI logs, old Desktop `acp-messages` cache files, and `VACUUM`.

## Steps for the Agent

1. Use the retention period the user gave. Use `--hours` for sub-day periods and `--days` otherwise; both accept fractions. If the user gave none, omit both to get the default of 1 day (24 hours).
2. Do a dry run first (this is the default). Show the user the session list and the "running Devin processes to stop first" list:

   ```bash
   python3 scripts/clean_devin_sessions.py              # default: 1 day
   python3 scripts/clean_devin_sessions.py --hours 6
   python3 scripts/clean_devin_sessions.py --days 30
   ```

3. After the user confirms, apply. This stops running Devin Desktop and CLI sessions, so warn the user to finish or save any work in progress first:

   ```bash
   python3 scripts/clean_devin_sessions.py --hours 6 --apply
   ```

4. Add these extras only if the user wants them:

   | Flag | Effect |
   |------|--------|
   | `--include-logs` | Also delete `cli/logs/*.log` older than the cutoff |
   | `--no-stop` | Leave running Devin processes alone and skip the sessions they hold |
   | `--include-desktop-cache` | Also delete Desktop `acp-messages/*.db` older than the cutoff. Skipped if Devin Desktop is still running |
   | `--vacuum` | Run `VACUUM` on `sessions.db` to give disk space back. Skipped while any devin process holds a session lock |
   | `--sql` | Bypass `devin rm` and delete with SQLite directly |
   | `--data-dir DIR` | Use another Devin data dir |

The script exits non-zero if any session fails to delete.

## Notes

- Deletion is permanent. There is no trash. Always show the dry-run output before `--apply`.
- `--days 0` (or `--hours 0`) selects every session except the one this agent runs in.
- Stopping interrupts in-flight Devin turns. A CLI TUI killed with a signal may leave its terminal in a raw state; run `reset` in that terminal to fix it. Devin Desktop restores unsaved editors through hot exit, as VS Code does.
- Orphaned Electron helper processes (`chrome_crashpad_handler`, `Devin Helper (Plugin)`) are not stopped. They do not hold sessions.
- `devin list` only shows sessions for the current directory. This script covers all directories.
- `devin rm` does not remove lock files. This script does.
- Test safely by copying the data dir and passing `--data-dir` (or setting `DEVIN_SESSIONS_DIR`). Overriding `HOME` does **not** redirect the devin binary.
- The schema was checked against devin CLI `3000.11.x`. If `devin rm` changes, use `--sql` as the fallback.
