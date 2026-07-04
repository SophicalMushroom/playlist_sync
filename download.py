"""Wrappers around the yt-dlp command-line tool.

yt-dlp is listed as a Python dependency in pyproject.toml, so it is
installed automatically alongside this package and available on PATH.
FFmpeg must be installed separately for the audio-extraction step.
"""

import json
import os
import subprocess


def fetch_playlist(url: str) -> list[dict]:
    """Retrieve every entry in a YouTube playlist via yt-dlp."""
    result = subprocess.run(
        [
            "yt-dlp", 
            "--flat-playlist", 
            "-J", 
            "--cookies-from-browser", "firefox",
            "--extractor-args", "youtube:player-client=android,web",
            "--playlist-items", "1-2000",
            url
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)

    entries = []
    for item in data.get("entries", []):
        vid_id = item.get("id")
        title = item.get("title", "")
        if not vid_id or title in ("[Deleted video]", "[Private video]"):
            continue
        entries.append({"id": vid_id, "title": title})

    print(f"Found {len(entries)} entries from {url}")
    return entries

def download_song(video_id: str, output_dir: str) -> str:
    """Download a single YouTube video as an MP3 into *output_dir*."""
    os.makedirs(output_dir, exist_ok=True)
    output_template = os.path.join(output_dir, "%(id)s.%(ext)s")
    url = f"https://www.youtube.com/watch?v={video_id}"

    result = subprocess.run(
        [
            "yt-dlp",
            "--output", output_template,
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "--no-playlist",
            "--cookies-from-browser", "firefox",
            "--extractor-args", "youtube:player-client=android,web",
            "--sleep-requests", "1",
            "--sleep-interval", "2",
            url,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed for video '{video_id}':\n{result.stderr.strip()}")

    downloaded = os.path.join(output_dir, f"{video_id}.mp3")
    if not os.path.exists(downloaded):
        raise FileNotFoundError(f"Expected yt-dlp output not found after download: {downloaded}")

    return downloaded
