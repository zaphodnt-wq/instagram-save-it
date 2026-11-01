# Instagram Save-It

A lightweight Python + `rclone` pipeline for downloading and archiving saved Instagram media to cloud storage without risking remote deletions.

## Architecture & Workflow

1. **Fetch Media Locally**: Download Instagram saved posts/media (e.g., via `instaloader`) to a local staging directory.
2. **Archive via `rclone copy`**: Copy media and metadata (`*.{jpg,jpeg,png,mp4,json}`) to remote cloud storage (`gdrive:`, `onedrive:`, etc.) using `rclone copy` (never `rclone sync`, so remote files are never deleted when cleaning up local staging).
3. **Verify & Clean Up**: Optionally run `rclone check` to verify upload integrity before purging local staging files.

## Quick Verification Commands

```bash
# List configured rclone remotes
rclone listremotes

# Check remote connectivity and storage quota
rclone about gdrive:

# Dry-run copy from staging to remote
rclone copy ./staging gdrive:instagram_backup_test \
  --dry-run \
  -vv \
  --include "*.{jpg,jpeg,png,mp4,json}"
```
