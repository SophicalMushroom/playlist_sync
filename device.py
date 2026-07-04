"""Device sync: mirror the local library to an external device."""

import hashlib
import json
import os
import shutil
from datetime import datetime

from metadata import MetadataStore
from utils import build_filename, format_prefix

MANIFEST_FILENAME = ".playlist_sync_manifest.json"


def _load_manifest(device_path: str) -> dict:
    manifest_path = os.path.join(device_path, MANIFEST_FILENAME)
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"synced_at": None, "songs": []}


def _save_manifest(device_path: str, songs: list) -> None:
    manifest = {"synced_at": datetime.now().isoformat(), "songs": songs}
    manifest_path = os.path.join(device_path, MANIFEST_FILENAME)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sync_device(store: MetadataStore, device_path: str, dry_run: bool = False) -> dict:
    os.makedirs(device_path, exist_ok=True)
    library_path = store.library_path
    total = store.total_songs()

    manifest = _load_manifest(device_path)

    manifest_by_id: dict[str, dict] = {}
    manifest_by_hash: dict[str, dict] = {}
    for entry in manifest["songs"]:
        if entry.get("youtube_id"):
            manifest_by_id[entry["youtube_id"]] = entry
        if entry.get("file_hash"):
            manifest_by_hash[entry["file_hash"]] = entry

    summary: dict[str, list] = {"copied": [], "renamed": [], "deleted": [], "skipped": []}
    new_manifest_songs: list[dict] = []

    for i, song in enumerate(store.songs):
        prefix = format_prefix(i + 1, total)
        target_filename = build_filename(prefix, song["title"])
        source_path = os.path.join(library_path, target_filename)

        if not os.path.exists(source_path):
            continue

        source_hash = _file_hash(source_path)
        vid_id = song.get("youtube_id")
        existing = manifest_by_id.get(vid_id) if vid_id else None
        if existing is None:
            existing = manifest_by_hash.get(source_hash)

        if existing is not None:
            existing_filename = existing["filename"]
            target_device_path = os.path.join(device_path, target_filename)
            existing_device_path = os.path.join(device_path, existing_filename)

            if existing_filename == target_filename and existing.get("file_hash") == source_hash:
                summary["skipped"].append(target_filename)
            elif existing_filename == target_filename:
                summary["copied"].append(target_filename)
                if not dry_run:
                    shutil.copy2(source_path, target_device_path)
            elif os.path.exists(existing_device_path):
                if existing.get("file_hash") == source_hash:
                    summary["renamed"].append(f"{existing_filename} → {target_filename}")
                    if not dry_run:
                        os.rename(existing_device_path, target_device_path)
                else:
                    summary["copied"].append(target_filename)
                    if not dry_run:
                        os.remove(existing_device_path)
                        shutil.copy2(source_path, target_device_path)
            else:
                summary["copied"].append(target_filename)
                if not dry_run:
                    shutil.copy2(source_path, target_device_path)
        else:
            target_device_path = os.path.join(device_path, target_filename)
            summary["copied"].append(target_filename)
            if not dry_run:
                shutil.copy2(source_path, target_device_path)

        new_manifest_songs.append({"youtube_id": vid_id, "filename": target_filename, "file_hash": source_hash})

    synced_filenames = {s["filename"] for s in new_manifest_songs}
    for old_entry in manifest["songs"]:
        fname = old_entry["filename"]
        if fname not in synced_filenames:
            summary["deleted"].append(fname)
            if not dry_run:
                old_path = os.path.join(device_path, fname)
                if os.path.exists(old_path):
                    os.remove(old_path)

    if not dry_run:
        _save_manifest(device_path, new_manifest_songs)

    return summary
