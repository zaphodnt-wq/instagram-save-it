import argparse
import getpass
import shutil
import sys
from pathlib import Path

import instaloader

from rclone_transfer import DEFAULT_REMOTE_DEST, DEFAULT_STAGING_DIR, transfer_media, verify_media

ARCHIVE_LOG_FILE = Path(__file__).parent / ".archived_shortcodes.txt"
SESSION_DIR = Path(__file__).parent / ".sessions"


def get_session_path(username: str) -> Path:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return SESSION_DIR / f"session-{username}"


def load_archived_shortcodes(archive_file: Path = ARCHIVE_LOG_FILE) -> set[str]:
    if not archive_file.exists():
        return set()
    return {
        line.strip()
        for line in archive_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def save_archived_shortcodes(shortcodes: list[str], archive_file: Path = ARCHIVE_LOG_FILE) -> None:
    if not shortcodes:
        return
    with archive_file.open("a", encoding="utf-8") as f:
        for code in shortcodes:
            f.write(f"{code}\n")


def create_instaloader(staging_dir: Path) -> instaloader.Instaloader:
    return instaloader.Instaloader(
        dirname_pattern=str(staging_dir),
        filename_pattern="{date_utc}_UTC_{shortcode}",
        compress_json=False,
        download_video_thumbnails=False,
        save_metadata=True,
    )


def login_and_save_session(username: str) -> None:
    loader = create_instaloader(DEFAULT_STAGING_DIR)
    session_path = get_session_path(username)

    if session_path.exists():
        try:
            loader.load_session_from_file(username, str(session_path))
            if loader.test_login() == username:
                print(f"Existing session for '{username}' is already valid ({session_path}).")
                return
        except Exception:
            pass

    password = getpass.getpass(f"Instagram password for {username}: ")
    try:
        loader.login(username, password)
    except instaloader.TwoFactorAuthRequiredException:
        two_factor_code = input("Enter 2FA code: ").strip()
        loader.two_factor_login(two_factor_code)

    loader.save_session_to_file(str(session_path))
    print(f"Saved Instagram session to {session_path}")


def download_saved_posts(
    username: str,
    staging_dir: Path = DEFAULT_STAGING_DIR,
    max_count: int = 20,
    fast_update: bool = True,
) -> list[str]:
    staging_dir.mkdir(parents=True, exist_ok=True)
    loader = create_instaloader(staging_dir)
    session_path = get_session_path(username)

    if session_path.exists():
        loader.load_session_from_file(username, str(session_path))
    else:
        loader.load_session_from_file(username)

    profile = instaloader.Profile.from_username(loader.context, username)
    archived = load_archived_shortcodes()
    newly_downloaded: list[str] = []

    print(f"Fetching saved posts for @{username} (max_count={max_count})...")
    for post in profile.get_saved_posts():
        if post.shortcode in archived:
            if fast_update:
                print(f"Reached already-archived post {post.shortcode}; stopping (--fast-update).")
                break
            print(f"Skipping already-archived post {post.shortcode}.")
            continue

        print(f"Downloading saved post {post.shortcode} by @{post.owner_username}...")
        loader.download_post(post, target=staging_dir)
        newly_downloaded.append(post.shortcode)

        if max_count and len(newly_downloaded) >= max_count:
            print(f"Reached max_count={max_count} posts for this run.")
            break

    print(f"Downloaded {len(newly_downloaded)} new saved post(s) into {staging_dir}.")
    return newly_downloaded


def clean_staging(staging_dir: Path = DEFAULT_STAGING_DIR) -> None:
    if not staging_dir.exists():
        return
    for item in staging_dir.iterdir():
        if item.is_file():
            item.unlink()
        elif item.is_dir():
            shutil.rmtree(item)
    print(f"Cleaned local staging directory: {staging_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download Instagram saved posts and archive them to Google Drive via rclone."
    )
    parser.add_argument("-u", "--username", required=True, help="Your Instagram username")
    parser.add_argument(
        "--login-only",
        action="store_true",
        help="Log in to Instagram, save session file, and exit",
    )
    parser.add_argument(
        "-n",
        "--max-count",
        type=int,
        default=10,
        help="Maximum number of saved posts to download per run (default: 10, 0 for unlimited)",
    )
    parser.add_argument(
        "--no-fast-update",
        action="store_true",
        help="Continue scanning past already-archived posts instead of stopping at the first match",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run rclone copy in --dry-run mode without uploading or cleaning staging",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete local staging files after rclone check verifies upload integrity",
    )
    parser.add_argument(
        "--remote",
        default=DEFAULT_REMOTE_DEST,
        help=f"rclone remote destination (default: {DEFAULT_REMOTE_DEST})",
    )
    args = parser.parse_args()

    if args.login_only:
        login_and_save_session(args.username)
        return 0

    new_shortcodes = download_saved_posts(
        username=args.username,
        staging_dir=DEFAULT_STAGING_DIR,
        max_count=args.max_count,
        fast_update=not args.no_fast_update,
    )

    has_staged_files = DEFAULT_STAGING_DIR.exists() and any(DEFAULT_STAGING_DIR.iterdir())
    if not has_staged_files:
        print("No files in staging to transfer.")
        return 0

    if not transfer_media(DEFAULT_STAGING_DIR, args.remote, dry_run=args.dry_run):
        return 1

    if args.dry_run:
        print("Dry-run complete. Staging files and archive log were not modified.")
        return 0

    if not verify_media(DEFAULT_STAGING_DIR, args.remote):
        print("Verification failed! Local staging files were kept for safety.", file=sys.stderr)
        return 1

    save_archived_shortcodes(new_shortcodes)
    print(f"Recorded {len(new_shortcodes)} post(s) in {ARCHIVE_LOG_FILE.name}.")

    if args.clean:
        clean_staging(DEFAULT_STAGING_DIR)

    return 0


if __name__ == "__main__":
    sys.exit(main())
