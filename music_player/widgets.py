"""Custom Textual widgets: Browser, Visualizer, ProgressBar, NowPlaying."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from rich.text import Text
from textual import events
from textual.message import Message
from textual.widget import Widget

from .library import Album, Artist, Track


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
        elif key == "enter":
            self._enter()
            event.stop()
        elif key in ("left", "backspace", "escape", "h"):
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
            return "↑↓ move   ↵ open   q quit"
        return "↑↓ move   ↵ open   ← back"

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
            marker = "› " if selected else "  "
            style = "bold white on #2a2a3a" if selected else "default"
            line = f"{marker}{row}"
            if selected and width > 0:
                line = line.ljust(width)  # full-width highlight bar
            out.append(line + "\n", style=style)

        out.append("\n")
        out.append(self._hint(), style="dim")
        return out


class Visualizer(Widget):
    """Renders frequency bars using Unicode half-block characters.

    Half-blocks give double vertical resolution: each character cell is
    two "half-rows". Bars are colored green (low) to yellow (mid) to red (high),
    classic EQ style.
    """

    DECAY = 0.72  # smoothing between frames so bars don't flicker

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
            msg = Text("[ no signal ]", style="dim italic")
            padding = "\n" * (height // 2)
            return Text(padding) + Text(" " * ((width - 12) // 2)) + msg

        # Resample bars to fit the display: each bar takes 2 columns (bar + gap).
        n_bars = max(1, width // 2)
        source = self._latest_bars
        indices = np.linspace(0, len(source) - 1, n_bars).astype(int)
        display = source[indices]

        # Each character row = 2 half-cells of vertical resolution.
        max_level = 2 * height
        levels = np.clip(display * max_level, 0, max_level).astype(int)

        lines: list[Text] = []
        for row in range(height):
            row_from_bottom = height - row  # top row is height, bottom is 1
            top_half = row_from_bottom * 2       # half-cell index for top of this row
            bot_half = row_from_bottom * 2 - 1   # half-cell index for bottom of this row

            frac = row_from_bottom / height
            if frac > 0.75:
                color = "bold red"
            elif frac > 0.45:
                color = "yellow"
            else:
                color = "green"

            line = Text()
            for level in levels:
                top_on = level >= top_half
                bot_on = level >= bot_half
                if top_on:
                    char = "█"
                elif bot_on:
                    char = "▄"
                else:
                    char = " "
                line.append(char, style=color)
                line.append(" ")  # column gap
            lines.append(line)

        # Join with newlines.
        result = Text()
        for i, line in enumerate(lines):
            if i > 0:
                result.append("\n")
            result.append_text(line)
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


BROWSE_HINT = "b browse   space pause   n/p next/prev   +/- vol   q quit"


def _format_time(seconds: float) -> str:
    """Format seconds as M:SS."""
    if seconds < 0 or seconds != seconds:  # negative or NaN
        seconds = 0
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"
