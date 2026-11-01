# Instagram Save-It

A lightweight **Go CLI (`ig-save`)** and Python + `rclone` pipeline for downloading saved Instagram posts, archiving clean media files (`.mp4`, `.jpg`) to Google Drive (`gdrive:rclone/save`) using least-privilege `drive.file` OAuth access, and automatically unsaving verified posts from Instagram.

## Architecture & Safety Guarantees

1. **Least-Privilege Cloud Scope (`scope=drive.file`)**: `rclone` is restricted at Google's OAuth level so it can **only** see and write files inside the `rclone/save` folder it created—it has zero visibility into the rest of your Google Drive.
2. **Fetch Clean Media (`ig-save` / `save_it.py`)**: Uses an authenticated Instagram session to download saved posts (`.mp4` and `.jpg`, without `.json` or `.txt` clutter) into `./staging/`.
3. **Archive via `rclone copy`**: Copies staged media to `gdrive:rclone/save` with live upload progress (`--progress`). Never uses `rclone sync`, so remote files are never deleted when cleaning up local staging.
4. **Verify, Unsave & Deduplicate**: Runs `rclone check --one-way` to verify upload integrity (`0 differences found`) before recording post shortcodes in `.archived_shortcodes.txt`, optionally unsaving them from Instagram (`--unsave`), and cleaning `./staging/` (`--clean`).

---

## Option A: Native Go CLI (`./ig-save`) — Zero Dependencies

The Go CLI uses 100% Go standard library (`main.go`, `instagram.go`, `rclone.go`) and compiles into a single standalone binary that runs in any terminal environment without needing Python or `.venv`.

### Build & Setup
```bash
# 1. Compile the standalone binary
go build -o ig-save .

# 2. Configure rclone with drive.file scope and create target folder (one-time)
rclone config create gdrive drive scope=drive.file
rclone mkdir gdrive:rclone/save

# 3. Save your Instagram session using your browser's sessionid cookie (one-time)
./ig-save -u YOUR_IG_USERNAME --login-only --sessionid "YOUR_SESSIONID_COOKIE"
```

### Usage
```bash
# Dry-run: download 3 saved posts to ./staging and preview rclone transfer
./ig-save -u YOUR_IG_USERNAME -n 3 --dry-run

# Live run: upload to gdrive:rclone/save, verify checksums, unsave from Instagram, and clean ./staging
./ig-save -u YOUR_IG_USERNAME -n 3 --clean --unsave

# Archive and unsave ALL saved posts
./ig-save -u YOUR_IG_USERNAME -n 0 --clean --unsave

# List archived files in Google Drive
rclone lsf gdrive:rclone/save
```

---

## Option B: Python CLI (`save_it.py`)

```bash
source .venv/bin/activate && pip install -r requirements.txt
python save_it.py -u YOUR_IG_USERNAME --login-only --sessionid "YOUR_SESSIONID_COOKIE"
python save_it.py -u YOUR_IG_USERNAME -n 3 --clean --unsave
```
