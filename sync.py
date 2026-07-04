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
    old_songs = list(store.songs)

    print("Fetching playlist from YouTube...")
    playlist_entries = fetch_playlist(store.playlist_url)
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
    final_songs = _reinsert_inert_songs(inert_songs, yt_ordered, old_songs)

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


def _reinsert_inert_songs(
    inert_songs: list, yt_ordered: list, old_songs: list
) -> list:
    """Re-insert manual/orphaned songs anchored behind their preceding YouTube song.

    For each inert song, we find the closest YouTube song that appeared before it
    in the old list and place it right after that song in the new ordering.
    If no YouTube song preceded it, it goes at the beginning.
    """
    if not inert_songs:
        return list(yt_ordered)

    # Build a position lookup for yt songs in the new order (by youtube_id)
    new_pos_by_id: dict[str, int] = {}
    for idx, song in enumerate(yt_ordered):
        vid = song.get("youtube_id")
        if vid:
            new_pos_by_id[vid] = idx

    # For each inert song, find its anchor (preceding YouTube song in old list)
    anchored: list[tuple[int | None, int, dict]] = []
    for old_idx, song in sorted(inert_songs, key=lambda x: x[0]):
        anchor_new_pos = None
        # Walk backwards from old_idx to find the first youtube song
        for j in range(old_idx - 1, -1, -1):
            prev = old_songs[j]
            vid = prev.get("youtube_id")
            if vid and prev["source"] == "youtube" and vid in new_pos_by_id:
                anchor_new_pos = new_pos_by_id[vid]
                break
        anchored.append((anchor_new_pos, old_idx, song))

    # Insert from last to first so earlier inserts don't shift later positions
    result = list(yt_ordered)
    # Group by anchor and preserve relative order within the same anchor
    # Sort by anchor position (None = -1 meaning beginning), then by old_idx
    anchored.sort(key=lambda x: (x[0] if x[0] is not None else -1, x[1]))

    # Insert in reverse so indices stay stable
    for anchor_pos, _, song in reversed(anchored):
        insert_at = (anchor_pos + 1) if anchor_pos is not None else 0
        result.insert(insert_at, song)

    return result
