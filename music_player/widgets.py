"""Custom Textual widgets: Browser, Visualizer, ProgressBar, NowPlaying."""

from __future__ import annotations

import numpy as np
from rich.text import Text
from textual import events
from textual.message import Message
from textual.widget import Widget

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
            return "↑↓ move   → open   q quit"
        return "↑↓ move   → open   ← back"

    def render(self) -> Text:
        width = self.size.width
        out = Text()
        out.append(self._heading() + "\n", style="bold cyan")
        out.append("\n")

        if self._empty:
            out.append("(no audio files found)\n", style="dim italic")
            out.append("\nPass a music directory: terminaltunes /path/to/music\n", style="dim")
            return out

        for i, row in enumerate(self._rows):
            selected = i == self._cursor
            # Selection is marked by the cursor + bold text only — no background
            # fill, so the terminal shows through everywhere.
            marker = "› " if selected else "  "
            style = "bold" if selected else "dim"
            out.append(f"{marker}{row}\n", style=style)

        out.append("\n")
        out.append(self._hint(), style="dim")
        return out


class Visualizer(Widget):
    """A radial / mirrored ASCII spectrum.

    The frequency spectrum is mirrored around a horizontal center line: each
    band grows both up and down symmetrically, so the whole thing pulses like
    a sculpted waveform rather than a flat row of bars. Intensity is drawn with
    a density ramp of ASCII/box glyphs (faint dots for the fringes, solid
    blocks at the core) — no color fills, so it stays fully transparent and
    reads as ASCII art.
    """

    DECAY = 0.78  # smoothing between frames: fast attack, slow release

    # Density ramp from the outer fringe of a band inward to its peak. The
    # tip of a bar uses the first glyph; the core near the center uses the last.
    RAMP = " ·:!|┃█"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._smoothed: np.ndarray | None = None
        self._latest_bars: np.ndarray | None = None

    def update_bars(self, bars: np.ndarray | None) -> None:
        """Push new bar values in and refresh."""
        if bars is None:
            self._latest_bars = None
            self._smoothed = None
        else:
            if self._smoothed is None or len(self._smoothed) != len(bars):
                self._smoothed = bars.copy()
            else:
                # Peak-hold with decay: fast attack, slow release.
                self._smoothed = np.maximum(bars, self._smoothed * self.DECAY)
            self._latest_bars = self._smoothed
        self.refresh()

    def render(self) -> Text:
        width = self.size.width
        height = self.size.height
        if width <= 0 or height <= 0:
            return Text("")
        if self._latest_bars is None:
            return self._idle(width, height)

        # One band per column (with a gap column between), resampled to width.
        n_cols = max(1, (width + 1) // 2)
        source = self._latest_bars
        indices = np.linspace(0, len(source) - 1, n_cols).astype(int)
        display = source[indices]

        # Mirror around the vertical center. Each half spans `half` rows.
        half = max(1, height // 2)
        center = half  # row index of the center line

        # How many rows each band reaches out from the center (0..half).
        reach = np.clip(display * half, 0, half)

        ramp = self.RAMP
        n_ramp = len(ramp) - 1  # index 0 is space (empty)

        lines: list[Text] = []
        for row in range(height):
            dist = abs(row - center)  # rows away from the center line
            line = Text()
            for col_reach in reach:
                if dist == 0:
                    # Center line: a steady spine, brighter where energy is high.
                    ch = "━" if col_reach < half * 0.5 else "═"
                    style = "dim" if col_reach < half * 0.5 else "default"
                elif dist <= col_reach:
                    # Inside the bar. Density ramps up toward the center.
                    frac = 1.0 - (dist - 1) / max(1, col_reach)  # 1 at core, ->0 at tip
                    ramp_idx = 1 + int(frac * (n_ramp - 1))
                    ramp_idx = max(1, min(n_ramp, ramp_idx))
                    ch = ramp[ramp_idx]
                    # Brighter near the core, dimmer at the fringes.
                    style = "default" if frac > 0.55 else "dim"
                else:
                    ch = " "
                    style = "default"
                line.append(ch, style=style)
                line.append(" ")  # gap column between bands
            lines.append(line)

        result = Text()
        for i, line in enumerate(lines):
            if i > 0:
                result.append("\n")
            result.append_text(line)
        return result

    def _idle(self, width: int, height: int) -> Text:
        """A calm ASCII center line when nothing is playing."""
        center = height // 2
        result = Text()
        for row in range(height):
            if row > 0:
                result.append("\n")
            if row == center:
                spine = "·" + " ·" * ((width - 1) // 2)
                result.append(spine[:width], style="dim")
        return result


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

    def render(self) -> Text:
        width = self.size.width
        if width <= 0:
            return Text("")

        pos_str = _format_time(self._pos)
        total_str = _format_time(self._total)
        labels = f"{pos_str}  " + "  " + f"  {total_str}"
        bar_width = width - len(pos_str) - len(total_str) - 4
        if bar_width < 4:
            return Text(f"{pos_str} / {total_str}")

        if self._total > 0:
            fraction = max(0.0, min(1.0, self._pos / self._total))
        else:
            fraction = 0.0

        filled = int(bar_width * fraction)
        bar = Text()
        bar.append(f"{pos_str} ", style="bold cyan")
        bar.append("█" * filled, style="cyan")
        bar.append("─" * (bar_width - filled), style="dim")
        bar.append(f" {total_str}", style="bold cyan")
        return bar


class NowPlaying(Widget):
    """Shows current track title, album, and artist."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._title = ""
        self._album = ""
        self._artist = ""
        self._status = "stopped"

    def set_track(self, title: str, album: str, artist: str) -> None:
        self._title = title
        self._album = album
        self._artist = artist
        self.refresh()

    def set_status(self, status: str) -> None:
        """status is one of: playing, paused, stopped."""
        self._status = status
        self.refresh()

    def render(self) -> Text:
        if not self._title:
            return Text("♪  no track loaded", style="dim italic")

        icon = {"playing": "▶", "paused": "⏸", "stopped": "■"}.get(self._status, "♪")
        text = Text()
        text.append(f"{icon}  ", style="bold magenta")
        text.append(self._title, style="bold white")
        text.append("\n")
        text.append(f"    {self._artist}", style="cyan")
        if self._album:
            text.append(f"  ·  ", style="dim")
            text.append(self._album, style="italic")
        return text


def _format_time(seconds: float) -> str:
    """Format seconds as M:SS."""
    if seconds < 0 or seconds != seconds:  # negative or NaN
        seconds = 0
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"
