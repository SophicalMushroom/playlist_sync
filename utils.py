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


def format_prefix(n: int, total: int) -> str:
    """Return a zero-padded numeric prefix for position *n* (1-indexed) of *total*."""
    width = max(5, len(str(total)))
    return str(n).zfill(width)


def build_filename(prefix: str, title: str) -> str:
    """Assemble the full on-disk MP3 filename from a prefix and a title stem."""
    return f"{prefix} - {title}.mp3"
