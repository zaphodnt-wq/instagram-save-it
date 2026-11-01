import subprocess
import sys
from pathlib import Path


def transfer_media(source_dir: Path, remote_dest: str, dry_run: bool = False) -> bool:
    """Copy media and metadata files from source_dir to an rclone remote destination."""
    cmd = [
        "rclone",
        "copy",
        str(source_dir),
        remote_dest,
        "--include", "*.{jpg,jpeg,png,mp4,json}",
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

    print(f"rclone output:\n{result.stdout}")
    return True


if __name__ == "__main__":
    test_source = Path("/tmp/rclone_test/source")
    transfer_media(test_source, "gdrive:instagram_backup_test", dry_run=True)
