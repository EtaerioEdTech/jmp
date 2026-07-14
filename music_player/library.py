"""Scan a music directory and build a nested Artist -> Album -> Track structure.

Uses mutagen to read metadata tags from common audio formats.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from mutagen import File as MutagenFile

# Formats that pygame + mutagen handle reliably.
AUDIO_EXTENSIONS: set[str] = {".mp3", ".flac", ".ogg", ".wav", ".m4a", ".opus"}

# Bump when the cached record shape changes so stale caches are ignored.
_CACHE_VERSION = 1


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


def scan_library(
    root: Path,
    *,
    use_cache: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Artist]:
    """Recursively scan `root` and return a dict of {artist_name: Artist}.

    Reading tags with mutagen is the slow part, so per-file results are cached
    on disk keyed by path + mtime + size (see `_load_cache`). On relaunch only
    new or changed files are re-read, which is what keeps startup fast once a
    large library has been scanned once. Pass `use_cache=False` to force a
    full re-read.

    `progress(done, total)`, if given, is called as files are examined so a
    caller can drive a loading indicator. Tracks are sorted by track number
    within each album.
    """
    root = Path(root).expanduser()

    paths = list(_iter_audio_files(root))
    total = len(paths)
    cache = _load_cache(root) if use_cache else {}
    fresh_cache: dict[str, dict] = {}
    records: list[dict] = []

    for i, path in enumerate(paths):
        rec = _record_for(path, cache)
        if rec is not None:
            records.append(rec)
            fresh_cache[str(path)] = rec
        if progress is not None:
            progress(i + 1, total)

    library = _build_library(records)

    if use_cache and fresh_cache != cache:
        _save_cache(root, fresh_cache)

    return library


def _record_for(path: Path, cache: dict[str, dict]) -> dict | None:
    """Return a cached-or-freshly-read tag record for `path`, or None if it
    can't be read. The record is a plain dict so it round-trips through JSON."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    cached = cache.get(key)
    if cached is not None and cached.get("mtime") == st.st_mtime and cached.get("size") == st.st_size:
        return cached

    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        return None
    if audio is None:
        return None

    artist_name = (
        _first(audio.get("albumartist"))
        or _strip_featured(_first(audio.get("artist")))
        or "Unknown Artist"
    )
    album_name = _first(audio.get("album")) or "Unknown Album"
    title = _first(audio.get("title")) or path.stem
    track_num = _parse_track_num(_first(audio.get("tracknumber")))
    year = _first(audio.get("date")) or _first(audio.get("year")) or ""
    year = str(year)[:4] if year else ""
    duration = getattr(audio.info, "length", 0.0) if hasattr(audio, "info") else 0.0

    return {
        "path": key,
        "mtime": st.st_mtime,
        "size": st.st_size,
        "artist": artist_name,
        "album": album_name,
        "title": title,
        "track_num": track_num,
        "year": year,
        "duration": duration,
    }


def _build_library(records: Iterable[dict]) -> dict[str, Artist]:
    """Assemble the nested Artist -> Album -> Track structure from flat tag
    records, resolving the nicest display name for each artist/album."""
    # Keyed by normalized name so case/whitespace variants collapse together.
    library: dict[str, Artist] = {}
    # Track the display-name variants seen for each key: {norm_key: {name: count}}.
    artist_names: dict[str, dict[str, int]] = {}
    album_names: dict[str, dict[str, dict[str, int]]] = {}

    for rec in records:
        artist_name = rec["artist"]
        album_name = rec["album"]
        artist_key = _norm_key(artist_name)
        album_key = _norm_key(album_name)

        artist = library.setdefault(artist_key, Artist(name=artist_name))
        album = artist.albums.setdefault(album_key, Album(name=album_name, year=rec["year"]))
        album.tracks.append(Track(
            path=rec["path"],
            title=rec["title"],
            track_num=rec["track_num"],
            duration=rec["duration"],
        ))

        # Tally display-name variants so we can pick the nicest one later.
        artist_names.setdefault(artist_key, {})[artist_name] = (
            artist_names.get(artist_key, {}).get(artist_name, 0) + 1
        )
        album_names.setdefault(artist_key, {}).setdefault(album_key, {})[album_name] = (
            album_names.get(artist_key, {}).get(album_key, {}).get(album_name, 0) + 1
        )

    # Resolve the best display name for each artist/album and re-key the
    # returned dicts by that display name (the shape callers expect).
    resolved: dict[str, Artist] = {}
    for artist_key, artist in library.items():
        artist.name = _best_display(artist_names[artist_key])
        albums_by_display: dict[str, Album] = {}
        for album_key, album in artist.albums.items():
            album.name = _best_display(album_names[artist_key][album_key])
            album.tracks.sort(key=lambda t: (t.track_num, t.title))
            albums_by_display[album.name] = album
        artist.albums = albums_by_display
        resolved[artist.name] = artist

    return resolved


# ---- on-disk metadata cache ----

def _cache_path(root: Path) -> Path:
    """A per-root cache file under the user cache dir. The root path is hashed
    so different libraries get distinct cache files."""
    digest = hashlib.sha1(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    base = Path.home() / ".cache" / "terminaltunes"
    return base / f"scan-{digest}.json"


def _load_cache(root: Path) -> dict[str, dict]:
    """Load the {path: record} cache for `root`, or an empty dict if missing,
    unreadable, or from an older cache version."""
    try:
        data = json.loads(_cache_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != _CACHE_VERSION:
        return {}
    records = data.get("records")
    return records if isinstance(records, dict) else {}


def _save_cache(root: Path, records: dict[str, dict]) -> None:
    """Persist the {path: record} cache for `root`. Best-effort: failures to
    write (e.g. read-only home) are silently ignored — the cache is an
    optimization, never required for correctness."""
    path = _cache_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": _CACHE_VERSION, "records": records}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


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


# Matches a "featured artist" suffix: "feat.", "ft.", "featuring", "with".
# Case-insensitive, requires a word boundary so it won't clip real names.
_FEATURED_RE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|with)\s+.*$", re.IGNORECASE)


def _norm_key(name: str) -> str:
    """Canonical key for grouping names that differ only by case/whitespace.

    "The Tallest Man On Earth" and "The Tallest Man on Earth" collapse to the
    same key. Distinct names (e.g. "Shallow Grave" vs "Shallow Graves") don't.
    """
    return " ".join(name.split()).casefold()


def _best_display(names: dict[str, int]) -> str:
    """Pick the nicest display variant from {name: occurrence_count}.

    Prefer a mixed-case (properly capitalized) variant over an all-lower or
    all-upper one, then break ties by how often the variant was seen.
    """
    def rank(item):
        name, count = item
        mixed = not (name.islower() or name.isupper())
        return (mixed, count)

    return max(names.items(), key=rank)[0]


def _strip_featured(value):
    """Drop a trailing "feat. X" / "ft. X" from an artist string.

    Leaves "&" alone so real act names (Simon & Garfunkel) stay intact.
    Returns None unchanged so the caller's `or` fallback still works.
    """
    if not value:
        return value
    return _FEATURED_RE.sub("", value).strip() or value


def _parse_track_num(value) -> int:
    """Track numbers may be "3" or "3/12". Return the leading integer."""
    if not value:
        return 0
    try:
        return int(str(value).split("/")[0])
    except ValueError:
        return 0
