# playlist-sync

CLI tool that manages a local MP3 library synced from a YouTube playlist.  
Songs are downloaded as MP3s via [yt-dlp](https://github.com/yt-dlp/yt-dlp), automatically numbered by playlist order, and can be mirrored to external devices (phone, USB drive, etc.).

This repo is meant to be uploaded as the top-level `playlist_sync/` folder.

---

## Requirements

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/download.html) installed and on PATH (used by yt-dlp for audio conversion)
- `mutagen` is installed automatically and is used to read local MP3 durations during import matching

---

## Installation

```bash
cd playlist_sync
pip install -e .
```

This installs the `playlist-sync` command and all Python dependencies (`click`, `yt-dlp`, `mutagen`).

If you import from a private or sign-in-restricted playlist, make sure you are logged into YouTube in Firefox, because the importer reads playlist data using Firefox browser cookies.

---

## File layout

```
playlist_sync/
├── README.md
├── pyproject.toml
├── cli.py          # Click CLI entry point
├── device.py       # Device sync logic
├── download.py     # yt-dlp wrapper
├── metadata.py     # metadata.json read/write
├── sync.py         # Core sync algorithm
└── utils.py        # Filename helpers
```

---

## How it works

`playlist-sync` keeps a `metadata.json` file in the working directory that records:

- The YouTube playlist URL being tracked
- The path to the local MP3 library
- Every tracked song (title, YouTube ID, source, date added)
- Named external devices

Songs are stored as `<prefix> - <Title>.mp3` (e.g. `00001 - Never Gonna Give You Up.mp3`), where the prefix reflects the song's position in the playlist (oldest = lowest number).

---

## Commands

### `import` — register an existing MP3 folder (start here if you already have files)

If you already have a folder of MP3s downloaded from your playlist (plus any manual tracks), use `import` instead of `init`. It fetches the playlist from YouTube, matches your existing files to playlist entries, and registers everything **without re-downloading a single file**. Any playlist songs that are missing from the folder are noted, and a subsequent `sync` will download only those.

Matching happens in three passes so that older local filenames can still line up with changed YouTube titles:

1. Exact title match after filename cleaning.
2. Aggressive alphanumeric-only match to handle punctuation differences such as apostrophes, `&`, and commas.
3. Similarity match for title changes such as `Official Music Video` becoming `Official Video`.

For the similarity pass, the importer also compares song length when it is available. If both local and YouTube durations are present and differ by more than 5 seconds, the match is rejected.

```bash
playlist-sync import --playlist-url <URL> --library <PATH> [--similarity-threshold <N>] [--yes|-y]
```

| Option | Required | Description |
|---|---|---|
| `--playlist-url` | Yes | Full URL of the YouTube playlist to track |
| `--library` | Yes | Path to the existing directory that already contains your MP3 files |
| `--similarity-threshold` | No | Minimum score for the third-pass title match (default `0.85`, range `0`–`1`) |
| `--yes`, `-y` | No | Auto-accept all similarity matches without prompting |
| `--duration-tolerance` | No | Maximum allowed difference in seconds between local and YouTube durations before a similarity match is rejected (default `5.0`) |

**Examples**

```bash
playlist-sync import --playlist-url "https://www.youtube.com/playlist?list=PLxxxx" --library "C:/Users/me/Documents/Songs"
```

**Sample output**

```
Fetching playlist from YouTube…

Found 3 potential similarity match(es). Please review each:

  Score: 0.92  |  Duration: 3:45 ≈ 3:46
  YouTube : Dua Lipa - Levitating (Official Music Video)
  Local   : Dua Lipa - Levitating (Official Video)
  Accept this match? [Y/n]: y

  Score: 0.88  |  Duration: no duration
  YouTube : Some Song Remastered 2024
  Local   : Some Song
  Accept this match? [Y/n]: n

Matched  139 existing file(s) to playlist entries.
Imported 4 manual song(s) (not in playlist).

Similarity-matched and accepted (1):
  [0.92, 3:45 ≈ 3:46] 'Dua Lipa - Levitating (Official Music Video)'
         → 'Dua Lipa - Levitating (Official Video)'

4 playlist song(s) not found on disk:
  Brand New Song Added Yesterday
  Another Missing Track
  One More
  Some Song Remastered 2024

Run 'playlist-sync sync' to download the missing songs.
```

> After `import` completes, all files are renumbered to match the current playlist order. Run `playlist-sync sync` to fetch any songs that weren't on disk yet.

> Each similarity match is shown individually before being accepted. Rejected matches are treated as missing songs and downloaded by the next `sync` run.

> Use `--yes` / `-y` to skip all prompts and auto-accept every similarity match — useful when you are confident the files are correct or when running non-interactively.

> To widen or narrow what counts as a similarity match, use `--similarity-threshold` (default `0.85`). Lower values match more aggressively and should be reviewed carefully.

> To relax or tighten the duration check, use `--duration-tolerance` (default `5.0`). Lower values are stricter; higher values allow more variation between local and YouTube versions.

> If you are importing from a private playlist, use Firefox for the YouTube login session the importer relies on.

---

### `init` — set up a new library

Download a YouTube playlist into a new local library.

```bash
playlist-sync init --playlist-url <URL> --library <PATH>
```

| Option | Required | Description |
|---|---|---|
| `--playlist-url` | Yes | Full URL of the YouTube playlist to track |
| `--library` | Yes | Path to the local directory where MP3 files will be stored (created if it doesn't exist) |

**Examples**

```bash
# Windows
playlist-sync init --playlist-url "https://www.youtube.com/playlist?list=PLxxxx" --library "C:/Users/me/Documents/Songs"

# macOS / Linux
playlist-sync init --playlist-url "https://www.youtube.com/playlist?list=PLxxxx" --library "~/Music/MySongs"
```

> Only needs to be run once per library. Creates `metadata.json` in the current directory and immediately downloads the full playlist.

---

### `sync` — pull new songs from the playlist

Fetch the YouTube playlist and download any songs added since the last sync. Songs removed from the playlist are marked as **orphaned** (the MP3 is kept but no longer tracked as a YouTube source).

By default, a preview of changes is shown and you are asked to confirm before anything is applied.

```bash
playlist-sync sync [--dry-run] [--yes|-y]
```

| Option | Description |
|---|---|
| `--dry-run` | Preview what would change without downloading or renaming anything (no confirmation prompt) |
| `--yes`, `-y` | Skip the confirmation prompt and apply changes immediately |

**Examples**

```bash
# Normal sync (preview + confirm)
playlist-sync sync

# See what would change without being asked to confirm
playlist-sync sync --dry-run

# Apply immediately (useful in scripts / CI)
playlist-sync sync --yes
```

**Sample output**

```
Will add (3):
  Some New Song
  Another New Song
  Yet Another Banger

Will orphan (removed from playlist) (1):
  Old Song That Got Removed

Apply these changes? [y/N]: y

Applying changes…

Added (3):
  Some New Song
  Another New Song
  Yet Another Banger

Orphaned (removed from playlist) (1):
  Old Song That Got Removed
```

---

### `status` — show library info

Print a summary of the current library state.

```bash
playlist-sync status
```

**Sample output**

```
Playlist URL : https://www.youtube.com/playlist?list=PLxxxx
Library path : C:/Users/me/Documents/Songs
Songs        : 142 total  (139 YouTube, 2 manual, 1 orphaned)
Devices:
  phone            →  D:/Music
  car-usb          →  E:/Music
```

---

### `add` — add a local MP3 to the library

Copy an existing MP3 file into the library and register it in metadata. The song is inserted at the top of the list by default (highest prefix number).

```bash
playlist-sync add <FILE> [--position <N>]
```

| Argument / Option | Description |
|---|---|
| `FILE` | Path to an existing `.mp3` file |
| `--position`, `-p` | 1-indexed position to insert at (1 = bottom / oldest). Defaults to top of the list |

**Examples**

```bash
# Add to the top (most recent) of the list
playlist-sync add "C:/Downloads/My Favourite Track.mp3"

# Add at position 5 (5th from the bottom)
playlist-sync add "C:/Downloads/My Favourite Track.mp3" --position 5
playlist-sync add "C:/Downloads/My Favourite Track.mp3" -p 5
```

> The file is copied into the library and all existing files are renumbered automatically.

---

### `remove` — remove a song from the library

Delete an MP3 and remove it from metadata. You can identify the song by its position number or by part of its title.

```bash
playlist-sync remove <TITLE_OR_POSITION>
```

| Argument | Description |
|---|---|
| `TITLE_OR_POSITION` | A position number (e.g. `42`) or a case-insensitive substring of the song title |

A confirmation prompt is shown before the file is deleted.

**Examples**

```bash
# Remove by position number
playlist-sync remove 42

# Remove by title (case-insensitive substring match)
playlist-sync remove "never gonna"

# Remove by exact title
playlist-sync remove "Never Gonna Give You Up"
```

> If the title substring matches multiple songs, a list of matches is printed and you must be more specific. All remaining files are renumbered after removal.

---

### `device` — manage external devices

Sub-command group for syncing the library to external devices (phones, USB drives, etc.).

#### `device add` — register a device

```bash
playlist-sync device add <NAME> <PATH>
```

| Argument | Description |
|---|---|
| `NAME` | A short name for the device (used with `device sync`) |
| `PATH` | Absolute path to the device's music folder |

**Examples**

```bash
playlist-sync device add phone "D:/Music"
playlist-sync device add car-usb "E:/Playlist"
```

#### `device remove` — unregister a device

```bash
playlist-sync device remove <NAME>
```

**Example**

```bash
playlist-sync device remove car-usb
```

#### `device sync` — mirror the library to a device

Copy new/changed songs to the device, rename files whose prefix changed, and delete files that are no longer in the library. A manifest file (`.playlist_sync_manifest.json`) is written to the device to track what was synced.

By default, a preview of changes is shown and you are asked to confirm before anything is applied.

```bash
playlist-sync device sync <NAME_OR_PATH> [--dry-run] [--yes|-y]
```

| Argument / Option | Description |
|---|---|
| `NAME_OR_PATH` | A registered device name (e.g. `phone`) or a direct path to any directory |
| `--dry-run` | Preview changes without copying, renaming, or deleting anything (no confirmation prompt) |
| `--yes`, `-y` | Skip the confirmation prompt and apply changes immediately |

**Examples**

```bash
# Sync to a registered device by name (preview + confirm)
playlist-sync device sync phone

# Sync to an ad-hoc path without registering it first
playlist-sync device sync "D:/Music"

# Dry run to a registered device
playlist-sync device sync phone --dry-run

# Apply immediately without confirmation
playlist-sync device sync phone --yes
```

**Sample output**

```
Device 'phone' → D:/Music
Syncing to: D:/Music

Will copy (5):
  00140 - Brand New Song.mp3
  ...

Will rename (prefix changed) (2):
  00041 - Old Title.mp3 → 00042 - Old Title.mp3
  ...

Will delete (1):
  00099 - Removed Song.mp3

137 file(s) already up to date.

Apply these changes? [y/N]: y

Applying changes…
Done.
```

---

## Typical workflow

### Starting fresh (no existing files)

```bash
# 1. One-time setup — download the full playlist into a new folder
playlist-sync init \
  --playlist-url "https://www.youtube.com/playlist?list=PLxxxx" \
  --library "C:/Users/me/Music/MyPlaylist"
```

### Starting with an existing folder

```bash
# 1. Register your existing files — nothing is re-downloaded
playlist-sync import \
  --playlist-url "https://www.youtube.com/playlist?list=PLxxxx" \
  --library "C:/Users/me/Music/MyPlaylist"

# 2. Download any playlist songs that weren't in your folder yet
playlist-sync sync
```

### Ongoing use (after either starting point)

```bash
# Register your devices (optional, one-time)
playlist-sync device add phone "D:/Music"

# Check what's new without committing
playlist-sync sync --dry-run

# Pull new songs
playlist-sync sync

# Mirror to your phone
playlist-sync device sync phone

# Check library health
playlist-sync status
```
