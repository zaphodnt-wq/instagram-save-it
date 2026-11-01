import argparse
import base64
import getpass
import json
import shutil
import sys
from pathlib import Path

import urllib.parse

import instaloader
import requests

from rclone_transfer import DEFAULT_REMOTE_DEST, DEFAULT_STAGING_DIR, transfer_media, verify_media

ARCHIVE_LOG_FILE = Path(__file__).parent / ".archived_shortcodes.txt"
UNSAVED_LOG_FILE = Path(__file__).parent / ".unsaved_shortcodes.txt"
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
        save_metadata=False,
        post_metadata_txt_pattern="",
    )


def _verify_session_via_v1(loader: instaloader.Instaloader) -> bool:
    """Verify the session against Instagram's active v1 saved posts endpoint."""
    resp = loader.context._session.get(
        "https://www.instagram.com/api/v1/feed/saved/posts/",
        params={"include_igtv_preview": "false"},
        headers={
            "X-IG-App-ID": "936619743392459",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.instagram.com/",
        },
        timeout=15,
    )
    if resp.status_code == 200:
        try:
            data = resp.json()
            return data.get("status") == "ok" and "items" in data
        except Exception:
            return False
    return False


def login_with_sessionid(username: str, sessionid: str) -> None:
    """Create and verify an Instaloader session using a browser sessionid cookie."""
    loader = create_instaloader(DEFAULT_STAGING_DIR)
    session_path = get_session_path(username)

    # Remove any accidental spaces/newlines and URL-decode %3A -> :
    clean_sessionid = "".join(urllib.parse.unquote(sessionid).split()).strip("\"'")
    ds_user_id = clean_sessionid.split(":")[0] if ":" in clean_sessionid else ""

    temp_session = requests.Session()
    temp_session.headers.update({"User-Agent": loader.context.user_agent})
    temp_session.get("https://www.instagram.com/", timeout=15)
    csrftoken = temp_session.cookies.get_dict().get("csrftoken", "missing")

    cookie_dict = {
        "sessionid": clean_sessionid,
        "csrftoken": csrftoken,
        "ds_user_id": ds_user_id,
        "ig_cb": "1",
    }
    loader.context.load_session(username, cookie_dict)
    if not _verify_session_via_v1(loader):
        logged_in_user = loader.test_login()
        if not logged_in_user:
            raise RuntimeError(
                "Could not verify login with the provided sessionid. "
                "Make sure you copied the full 'sessionid' cookie value from an active instagram.com tab."
            )

    loader.save_session_to_file(str(session_path))
    print(f"Verified login for @{username} and saved session to {session_path}")


def _complete_two_factor_with_methods(
    loader: instaloader.Instaloader, username: str, two_factor_code: str
) -> bool:
    """Try Instagram's v1 2FA endpoint across WhatsApp (6), Authenticator (3), SMS (1), and Backup (2)."""
    if not loader.context.two_factor_auth_pending:
        return False
    session, user, two_factor_id = loader.context.two_factor_auth_pending
    endpoints = [
        "https://www.instagram.com/api/v1/web/accounts/login/ajax/two_factor/",
        "https://www.instagram.com/accounts/login/ajax/two_factor/",
    ]
    # 6 = WhatsApp, 3 = TOTP Authenticator, 1 = SMS, 2 = Backup Code
    for endpoint in endpoints:
        for method in ("6", "3", "1", "2"):
            resp = session.post(
                endpoint,
                data={
                    "username": user,
                    "verificationCode": two_factor_code,
                    "identifier": two_factor_id,
                    "verification_method": method,
                    "trust_signal": "true",
                },
                allow_redirects=True,
            )
            try:
                resp_json = resp.json()
            except Exception:
                continue
            if resp_json.get("status") == "ok" and resp_json.get("authenticated"):
                csrf = resp.cookies.get("csrftoken") or session.cookies.get_dict().get("csrftoken", "")
                session.headers.update({"X-CSRFToken": csrf})
                loader.context._session = session
                loader.context.username = username
                loader.context.two_factor_auth_pending = None
                return True
    return False


def login_and_save_session(username: str, sessionid: str | None = None) -> None:
    if sessionid:
        login_with_sessionid(username, sessionid)
        return

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
        two_factor_code = input(
            "Enter 2FA code (or an 8-digit Instagram backup code without spaces): "
        ).strip().replace(" ", "")
        if not _complete_two_factor_with_methods(loader, username, two_factor_code):
            loader.two_factor_login(two_factor_code)

    loader.save_session_to_file(str(session_path))
    print(f"Saved Instagram session to {session_path}")


def iter_saved_posts(loader: instaloader.Instaloader, username: str):
    """Yield saved Post objects using Instagram's active v1 API (with GraphQL fallback)."""
    max_id: str | None = None
    used_v1 = False

    while True:
        params: dict[str, str] = {"include_igtv_preview": "false"}
        if max_id:
            params["max_id"] = max_id

        resp = loader.context._session.get(
            "https://www.instagram.com/api/v1/feed/saved/posts/",
            params=params,
            headers={
                "X-IG-App-ID": "936619743392459",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"https://www.instagram.com/{username}/saved/all-posts/",
            },
            timeout=20,
        )
        if resp.status_code != 200:
            break

        data = resp.json()
        if data.get("status") != "ok" or "items" not in data:
            break

        used_v1 = True
        for item in data.get("items", []):
            media = item.get("media")
            if media and "code" in media and "pk" in media:
                yield instaloader.Post.from_iphone_struct(loader.context, media)

        if not data.get("more_available") or not data.get("next_max_id"):
            break
        max_id = str(data["next_max_id"])

    if not used_v1:
        profile = instaloader.Profile.from_username(loader.context, username)
        yield from profile.get_saved_posts()


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

    archived = load_archived_shortcodes()
    newly_downloaded: list[str] = []

    print(f"Fetching saved posts for @{username} (max_count={max_count})...")
    for post in iter_saved_posts(loader, username):
        if post.shortcode in archived:
            if fast_update:
                print(f"Reached already-archived post {post.shortcode}; stopping (--fast-update).")
                break
            print(f"Skipping already-archived post {post.shortcode}.")
            continue

        print(f"Downloading saved post {post.shortcode}...")
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


def unsave_posts(username: str, shortcodes: list[str]) -> list[str]:
    """Unsave verified posts from the user's Instagram Saved collection."""
    if not shortcodes:
        return []

    loader = create_instaloader(DEFAULT_STAGING_DIR)
    session_path = get_session_path(username)
    if session_path.exists():
        loader.load_session_from_file(username, str(session_path))
    else:
        loader.load_session_from_file(username)

    session = loader.context._session

    # Refresh cookies via an authenticated GET and deduplicate cookie domains so
    # Cookie: csrftoken and X-CSRFToken header match 100% on POST requests.
    resp_init = session.get(
        "https://www.instagram.com/api/v1/feed/saved/posts/",
        params={"include_igtv_preview": "false"},
        headers={
            "X-IG-App-ID": "936619743392459",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.instagram.com/",
        },
        timeout=15,
    )
    clean_cookies: dict[str, str] = {}
    for cookie in session.cookies:
        clean_cookies[cookie.name] = cookie.value
    for cookie in resp_init.cookies:
        clean_cookies[cookie.name] = cookie.value

    csrf = clean_cookies.get("csrftoken", "")
    session.cookies.clear()
    session.cookies.update(clean_cookies)
    session.headers.update({"X-CSRFToken": csrf})
    loader.save_session_to_file(str(session_path))

    unsaved_now: list[str] = []
    ds_user_id = clean_cookies.get("ds_user_id") or clean_cookies.get("sessionid", "").split(":")[0]
    sessionid_val = clean_cookies.get("sessionid", "")
    igt_token = base64.b64encode(
        json.dumps(
            {"ds_user_id": str(ds_user_id), "sessionid": sessionid_val},
            separators=(",", ":"),
        ).encode()
    ).decode()

    for shortcode in shortcodes:
        media_id = instaloader.Post.shortcode_to_mediaid(shortcode)
        errors: list[str] = []
        success = False

        # Attempt 1: Web endpoint (/web/save/<media_id>/unsave/)
        web_url = f"https://www.instagram.com/web/save/{media_id}/unsave/"
        resp_web = session.post(
            web_url,
            headers={
                "X-CSRFToken": csrf,
                "X-IG-App-ID": "936619743392459",
                "X-Instagram-AJAX": "1",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": "https://www.instagram.com",
                "Referer": f"https://www.instagram.com/p/{shortcode}/",
            },
            timeout=15,
        )
        if resp_web.status_code == 200:
            try:
                if resp_web.json().get("status") == "ok":
                    success = True
            except Exception:
                pass
        if not success:
            errors.append(f"web={resp_web.status_code}")

        # Attempt 2: Mobile v1 API (i.instagram.com/api/v1/media/<media_id>/unsave/)
        if not success:
            mobile_headers = dict(loader.context.iphone_headers)
            mobile_headers.update(
                {
                    "Authorization": f"Bearer IGT:2:{igt_token}",
                    "ig-intended-user-id": str(ds_user_id),
                    "ig-u-ds-user-id": str(ds_user_id),
                }
            )
            resp_mob = requests.post(
                f"https://i.instagram.com/api/v1/media/{media_id}/unsave/",
                headers=mobile_headers,
                data={"media_id": str(media_id), "module_name": "feed_contextual_saved_collections"},
                timeout=15,
            )
            if resp_mob.status_code == 200:
                try:
                    if resp_mob.json().get("status") == "ok":
                        success = True
                except Exception:
                    pass
            if not success:
                errors.append(f"mobile={resp_mob.status_code}:{resp_mob.text[:120].strip()}")

        if success:
            print(f"Unsaved post {shortcode} from Instagram.")
            unsaved_now.append(shortcode)
        else:
            print(
                f"Warning: could not unsave post {shortcode} ({', '.join(errors)}).",
                file=sys.stderr,
            )

    if unsaved_now:
        save_archived_shortcodes(unsaved_now, UNSAVED_LOG_FILE)
    return unsaved_now


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
        "--sessionid",
        nargs="+",
        help="Instagram 'sessionid' cookie copied from a logged-in browser (bypasses 2FA)",
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
        "--unsave",
        action="store_true",
        help="Remove posts from your Instagram Saved list after Google Drive verification passes",
    )
    parser.add_argument(
        "--reset-archive",
        action="store_true",
        help="Clear the local .archived_shortcodes.txt log before downloading",
    )
    parser.add_argument(
        "--remote",
        default=DEFAULT_REMOTE_DEST,
        help=f"rclone remote destination (default: {DEFAULT_REMOTE_DEST})",
    )
    args = parser.parse_args()

    if args.login_only or args.sessionid:
        raw_sessionid = "".join(args.sessionid) if args.sessionid else None
        login_and_save_session(args.username, sessionid=raw_sessionid)
        if args.login_only:
            return 0

    if args.reset_archive and ARCHIVE_LOG_FILE.exists():
        ARCHIVE_LOG_FILE.unlink()
        print(f"Cleared archive history ({ARCHIVE_LOG_FILE.name}).")

    new_shortcodes = download_saved_posts(
        username=args.username,
        staging_dir=DEFAULT_STAGING_DIR,
        max_count=args.max_count,
        fast_update=not args.no_fast_update,
    )

    has_staged_files = DEFAULT_STAGING_DIR.exists() and any(DEFAULT_STAGING_DIR.iterdir())
    if not has_staged_files:
        if args.unsave and not args.dry_run:
            already_archived = load_archived_shortcodes(ARCHIVE_LOG_FILE)
            already_unsaved = load_archived_shortcodes(UNSAVED_LOG_FILE)
            pending_unsave = [c for c in already_archived if c not in already_unsaved]
            if pending_unsave:
                unsave_posts(args.username, pending_unsave)
                return 0
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

    if args.unsave:
        already_archived = load_archived_shortcodes(ARCHIVE_LOG_FILE)
        already_unsaved = load_archived_shortcodes(UNSAVED_LOG_FILE)
        to_unsave = list(dict.fromkeys(new_shortcodes + [c for c in already_archived if c not in already_unsaved]))
        unsave_posts(args.username, to_unsave)

    if args.clean:
        clean_staging(DEFAULT_STAGING_DIR)

    return 0


if __name__ == "__main__":
    sys.exit(main())
