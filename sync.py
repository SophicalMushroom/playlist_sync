"""Core sync algorithm: reconcile the local library with the YouTube playlist."""

import os
import re
from datetime import date

from download import download_song, fetch_playlist
from metadata import MetadataStore
from utils import build_filename, clean_filename, format_prefix


def run_sync(store: MetadataStore, dry_run: bool = False) -> dict:
    """Sync the local library against the current YouTube playlist."""
    library_path = store.library_path

    inert_songs = [
        (i, s)
        for i, s in enumerate(store.songs)
        if s["source"] in ("manual", "orphaned")
    ]
    old_total = len(store.songs)

    print("Fetching playlist from YouTube...")
    playlist_entries = fetch_playlist(store.playlist_url)
    playlist_entries.reverse()
    playlist_ids = [e["id"] for e in playlist_entries]
    playlist_id_set = set(playlist_ids)
    playlist_by_id = {e["id"]: e for e in playlist_entries}

    current_yt_by_id = {
        s["youtube_id"]: s
        for s in store.songs
        if s.get("youtube_id") and s["source"] == "youtube"
    }

    new_ids = [vid for vid in playlist_ids if vid not in current_yt_by_id]
    removed_ids = {vid for vid in current_yt_by_id if vid not in playlist_id_set}

    summary: dict[str, list] = {"added": [], "orphaned": [], "errors": []}

    for vid in removed_ids:
        song = current_yt_by_id[vid]
        summary["orphaned"].append(song["title"])
        if not dry_run:
            store.mark_orphaned(song)
            print(f"  Orphaned (removed from playlist): {song['title']}")

    new_song_dicts: dict[str, dict] = {}

    # Build map of previously orphaned songs that can be reactivated
    orphaned_by_id: dict[str, tuple[int, dict]] = {}
    for idx, song in inert_songs:
        vid_id = song.get("youtube_id")
        if vid_id:
            orphaned_by_id[vid_id] = (idx, song)

    reactivated_indices: set[int] = set()

    for vid in new_ids:
        yt_title = playlist_by_id[vid]["title"]
        clean_title = clean_filename(yt_title)

        # Reactivate previously orphaned song instead of re-downloading
        if vid in orphaned_by_id:
            old_idx, orphaned_song = orphaned_by_id[vid]
            orphaned_song["source"] = "youtube"
            orphaned_song["title"] = clean_title
            new_song_dicts[vid] = orphaned_song
            reactivated_indices.add(old_idx)
            summary["added"].append(clean_title)
            if not dry_run:
                print(f"  Reactivated: {clean_title}")
            continue

        summary["added"].append(clean_title)

        if not dry_run:
            print(f"  Downloading: {clean_title}")
            try:
                temp_path = download_song(vid, library_path)
                bare_path = os.path.join(library_path, f"{clean_title}.mp3")
                if temp_path != bare_path:
                    if os.path.exists(bare_path):
                        os.remove(bare_path)
                    os.rename(temp_path, bare_path)
            except Exception as exc:
                summary["errors"].append(f"{clean_title}: {exc}")
                print(f"  ERROR: {exc}")
                continue

        new_song_dicts[vid] = {
            "youtube_id": vid,
            "title": clean_title,
            "source": "youtube",
            "added_date": date.today().isoformat(),
        }

    yt_ordered: list[dict] = []
    for vid in playlist_ids:
        if vid in current_yt_by_id:
            yt_ordered.append(current_yt_by_id[vid])
        elif vid in new_song_dicts:
            yt_ordered.append(new_song_dicts[vid])

    for vid in removed_ids:
        yt_ordered.append(current_yt_by_id[vid])

    # Remove reactivated songs from inert list to avoid duplicates
    inert_songs = [(i, s) for i, s in inert_songs if i not in reactivated_indices]
    final_songs = _reinsert_inert_songs(inert_songs, yt_ordered, old_total)

    if not dry_run:
        renumber_files(library_path, final_songs)
        store.replace_songs(final_songs)
        store.save()

    return summary


def renumber_files(library_path: str, songs: list) -> None:
    """Rename every tracked MP3 in *library_path* to match the new ordering."""
    total = len(songs)
    temp_map: dict[int, str] = {}

    for i, song in enumerate(songs):
        current_fname = _find_file(library_path, song["title"])
        if current_fname is None:
            continue
        temp_name = f"__sync_temp_{i:05d}__.mp3"
        os.rename(os.path.join(library_path, current_fname), os.path.join(library_path, temp_name))
        temp_map[i] = temp_name

    for i, song in enumerate(songs):
        temp_name = temp_map.get(i)
        if temp_name is None:
            continue
        prefix = format_prefix(i + 1, total)
        final_name = build_filename(prefix, song["title"])
        os.rename(os.path.join(library_path, temp_name), os.path.join(library_path, final_name))


def _find_file(library_path: str, title: str) -> str | None:
    pattern = re.compile(r"^(\d+ - )?" + re.escape(title) + r"\.mp3$")
    bare_match = None
    for fname in sorted(os.listdir(library_path)):
        if pattern.match(fname):
            if re.match(r"^\d+ - ", fname):
                return fname  # Prefer prefixed (managed) files
            bare_match = fname
    return bare_match


def _reinsert_inert_songs(inert_songs: list, yt_ordered: list, old_total: int) -> list:
    result = list(yt_ordered)
    if not inert_songs:
        return result
    if old_total == 0:
        result.extend(song for _, song in inert_songs)
        return result

    final_size = len(yt_ordered) + len(inert_songs)
    # Pre-calculate target positions based on final list size
    placements = []
    for old_idx, song in sorted(inert_songs, key=lambda x: x[0]):
        new_idx = round(old_idx / old_total * final_size)
        new_idx = max(0, min(new_idx, len(yt_ordered)))
        placements.append((new_idx, old_idx, song))

    # Insert from highest to lowest target index to avoid position shifting
    for new_idx, _, song in sorted(placements, key=lambda x: (-x[0], -x[1])):
        result.insert(new_idx, song)
    return result
