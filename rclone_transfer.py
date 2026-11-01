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
    """Copy media and metadata files from source_dir to an rclone remote destination."""
    cmd = [
        "rclone",
        "copy",
        str(source_dir),
        remote_dest,
        "--include", "*.{jpg,jpeg,png,mp4,json,txt}",
        "--transfers", "4",
        "--checkers", "8",
        "--fast-list",
        "--stats-one-line",
        "-v",
    ]

    if dry_run:
        cmd.append("--dry-run")

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"rclone failed with code {result.returncode}:\n{result.stderr}", file=sys.stderr)
        return False

    output = (result.stdout + "\n" + result.stderr).strip()
    print(f"rclone output:\n{output}")
    return True


def verify_media(
    source_dir: Path = DEFAULT_STAGING_DIR,
    remote_dest: str = DEFAULT_REMOTE_DEST,
) -> bool:
    """Verify that all media/metadata files in source_dir exist and match on remote_dest."""
    cmd = [
        "rclone",
        "check",
        str(source_dir),
        remote_dest,
        "--include", "*.{jpg,jpeg,png,mp4,json,txt}",
        "--one-way",
    ]
    print(f"Verifying: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode != 0:
        print(f"rclone check failed:\n{output}", file=sys.stderr)
        return False
    print(f"rclone check passed:\n{output}")
    return True


if __name__ == "__main__":
    dry_run_flag = "--live" not in sys.argv
    DEFAULT_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    ok = transfer_media(DEFAULT_STAGING_DIR, DEFAULT_REMOTE_DEST, dry_run=dry_run_flag)
    if ok and not dry_run_flag:
        verify_media(DEFAULT_STAGING_DIR, DEFAULT_REMOTE_DEST)
