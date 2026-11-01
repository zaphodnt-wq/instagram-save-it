# Instagram Save-It

A lightweight, cross-platform **Go CLI (`ig-save` / `ig-save.exe`)** and Python + `rclone` pipeline for downloading saved Instagram posts, archiving clean media files (`.mp4`, `.jpg`) to Google Drive (`gdrive:rclone/save`) using least-privilege `drive.file` OAuth access, and automatically unsaving verified posts from Instagram.

---

## Current Project Checkpoint

- **Repository:** `https://github.com/zaphodnt-wq/instagram-save-it`
- **Completed & Verified Workflows:**
  - **Least-Privilege Cloud Storage (`scope=drive.file`):** `rclone` can only see and write inside the `rclone/save` folder it created—zero access to personal Google Drive files.
  - **Native Go CLI ([`main.go`](main.go), [`instagram.go`](instagram.go), [`rclone.go`](rclone.go)):** 100% Go standard library, zero external dependencies, tested on both **Linux (`./ig-save`)** and **Windows PowerShell (`.\ig-save.exe`)**.
  - **Python Reference CLI ([`save_it.py`](save_it.py), [`rclone_transfer.py`](rclone_transfer.py), [`tag_media.py`](tag_media.py)):** Complete Python implementation with `.venv` support.
  - **End-to-End Safety Pipeline:**
    1. Fetch saved posts via Instagram's `v1` API into `./staging/` (clean `.mp4` and `.jpg` files only—no `.json` or `.txt` clutter).
    2. Upload to `gdrive:rclone/save` via `rclone copy --progress` (never `rclone sync`, so remote files are never deleted).
    3. Verify upload integrity via `rclone check --one-way` (`0 differences found`).
    4. Record post shortcodes in `.archived_shortcodes.txt` and `.unsaved_shortcodes.txt`.
    5. Automatically unsave verified posts from Instagram (`--unsave`) and clean `./staging/` (`--clean`).

---

## Build, Cross-Compile & Run (`ig-save`)

### 1. Compile / Cross-Compile from Linux
```bash
# Build for current Linux machine
go build -o ig-save .

# Build 100% static binary for Intel/AMD Linux (x86_64)
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o ig-save-linux-amd64 .

# Build 100% static binary for ARM64 Linux (e.g., NVIDIA Grace/Spark, Raspberry Pi)
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -o ig-save-linux-arm64 .

# Cross-compile for Windows PowerShell (.exe)
CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -o ig-save.exe .
```

### 2. One-Time Session Setup
```bash
# Linux
./ig-save -u YOUR_IG_USERNAME --login-only --sessionid "YOUR_SESSIONID_COOKIE"

# Windows PowerShell
.\ig-save.exe -u YOUR_IG_USERNAME --login-only --sessionid "YOUR_SESSIONID_COOKIE"
```

### 3. Daily Usage
```bash
# Dry-run: download 3 saved posts to ./staging and preview rclone transfer
./ig-save -u YOUR_IG_USERNAME -n 3 --dry-run

# Live run: upload to gdrive:rclone/save, verify checksums, unsave from Instagram, and clean ./staging
./ig-save -u YOUR_IG_USERNAME -n 3 --clean --unsave

# Archive and unsave ALL saved posts
./ig-save -u YOUR_IG_USERNAME -n 0 --clean --unsave

# Reset local archive history if you deleted files on Drive and want to re-download
./ig-save -u YOUR_IG_USERNAME -n 3 --clean --reset-archive

# List archived files in Google Drive
rclone lsf gdrive:rclone/save
```

---

## Tips, Tricks & Troubleshooting Notes

1. **Copying State Across Machines:**
   - When moving `ig-save` or `ig-save.exe` to another computer, you can copy `.sessions/go-session-<username>.json` (your Instagram session) and `.archived_shortcodes.txt` (your backup history) so you don't need to log in again or re-download old posts.
2. **Windows `rclone` Port `53682` Conflict & Config Sharing:**
   - If `rclone config create` on Windows says `listen tcp 127.0.0.1:53682: bind: Only one usage of each socket address`, your IDE is port-forwarding `53682` from your remote Linux session (or a background `rclone` process is running).
   - **Fix:** Either copy `~/.config/rclone/rclone.conf` from Linux directly to `C:\Users\<you>\AppData\Roaming\rclone\rclone.conf` on Windows, or close port `53682` in the IDE's **Ports** tab and run `Stop-Process -Name rclone -Force -ErrorAction SilentlyContinue` in PowerShell.
3. **Google Drive Trash Quirk:**
   - If you manually delete files or the `rclone/save` folder in the Google Drive web browser, make sure to **empty your Google Drive Trash** (or refresh) so `rclone` doesn't see or upload into a trashed folder.
4. **How to Grab a Fresh Instagram `sessionid` Cookie:**
   - If your Instagram session ever expires, open [instagram.com](https://www.instagram.com) in your browser, press **`F12`** -> **Application** (Chrome/Edge/Brave) or **Storage** (Firefox) -> **Cookies** -> `https://www.instagram.com`, copy the **`sessionid`** value, and run `./ig-save -u YOUR_IG_USERNAME --login-only --sessionid "PASTE_HERE"`.
5. **Revoking / Auditing Google Drive Permissions:**
   - You can verify or revoke `rclone`'s Google Account permissions anytime at [myaccount.google.com/connections](https://myaccount.google.com/connections). Always use `scope=drive.file` so `rclone` can only access files it creates.
6. **Why `.txt` and `.json` Sidecars Are Excluded for VLC:**
   - VLC Media Player automatically treats a `.txt` file with the same base name as an `.mp4` as a subtitle track, and flashes long MP4 Title tags via its On-Screen Display (OSD) across vertical `9:16` videos. Saving only clean `.mp4`/`.jpg` files keeps VLC playback completely unobstructed.
   - *(If you ever want to adjust VLC's OSD title font size/position: **Tools -> Preferences (`Ctrl+P`) -> Subtitles / OSD** -> set Font size to **Smaller**, or under **Show settings: All -> Video -> Subtitles / OSD**, set **Position of video title** to **Bottom**.)*
7. **Running `ig-save` from Anywhere:**
   - Copy `ig-save` into `~/bin` or `/usr/local/bin` on Linux (or a folder on your Windows `PATH`) so you can run `ig-save` without `./`.

---

## To-Do List / Next Tasks

- [ ] **Create a Personal Google Cloud OAuth `client_id` for `rclone` (High Priority before 2026 retirement):**
  - *Quick Note:* Newer versions of `rclone` print a `NOTICE` that `rclone`'s shared built-in Google Drive `client_id` is being retired during 2026 (`https://rclone.org/drive/#making-your-own-client-id`).
  - *How to do it:* Create a personal OAuth Client ID in Google Cloud Console (**APIs & Services -> Credentials -> Create Credentials -> OAuth client ID**), make sure the Application type is set to **Desktop app** (not Web application), copy the `.apps.googleusercontent.com` Client ID and Client Secret into `rclone`, and authorize with `scope=drive.file`. This also removes `rclone`'s shared global API rate limit for even faster uploads!
- [ ] **Optional Scheduled Automation:**
  - Set up a Linux `cron` job / `systemd` timer or Windows Task Scheduler task to run `ig-save -u zaphodnt -n 0 --clean --unsave` automatically on a schedule.
- [ ] **Optional Collection / Subfolder Routing:**
  - Route saved posts into subfolder categories inside `gdrive:rclone/save/` based on Instagram Saved Collections or creator `@username`.
