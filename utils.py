"""Shared utility functions: filename cleaning and prefix formatting."""

import re
import unicodedata


def clean_filename(text: str) -> str:
    """Convert a YouTube video title into a safe local filename stem.

    Steps applied in order:
      1. NFKD Unicode normalisation — decomposes ligatures, superscripts, etc.
      2. Non-ASCII bytes dropped — keeps only plain ASCII characters.
      3. Filesystem-illegal characters removed: < > : \" / \\ | ? *
      4. Runs of whitespace collapsed to a single space; leading/trailing stripped.

    Returns "untitled" if the result would otherwise be empty.
    """
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r'[<>:"/\\|?*]', "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or "untitled"


def normalize_for_matching(text: str) -> str:
    """Aggressively normalize a title to alphanumeric-only lowercase for fuzzy matching.

    Used during import to match local files that were cleaned by different logic
    (e.g. the old cleanText function which stripped different characters).
    """
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]", "", text.lower())
    return text


def read_mp3_duration(filepath: str) -> float | None:
    """Return the duration of an MP3 file in seconds, or None on failure.

    Uses mutagen for fast, pure-Python reading (no ffmpeg subprocess needed).
    """
    try:
        from mutagen.mp3 import MP3
        return MP3(filepath).info.length
    except Exception:
        return None


def format_prefix(n: int, total: int) -> str:
    """Return a zero-padded numeric prefix for position *n* (1-indexed) of *total*."""
    width = max(5, len(str(total)))
    return str(n).zfill(width)


def build_filename(prefix: str, title: str) -> str:
    """Assemble the full on-disk MP3 filename from a prefix and a title stem."""
    return f"{prefix} - {title}.mp3"
