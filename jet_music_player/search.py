"""Match a free-form query from the command line against a scanned library.

Backs `jmp vampire weekend`: the words after the command are joined into a
query and resolved here to something playable. Matching reuses `_norm_key`
from `library`, so the same case/punctuation/leading-"The" folding that groups
"AC/DC" with "AC DC" in the browser also makes `jmp acdc` find them.

Resolution is ranked, best match first:

    exact artist  >  exact album  >  exact track
    prefix artist >  prefix album >  prefix track
    substring artist > substring album > substring track

An unambiguous winner (one match, or a strictly better one than the runner-up)
is played directly. Anything else — a tie, or nothing at all — is handed back
for the browser to show as a filtered list, so the user picks rather than
having the wrong album start.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal

from .library import Artist, Track, _norm_key

# What a query resolved to. "artist"/"album" carry every track in that scope
# (the caller shuffles an artist); "track" is a single song.
MatchKind = Literal["artist", "album", "track"]

# Score tiers, high to low. The `kind` tiebreak (artist=2, album=1, track=0) is
# added on top, so an exact artist beats an exact album beats an exact track.
_EXACT = 300
_PREFIX = 200
_SUBSTRING = 100

_KIND_RANK: dict[MatchKind, int] = {"artist": 2, "album": 1, "track": 0}


def _fold(name: str) -> str:
    """`_norm_key` plus accent folding, so an ASCII query finds an accented
    name: `jmp beyonce` -> "Beyoncé", `jmp bjork` -> "Björk", `jmp sigur ros`
    -> "Sigur Rós".

    Folding lives here rather than in `_norm_key` because that key also decides
    which tags group into one artist in the browser, and collapsing accents
    there would merge names that are genuinely distinct in some languages.
    Decomposing to NFD and dropping the combining marks turns "é" into "e".
    """
    decomposed = unicodedata.normalize("NFD", _norm_key(name))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


@dataclass
class Match:
    """One thing in the library a query could have meant."""
    kind: MatchKind
    label: str              # display name: artist, album, or track title
    artist: str             # owning artist (== label when kind is "artist")
    album: str              # owning album ("" when kind is "artist")
    tracks: list[Track]     # everything this match would play
    score: int


def resolve(query: str, library: dict[str, Artist]) -> list[Match]:
    """Rank everything in `library` that `query` could refer to, best first.

    Returns [] when nothing matches. The caller decides whether the top match
    is unambiguous enough to play (see `best_match`).
    """
    q = _fold(query)
    if not q or not library:
        return []

    matches: list[Match] = []
    for artist_name, artist in library.items():
        artist_tracks = [t for alb in artist.albums.values() for t in alb.tracks]
        score = _score(q, artist_name, "artist")
        if score and artist_tracks:
            matches.append(Match(
                kind="artist",
                label=artist_name,
                artist=artist_name,
                album="",
                tracks=artist_tracks,
                score=score,
            ))

        for album_name, album in artist.albums.items():
            score = _score(q, album_name, "album")
            if score and album.tracks:
                matches.append(Match(
                    kind="album",
                    label=album_name,
                    artist=artist_name,
                    album=album_name,
                    tracks=list(album.tracks),
                    score=score,
                ))

            for track in album.tracks:
                score = _score(q, track.title, "track")
                if score:
                    matches.append(Match(
                        kind="track",
                        label=track.title,
                        artist=artist_name,
                        album=album_name,
                        tracks=[track],
                        score=score,
                    ))

    # Best score first; ties broken by shorter (tighter) label, then by name so
    # the order is stable across runs rather than dict-insertion dependent.
    matches.sort(key=lambda m: (-m.score, len(m.label), m.label.lower()))
    return matches


def best_match(matches: list[Match]) -> Match | None:
    """The single match to play, or None when the query is ambiguous.

    Unambiguous means either exactly one match, or a top match that scores
    strictly higher than the runner-up. Two equally-good hits ("Live" as both
    an album and a track title) return None so the caller can show both rather
    than guessing.
    """
    if not matches:
        return None
    if len(matches) == 1 or matches[0].score > matches[1].score:
        return matches[0]
    return None


def _score(query: str, name: str, kind: MatchKind) -> int:
    """Score `query` against one library name, or 0 for no match.

    Both sides go through `_fold`, so case, punctuation, and accents never
    matter. The word-boundary check on substrings keeps `jmp aphex` from
    matching an album whose name merely contains those letters mid-word.
    """
    n = _fold(name)
    if not n:
        return 0
    rank = _KIND_RANK[kind]
    if n == query:
        return _EXACT + rank
    if n.startswith(query):
        return _PREFIX + rank
    # Require the substring to start at a word boundary: "weekend" should hit
    # "Vampire Weekend", but "end" should not.
    if f" {query}" in f" {n}":
        return _SUBSTRING + rank

    # Spaceless fallback, so a name typed as one word still lands: `jmp acdc`
    # -> "AC/DC" (which normalizes to "ac dc"), `jmp lcdsoundsystem` -> "LCD
    # Soundsystem". Done here rather than in `_norm_key` because collapsing all
    # spaces at the library level would fuse genuinely distinct names.
    squashed = n.replace(" ", "")
    q_squashed = query.replace(" ", "")
    if squashed == q_squashed:
        return _EXACT + rank
    if squashed.startswith(q_squashed):
        return _PREFIX + rank
    return 0
