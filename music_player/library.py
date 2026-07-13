"""Scan a music directory and build a nested Artist -> Album -> Track structure.

Uses mutagen to read metadata tags from common audio formats.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from mutagen import File as MutagenFile

# Formats that pygame + mutagen handle reliably.
AUDIO_EXTENSIONS: set[str] = {".mp3", ".flac", ".ogg", ".wav", ".m4a", ".opus"}


@dataclass
class Track:
    """A single audio track on disk."""
    path: str
    title: str
    track_num: int = 0
    duration: float = 0.0  # seconds


@dataclass
class Album:
    """A collection of tracks by one artist."""
    name: str
    year: str = ""
    tracks: list[Track] = field(default_factory=list)


@dataclass
class Artist:
    """An artist with one or more albums."""
    name: str
    albums: dict[str, Album] = field(default_factory=dict)


def scan_library(root: Path) -> dict[str, Artist]:
    """Recursively scan `root` and return a dict of {artist_name: Artist}.

    Sorts tracks by track number within each album.
    """
    root = Path(root).expanduser()
    library: dict[str, Artist] = {}

    for path in _iter_audio_files(root):
        try:
            audio = MutagenFile(path, easy=True)
        except Exception:
            continue
        if audio is None:
            continue

        artist_name = _first(audio.get("albumartist")) or _first(audio.get("artist")) or "Unknown Artist"
        album_name = _first(audio.get("album")) or "Unknown Album"
        title = _first(audio.get("title")) or path.stem
        track_num = _parse_track_num(_first(audio.get("tracknumber")))
        year = _first(audio.get("date")) or _first(audio.get("year")) or ""
        year = str(year)[:4] if year else ""
        duration = getattr(audio.info, "length", 0.0) if hasattr(audio, "info") else 0.0

        artist = library.setdefault(artist_name, Artist(name=artist_name))
        album = artist.albums.setdefault(album_name, Album(name=album_name, year=year))
        album.tracks.append(Track(
            path=str(path),
            title=title,
            track_num=track_num,
            duration=duration,
        ))

    # Sort tracks within each album by track number, then title.
    for artist in library.values():
        for album in artist.albums.values():
            album.tracks.sort(key=lambda t: (t.track_num, t.title))

    return library


def _iter_audio_files(root: Path) -> Iterable[Path]:
    """Yield audio files under `root`."""
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
            yield path


def _first(value):
    """Mutagen returns lists for many tags. Grab the first entry."""
    if value is None:
        return None
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _parse_track_num(value) -> int:
    """Track numbers may be "3" or "3/12". Return the leading integer."""
    if not value:
        return 0
    try:
        return int(str(value).split("/")[0])
    except ValueError:
        return 0
