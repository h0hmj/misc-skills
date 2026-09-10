---
name: clean-macos-wechat
description: Free disk space used by WeChat for Mac (个人微信 / com.tencent.xinWeChat, not WeCom) while keeping the logged-in account. Use when the user wants to delete local WeChat chat history, pictures, videos, files, and caches on macOS without scanning a QR code again.
---

# Clean macOS WeChat (keep login)

Delete local WeChat chat records, media, mini-program data, and caches on macOS. Keep only the files that restore the last logged-in **personal** WeChat account.

## When to Use

- The user wants to reclaim space taken by **WeChat** (微信) on this Mac.
- They are willing to lose **local** chat history, pictures, videos, and received files.
- They want the client to stay logged in (no QR scan if auto-login still works).

Do not use for WeCom (企业微信 / `com.tencent.WeWorkMac`), to decrypt chat databases, or to wipe the whole WeChat container.

## Safety

Follow these rules even for a manual cleanup:

- **Quit WeChat first.** `--apply` must refuse if a `WeChat` or `WeChatAppEx` process is running. Quit with `osascript -e 'quit app "WeChat"'` — never quit 企业微信.
- **Never delete** `~/Library/Containers/com.tencent.xinWeChat` or its `Data` folder as a whole. That container maps the real user **Pictures**, **Movies**, **Desktop**, **Downloads**, and **Keychains**.
- Never follow those user-folder names, and never follow directory symlinks when deleting.
- Do not touch the macOS Keychain. `Data/Library/Keychains` is a symlink to the real keychain.
- Do not touch `com.tencent.WeWorkMac` or WeCom group containers.
- Do not print account folder names, `wxid_*` path components, or `key_info.db` contents. Show those paths as `<account>`.
- Default is **dry-run**. Pass `--apply` only after the size table looks right.
- Chat **text** that WeChat **roams from the server** may reappear after login. Media and files stay gone unless the client re-downloads them.

## File map

WeChat 4.x (current App Store / official Mac client):

```
~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files
~/Library/Containers/com.tencent.xinWeChat/Data/Documents/app_data
```

Keep (login):

```
xwechat_files/all_users/              # login/<account>/key_info.db + global config
<account>/config/                     # login_config*
<account>/login/                      # present on some installs
<account>/db_storage/general/
<account>/db_storage/MMKV/
app_data/login/
app_data/config/
app_data/wxid_*/                      # tiny account marker dirs
app_data/radium/device_uuid_0          # device id
<container>/Data/Library/Preferences/com.tencent.xinWeChat.plist
```

Delete (when present):

```
<account>/msg/                        # attach / file / video
<account>/cache/
<account>/business/                   # stickers, favorites media, moments
<account>/apm_record/
<account>/temp/
<account>/resource/
<account>/db_storage/*                # except general/ and MMKV/
xwechat_files/Backup/
app_data/radium/*                     # except device_uuid_0 (mini programs)
app_data/log/
app_data/crashinfo/
app_data/xplugin/
app_data/net/
<container>/Data/Library/Caches
<container>/Data/Library/HTTPStorages
<container>/Data/Library/Logs
<container>/Data/tmp
```

Other non-keep siblings under `xwechat_files/<account>/` and `app_data/` are also deleted.

WeChat 3.x leftover (skip if missing):

```
~/Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat
```

Only known bulky names are removed (`FileStorage`, `Message`, `Cache`, `Caches`, `predownload`, `mmxpt`, `Video`, `Image`, `Files`, `RevokeMsg`, `MessageTemp`). Everything else in that tree is left in place.

## How It Works

1. Resolve this skill directory (the folder that contains this `SKILL.md`).
2. Run the stdlib helper (Python 3.10+). Dry-run is the default:

```bash
python3 scripts/clean_macos_wechat.py
```

From a repo root that contains this skill:

```bash
python3 skills/clean-macos-wechat/scripts/clean_macos_wechat.py
```

3. If WeChat is running, quit it, then re-run:

```bash
osascript -e 'quit app "WeChat"'
```

4. Show the keep vs delete table to the user. Only then apply:

```bash
python3 scripts/clean_macos_wechat.py --apply
```

5. Relaunch WeChat and confirm the account is still logged in without a QR scan.
6. If auto-login fails, restore is not available (this skill does not back up). Re-login once.

### Flags

| Flag | Description |
|---|---|
| *(none)* | Dry-run: print each path, keep/delete, and size. No deletes. |
| `--apply` | Delete the delete-set. Refuses if WeChat is running. |
| `--home PATH` | Override the user home (tests). Default is `Path.home()`. Skips the live WeChat process check. |

No extra packages.

## Notes

- Contacts and the session list are deleted with the chat DBs. They re-sync from the server after login.
- If recent cloud-roamed **text** returns after login, that is server roaming, not leftover local files.
- After a successful cleanup, suggest turning off auto-download of images/videos/files in WeChat settings so the cache does not grow back immediately.
