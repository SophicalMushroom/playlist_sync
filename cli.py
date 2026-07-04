"""CLI entry point for playlist-sync."""

import difflib
import os
import re
import shutil
from datetime import date

import click

from device import sync_device
from download import fetch_playlist
from metadata import MetadataStore
from sync import renumber_files, run_sync
from utils import build_filename, clean_filename, format_prefix, normalize_for_matching, read_mp3_duration


def _load_store() -> MetadataStore:
    try:
        return MetadataStore.load_from_cwd()
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc))


def _print_section(header: str, items: list) -> None:
    if items:
        click.echo(f"\n{header} ({len(items)}):")
        for item in items:
            click.echo(f"  {item}")


@click.group()
def cli() -> None:
    """playlist-sync — manage a local MP3 library synced from a YouTube playlist."""


@cli.command("import")
@click.option("--playlist-url", required=True, help="Full URL of the YouTube playlist to track.")
@click.option("--library", required=True, type=click.Path(), help="Path to an existing local directory that already contains MP3 files.")
@click.option("--similarity-threshold", default=0.85, show_default=True,
              help="Minimum similarity score (0-1) for fuzzy title matching. Lower = more lenient.")
@click.option("--yes", "-y", is_flag=True,
              help="Auto-accept all similarity matches without prompting.")
@click.option("--duration-tolerance", default=5.0, show_default=True,
              help="Maximum allowed difference in seconds between local and YouTube "
                   "durations before a similarity match is rejected.")
def import_library(playlist_url: str, library: str, similarity_threshold: float, yes: bool, duration_tolerance: float) -> None:
    """Register an existing MP3 folder without re-downloading anything.

    Fetches the playlist from YouTube, matches existing files by title,
    and registers unmatched files as manual songs.  Run 'sync' afterwards
    to download any playlist songs that are missing from the folder.

    Matching is attempted in three passes (each progressively more lenient):
      1. Exact match after clean_filename normalisation.
      2. Alphanumeric-only match — handles apostrophes, '&', punctuation
         differences between old downloaders and the current one.
      3. Similarity match — handles YouTube title changes such as
         'Official Music Video' becoming 'Official Video'.
         Each candidate is shown for confirmation unless --yes is given.
    """
    library = os.path.abspath(library)
    if not os.path.isdir(library):
        raise click.ClickException(f"Directory not found: {library}")

    try:
        store = MetadataStore.create(playlist_url, library)
    except FileExistsError as exc:
        raise click.ClickException(str(exc))

    click.echo("Fetching playlist from YouTube…")
    playlist_entries = fetch_playlist(store.playlist_url)
    playlist_entries.reverse()  # bottom of playlist → index 0 → prefix 00001

    # --- Scan local library ---
    prefix_re = re.compile(r"^\d+ - (.+)\.mp3$")
    bare_re   = re.compile(r"^(.+)\.mp3$")

    # exact_titles[clean_title.lower()]           → (filename, title)
    # fuzzy_titles[normalize_for_matching(title)] → (filename, title)
    exact_titles: dict[str, tuple[str, str]] = {}
    fuzzy_titles: dict[str, tuple[str, str]] = {}

    for fname in sorted(os.listdir(library)):
        m = prefix_re.match(fname) or bare_re.match(fname)
        if not m:
            continue
        title = clean_filename(m.group(1))
        exact_key = title.lower()
        fuzzy_key = normalize_for_matching(title)
        if exact_key not in exact_titles:
            exact_titles[exact_key] = (fname, title)
        if fuzzy_key and fuzzy_key not in fuzzy_titles:
            fuzzy_titles[fuzzy_key] = (fname, title)

    # --- Pass 1 and Pass 2 (automatic — no confirmation needed) ---
    yt_to_local: dict[str, str] = {}       # youtube_id → local title used in metadata
    matched_local_keys: set[str] = set()   # exact_titles keys consumed by any pass

    for entry in playlist_entries:
        clean_title = clean_filename(entry["title"])
        exact_key   = clean_title.lower()
        fuzzy_key   = normalize_for_matching(clean_title)

        # Pass 1 — exact title match
        if exact_key in exact_titles and exact_key not in matched_local_keys:
            matched_local_keys.add(exact_key)
            yt_to_local[entry["id"]] = exact_titles[exact_key][1]

        # Pass 2 — alphanumeric-only (catches apostrophes, & etc.)
        elif fuzzy_key and fuzzy_key in fuzzy_titles:
            _f, local_title = fuzzy_titles[fuzzy_key]
            local_exact_key = local_title.lower()
            if local_exact_key not in matched_local_keys:
                matched_local_keys.add(local_exact_key)
                yt_to_local[entry["id"]] = local_title

    # --- Pass 3 — similarity matching (requires user confirmation) ---
    # Duration from YouTube and mutagen is used to confirm or veto candidates:
    #   Both present, differ > --duration-tolerance → reject outright (wrong song)
    #   Both present, within tolerance              → confirmed, show in duration note
    #   Either unavailable                          → title similarity only
    paren_re = re.compile(r"\([^)]*\)")   # strips parenthetical e.g. "(Official Video)"

    unmatched_local = {
        k: v for k, v in exact_titles.items() if k not in matched_local_keys
    }

    # Pre-read local MP3 durations (mutagen, ~ms each)
    local_durations: dict[str, float | None] = {
        k: read_mp3_duration(os.path.join(library, fname))
        for k, (fname, _title) in unmatched_local.items()
    }

    # Collect every candidate (vid_id, yt_title, local_key, local_title, score, dur_note).
    # We scan all unmatched pairs without committing so we can sort by score and let
    # the best match claim each local file first.
    Candidate = tuple  # (score, vid_id, yt_title, local_key, local_title, dur_note)
    candidates: list[Candidate] = []

    for entry in playlist_entries:
        if entry["id"] in yt_to_local:
            continue

        clean_title  = clean_filename(entry["title"])
        # Strip parentheticals before comparing so "(Official Music Video)" vs
        # "(Official Video)" doesn't dominate the score
        fuzzy_needle = normalize_for_matching(paren_re.sub("", clean_title).strip())
        yt_duration  = entry.get("duration")
        if not fuzzy_needle:
            continue

        best_ratio       = 0.0
        best_local_key   = None
        best_local_title = None
        best_dur_note    = ""

        for local_key, (_fname, local_title) in unmatched_local.items():
            fuzzy_candidate = normalize_for_matching(paren_re.sub("", local_title).strip())
            ratio = difflib.SequenceMatcher(None, fuzzy_needle, fuzzy_candidate).ratio()
            if ratio <= best_ratio:
                continue

            local_dur = local_durations.get(local_key)
            if yt_duration is not None and local_dur is not None:
                if abs(yt_duration - local_dur) > duration_tolerance:
                    continue  # duration mismatch — definitely not the same song
                dur_note = (
                    f"{int(local_dur//60)}:{int(local_dur%60):02d}"
                    f" ≈ {int(yt_duration//60)}:{int(yt_duration%60):02d}"
                )
            else:
                dur_note = "no duration"

            best_ratio       = ratio
            best_local_key   = local_key
            best_local_title = local_title
            best_dur_note    = dur_note

        if best_ratio >= similarity_threshold and best_local_key is not None:
            candidates.append((best_ratio, entry["id"], clean_title,
                               best_local_key, best_local_title, best_dur_note))

    # Sort best scores first so that if two YouTube entries want the same local
    # file, the higher-confidence match gets to claim it first.
    candidates.sort(key=lambda c: c[0], reverse=True)

    # Apply candidates — auto-accept with --yes, otherwise prompt per candidate
    similarity_matches: list[tuple[str, str, float, str]] = []  # accepted matches for summary
    claimed_local_keys: set[str] = set()

    if candidates and not yes:
        click.echo(f"\nFound {len(candidates)} potential similarity match(es). Please review each:")

    for score, vid_id, yt_title, local_key, local_title, dur_note in candidates:
        if local_key in claimed_local_keys:
            continue  # already claimed by a better-scoring YouTube entry

        if yes:
            accepted = True
        else:
            click.echo(
                f"\n  Score: {score:.2f}  |  Duration: {dur_note}\n"
                f"  YouTube : {yt_title}\n"
                f"  Local   : {local_title}"
            )
            accepted = click.confirm("  Accept this match?", default=True)

        if accepted:
            claimed_local_keys.add(local_key)
            matched_local_keys.add(local_key)
            yt_to_local[vid_id] = local_title
            similarity_matches.append((yt_title, local_title, score, dur_note))

    # --- Build ordered song list ---
    # YouTube songs first, in playlist order.
    yt_songs: list[dict] = []
    yt_pos_by_id: dict[str, int] = {}
    for entry in playlist_entries:
        # Use the local file's title if matched; otherwise use the clean YouTube title
        title = yt_to_local.get(entry["id"], clean_filename(entry["title"]))
        yt_pos_by_id[entry["id"]] = len(yt_songs)
        yt_songs.append({
            "youtube_id": entry["id"],
            "title": title,
            "source": "youtube",
            "added_date": date.today().isoformat(),
        })

    # Map matched local title → youtube_id so we can locate anchors on disk.
    local_key_to_yt_id: dict[str, str] = {
        local_title.lower(): vid_id for vid_id, local_title in yt_to_local.items()
    }

    # Walk local files in on-disk order. Each unmatched (manual) file is anchored
    # behind the most recent matched YouTube song so its relative position is kept.
    manual_anchored: list[tuple[int | None, int, dict]] = []
    last_anchor_pos: int | None = None
    for disk_idx, (exact_key, (_fname, title)) in enumerate(exact_titles.items()):
        if exact_key in local_key_to_yt_id:
            last_anchor_pos = yt_pos_by_id.get(
                local_key_to_yt_id[exact_key], last_anchor_pos
            )
        elif exact_key not in matched_local_keys:
            manual_anchored.append((last_anchor_pos, disk_idx, {
                "youtube_id": None,
                "title": title,
                "source": "manual",
                "added_date": date.today().isoformat(),
            }))

    songs = list(yt_songs)
    # Insert from highest anchor to lowest so earlier inserts don't shift later ones.
    manual_anchored.sort(key=lambda x: (x[0] if x[0] is not None else -1, x[1]))
    for anchor_pos, _, song in reversed(manual_anchored):
        insert_at = (anchor_pos + 1) if anchor_pos is not None else 0
        songs.insert(insert_at, song)

    # Exclude unmatched YouTube songs (no local file) so a subsequent `sync`
    # detects them as new and downloads them.
    songs_to_save = [
        s for s in songs
        if s["source"] != "youtube" or s.get("youtube_id") in yt_to_local
    ]

    store.replace_songs(songs_to_save)
    renumber_files(library, songs_to_save)
    store.save()

    # --- Summary output ---
    manual_count  = sum(1 for s in songs_to_save if s["source"] == "manual")
    matched_count = len(matched_local_keys)
    missing = [
        s["title"] for s in songs
        if s["source"] == "youtube" and s["youtube_id"] not in yt_to_local
    ]

    click.echo(f"\nMatched  {matched_count} existing file(s) to playlist entries.")
    if manual_count:
        click.echo(f"Imported {manual_count} manual song(s) (not in playlist).")

    if similarity_matches:
        click.echo(f"\nSimilarity-matched and accepted ({len(similarity_matches)}):")
        for yt_title, local_title, score, dur_note in similarity_matches:
            click.echo(f"  [{score:.2f}, {dur_note}] '{yt_title}'")
            click.echo(f"         → '{local_title}'")

    if missing:
        click.echo(f"\n{len(missing)} playlist song(s) not found on disk:")
        for title in missing:
            click.echo(f"  {title}")
        click.echo("\nRun 'playlist-sync sync' to download the missing songs.")
    else:
        click.echo("\nAll playlist songs are present. Run 'playlist-sync sync' to stay up to date.")


@cli.command()
@click.option("--playlist-url", required=True, help="Full URL of the YouTube playlist to track.")
@click.option("--library", required=True, type=click.Path(), help="Path to the local directory where MP3 files will be stored.")
def init(playlist_url: str, library: str) -> None:
    library = os.path.abspath(library)
    os.makedirs(library, exist_ok=True)

    try:
        store = MetadataStore.create(playlist_url, library)
    except FileExistsError as exc:
        raise click.ClickException(str(exc))

    click.echo(f"Created metadata.json  (library: {library})")
    click.echo("Starting initial download — this may take a while…")

    summary = run_sync(store)
    _print_section("Downloaded", summary["added"])
    if summary["errors"]:
        _print_section("Errors", summary["errors"])
    click.echo(f"\nDone. {len(summary['added'])} songs downloaded.")


@cli.command()
@click.option("--dry-run", is_flag=True, help="Show what would change without making any modifications.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt and apply changes immediately.")
def sync(dry_run: bool, yes: bool) -> None:
    store = _load_store()
    if dry_run:
        click.echo("[Dry run — no changes will be made]\n")
        summary = run_sync(store, dry_run=True)
        _print_section("Will add", summary["added"])
        _print_section("Will orphan (removed from playlist)", summary["orphaned"])
        if not any(summary.values()):
            click.echo("Already up to date.")
        return

    # Preview changes first
    summary = run_sync(store, dry_run=True)
    _print_section("Will add", summary["added"])
    _print_section("Will orphan (removed from playlist)", summary["orphaned"])

    if not any(summary.values()):
        click.echo("Already up to date.")
        return

    if not yes:
        click.echo()
        click.confirm("Apply these changes?", abort=True)

    click.echo("\nApplying changes…")
    summary = run_sync(store, dry_run=False)
    _print_section("Added", summary["added"])
    _print_section("Orphaned (removed from playlist)", summary["orphaned"])
    if summary["errors"]:
        _print_section("Errors", summary["errors"])


@cli.command("add")
@click.argument("file", type=click.Path(exists=True))
@click.option("--position", "-p", type=int, default=None, help="1-indexed position to insert at (1 = first entry / lowest prefix number). Defaults to the end.")
def add_song(file: str, position: int | None) -> None:
    store = _load_store()
    stem = os.path.splitext(os.path.basename(file))[0]
    title = clean_filename(stem)
    if not title:
        raise click.ClickException("Could not derive a valid title from the filename.")
    if store.get_by_title(title):
        raise click.ClickException(f"A song named '{title}' already exists in the library.")

    dest = os.path.join(store.library_path, f"{title}.mp3")
    if not os.path.exists(dest):
        shutil.copy2(file, dest)
        click.echo(f"Copied:  {os.path.basename(dest)}")

    store.add_song(title, youtube_id=None, source="manual", position=position)
    renumber_files(store.library_path, store.songs)
    store.save()

    idx = next(i for i, s in enumerate(store.songs) if s["title"] == title)
    prefix = format_prefix(idx + 1, store.total_songs())
    click.echo(f"Added '{title}' at position {idx + 1}  (prefix {prefix}).")


@cli.command("remove")
@click.argument("title_or_position")
def remove_song(title_or_position: str) -> None:
    store = _load_store()
    song: dict | None = None

    try:
        pos = int(title_or_position)
        song = store.get_by_position(pos)
        if song is None:
            raise click.ClickException(
                f"No song at position {pos} (library has {store.total_songs()} songs)."
            )
    except ValueError:
        needle = title_or_position.lower()
        matches = [s for s in store.songs if needle in s["title"].lower()]
        if not matches:
            raise click.ClickException(f"No song found matching '{title_or_position}'.")
        if len(matches) > 1:
            listing = "\n".join(f"  {s['title']}" for s in matches)
            raise click.ClickException(f"Multiple matches — be more specific:\n{listing}")
        song = matches[0]

    title = song["title"]
    idx = store.songs.index(song)
    prefix = format_prefix(idx + 1, store.total_songs())
    filename = build_filename(prefix, title)
    filepath = os.path.join(store.library_path, filename)

    click.confirm(f"Delete '{filename}'?", abort=True)

    if os.path.exists(filepath):
        os.remove(filepath)
        click.echo(f"Deleted:  {filename}")
    else:
        click.echo(f"File not found on disk (removing from metadata only): {filename}")

    store.remove_song(song)
    renumber_files(store.library_path, store.songs)
    store.save()
    click.echo(f"Removed '{title}'. Library now has {store.total_songs()} songs.")


@cli.command()
def status() -> None:
    store = _load_store()
    songs = store.songs

    youtube_count = sum(1 for s in songs if s["source"] == "youtube")
    manual_count = sum(1 for s in songs if s["source"] == "manual")
    orphaned_count = sum(1 for s in songs if s["source"] == "orphaned")

    click.echo(f"Playlist URL : {store.playlist_url}")
    click.echo(f"Library path : {store.library_path}")
    click.echo(
        f"Songs        : {len(songs)} total  "
        f"({youtube_count} YouTube, {manual_count} manual, {orphaned_count} orphaned)"
    )

    if store.devices:
        click.echo("Devices:")
        for name, path in store.devices.items():
            click.echo(f"  {name:15s}  →  {path}")
    else:
        click.echo("Devices      : none registered")


@cli.group()
def device() -> None:
    """Manage named external devices (phone, USB drive, etc.)."""


@device.command("sync")
@click.argument("name_or_path")
@click.option("--dry-run", is_flag=True, help="Show what would change without making any modifications.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt and apply changes immediately.")
def device_sync_cmd(name_or_path: str, dry_run: bool, yes: bool) -> None:
    store = _load_store()
    if name_or_path in store.devices:
        device_path = store.devices[name_or_path]
        click.echo(f"Device '{name_or_path}' → {device_path}")
    else:
        device_path = name_or_path

    device_path = os.path.abspath(device_path)
    click.echo(f"Syncing to: {device_path}")

    if dry_run:
        click.echo("[Dry run — no changes will be made]\n")
        summary = sync_device(store, device_path, dry_run=True)
        _print_section("Will copy", summary["copied"])
        _print_section("Will rename (prefix changed)", summary["renamed"])
        _print_section("Will delete", summary["deleted"])
        click.echo(f"\n{len(summary['skipped'])} file(s) already up to date.")
        return

    # Preview changes first
    summary = sync_device(store, device_path, dry_run=True)
    _print_section("Will copy", summary["copied"])
    _print_section("Will rename (prefix changed)", summary["renamed"])
    _print_section("Will delete", summary["deleted"])
    click.echo(f"\n{len(summary['skipped'])} file(s) already up to date.")

    has_changes = summary["copied"] or summary["renamed"] or summary["deleted"]
    if not has_changes:
        return

    if not yes:
        click.echo()
        click.confirm("Apply these changes?", abort=True)

    click.echo("\nApplying changes…")
    sync_device(store, device_path, dry_run=False)
    click.echo("Done.")


@device.command("add")
@click.argument("name")
@click.argument("path")
def device_add(name: str, path: str) -> None:
    store = _load_store()
    if name in store.devices:
        raise click.ClickException(f"Device '{name}' is already registered at {store.devices[name]}.")
    store.add_device(name, os.path.abspath(path))
    store.save()
    click.echo(f"Registered '{name}'  →  {store.devices[name]}")


@device.command("remove")
@click.argument("name")
def device_remove(name: str) -> None:
    store = _load_store()
    try:
        store.remove_device(name)
    except KeyError as exc:
        raise click.ClickException(str(exc))
    store.save()
    click.echo(f"Removed device '{name}'.")


if __name__ == "__main__":
    cli()
