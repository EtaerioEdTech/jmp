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

    # A synthetic first row at the ARTISTS level that shuffles the whole
    # library, presented as if it were an artist. Selecting it (Enter or `s`)
    # plays every track in random order.
    SHUFFLE_ALL_LABEL = "⤮  Shuffle All"

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
        # True while the library is still being scanned in the background; shows
        # a "Scanning…" placeholder instead of the empty-library message.
        self._scanning = False
        self._rebuild_rows()

    def set_scanning(self, scanning: bool) -> None:
        self._scanning = scanning
        self.refresh()

    # ---- data ----

    def set_library(self, library: dict[str, Artist]) -> None:
        self._library = library
        self._empty = not library
        self._scanning = False
        self._level = self.ARTISTS
        self._cursor = 0
        self._rebuild_rows()
        self.refresh()

    def _artists_sorted(self) -> list[str]:
        return sorted(self._library, key=str.lower)

    def _on_shuffle_all(self) -> bool:
        """True when the cursor is on the synthetic 'Shuffle All' row."""
        return self._level == self.ARTISTS and self._cursor == 0

    def _cursor_artist(self) -> str:
        """The artist under the cursor at the ARTISTS level, accounting for the
        'Shuffle All' row occupying index 0. Assumes not on the shuffle row."""
        return self._artists_sorted()[self._cursor - 1]

    def _all_tracks(self) -> list[Track]:
        """Every track in the library, in no particular order."""
        return [
            t
            for artist in self._library.values()
            for album in artist.albums.values()
            for t in album.tracks
        ]

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
            # "Shuffle All" sits at the top, as if it were an artist.
            self._rows = [self.SHUFFLE_ALL_LABEL] + self._artists_sorted()
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
        if self._on_shuffle_all():
            self._shuffle_all()
            return
        if self._level == self.ARTISTS:
            name = self._cursor_artist()
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

    def _shuffle_all(self) -> None:
        """Shuffle every track in the library."""
        tracks = self._all_tracks()
        if not tracks:
            return
        random.shuffle(tracks)
        self.post_message(self.ShuffleChosen(tracks, "Shuffle All"))

    def _move(self, delta: int) -> None:
        if not self._rows:
            return
        self._cursor = (self._cursor + delta) % len(self._rows)
        self.refresh()

    def _enter(self) -> None:
        if not self._rows:
            return
        if self._on_shuffle_all():
            self._shuffle_all()
            return
        if self._level == self.ARTISTS:
            self._artist_name = self._cursor_artist()
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

    def _back(self) -> bool:
        """Go back up one drill-down level. Returns True if a level was popped,
        False if already at the top (ARTISTS) — the caller uses that to decide
        whether Escape should instead cross over to the player view."""
        if self._level == self.TRACKS:
            self._level = self.ALBUMS
            # Restore cursor to the album we came from.
            albums = self._albums_sorted()
            self._cursor = albums.index(self._album_name) if self._album_name in albums else 0
            self._rebuild_rows()
            self.refresh()
            return True
        elif self._level == self.ALBUMS:
            self._level = self.ARTISTS
            artists = self._artists_sorted()
            # +1 for the "Shuffle All" row occupying index 0; fall back to the
            # first real artist (index 1) rather than the shuffle row.
            self._cursor = artists.index(self._artist_name) + 1 if self._artist_name in artists else 1
            self._rebuild_rows()
            self.refresh()
            return True
        # At ARTISTS level, back does nothing here.
        return False

    def back_up_level(self) -> bool:
        """Public entry for the app's Escape handler: pop one browser level.
        Returns False when already at the top level (ARTISTS)."""
        return self._back()

    # ---- render ----

    def _heading(self) -> str:
        if self._level == self.ARTISTS:
            return "ARTISTS"
        if self._level == self.ALBUMS:
            return f"ALBUMS — {self._artist_name}"
        return f"{self._artist_name} — {self._album_name}"

    def _hint(self) -> str:
        if self._level == self.ARTISTS:
            return "↑↓ move   → open   s shuffle   d dir   q quit"
        return "↑↓ move   → open   ← back   s shuffle   d dir"

    # Fixed chrome around the scrolling row window: heading + rule + blank line
    # above, blank line + hint below.
    _HEADER_LINES = 3
    _FOOTER_LINES = 2

    def _visible_window(self, capacity: int) -> tuple[int, int]:
        """The [start, end) slice of `self._rows` to show so the cursor stays
        on screen, given how many rows fit (`capacity`). Scrolls only when the
        cursor would otherwise fall outside the window."""
        n = len(self._rows)
        if capacity <= 0:
            return 0, 0
        if capacity >= n:
            return 0, n
        # Center the cursor in the window where possible, then clamp to the ends
        # so we never scroll past the first or last row.
        start = self._cursor - capacity // 2
        start = max(0, min(start, n - capacity))
        return start, start + capacity

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

        if self._scanning:
            out.append("⣷ Scanning your music library…\n", style="dim italic")
            return out

        if self._empty:
            out.append("(no audio files found)\n", style="dim italic")
            out.append("\nPass a music directory: jmp /path/to/music\n", style="dim")
            return out

        # Only render the slice of rows that fits, scrolling it with the cursor.
        # A plain widget's render output is clipped (never scrolled) by Textual,
        # so without this a long list would run off the bottom of the screen.
        # Reserve one line each for the "⋯" more-above / more-below cues so the
        # window that actually holds rows still guarantees the cursor is shown.
        capacity = self.size.height - self._HEADER_LINES - self._FOOTER_LINES
        rows_capacity = capacity - 2 if capacity < len(self._rows) else capacity
        start, end = self._visible_window(rows_capacity)

        # A dim "⋯ more above" / "⋯ more below" cue when the list is clipped,
        # so it's clear the window is scrolled rather than truncated.
        if start > 0:
            out.append("  ⋯\n", style="dim")

        for i in range(start, end):
            row = self._rows[i]
            selected = i == self._cursor
            # Selection is marked by a Braille cursor + bold text — no background
            # fill, so the terminal shows through everywhere.
            marker = "⣿ " if selected else "  "
            style = "bold" if selected else "dim"
            out.append(f"{marker}{row}\n", style=style)

        if end < len(self._rows):
            out.append("  ⋯\n", style="dim")

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


class BrowserBanner(Widget):
    """The static Braille header shown above the file browser on the home page.

    Two left-aligned lines in the same Braille bitmap font as the player banner:
    a big "JET" (scale 2) with "MUSIC IN YOUR TERMINAL" (scale 1) beneath it.
    Purely decorative and unchanging — no scrolling, no state.
    """

    TITLE = "JET"
    TITLE_SCALE = 2
    SUBTITLE = "MUSIC IN YOUR TERMINAL"
    SUBTITLE_SCALE = 1
    LINE_GAP = 3   # dot-rows between the two lines

    def render(self) -> Text:
        width = self.size.width
        height = self.size.height
        if width <= 0 or height <= 0:
            return Text("")

        canvas = Canvas(width, height)
        # Left-aligned: both lines start at the left edge (dot x = 0).
        draw_text(canvas, self.TITLE, 0, 0, self.TITLE_SCALE)
        y1 = text_height_dots(self.TITLE_SCALE) + self.LINE_GAP
        draw_text(canvas, self.SUBTITLE, 0, y1, self.SUBTITLE_SCALE)
        return _canvas_to_text(canvas)


class UpNext(Widget):
    """A one-line "Up Next: <track> by <artist>" footer for the player.

    Shows what will play when the current track finishes. Blank when there's
    nothing queued (no playlist / no next track).

    The widget itself is the invisible fixed-width container: it spans the full
    player width. When the line fits, it sits right-aligned in that width (the
    tucked-into-the-corner look). When the window is too narrow for the whole
    line, it marquees horizontally *within* the widget instead of being clipped,
    so the queued track is always fully readable as it scrolls past.
    """

    SPEED = 0.5        # cells scrolled per frame when marqueeing
    LOOP_SEP = "   ·   "  # gap joining the marquee's wrap, so the end of the
                          # line is clear before it repeats

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._title = ""
        self._artist = ""
        self._scroll = 0.0

    def set_next(self, title: str, artist: str) -> None:
        # Reset the scroll so a newly queued track starts from the right edge.
        if title != self._title or artist != self._artist:
            self._scroll = 0.0
        self._title = title or ""
        self._artist = artist or ""
        self.refresh()

    def clear(self) -> None:
        self._title = ""
        self._artist = ""
        self._scroll = 0.0
        self.refresh()

    def tick(self) -> None:
        """Advance the marquee scroll; called each frame by the app. Only does
        anything when the line is actually too wide to fit (see render)."""
        if not self._title:
            return
        self._scroll += self.SPEED
        self.refresh()

    def _content(self) -> Text:
        """The full "Up Next: … by …" line, unclipped."""
        out = Text()
        out.append("Up Next: ", style="dim bold")
        out.append(self._title, style="dim")
        if self._artist:
            out.append(" by ", style="dim")
            out.append(self._artist, style="dim")
        return out

    def render(self) -> Text:
        if not self._title:
            return Text("")

        width = self.size.width
        content = self._content()
        text_w = content.cell_len

        # Fits: right-align in the widget width (the original tucked-in look).
        # Also covers the pre-layout frame where width is still 0.
        if width <= 0 or text_w <= width:
            self._scroll = 0.0
            return content

        # Too wide: marquee within the widget width. Build one loop period
        # (line + separator), then slice a `width`-wide window that slides left,
        # tiling copies so the wrap-around is seamless.
        loop = content.copy()
        loop.append(self.LOOP_SEP, style="dim")
        period = loop.cell_len

        tiled = Text()
        # Enough copies to always cover a full window plus the sliding offset.
        copies = width // period + 2
        for _ in range(copies):
            tiled.append_text(loop)

        off = int(self._scroll) % period
        window = tiled[off:off + width]
        return window


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

    # A Braille cell is 4 dots tall. The elapsed portion is the solid full-cell
    # block; the remaining track is the slim, vertically centered two-dot line.
    # So the bar fills with solid from the left as the song plays, leaving a
    # thin centered channel ahead of the playhead.
    _DONE = chr(0x28FF)   # ⣿ full cell — elapsed (solid)
    _TRACK = chr(0x2836)  # ⠶ middle two dot-rows — remaining (slim centered line)

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
    """Two-line title in Braille bitmap text:

        ARTIST   (big)
        track    (half-size, below)

    Each line is centered when it fits the width and marquees (loops) only when
    the window is too narrow to hold it. Monochrome and transparent like the
    rest.
    """

    ARTIST_SCALE = 2   # artist line (each font pixel → 2×2 Braille dots)
    TRACK_SCALE = 1    # track line, half the height, below the artist
    SPEED = 0.7        # dot-columns scrolled per frame when marqueeing
    LOOP_SEP = " - "   # hyphen separator joining a marquee's wrap, so the end
                       # of a scrolling title is clear before it repeats
    LINE_GAP = 4       # dot-rows of vertical padding between artist and track

    # Shown as the (scrolling) artist line when nothing is playing yet.
    WAITING_ARTIST = "WAITING FOR TRACK"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._artist = ""       # top line
        self._track = ""        # bottom line, shown smaller
        self._status = "stopped"
        self._scroll = 0.0

    def set_track(self, title: str, album: str, artist: str) -> None:
        # `album` is accepted for API compatibility but no longer shown.
        self._artist = artist or ""
        self._track = title or ""
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

        canvas = Canvas(width, height)

        # Nothing playing yet: show "WAITING FOR TRACK" as a single scrolling
        # artist line (no track line beneath), and always marquee it.
        if not self._artist and not self._track:
            artist_h = text_height_dots(self.ARTIST_SCALE)
            y0 = max(0, (canvas.gh - artist_h) // 2)
            self._draw_line(
                canvas, self.WAITING_ARTIST, self.ARTIST_SCALE, y0,
                direction=1, force_scroll=True,
            )
            return _canvas_to_text(canvas)

        # Stack the two lines vertically: big artist, small track beneath, with
        # LINE_GAP dot-rows of padding. Center the block in the available rows.
        artist_h = text_height_dots(self.ARTIST_SCALE)
        track_h = text_height_dots(self.TRACK_SCALE) if self._track else 0
        gap = self.LINE_GAP if (self._artist and self._track) else 0
        block_h = artist_h + gap + track_h
        y0 = max(0, (canvas.gh - block_h) // 2)

        if self._artist:
            self._draw_line(canvas, self._artist, self.ARTIST_SCALE, y0, direction=1)
        if self._track:
            # Same scroll direction as the artist line above it.
            self._draw_line(canvas, self._track, self.TRACK_SCALE, y0 + artist_h + gap, direction=1)

        return _canvas_to_text(canvas)

    def _draw_line(
        self, canvas: Canvas, text: str, scale: int, y0: int,
        direction: int = -1, force_scroll: bool = False,
    ) -> None:
        """Draw one line: centered if it fits the width, else marqueed.

        `direction` sets the scroll sense: -1 slides left, +1 slides right.
        `force_scroll` marquees even when the text would fit (used for the
        "WAITING FOR TRACK" placeholder, which should always be in motion).
        """
        text_w = text_width_dots(text, scale)
        if text_w <= canvas.gw and not force_scroll:
            x0 = (canvas.gw - text_w) // 2
            draw_text(canvas, text, x0, y0, scale)
            return
        # Marquee: repeat the line + a wrap gap so the loop is even, then tile
        # enough copies to cover the full width (the loop can be narrower than
        # the canvas when force_scroll is set, so two copies may not be enough).
        loop = text + self.LOOP_SEP
        period = text_width_dots(loop, scale)
        off = (direction * int(self._scroll)) % period
        x = -off
        # +2 copies of headroom so both the leading (possibly negative) and
        # trailing edges are always drawn past the canvas bounds.
        copies = canvas.gw // period + 2
        for _ in range(copies):
            draw_text(canvas, loop, x, y0, scale)
            x += period


_STYLES = ("dim", "default", "bold")


def _canvas_to_text(canvas: Canvas) -> Text:
    """Turn a Braille Canvas into styled Text (transparent; dim→normal→bold by
    per-cell dot density).

    Emits *run-length* spans — consecutive cells sharing a style become one
    `Text.append` — instead of one append per cell. Rows are mostly empty or
    uniform, so this collapses thousands of calls into a handful per row, which
    keeps the frame rate up as the window (and canvas) grows.
    """
    codes = canvas.codes()
    density = canvas.density()

    # Map each cell to a style tier 0/1/2 (empty cells → tier 0, rendered as a
    # space). Vectorized so the per-cell work happens in numpy, not Python.
    empty = codes == 0
    tier = np.where(density >= 5, 2, np.where(density >= 3, 1, 0)).astype(np.int8)
    # Empty cells all share one "space" run regardless of density; give them a
    # sentinel tier so they group together and never carry a style.
    tier = np.where(empty, -1, tier)

    # Precompute the glyph string for every cell once.
    glyph_codes = (0x2800 + codes).astype(np.int32)

    result = Text()
    height, width = canvas.height, canvas.width
    for r in range(height):
        if r > 0:
            result.append("\n")
        row_tier = tier[r]
        row_glyphs = glyph_codes[r]
        row_empty = empty[r]
        # Walk run-boundaries: a new run starts wherever the tier changes.
        c = 0
        while c < width:
            t = row_tier[c]
            end = c + 1
            while end < width and row_tier[end] == t:
                end += 1
            if t == -1:  # empty run → spaces, no style
                result.append(" " * (end - c))
            else:
                segment = "".join(chr(int(g)) for g in row_glyphs[c:end])
                result.append(segment, style=_STYLES[t])
            c = end
    return result


def _format_time(seconds: float) -> str:
    """Format seconds as M:SS."""
    if seconds < 0 or seconds != seconds:  # negative or NaN
        seconds = 0
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"
