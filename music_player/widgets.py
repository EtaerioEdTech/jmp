"""Custom Textual widgets: Browser, Visualizer, ProgressBar, NowPlaying.

The whole UI is rendered in one visual language: Braille glyphs for the
visualizer, the progress bar, the browser cursor/rule, and the play icons, all
monochrome (brightness only) so it stays transparent and futuristic.
"""

from __future__ import annotations

import random

import numpy as np
from rich.text import Text
from textual import events
from textual.message import Message
from textual.widget import Widget

from .braille import Canvas, draw_text, text_height_dots, text_width_dots
from .library import Artist, Track


class Browser(Widget, can_focus=True):
    """A pure-text, SSH-menu-style library browser.

    No tree widget, no boxes: a single-column list of the current level with
    a `›` cursor. Arrow keys move, Enter drills down (artist -> album -> track)
    or plays a track, Left / Backspace goes back up a level.
    """

    # Levels of the drill-down.
    ARTISTS = "artists"
    ALBUMS = "albums"
    TRACKS = "tracks"

    class TrackChosen(Message):
        """Posted when the user picks a track to play."""

        def __init__(self, track: Track, playlist: list[Track], artist: str, album: str) -> None:
            super().__init__()
            self.track = track
            self.playlist = playlist
            self.artist = artist
            self.album = album

    class ShuffleChosen(Message):
        """Posted when the user shuffles the highlighted artist/album/track."""

        def __init__(self, tracks: list[Track], scope: str) -> None:
            super().__init__()
            self.tracks = tracks   # already shuffled
            self.scope = scope     # human label, e.g. "Radiohead" or "Kid A"

    def __init__(self, library: dict[str, Artist], **kwargs) -> None:
        super().__init__(**kwargs)
        self._library = library
        self._level = self.ARTISTS
        self._cursor = 0
        # Selection context as we drill down.
        self._artist_name = ""
        self._album_name = ""
        self._rows: list[str] = []
        self._empty = not library
        self._rebuild_rows()

    # ---- data ----

    def set_library(self, library: dict[str, Artist]) -> None:
        self._library = library
        self._empty = not library
        self._level = self.ARTISTS
        self._cursor = 0
        self._rebuild_rows()
        self.refresh()

    def _artists_sorted(self) -> list[str]:
        return sorted(self._library, key=str.lower)

    def _albums_sorted(self) -> list[str]:
        artist = self._library[self._artist_name]
        return sorted(artist.albums, key=str.lower)

    def _tracks(self) -> list[Track]:
        return self._library[self._artist_name].albums[self._album_name].tracks

    def _rebuild_rows(self) -> None:
        """Recompute the visible row labels for the current level."""
        if self._empty:
            self._rows = []
            return
        if self._level == self.ARTISTS:
            self._rows = self._artists_sorted()
        elif self._level == self.ALBUMS:
            rows = []
            for name in self._albums_sorted():
                album = self._library[self._artist_name].albums[name]
                rows.append(f"{name}  ({album.year})" if album.year else name)
            self._rows = rows
        else:  # TRACKS
            rows = []
            for t in self._tracks():
                num = f"{t.track_num:02d}. " if t.track_num else ""
                rows.append(f"{num}{t.title}")
            self._rows = rows

    # ---- navigation ----

    def on_key(self, event: events.Key) -> None:
        key = event.key
        if self._empty:
            return
        if key in ("up", "k"):
            self._move(-1)
            event.stop()
        elif key in ("down", "j"):
            self._move(1)
            event.stop()
        elif key in ("right", "enter", "l"):
            # Advance: drill in (or play a track).
            self._enter()
            event.stop()
        elif key in ("left", "backspace", "h"):
            # Retreat: back up a level.
            self._back()
            event.stop()
        elif key in ("s", "S"):
            self._shuffle()
            event.stop()

    def _shuffle(self) -> None:
        """Shuffle the folder under the cursor: all of an artist's songs, a
        whole album, or (on a track) that track's album."""
        if not self._rows:
            return
        if self._level == self.ARTISTS:
            name = self._artists_sorted()[self._cursor]
            artist = self._library[name]
            tracks = [t for alb in artist.albums.values() for t in alb.tracks]
            scope = name
        elif self._level == self.ALBUMS:
            name = self._albums_sorted()[self._cursor]
            tracks = list(self._library[self._artist_name].albums[name].tracks)
            scope = name
        else:  # TRACKS: shuffle the album this track belongs to
            tracks = list(self._tracks())
            scope = self._album_name
        if not tracks:
            return
        shuffled = list(tracks)
        random.shuffle(shuffled)
        self.post_message(self.ShuffleChosen(shuffled, scope))

    def _move(self, delta: int) -> None:
        if not self._rows:
            return
        self._cursor = (self._cursor + delta) % len(self._rows)
        self.refresh()

    def _enter(self) -> None:
        if not self._rows:
            return
        if self._level == self.ARTISTS:
            self._artist_name = self._artists_sorted()[self._cursor]
            self._level = self.ALBUMS
            self._cursor = 0
            self._rebuild_rows()
            self.refresh()
        elif self._level == self.ALBUMS:
            self._album_name = self._albums_sorted()[self._cursor]
            self._level = self.TRACKS
            self._cursor = 0
            self._rebuild_rows()
            self.refresh()
        else:  # TRACKS -> play
            tracks = self._tracks()
            track = tracks[self._cursor]
            self.post_message(
                self.TrackChosen(track, list(tracks), self._artist_name, self._album_name)
            )

    def _back(self) -> None:
        if self._level == self.TRACKS:
            self._level = self.ALBUMS
            # Restore cursor to the album we came from.
            albums = self._albums_sorted()
            self._cursor = albums.index(self._album_name) if self._album_name in albums else 0
            self._rebuild_rows()
            self.refresh()
        elif self._level == self.ALBUMS:
            self._level = self.ARTISTS
            artists = self._artists_sorted()
            self._cursor = artists.index(self._artist_name) if self._artist_name in artists else 0
            self._rebuild_rows()
            self.refresh()
        # At ARTISTS level, back does nothing.

    # ---- render ----

    def _heading(self) -> str:
        if self._level == self.ARTISTS:
            return "ARTISTS"
        if self._level == self.ALBUMS:
            return f"ALBUMS — {self._artist_name}"
        return f"{self._artist_name} — {self._album_name}"

    def _hint(self) -> str:
        if self._level == self.ARTISTS:
            return "↑↓ move   → open   s shuffle   q quit"
        return "↑↓ move   → open   ← back   s shuffle"

    def render(self) -> Text:
        width = self.size.width
        out = Text()
        # Heading + a Braille rule under it, in the same visual language as the
        # visualizer and progress bar.
        heading = self._heading()
        out.append(heading + "\n", style="bold")
        rule_w = max(4, min(width, len(heading) + 6))
        out.append(chr(0x28FF) * rule_w + "\n", style="dim")
        out.append("\n")

        if self._empty:
            out.append("(no audio files found)\n", style="dim italic")
            out.append("\nPass a music directory: ttunes /path/to/music\n", style="dim")
            return out

        for i, row in enumerate(self._rows):
            selected = i == self._cursor
            # Selection is marked by a Braille cursor + bold text — no background
            # fill, so the terminal shows through everywhere.
            marker = "⣿ " if selected else "  "
            style = "bold" if selected else "dim"
            out.append(f"{marker}{row}\n", style=style)

        out.append("\n")
        out.append(self._hint(), style="dim")
        return out


class Visualizer(Widget):
    """A Braille-rendered audio visualizer with several switchable modes.

    All modes rasterize into a high-resolution Braille sub-pixel canvas (2×4
    dots per character cell → 8× resolution), so everything reads as a fine,
    light, futuristic line rather than chunky blocks. Fully transparent —
    brightness is carried by dim/normal/bold styling, never color fills.

    Modes (cycle with the `v` key):
        - "bars"     : 32-band frequency spectrum as vertical bars
        - "mirror"   : spectrum mirrored above/below a center line
    """

    MODES = ("bars", "mirror")
    MODE_LABELS = {
        "bars": "SPECTRUM",
        "mirror": "MIRROR",
    }

    DECAY = 0.80  # spectrum smoothing between frames: fast attack, slow release

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._mode_idx = 0
        self._smoothed: np.ndarray | None = None   # smoothed spectrum bars

    @property
    def mode(self) -> str:
        return self.MODES[self._mode_idx]

    @property
    def mode_label(self) -> str:
        return self.MODE_LABELS[self.mode]

    def cycle_mode(self) -> str:
        """Advance to the next visualization and return its label."""
        self._mode_idx = (self._mode_idx + 1) % len(self.MODES)
        self.refresh()
        return self.mode_label

    def update_frame(self, bars: np.ndarray | None, wave: np.ndarray | None) -> None:
        """Feed the current spectrum, advance animation, refresh. (`wave` is
        accepted for API compatibility but unused by the current modes.)"""
        if bars is None:
            self._smoothed = None
        else:
            b = np.asarray(bars, dtype=float)
            if self._smoothed is None or len(self._smoothed) != len(b):
                self._smoothed = b.copy()
            else:
                self._smoothed = np.maximum(b, self._smoothed * self.DECAY)
        self.refresh()

    # ---- rendering ----

    def render(self) -> Text:
        width = self.size.width
        height = self.size.height
        if width <= 0 or height <= 0:
            return Text("")

        canvas = Canvas(width, height)
        if self.mode == "bars":
            self._draw_bars(canvas)
        else:
            self._draw_mirror(canvas)

        return self._emit(canvas)

    def _emit(self, canvas: Canvas) -> Text:
        """Turn a Canvas into styled Braille Text (transparent, dim→bold)."""
        return _canvas_to_text(canvas)

    # ---- individual modes ----

    def _draw_flatline(self, canvas: Canvas) -> None:
        """A calm centered line when there's no signal yet."""
        mid = (canvas.gh - 1) / 2.0
        xs = np.arange(canvas.gw)
        ys = np.full(canvas.gw, mid)
        canvas.plot(xs[::3], ys[::3])  # dotted, faint

    def _draw_bars(self, canvas: Canvas) -> None:
        """32-band spectrum as vertical Braille bars across the width."""
        bars = self._smoothed
        if bars is None:
            self._draw_flatline(canvas)
            return
        n_cols = canvas.gw
        idx = np.linspace(0, len(bars) - 1, n_cols).astype(int)
        vals = np.clip(bars[idx], 0, 1)
        heights = (vals * canvas.gh).astype(int)
        for c in range(n_cols):
            canvas.vbar(c, heights[c])

    def _draw_mirror(self, canvas: Canvas) -> None:
        """Spectrum mirrored above and below the center line."""
        bars = self._smoothed
        if bars is None:
            self._draw_flatline(canvas)
            return
        n_cols = canvas.gw
        idx = np.linspace(0, len(bars) - 1, n_cols).astype(int)
        vals = np.clip(bars[idx], 0, 1)
        half = canvas.gh / 2.0
        mid = (canvas.gh - 1) / 2.0
        reach = (vals * half * 0.95)
        for c in range(n_cols):
            r = reach[c]
            top = int(mid - r)
            bot = int(mid + r)
            canvas.buf[max(0, top):min(canvas.gh, bot + 1), c] += 1.0


class ProgressBar(Widget):
    """A one-line progress bar with elapsed / total time labels."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._pos = 0.0
        self._total = 0.0

    def update_progress(self, pos_seconds: float, total_seconds: float) -> None:
        self._pos = pos_seconds
        self._total = total_seconds
        self.refresh()

    # A Braille cell is 4 dots tall. The elapsed portion is the slim, centered
    # middle-two-dot line; the remaining track is the full-height block. As the
    # song plays the tall block collapses to the centered line behind the
    # playhead — the bar "fills in" from the top and bottom toward the middle.
    _DONE = chr(0x2836)   # ⠶ middle two dot-rows — elapsed (slim centered line)
    _TRACK = chr(0x28FF)  # ⣿ full cell — remaining track

    def render(self) -> Text:
        width = self.size.width
        if width <= 0:
            return Text("")

        pos_str = _format_time(self._pos)
        total_str = _format_time(self._total)
        bar_width = width - len(pos_str) - len(total_str) - 4
        if bar_width < 4:
            return Text(f"{pos_str} / {total_str}")

        fraction = max(0.0, min(1.0, self._pos / self._total)) if self._total > 0 else 0.0
        filled = int(bar_width * fraction)

        out = Text()
        out.append(f"{pos_str} ", style="bold")
        out.append(self._DONE * filled, style="default")
        out.append(self._TRACK * (bar_width - filled), style="dim")
        out.append(f" {total_str}", style="bold")
        return out


class Banner(Widget):
    """Big "ARTIST - TRACK - ALBUM" title rendered in Braille bitmap text.

    When the title is wider than the widget it scrolls as a marquee (looping),
    otherwise it sits centered. Monochrome and transparent like the rest.
    """

    SCALE = 2          # font enlargement (each font pixel → 2×2 Braille dots)
    SPEED = 1.4        # dot-columns scrolled per frame when marqueeing
    # Separator appended between marquee loops so "…ALBUM" reads clearly apart
    # from the "ARTIST…" that follows it when the text wraps around.
    LOOP_SEP = "      ·      "

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._title = ""        # plain "ARTIST - TRACK - ALBUM" (centered case)
        self._loop = ""         # title + trailing separator (marquee unit)
        self._status = "stopped"
        self._scroll = 0.0

    def set_track(self, title: str, album: str, artist: str) -> None:
        parts = [p for p in (artist, title, album) if p]
        self._title = "   -   ".join(parts)
        # The looping unit carries trailing spaces so consecutive loops don't
        # run the album straight into the artist name.
        self._loop = self._title + self.LOOP_SEP
        self._scroll = 0.0
        self.refresh()

    def set_status(self, status: str) -> None:
        self._status = status
        self.refresh()

    def tick(self) -> None:
        """Advance the marquee scroll; called each frame by the app."""
        self._scroll += self.SPEED
        self.refresh()

    def render(self) -> Text:
        width = self.size.width
        height = self.size.height
        if width <= 0 or height <= 0:
            return Text("")
        if not self._title:
            return Text("⠶  no track loaded", style="dim italic")

        canvas = Canvas(width, height)
        text_w = text_width_dots(self._title, self.SCALE)
        # Vertically center the glyphs in the available dot-rows.
        y0 = max(0, (canvas.gh - text_height_dots(self.SCALE)) // 2)

        if text_w <= canvas.gw:
            # Fits: center it, no scrolling (plain title, no trailing separator).
            x0 = (canvas.gw - text_w) // 2
            draw_text(canvas, self._title, x0, y0, self.SCALE)
        else:
            # Marquee: repeat the loop unit (title + trailing separator) and
            # slide left, so the album is clearly spaced from the next artist.
            period = text_width_dots(self._loop, self.SCALE)
            off = int(self._scroll) % period
            draw_text(canvas, self._loop, -off, y0, self.SCALE)
            draw_text(canvas, self._loop, -off + period, y0, self.SCALE)

        return _canvas_to_text(canvas)


def _canvas_to_text(canvas: Canvas) -> Text:
    """Turn a Braille Canvas into styled Text (transparent; dim→normal→bold by
    per-cell dot density)."""
    codes = canvas.codes()
    density = canvas.density()
    styles = ("dim", "default", "bold")
    result = Text()
    for r in range(canvas.height):
        if r > 0:
            result.append("\n")
        row_codes = codes[r]
        row_den = density[r]
        for c in range(canvas.width):
            code = int(row_codes[c])
            if code == 0:
                result.append(" ")
            else:
                d = row_den[c]
                tier = 2 if d >= 5 else (1 if d >= 3 else 0)
                result.append(chr(0x2800 + code), style=styles[tier])
    return result


def _format_time(seconds: float) -> str:
    """Format seconds as M:SS."""
    if seconds < 0 or seconds != seconds:  # negative or NaN
        seconds = 0
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"
