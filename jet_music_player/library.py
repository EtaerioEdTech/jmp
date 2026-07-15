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


@dataclass
class FolderNode:
    """A directory in the on-disk folder view.

    Mirrors the real filesystem tree beneath the music root: `subfolders` are
    the immediate child directories that contain audio (directly or deeper),
    and `tracks` are the audio files sitting *directly* in this folder. A
    folder can hold both (its own loose files plus nested subfolders).

    This is built from the already-scanned tracks (see `build_folder_tree`), so
    it needs no extra disk I/O and stays consistent with the metadata view.
    """
    name: str                                    # display name (the dir's basename)
    path: str                                    # absolute path of the directory
    subfolders: dict[str, "FolderNode"] = field(default_factory=dict)
    tracks: list[Track] = field(default_factory=list)

    def all_tracks(self) -> list[Track]:
        """Every track under this folder, recursing through all subfolders.

        Used for shuffle-a-folder: shuffling a bucket plays everything beneath
        it. Order is a stable depth-first walk (own tracks first, then each
        subfolder); the caller shuffles when it wants random order.
        """
        out = list(self.tracks)
        for name in sorted(self.subfolders, key=str.lower):
            out.extend(self.subfolders[name].all_tracks())
        return out


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
        # Group by the primary/lead artist: collaboration tags like
        # "B.B. King, Pat Metheny, Dave Brubeck" file under "B.B. King". This
        # runs at grouping time (not on the cached record) so it takes effect
        # without a cache rebuild, and the raw tag is left intact on disk.
        artist_name = _primary_artist(rec["artist"])
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


# ---- folder view ----

def build_folder_tree(root: Path, library: dict[str, Artist]) -> FolderNode:
    """Build the on-disk folder tree for the folder view from an already-scanned
    `library`.

    Every track knows its own file path, so we can reconstruct the directory
    structure beneath `root` without touching the disk again. Each track is
    filed under the folder that directly contains it; intermediate directories
    are created as needed so the tree mirrors the real filesystem. Only folders
    that actually contain audio (directly or via a descendant) appear.

    The returned root node's tracks are files sitting directly in `root`; its
    subfolders are the first-level directories under it. Track display names in
    this view come from the filename (see `Browser`), not tags.
    """
    root = Path(root).expanduser().resolve()
    tree = FolderNode(name=root.name or str(root), path=str(root))

    # Walk every track once, threading it down (creating) the chain of folder
    # nodes from the root to the file's own directory.
    for artist in library.values():
        for album in artist.albums.values():
            for track in album.tracks:
                _file_track(tree, root, track)
    return tree


def _file_track(tree: FolderNode, root: Path, track: Track) -> None:
    """Place `track` in `tree` under the folder chain implied by its path."""
    try:
        rel = Path(track.path).resolve().relative_to(root)
    except ValueError:
        # Track lives outside the root (shouldn't happen for a normal scan);
        # drop it straight in the root folder so it's never lost.
        tree.tracks.append(track)
        return

    node = tree
    # rel.parts is (dir, dir, ..., filename); everything but the last is a dir.
    for part in rel.parts[:-1]:
        child = node.subfolders.get(part)
        if child is None:
            child = FolderNode(name=part, path=str(Path(node.path) / part))
            node.subfolders[part] = child
        node = child
    node.tracks.append(track)


# ---- on-disk metadata cache ----

def _cache_path(root: Path) -> Path:
    """A per-root cache file under the user cache dir. The root path is hashed
    so different libraries get distinct cache files."""
    digest = hashlib.sha1(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    base = Path.home() / ".cache" / "jet-music-player"
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

# A leading "The " (grouped so "The Tallest Man…" == "Tallest Man…").
_LEADING_THE_RE = re.compile(r"^the\s+")
# Any run of non-word, non-space characters — punctuation. Turned into a space
# so "AC/DC" == "AC DC", "B.B. King" == "B B King", "JAY-Z" == "JAY Z".
_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
# Two adjacent single-letter "words" — the residue of spelled-out initials once
# punctuation is gone. Joined so "B B King" (from "B.B. King" / "B. B. King")
# collapses to "bb king", matching "BB King". Applied repeatedly for runs of
# three or more initials.
_INITIALS_RE = re.compile(r"\b(\w) (\w)\b")


def _norm_key(name: str) -> str:
    """Canonical key for grouping names that are the same act/album written
    differently — case, whitespace, punctuation, or a leading "The".

    All of these collapse to one key:
        "The Tallest Man On Earth" / "The Tallest Man on Earth"
        "AC/DC" / "AC DC"
        "B. B. King" / "B.B. King" / "B.B.King"
        "JAY-Z" / "Jay-Z"
        "The California Honeydrops" / "California Honeydrops,"

    Distinct names (e.g. "Shallow Grave" vs "Shallow Graves") still differ.
    Punctuation is replaced with a space rather than deleted, so it can't fuse
    two separate words into one (keeping the key from over-merging).
    """
    n = " ".join(name.split()).casefold()
    # Drop a leading "The " only when a name remains after it (so a band
    # literally named "The" is left alone).
    stripped = _LEADING_THE_RE.sub("", n)
    if stripped:
        n = stripped
    n = _PUNCT_RE.sub(" ", n)
    n = " ".join(n.split())
    # Collapse spelled-out initials ("b b king" -> "bb king"). Loop so runs of
    # 3+ single letters join fully; the regex only fuses one pair per pass.
    prev = None
    while prev != n:
        prev = n
        n = _INITIALS_RE.sub(r"\1\2", n)
    return n


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


# The primary artist is everything before the first collaboration separator:
# a comma, " - ", or a feat./ft./featuring/with/x join. "&" is deliberately
# NOT a separator, so real act names built with "&" (Simon & Garfunkel,
# Jay-Z & Kanye West) stay whole rather than splitting at the ampersand.
_PRIMARY_ARTIST_RE = re.compile(
    r"\s*(?:,|\s-\s|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b|\bwith\b|\bx\b).*$",
    re.IGNORECASE,
)


def _primary_artist(name: str) -> str:
    """Reduce a collaboration/compilation artist tag to its lead artist.

    "B.B. King, Pat Metheny, Dave Brubeck"  -> "B.B. King"
    "BB King, Big Mama Thornton, ..."        -> "BB King"
    "Miles Davis feat. John Coltrane"        -> "Miles Davis"
    "California Honeydrops,"                  -> "California Honeydrops"

    Left unchanged when there's no separator, or when cutting would leave
    nothing (so a name that starts with a separator-like token isn't lost).
    """
    if not name:
        return name
    cut = _PRIMARY_ARTIST_RE.sub("", name).strip()
    return cut or name


def _parse_track_num(value) -> int:
    """Track numbers may be "3" or "3/12". Return the leading integer."""
    if not value:
        return 0
    try:
        return int(str(value).split("/")[0])
    except ValueError:
        return 0
