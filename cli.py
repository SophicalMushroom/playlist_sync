"""CLI entry point for playlist-sync."""

import os
import re
import shutil
from datetime import date

import click

from device import sync_device
from download import fetch_playlist
from metadata import MetadataStore
from sync import renumber_files, run_sync
from utils import build_filename, clean_filename, format_prefix


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
def import_library(playlist_url: str, library: str) -> None:
    """Register an existing MP3 folder without re-downloading anything.

    Fetches the playlist from YouTube, matches existing files by title,
    and registers unmatched files as manual songs.  Run 'sync' afterwards
    to download any playlist songs that are missing from the folder.
    """
    library = os.path.abspath(library)
    if not os.path.isdir(library):
        raise click.ClickException(f"Directory not found: {library}")

    try:
        store = MetadataStore.create(playlist_url, library)
    except FileExistsError as exc:
        raise click.ClickException(str(exc))

    click.echo("Fetching playlist from YouTube\u2026")
    playlist_entries = fetch_playlist(store.playlist_url)
    playlist_entries.reverse()  # oldest first, matching internal convention

    # Scan the library for existing MP3s, stripping any numeric prefix to get the bare title.
    prefix_re = re.compile(r"^\d+ - (.+)\.mp3$")
    bare_re = re.compile(r"^(.+)\.mp3$")
    existing_titles: dict[str, str] = {}  # lowercase clean_title -> original filename
    existing_original_titles: dict[str, str] = {}  # lowercase -> original-case title
    for fname in sorted(os.listdir(library)):
        m = prefix_re.match(fname) or bare_re.match(fname)
        if m:
            title = clean_filename(m.group(1))
            key = title.lower()
            if key in existing_titles:
                click.echo(f"  Warning: duplicate title '{title}' from '{fname}' (already from '{existing_titles[key]}')")
                continue
            existing_titles[key] = fname
            existing_original_titles[key] = title

    # Build song list in playlist order, noting which titles are already on disk.
    songs: list[dict] = []
    matched_titles: set[str] = set()
    for entry in playlist_entries:
        clean_title = clean_filename(entry["title"])
        songs.append({
            "youtube_id": entry["id"],
            "title": clean_title,
            "source": "youtube",
            "added_date": date.today().isoformat(),
        })
        if clean_title.lower() in existing_titles:
            matched_titles.add(clean_title.lower())

    # Files not matched to any playlist entry are registered as manual songs.
    for key, _fname in existing_titles.items():
        if key not in matched_titles:
            songs.append({
                "youtube_id": None,
                "title": existing_original_titles[key],
                "source": "manual",
                "added_date": date.today().isoformat(),
            })

    store.replace_songs(songs)
    renumber_files(library, songs)
    store.save()

    manual_count = sum(1 for s in songs if s["source"] == "manual")
    missing = [s["title"] for s in songs if s["source"] == "youtube" and s["title"].lower() not in matched_titles]

    click.echo(f"Matched  {len(matched_titles)} existing file(s) to playlist entries.")
    if manual_count:
        click.echo(f"Imported {manual_count} manual song(s) (not in playlist).")
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
@click.option("--position", "-p", type=int, default=None, help="1-indexed position to insert at (1 = bottom of list). Defaults to top.")
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
