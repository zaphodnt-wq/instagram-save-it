import subprocess
import sys
from pathlib import Path


DEFAULT_STAGING_DIR = Path(__file__).parent / "staging"
DEFAULT_REMOTE_DEST = "gdrive:rclone/save"


def transfer_media(
    source_dir: Path = DEFAULT_STAGING_DIR,
    remote_dest: str = DEFAULT_REMOTE_DEST,
    dry_run: bool = False,
) -> bool:
    """Copy media files from source_dir to an rclone remote destination with live progress."""
    cmd = [
        "rclone",
        "copy",
        str(source_dir),
        remote_dest,
        "--include", "*.{jpg,jpeg,png,mp4}",
        "--transfers", "4",
        "--checkers", "8",
        "--progress",
        "-v",
    ]

    if dry_run:
        cmd.append("--dry-run")

    print(f"Running: {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd)

    if result.returncode != 0:
        print(f"rclone failed with code {result.returncode}", file=sys.stderr)
        return False

    return True


def verify_media(
    source_dir: Path = DEFAULT_STAGING_DIR,
    remote_dest: str = DEFAULT_REMOTE_DEST,
) -> bool:
    """Verify that all media files in source_dir exist and match on remote_dest."""
    cmd = [
        "rclone",
        "check",
        str(source_dir),
        remote_dest,
        "--include", "*.{jpg,jpeg,png,mp4}",
        "--one-way",
    ]
    print(f"Verifying: {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("rclone check failed!", file=sys.stderr)
        return False
    print("rclone check passed!")
    return True


if __name__ == "__main__":
    dry_run_flag = "--live" not in sys.argv
    DEFAULT_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    ok = transfer_media(DEFAULT_STAGING_DIR, DEFAULT_REMOTE_DEST, dry_run=dry_run_flag)
    if ok and not dry_run_flag:
        verify_media(DEFAULT_STAGING_DIR, DEFAULT_REMOTE_DEST)
