import argparse
import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mutagen.mp4 import MP4
import piexif


def extract_post_metadata(json_path: Path) -> dict[str, Any]:
    """Parse an Instaloader JSON file into clean fields for VLC / EXIF tagging."""
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    node = raw.get("node", raw)
    iphone = node.get("iphone_struct") or {}

    shortcode = node.get("shortcode") or node.get("code") or iphone.get("code") or ""

    # Extract caption from JSON or fallback to sibling .txt file
    caption = ""
    raw_caption = node.get("caption")
    if isinstance(raw_caption, str):
        caption = raw_caption
    elif isinstance(raw_caption, dict):
        caption = raw_caption.get("text") or ""
    if not caption and isinstance(iphone.get("caption"), dict):
        caption = iphone["caption"].get("text") or ""
    if not caption:
        edges = (node.get("edge_media_to_caption") or {}).get("edges") or []
        if edges and isinstance(edges[0].get("node"), dict):
            caption = edges[0]["node"].get("text") or ""
    if not caption:
        txt_path = json_path.with_suffix(".txt")
        if txt_path.exists():
            caption = txt_path.read_text(encoding="utf-8").strip()

    caption = caption.strip()

    # Extract author info
    user = iphone.get("user") or node.get("owner") or {}
    username = user.get("username") or ""
    full_name = user.get("full_name") or ""
    if username and full_name and full_name.lower() != username.lower():
        artist = f"@{username} ({full_name})"
    elif username:
        artist = f"@{username}"
    else:
        artist = full_name or "Instagram"

    # Extract timestamp
    ts = node.get("date") or node.get("taken_at_timestamp") or iphone.get("taken_at")
    dt_utc: datetime | None = None
    if isinstance(ts, (int, float)):
        dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)

    iso_date = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ") if dt_utc else ""
    exif_date = dt_utc.strftime("%Y:%m:%d %H:%M:%S") if dt_utc else ""

    # Extract likes
    likes = (node.get("edge_media_preview_like") or {}).get("count")
    if likes is None:
        likes = iphone.get("like_count")

    url = f"https://www.instagram.com/p/{shortcode}/" if shortcode else ""

    # Build concise single-line Title so VLC's OSD doesn't wrap across vertical videos
    first_line = next((line.strip() for line in caption.splitlines() if line.strip()), "")
    short_author = f"@{username}" if username else artist
    if first_line:
        raw_title = f"{short_author}: {first_line}" if short_author else first_line
        title = raw_title[:38].rstrip() + ("…" if len(raw_title) > 38 else "")
    else:
        title = f"{short_author} ({shortcode})".strip()

    # Build full comment / description block for VLC (Ctrl+I -> Media Information)
    details: list[str] = []
    if caption:
        details.append(caption)
    meta_footer: list[str] = []
    if artist:
        meta_footer.append(f"Author: {artist}")
    if url:
        meta_footer.append(f"URL: {url}")
    if iso_date:
        meta_footer.append(f"Posted: {iso_date}")
    if likes is not None:
        meta_footer.append(f"Likes: {likes:,}")
    if meta_footer:
        details.append("\n".join(meta_footer))

    comment_block = "\n\n".join(details)

    return {
        "shortcode": shortcode,
        "title": title,
        "artist": artist,
        "album": "Instagram Saved Posts",
        "caption": caption,
        "comment": comment_block,
        "url": url,
        "iso_date": iso_date,
        "exif_date": exif_date,
    }


def embed_mp4_tags(mp4_path: Path, meta: dict[str, Any]) -> None:
    """Write QuickTime/MP4 metadata atoms read by VLC Media Player."""
    video = MP4(str(mp4_path))
    if video.tags is None:
        video.add_tags()

    video.tags["\xa9nam"] = [meta["title"]]
    video.tags["\xa9ART"] = [meta["artist"]]
    video.tags["\xa9alb"] = [meta["album"]]
    if meta["comment"]:
        video.tags["\xa9cmt"] = [meta["comment"]]
        video.tags["desc"] = [meta["comment"]]
    if meta["iso_date"]:
        video.tags["\xa9day"] = [meta["iso_date"]]
    if meta["url"]:
        video.tags["purl"] = [meta["url"]]

    video.save()


def _to_utf16le_bytes(text: str) -> bytes:
    return (text + "\x00").encode("utf-16le")


def embed_jpg_tags(jpg_path: Path, meta: dict[str, Any]) -> None:
    """Write EXIF metadata into a JPEG image."""
    try:
        exif_dict = piexif.load(str(jpg_path))
    except Exception:
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}

    if meta["comment"]:
        exif_dict["0th"][piexif.ImageIFD.ImageDescription] = meta["comment"].encode("utf-8")
        exif_dict["0th"][piexif.ImageIFD.XPComment] = _to_utf16le_bytes(meta["comment"])
    if meta["title"]:
        exif_dict["0th"][piexif.ImageIFD.XPTitle] = _to_utf16le_bytes(meta["title"])
    if meta["artist"]:
        exif_dict["0th"][piexif.ImageIFD.Artist] = meta["artist"].encode("utf-8")
        exif_dict["0th"][piexif.ImageIFD.XPAuthor] = _to_utf16le_bytes(meta["artist"])
    if meta["exif_date"]:
        exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = meta["exif_date"].encode("ascii")

    exif_bytes = piexif.dump(exif_dict)
    piexif.insert(exif_bytes, str(jpg_path))


def tag_directory(directory: Path) -> int:
    """Find all Instaloader .json files in directory and embed their metadata into matching media files."""
    if not directory.exists():
        return 0

    tagged_count = 0
    for json_path in sorted(directory.glob("*.json")):
        try:
            meta = extract_post_metadata(json_path)
        except Exception as err:
            print(f"Skipping {json_path.name}: failed to parse JSON ({err})")
            continue

        stem = json_path.stem
        # Match single media files (<stem>.mp4, <stem>.jpg) and carousel items (<stem>_1.mp4, <stem>_2.jpg, etc.)
        media_files = [
            p
            for p in sorted(directory.glob(f"{stem}*"))
            if p.suffix.lower() in {".mp4", ".jpg", ".jpeg"}
        ]

        for media_path in media_files:
            try:
                if media_path.suffix.lower() == ".mp4":
                    embed_mp4_tags(media_path, meta)
                elif media_path.suffix.lower() in {".jpg", ".jpeg"}:
                    embed_jpg_tags(media_path, meta)
                tagged_count += 1
                print(
                    f"Tagged {media_path.name} -> Title: {meta['title']!r} | Artist: {meta['artist']!r}"
                )
            except Exception as err:
                print(f"Warning: could not tag {media_path.name}: {err}")

    return tagged_count


def retag_remote_directory(remote_dest: str) -> bool:
    """Download existing files from remote_dest, embed metadata into MP4/JPG files, and re-upload."""
    with tempfile.TemporaryDirectory(prefix="ig_retag_") as tmp:
        tmp_dir = Path(tmp)
        print(f"Downloading existing media & JSON from {remote_dest} to temporary workspace...")
        dl = subprocess.run(
            ["rclone", "copy", remote_dest, str(tmp_dir), "-v", "--stats-one-line"],
            capture_output=True,
            text=True,
        )
        if dl.returncode != 0:
            print(f"Failed to download from {remote_dest}:\n{dl.stderr}")
            return False

        count = tag_directory(tmp_dir)
        if count == 0:
            print("No matching media files found to tag.")
            return True

        print(f"Uploading {count} newly tagged media file(s) back to {remote_dest}...")
        ul = subprocess.run(
            [
                "rclone",
                "copy",
                str(tmp_dir),
                remote_dest,
                "--include",
                "*.{jpg,jpeg,mp4}",
                "-v",
                "--stats-one-line",
            ],
            capture_output=True,
            text=True,
        )
        if ul.returncode != 0:
            print(f"Failed to upload tagged files:\n{ul.stderr}")
            return False

        print(f"Successfully updated tags on {count} remote file(s) in {remote_dest}!")
        return True


def clear_remote_tags_and_json(remote_dest: str) -> bool:
    """Strip embedded tags from MP4/JPG files on remote_dest and remove .json/.txt sidecars."""
    with tempfile.TemporaryDirectory(prefix="ig_clear_") as tmp:
        tmp_dir = Path(tmp)
        print(f"Downloading media from {remote_dest} to strip embedded tags...")
        dl = subprocess.run(
            [
                "rclone",
                "copy",
                remote_dest,
                str(tmp_dir),
                "--include",
                "*.{jpg,jpeg,mp4}",
                "-v",
                "--stats-one-line",
            ],
            capture_output=True,
            text=True,
        )
        if dl.returncode != 0:
            print(f"Failed to download from {remote_dest}:\n{dl.stderr}")
            return False

        cleared = 0
        for media_path in sorted(tmp_dir.iterdir()):
            if media_path.suffix.lower() == ".mp4":
                video = MP4(str(media_path))
                video.delete()
                video.save()
                cleared += 1
                print(f"Cleared embedded tags from {media_path.name}")
            elif media_path.suffix.lower() in {".jpg", ".jpeg"}:
                piexif.remove(str(media_path))
                cleared += 1
                print(f"Cleared embedded EXIF from {media_path.name}")

        if cleared:
            print(f"Uploading {cleared} clean media file(s) back to {remote_dest}...")
            ul = subprocess.run(
                [
                    "rclone",
                    "copy",
                    str(tmp_dir),
                    remote_dest,
                    "--include",
                    "*.{jpg,jpeg,mp4}",
                    "-v",
                    "--stats-one-line",
                ],
                capture_output=True,
                text=True,
            )
            if ul.returncode != 0:
                print(f"Failed to upload clean files:\n{ul.stderr}")
                return False

        print(f"Removing .json and .txt sidecar files from {remote_dest}...")
        subprocess.run(
            ["rclone", "delete", remote_dest, "--include", "*.{json,txt}"],
            capture_output=True,
            text=True,
        )
        print(f"Done! {remote_dest} now contains only clean media files.")
        return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Embed or clear Instaloader JSON metadata on MP4 and JPG files."
    )
    parser.add_argument(
        "-d",
        "--dir",
        type=Path,
        default=Path(__file__).parent / "staging",
        help="Local directory containing .json and .mp4/.jpg files (default: ./staging)",
    )
    parser.add_argument(
        "--retag-remote",
        nargs="?",
        const="gdrive:rclone/save",
        help="Download existing files from rclone remote, tag MP4/JPGs, and re-upload (default: gdrive:rclone/save)",
    )
    parser.add_argument(
        "--clear-remote",
        nargs="?",
        const="gdrive:rclone/save",
        help="Strip embedded tags and delete .json/.txt sidecar files on remote (default: gdrive:rclone/save)",
    )
    args = parser.parse_args()

    if args.clear_remote:
        raise SystemExit(0 if clear_remote_tags_and_json(args.clear_remote) else 1)

    if args.retag_remote:
        raise SystemExit(0 if retag_remote_directory(args.retag_remote) else 1)

    total = tag_directory(args.dir)
    print(f"Tagged {total} media file(s) in {args.dir}.")
