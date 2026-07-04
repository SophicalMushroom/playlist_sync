"""MetadataStore — load, query, and persist the metadata.json config file.

Schema
------
{
  "playlist_url": "https://www.youtube.com/playlist?list=...",
  "library_path": "C:/Users/.../Songs",
  "devices": {
    "phone":   "D:/Music",
    "car-usb": "E:/Music"
  },
  "songs": [
    {
      "youtube_id": "dQw4w9WgXcQ",   // null for manual songs
      "title":      "Never Gonna Give You Up",
      "source":     "youtube",        // "youtube" | "manual" | "orphaned"
      "added_date": "2024-06-28"
    }
  ]
}

Ordering convention
-------------------
  songs[0]  → prefix 00001  (first entry on YouTube playlist page)
  songs[-1] → highest prefix (last entry on YouTube playlist page)
"""

import json
import os
from datetime import date
from typing import Optional

METADATA_FILENAME = "metadata.json"


class MetadataStore:
    """Manages reading and writing metadata.json."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._data: Optional[dict] = None

    @classmethod
    def load_from_cwd(cls) -> "MetadataStore":
        """Load metadata.json from the current working directory."""
        path = os.path.join(os.getcwd(), METADATA_FILENAME)
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"No {METADATA_FILENAME} found in {os.getcwd()}. "
                "Run 'playlist-sync init' first."
            )
        store = cls(path)
        store.load()
        return store

    @classmethod
    def create(cls, playlist_url: str, library_path: str) -> "MetadataStore":
        """Create a new metadata.json in the current working directory."""
        path = os.path.join(os.getcwd(), METADATA_FILENAME)
        if os.path.exists(path):
            raise FileExistsError(f"{path} already exists.")
        store = cls(path)
        store._data = {
            "playlist_url": playlist_url,
            "library_path": library_path,
            "devices": {},
            "songs": [],
        }
        store.save()
        return store

    def load(self) -> None:
        with open(self.path, "r", encoding="utf-8") as f:
            self._data = json.load(f)

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    @property
    def data(self) -> dict:
        if self._data is None:
            self.load()
        return self._data

    @property
    def playlist_url(self) -> str:
        return self.data["playlist_url"]

    @property
    def library_path(self) -> str:
        return self.data["library_path"]

    @property
    def songs(self) -> list:
        return self.data["songs"]

    @property
    def devices(self) -> dict:
        return self.data["devices"]

    def get_by_id(self, youtube_id: str) -> Optional[dict]:
        for song in self.songs:
            if song.get("youtube_id") == youtube_id:
                return song
        return None

    def get_by_title(self, title: str) -> Optional[dict]:
        title_lower = title.lower()
        for song in self.songs:
            if song["title"].lower() == title_lower:
                return song
        return None

    def get_by_position(self, position: int) -> Optional[dict]:
        idx = position - 1
        if 0 <= idx < len(self.songs):
            return self.songs[idx]
        return None

    def add_song(
        self,
        title: str,
        youtube_id: Optional[str],
        source: str,
        position: Optional[int] = None,
    ) -> dict:
        song = {
            "youtube_id": youtube_id,
            "title": title,
            "source": source,
            "added_date": date.today().isoformat(),
        }
        if position is None or position > len(self.songs):
            self.songs.append(song)
        else:
            idx = max(position - 1, 0)
            self.songs.insert(idx, song)
        return song

    def remove_song(self, song: dict) -> None:
        self.songs.remove(song)

    def mark_orphaned(self, song: dict) -> None:
        song["source"] = "orphaned"

    def replace_songs(self, new_songs: list) -> None:
        self.data["songs"] = new_songs

    def add_device(self, name: str, path: str) -> None:
        self.devices[name] = path

    def remove_device(self, name: str) -> None:
        if name not in self.devices:
            raise KeyError(f"Device '{name}' not registered.")
        del self.devices[name]

    def total_songs(self) -> int:
        return len(self.songs)
