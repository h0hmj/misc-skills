---
name: clean-macos-qq
description: Free disk space used by QQ for Mac while keeping the logged-in account. Use when the user wants to delete local QQ chat history, pictures, videos, files, and caches on macOS without scanning a QR code again.
---

# Clean macOS QQ (keep login)

Delete local QQ NT chat records, media, and caches on macOS. Keep only the files that restore the last logged-in account.

## When to Use

- The user wants to reclaim space taken by QQ on this Mac.
- They are willing to lose **local** chat history, pictures, videos, and received files.
- They want the client to stay logged in (no QR / password if auto-login still works).

Do not use to decrypt chat databases, extract A1 tickets, or wipe the whole QQ container.

## Safety

Follow these rules even for a manual cleanup:

- **Quit QQ first.** `--apply` must refuse if a `QQ` process is running.
- **Never delete** `~/Library/Containers/com.tencent.qq/Data` as a whole. That container also maps the real user **Pictures**, **Movies**, **Desktop**, and **Downloads**.
- Never follow those user-folder names, and never follow directory symlinks when deleting.
- Do not touch the macOS Keychain.
- Default is **dry-run**. Pass `--apply` only after the size table looks right.
- Chat text that QQ **roams from the server** may reappear after login. Media and files stay gone unless the client re-downloads them.

## File map

QQ NT 6.9+ may use either root. Prefer the sandbox path when it exists:

```
~/Library/Containers/com.tencent.qq/Data/Library/Application Support/QQ   # sandbox (7.x)
~/Library/Application Support/QQ                                           # unsandboxed (some 6.9.x)
```

Keep (login):

```
<qq-root>/auth/                 # login.enc — account list, auto-login flags
<qq-root>/global/nt_db/         # login.db (+ wal/shm) — A1 tickets
<qq-root>/versions/             # tiny updater metadata
```

Delete (when present):

```
<qq-root>/nt_qq_*/              # chat DBs, Pic/Video/File/Ptt/Emoji, nt_temp
<qq-root>/global/nt_data/       # VAS bubbles / poke faces
<qq-root>/Partitions/            # Electron profile (skip with --keep-electron-profile)
<qq-root>/Local Storage/         # Electron profile (skip with --keep-electron-profile)
<qq-root>/dynamic_shiply_res/
<qq-root>/log/
~/Library/Caches/com.tencent.qq
<container>/Data/Library/Caches/com.tencent.qq
<container>/Data/Documents/TPReportPluginCache
```

Other non-keep siblings under `<qq-root>` (GPUCache, Cookies, Session Storage, …) are also deleted.

## How It Works

1. Resolve this skill directory (the folder that contains this `SKILL.md`).
2. Run the stdlib helper (Python 3.10+). Dry-run is the default:

```bash
python3 scripts/clean_macos_qq.py
```

From a repo root that contains this skill:

```bash
python3 skills/clean-macos-qq/scripts/clean_macos_qq.py
```

3. If QQ is running, quit it, then re-run:

```bash
osascript -e 'quit app "QQ"'
```

4. Show the keep vs delete table to the user. Only then apply:

```bash
python3 scripts/clean_macos_qq.py --apply
```

5. Relaunch QQ and confirm the account is still logged in without a QR scan.
6. If auto-login fails, restore is not available (this skill does not back up). Re-login once, then retry later with `--keep-electron-profile` if the tickets were not the problem.

### Flags

| Flag | Description |
|---|---|
| *(none)* | Dry-run: print each path, keep/delete, and size. No deletes. |
| `--apply` | Delete the delete-set. Refuses if QQ is running. |
| `--keep-electron-profile` | Skip `Partitions/` and `Local Storage/` (and other Chromium profile dirs). |
| `--home PATH` | Override the user home (tests). Default is `Path.home()`. Skips the live QQ process check. |

No extra packages.

## Notes

- `login.enc` is JSON despite the suffix. Do not print its account fields in logs.
- If recent cloud-roamed **text** returns after login, that is server roaming, not leftover local files.
- After a successful cleanup, suggest turning off auto-download of images/videos/files in QQ settings so the cache does not grow back immediately.
