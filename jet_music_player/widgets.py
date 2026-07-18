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
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget

from .braille import (
    DOT_H,
    Canvas,
    draw_text,
    text_height_dots,
    text_width_dots,
)


def _file_label(track: Track) -> str:
    """The folder-view display name for a track: its filename without the
    extension (a pure on-disk name, independent of metadata tags)."""
    return Path(track.path).stem


def _snap(dot_y: int) -> int:
    """Round a dot-row offset down to a Braille cell boundary (multiple of
    DOT_H). Bitmap-font text only renders solid when its top sits on a cell
    boundary; off-grid offsets split each glyph across two rows of cells and it
    reads as a hollow outline."""
    return (dot_y // DOT_H) * DOT_H
from pathlib import Path

from .library import Artist, FolderNode, Track


class Browser(Widget, can_focus=True):
    """A pure-text, SSH-menu-style library browser.

    No tree widget, no boxes: a single-column list of the current level with
    a `›` cursor. Arrow keys move, Enter drills down or plays a track, Left /
    Backspace goes back up a level.

    Two *view modes*, toggled with `b` (see `toggle_view_mode`):

      * "metadata" — the tag-based Artist -> Album -> Track drill-down.
      * "folders"  — a pure on-disk view that mirrors the directory tree,
        drilling folder-by-folder. Lets the user treat any folder as a
        playlist/bucket and shuffle everything under it.
    """

    # View modes.
    METADATA = "metadata"
    FOLDERS = "folders"

    # Levels of the metadata drill-down.
    ARTISTS = "artists"
    ALBUMS = "albums"
    TRACKS = "tracks"

    # A synthetic first row at the top level that shuffles the whole library,
    # presented as if it were an entry. Selecting it (Enter or `s`) plays every
    # track in random order. Shown at the ARTISTS level (metadata view) and at
    # the folder-view root.
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

    def __init__(
        self,
        library: dict[str, Artist],
        folder_tree: FolderNode | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._library = library
        self._folder_tree = folder_tree
        self._view = self.METADATA
        self._level = self.ARTISTS
        self._cursor = 0
        # Selection context as we drill down (metadata view).
        self._artist_name = ""
        self._album_name = ""
        # Navigation stack for the folder view: the chain of FolderNodes from
        # the root down to the folder currently shown. [] means metadata view;
        # [root] means the folder-view top level.
        self._folder_stack: list[FolderNode] = []
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

    def set_library(
        self, library: dict[str, Artist], folder_tree: FolderNode | None = None
    ) -> None:
        self._library = library
        self._folder_tree = folder_tree
        self._empty = not library
        self._scanning = False
        # Reset to the metadata Artists top level on a fresh library.
        self._view = self.METADATA
        self._level = self.ARTISTS
        self._folder_stack = []
        self._cursor = 0
        self._rebuild_rows()
        self.refresh()

    # ---- view mode ----

    @property
    def in_folder_view(self) -> bool:
        """True when the folder structure is currently shown (vs. Artists)."""
        return self._view == self.FOLDERS

    def reset_to_artists(self) -> None:
        """Return to the metadata Artists top level. Used when crossing into the
        browser from the player, so `b` from the player always lands on the
        default Artists view (a second `b` then flips to folders)."""
        self._view = self.METADATA
        self._level = self.ARTISTS
        self._folder_stack = []
        self._cursor = 0
        self._rebuild_rows()
        self.refresh()

    def toggle_view_mode(self) -> None:
        """Flip between the metadata (Artists) view and the folder view.

        Always lands at that view's top level with the cursor reset, so `b`
        reads as "show me the other structure from the top". A no-op if there's
        no folder tree to show.
        """
        if self._empty:
            return
        if self._view == self.METADATA:
            if self._folder_tree is None:
                return
            self._view = self.FOLDERS
            self._folder_stack = [self._folder_tree]
        else:
            self._view = self.METADATA
            self._level = self.ARTISTS
            self._folder_stack = []
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

    # ---- folder-view data ----

    def _folder_current(self) -> FolderNode:
        """The folder currently shown (top of the navigation stack)."""
        return self._folder_stack[-1]

    def _folder_at_root(self) -> bool:
        """True when the folder view is at its top level."""
        return len(self._folder_stack) == 1

    def _subfolders_sorted(self) -> list[str]:
        return sorted(self._folder_current().subfolders, key=str.lower)

    def _folder_tracks(self) -> list[Track]:
        """Tracks sitting directly in the current folder, name-sorted."""
        return sorted(self._folder_current().tracks, key=lambda t: _file_label(t).lower())

    def _folder_shuffle_all_offset(self) -> int:
        """Number of synthetic rows before the folder listing: a 'Shuffle All'
        row at the folder-view root only."""
        return 1 if self._folder_at_root() else 0

    def _rebuild_rows(self) -> None:
        """Recompute the visible row labels for the current level/view."""
        if self._empty:
            self._rows = []
            return
        if self._view == self.FOLDERS:
            self._rebuild_folder_rows()
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

    def _rebuild_folder_rows(self) -> None:
        """Rows for the current folder: subfolders (with a trailing '/') first,
        then this folder's own tracks (by filename). 'Shuffle All' leads the
        list at the folder-view root."""
        rows: list[str] = []
        if self._folder_at_root():
            rows.append(self.SHUFFLE_ALL_LABEL)
        for name in self._subfolders_sorted():
            rows.append(f"⌁  {name}/")
        for t in self._folder_tracks():
            rows.append(_file_label(t))
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
        whole album, or (on a track) that track's album. In the folder view,
        shuffles the highlighted folder and everything nested under it."""
        if not self._rows:
            return
        if self._view == self.FOLDERS:
            self._shuffle_folder_view()
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

    def _shuffle_folder_view(self) -> None:
        """Shuffle in the folder view. On the 'Shuffle All' row, shuffles the
        whole library; on a subfolder row, shuffles that folder and every track
        nested beneath it; on a track row, shuffles the current folder's own
        tracks (the local bucket)."""
        kind, obj = self._folder_row_target()
        if kind == "shuffle_all":
            self._shuffle_all()
            return
        if kind == "folder":
            tracks = obj.all_tracks()
            scope = obj.name
        else:  # "track": shuffle this folder's directly-held tracks
            tracks = self._folder_tracks()
            scope = self._folder_current().name
        if not tracks:
            return
        shuffled = list(tracks)
        random.shuffle(shuffled)
        self.post_message(self.ShuffleChosen(shuffled, scope))

    def _folder_row_target(self) -> tuple[str, object]:
        """Resolve what the cursor points at in the folder view.

        Returns ("shuffle_all", None), ("folder", FolderNode), or
        ("track", Track). Row order is: [Shuffle All (root only)], subfolders,
        then this folder's tracks — matching `_rebuild_folder_rows`.
        """
        idx = self._cursor
        off = self._folder_shuffle_all_offset()
        if off and idx == 0:
            return ("shuffle_all", None)
        idx -= off
        subs = self._subfolders_sorted()
        if idx < len(subs):
            return ("folder", self._folder_current().subfolders[subs[idx]])
        idx -= len(subs)
        tracks = self._folder_tracks()
        return ("track", tracks[idx])

    def _move(self, delta: int) -> None:
        if not self._rows:
            return
        self._cursor = (self._cursor + delta) % len(self._rows)
        self.refresh()

    def _enter(self) -> None:
        if not self._rows:
            return
        if self._view == self.FOLDERS:
            self._enter_folder_view()
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

    def _enter_folder_view(self) -> None:
        """Advance in the folder view: drill into a subfolder, or play a track.

        Playing a track uses the current folder's own tracks as the playlist
        (in filename order), so it behaves like an ad-hoc bucket. The 'artist'
        the player shows for these is the folder name.
        """
        kind, obj = self._folder_row_target()
        if kind == "shuffle_all":
            self._shuffle_all()
            return
        if kind == "folder":
            self._folder_stack.append(obj)
            self._cursor = 0
            self._rebuild_rows()
            self.refresh()
            return
        # Track: play it within this folder's tracks.
        tracks = self._folder_tracks()
        folder = self._folder_current()
        self.post_message(
            self.TrackChosen(obj, list(tracks), folder.name, "")
        )

    def _back(self) -> bool:
        """Go back up one drill-down level. Returns True if a level was popped,
        False if already at the top — the caller uses that to decide whether
        Escape should instead cross over to the player view."""
        if self._view == self.FOLDERS:
            return self._back_folder_view()
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

    def _back_folder_view(self) -> bool:
        """Pop one folder off the navigation stack, restoring the cursor onto
        the subfolder we came from. Returns False at the folder-view root, so
        Escape then crosses over to the player."""
        if self._folder_at_root():
            return False
        child = self._folder_stack.pop()
        self._rebuild_rows()
        # Put the cursor back on the folder we just left.
        subs = self._subfolders_sorted()
        off = self._folder_shuffle_all_offset()
        self._cursor = off + (subs.index(child.name) if child.name in subs else 0)
        self.refresh()
        return True

    def back_up_level(self) -> bool:
        """Public entry for the app's Escape handler: pop one browser level.
        Returns False when already at the top level (ARTISTS)."""
        return self._back()

    # ---- public nav entry points for the touch d-pad ----
    # These mirror the on_key handlers so the on-screen controls drive the same
    # navigation as the keyboard, without synthesizing key events.

    def nav_up(self) -> None:
        """Move the selection up one row (touch ▲)."""
        if not self._empty:
            self._move(-1)

    def nav_down(self) -> None:
        """Move the selection down one row (touch ▼)."""
        if not self._empty:
            self._move(1)

    def nav_select(self) -> None:
        """Open/drill-in or play the highlighted row (touch ▶ / select)."""
        if not self._empty:
            self._enter()

    # ---- render ----

    def _heading(self) -> str:
        if self._view == self.FOLDERS:
            if self._folder_at_root():
                return "FOLDERS"
            # Breadcrumb of the path below the root, e.g. "FOLDERS — Rock / 90s".
            crumb = " / ".join(n.name for n in self._folder_stack[1:])
            return f"FOLDERS — {crumb}"
        if self._level == self.ARTISTS:
            return "ARTISTS"
        if self._level == self.ALBUMS:
            return f"ALBUMS — {self._artist_name}"
        return f"{self._artist_name} — {self._album_name}"

    def _hint(self) -> str:
        if self._view == self.FOLDERS:
            if self._folder_at_root():
                return "↑↓ move   → open   s shuffle   b artists   d dir   q quit"
            return "↑↓ move   → open   ← back   s shuffle   b artists   d dir"
        if self._level == self.ARTISTS:
            return "↑↓ move   → open   s shuffle   b folders   d dir   q quit"
        return "↑↓ move   → open   ← back   s shuffle   b folders   d dir"

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
        - "radial"   : spectrum swept around a circle — a pulsing bloom/iris
    """

    MODES = ("bars", "mirror", "radial")
    MODE_LABELS = {
        "bars": "SPECTRUM",
        "mirror": "MIRROR",
        "radial": "RADIAL BLOOM",
    }

    DECAY = 0.80  # spectrum smoothing between frames: fast attack, slow release

    # When the volume changes, the spectrum is briefly replaced by a volume
    # meter for VOLUME_FRAMES ticks (~1 s at 30 fps) before the spectrum returns.
    VOLUME_FRAMES = 30

    class Tapped(Message):
        """Posted when the visualizer area is tapped — used by the touch
        overlay to toggle play/pause (there's no center d-pad button)."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._mode_idx = 0
        self._smoothed: np.ndarray | None = None   # smoothed spectrum bars
        self._vol_frames = 0        # ticks remaining to show the volume meter
        self._vol_value = 0.0       # volume (0..1) to display
        self._vol_anim = 0.0        # animation phase for the meter's flair
        self._radial_phase = 0.0    # slow rotation of the radial-bloom ring

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

    def on_click(self, event: events.Click) -> None:
        """Tapping the visualizer toggles play/pause (touch controls)."""
        event.stop()
        self.post_message(self.Tapped())

    def show_volume(self, volume: float) -> None:
        """Momentarily replace the spectrum with a volume meter showing `volume`
        (0..1). Called when the user changes the volume; auto-expires after
        VOLUME_FRAMES ticks."""
        self._vol_value = max(0.0, min(1.0, volume))
        self._vol_frames = self.VOLUME_FRAMES
        self.refresh()

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
        # Count down the volume-meter overlay, if active.
        if self._vol_frames > 0:
            self._vol_frames -= 1
            self._vol_anim += 1.0
        # Slowly rotate the radial bloom so it feels alive even between beats.
        self._radial_phase += 0.02
        self.refresh()

    # ---- rendering ----

    def render(self) -> Text:
        width = self.size.width
        height = self.size.height
        if width <= 0 or height <= 0:
            return Text("")

        canvas = Canvas(width, height)
        if self._vol_frames > 0:
            self._draw_volume(canvas)
        elif self.mode == "bars":
            self._draw_bars(canvas)
        elif self.mode == "mirror":
            self._draw_mirror(canvas)
        else:  # radial
            self._draw_radial(canvas)

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

    def _draw_volume(self, canvas: Canvas) -> None:
        """The transient volume overlay: a big "NN%" over an animated meter bar.

        Flair: the filled part of the meter shimmers with a travelling wave, and
        a little spark flickers at the fill's leading edge. Everything fades as
        the overlay's remaining frames run out, so it dissolves rather than
        snapping back to the spectrum."""
        gw, gh = canvas.gw, canvas.gh
        pct = int(round(self._vol_value * 100))
        label = f"{pct}%"

        # A tight block of: percentage text, a small gap, then the meter bar.
        # Pick the largest scale that leaves room for the meter, then center the
        # whole block both horizontally and vertically.
        TEXT_GAP = DOT_H       # dot-rows between text and bar
        BAR_H = 3              # filled-bar thickness in dots
        scale = 2
        while scale > 1 and (
            text_width_dots(label, scale) > gw - 4
            or text_height_dots(scale) + TEXT_GAP + BAR_H > gh
        ):
            scale -= 1
        tw = text_width_dots(label, scale)
        th = text_height_dots(scale)

        block_h = th + TEXT_GAP + BAR_H
        # Vertically center the block; snap the text's top to the cell grid so
        # its glyphs render solid rather than hollow.
        top = max(0, (gh - block_h) // 2)
        ty = _snap(top)
        tx = max(0, (gw - tw) // 2)
        draw_text(canvas, label, tx, ty, scale)

        # --- meter bar, centered under the text and spanning most of the width ---
        bar_y = min(gh - 1, ty + th + TEXT_GAP + BAR_H // 2)
        bar_w = gw - 2 * max(2, gw // 10)   # wide: ~80% of the visualizer width
        x_lo = (gw - bar_w) // 2
        x_hi = x_lo + bar_w
        fill_x = x_lo + int(round(bar_w * self._vol_value))

        # Unfilled remainder: sparse faint ticks so the full track is implied
        # without a solid rule reading as noise.
        if fill_x < x_hi:
            rest = np.arange(fill_x + 2, x_hi, 3)
            if len(rest):
                canvas.plot(rest, np.full_like(rest, bar_y))

        # Filled portion: a BAR_H-tall bar whose top edge shimmers with a
        # travelling sine wave, so the level reads as "live".
        if fill_x > x_lo:
            xs = np.arange(x_lo, fill_x)
            phase = self._vol_anim * 0.6
            wobble = np.round(np.sin(xs * 0.4 + phase)).astype(int)  # -1..+1 dots
            for dy in range(-(BAR_H // 2), BAR_H // 2 + 1):
                ys = np.clip(bar_y + dy + wobble, 0, gh - 1)
                canvas.plot(xs, ys)

        # Leading-edge spark: a small vertical burst at the fill head, jittering
        # and plotted twice (brighter) so it pops as the meter's live tip.
        if self._vol_value > 0.0:
            jitter = int(self._vol_anim) % 3 - 1
            tip = min(fill_x, x_hi - 1)
            spark_y = np.clip(
                np.array([bar_y - 2 + jitter, bar_y, bar_y + 2 - jitter]), 0, gh - 1
            )
            spark_x = np.full_like(spark_y, tip)
            canvas.plot(spark_x, spark_y)
            canvas.plot(spark_x, spark_y)

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

    def _draw_radial(self, canvas: Canvas) -> None:
        """The 32-band spectrum swept around a circle: a pulsing bloom/iris.

        Each band becomes a radial spoke from an inner ring outward, band 0
        (bass) at the top and frequencies increasing clockwise. The 32 bands are
        interpolated up to many angular spokes so the ring reads as a smooth
        bloom rather than 32 separate spikes. Overall loudness scales the whole
        reach, so loud passages bloom outward; a slow rotation (`_radial_phase`)
        keeps it alive between beats, and a faint inner ring holds the shape
        during silence.
        """
        bars = self._smoothed
        if bars is None:
            self._draw_flatline(canvas)
            return

        gw, gh = canvas.gw, canvas.gh
        cx = (gw - 1) / 2.0
        cy = (gh - 1) / 2.0

        # Fill the frame: the visualizer area is much wider than tall, so the
        # bloom is an ellipse fitted to the actual half-extents (cx wide, cy
        # tall) rather than a small circle limited by height. `radius` below is
        # a normalized 0..1 reach that's scaled onto x by cx and onto y by cy,
        # so at full amplitude spokes reach right to the frame edge.
        inner_frac = 0.34               # resting-ring radius (fraction of edge)
        reach_frac = 0.66               # extra radius at full amplitude
        r_unit = max(cx, cy)            # only used to pick angular resolution

        # Angular resolution: enough spokes to make a continuous ring at this
        # size, but capped so the per-frame work stays light.
        n_spokes = int(np.clip(r_unit * 2.5, 96, 640))

        # Interpolate the 32 bands around the full circle. Sampling positions map
        # spoke -> band; wrap so band 31 blends smoothly back to band 0.
        band_pos = np.linspace(0.0, len(bars), n_spokes, endpoint=False)
        lo = np.floor(band_pos).astype(int)
        frac = band_pos - lo
        amp = (bars[lo % len(bars)] * (1 - frac)
               + bars[(lo + 1) % len(bars)] * frac)
        amp = np.clip(amp, 0.0, 1.0)

        # Overall loudness gives the whole ring a "breath" — louder = bigger. A
        # high resting floor keeps the bloom large even at moderate loudness.
        energy = float(np.clip(bars.mean() * 1.8, 0.0, 1.0))
        radius = inner_frac + amp * reach_frac * (0.7 + 0.3 * energy)  # 0..1 reach

        # Angle per spoke: 0 at the top (−90°), increasing clockwise. Add the
        # slow rotation phase so the bloom turns over time.
        theta = -np.pi / 2 + self._radial_phase + np.arange(n_spokes) * (2 * np.pi / n_spokes)
        cos_t = np.cos(theta)
        sin_t = np.sin(theta)

        # Draw each spoke as a radial run of dots from the inner ring outward,
        # scaling the normalized radius onto the elliptical frame (cx × cy).
        # Oversample along the radius so the stroke is continuous.
        steps = max(6, int(max(cx, cy)))
        t = np.linspace(inner_frac, 1.0, steps)[None, :]  # normalized 0..1 reach
        # frac_r: (n_spokes, steps) from the inner ring out to each spoke's reach.
        frac_r = inner_frac + (radius - inner_frac)[:, None] * (
            (t - inner_frac) / max(1e-6, 1.0 - inner_frac)
        )
        xs = cx + frac_r * cx * cos_t[:, None]
        ys = cy + frac_r * cy * sin_t[:, None]
        canvas.plot(xs.ravel(), ys.ravel())

        # Brighten the resting inner ring and each spoke's tip so the shape holds
        # even when the music is quiet. Both are scaled onto the same ellipse.
        canvas.plot(cx + inner_frac * cx * cos_t, cy + inner_frac * cy * sin_t)
        tip_x = cx + radius * cx * cos_t
        tip_y = cy + radius * cy * sin_t
        canvas.plot(tip_x, tip_y)
        canvas.plot(tip_x, tip_y)


class BrowserBanner(Widget):
    """The static Braille header shown above the file browser on the home page.

    A big left-aligned "JET" (scale 2) with "TERMINAL MUSIC PLAYER" (scale 1)
    beneath it, in the same Braille bitmap font as the player banner. The
    lettering stays put, but animated horizontal speed-streaks stream leftward
    through its wake (the space to the right of "JET") to imply fast motion —
    like wind rushing past. Driven each frame by the app's tick loop.
    """

    TITLE = "JET"
    TITLE_SCALE = 2
    SUBTITLE = "TERMINAL MUSIC PLAYER"
    SUBTITLE_SCALE = 1
    LINE_GAP = 3       # dot-rows between line 1 and the subtitle

    # Wind streaks: horizontal dashes flowing left through the wake to the right
    # of the letters. Each streak gets its own continuous-random speed, phase,
    # length and loop period (see _wind_streaks), so no two ever line up into a
    # repeating vertical "wall" — the motion reads as chaotic rushing air.
    WIND_LANES = (1, 3, 5, 7, 9)   # dot-rows within the 10-dot title band
    WIND_PER_LANE = 2              # streaks per lane (kept low so long streaks in
                                   # the same lane rarely collide into a blob)
    WIND_GAP = 3                   # dot-cols between the letters and the wake
    WIND_SPEED_MIN = 1.8           # dots/frame
    WIND_SPEED_MAX = 3.6
    WIND_LEN_MIN = 7               # dash length in dots (longer than before)
    WIND_LEN_MAX = 12

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._phase = 0.0
        # Streak parameters, generated lazily once we know the wake width. Kept
        # fixed thereafter so the streaks don't flicker/regenerate each frame.
        self._streaks: list[tuple[int, float, float, int, float]] | None = None
        self._streaks_span = -1

    def tick(self) -> None:
        """Advance the wind animation; called each frame by the app."""
        self._phase += 1.0
        self.refresh()

    def render(self) -> Text:
        width = self.size.width
        height = self.size.height
        if width <= 0 or height <= 0:
            return Text("")

        canvas = Canvas(width, height)
        title_w = text_width_dots(self.TITLE, self.TITLE_SCALE)
        title_h = text_height_dots(self.TITLE_SCALE)

        # Wind first, in the wake to the right of the letters, so the letters
        # (drawn next) always sit clean on top.
        self._draw_wind(canvas, wake_x0=title_w + self.WIND_GAP, band_h=title_h)

        # Line 1: "JET", left-aligned, on top of the wind.
        draw_text(canvas, self.TITLE, 0, 0, self.TITLE_SCALE)

        # Line 2: subtitle, snapped to the cell grid so its glyphs stay solid.
        y1 = _snap(title_h + self.LINE_GAP)
        draw_text(canvas, self.SUBTITLE, 0, y1, self.SUBTITLE_SCALE)
        return _canvas_to_text(canvas)

    def _wind_streaks(self, span: int, band_h: int):
        """Return the fixed per-streak parameters for a given wake `span`, one
        tuple ``(y, speed, offset, length, period)`` per streak:

          * ``speed``  — drawn from a continuous range, so every streak moves at
            a distinct rate and they never resynchronize into a vertical wall.
          * ``offset`` — a random starting position along the streak's period.
          * ``period`` — the streak's own loop length (wake span plus a random
            margin), decoupled per streak so wraps don't line up either.

        Regenerated only when the wake width changes."""
        if self._streaks is not None and self._streaks_span == span:
            return self._streaks
        rng = np.random.default_rng(0x5EED)   # fixed seed → stable across frames
        streaks: list[tuple[int, float, float, int, float]] = []
        for y in self.WIND_LANES:
            if y >= band_h:
                continue
            for _ in range(self.WIND_PER_LANE):
                speed = float(rng.uniform(self.WIND_SPEED_MIN, self.WIND_SPEED_MAX))
                length = int(rng.integers(self.WIND_LEN_MIN, self.WIND_LEN_MAX + 1))
                period = span + float(rng.uniform(4, 16))
                offset = float(rng.uniform(0, period))
                streaks.append((y, speed, offset, length, period))
        self._streaks = streaks
        self._streaks_span = span
        return streaks

    def _draw_wind(self, canvas: Canvas, wake_x0: int, band_h: int) -> None:
        """Stream horizontal speed-streaks leftward through the wake region
        [wake_x0, canvas width). Nothing is drawn over the letters (wake_x0
        starts past them)."""
        gw = canvas.gw
        span = gw - wake_x0
        if span <= 4:
            return  # too narrow for a wake; skip the effect
        for y, speed, offset, length, period in self._wind_streaks(span, band_h):
            # Each streak slides left on its own period, so streaks with
            # different speeds never realign.
            x0 = wake_x0 + int((offset - self._phase * speed) % period)
            xs = np.arange(x0, x0 + length)
            ys = np.full_like(xs, y)
            m = (xs >= wake_x0) & (xs < gw)
            if m.any():
                canvas.plot(xs[m], ys[m])


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


class TouchButton(Widget, can_focus=False):
    """A single tappable control in the touch overlay.

    Renders one glyph centered in its box and posts a TouchPad.Pressed carrying
    its `action` name when clicked/tapped. Kept dumb: the app decides what each
    action does based on the current view (browser vs player).
    """

    def __init__(self, action: str, glyph: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.action_name = action
        self.glyph = glyph

    def render(self) -> Text:
        # Center the glyph vertically within the button's height.
        h = max(1, self.size.height)
        pad = (h - 1) // 2
        out = Text()
        out.append("\n" * pad)
        out.append(self.glyph, style="bold")
        return out

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(TouchPad.Pressed(self.action_name))


class TouchPad(Widget, can_focus=False):
    """On-screen touch controls for Android/Termux, docked bottom-right.

    A 4-way d-pad (no center button) plus two dedicated buttons. The app routes
    each press to an existing action depending on whether the browser or the
    player is showing:

        ▲  browser: move up      player: volume up
        ▼  browser: move down    player: volume down
        ▶  browser: open/select  player: next track
        ◀  browser: back a level player: back to browser
              (at the top level, ◀ quits — see the app's handler)
        v  cycle visualizer
        d  change directory

    Play/pause isn't here: tapping the visualizer/now-playing area toggles it.
    """

    class Pressed(Message):
        """Posted when a touch control is tapped. `action` is one of:
        "up", "down", "left", "right", "viz", "dir"."""

        def __init__(self, action: str) -> None:
            super().__init__()
            self.action = action

    def compose(self) -> ComposeResult:
        # Layout (each cell is a TouchButton):
        #        [ ▲ ]
        #   [ ◀ ] [ ▶ ]
        #   [ v ] [ d ]
        #        [ ▼ ]
        with Vertical(id="touchpad-inner"):
            with Horizontal(classes="touch-row"):
                yield TouchButton("up", "▲", classes="touch-btn touch-up")
            with Horizontal(classes="touch-row"):
                yield TouchButton("left", "◀", classes="touch-btn")
                yield TouchButton("right", "▶", classes="touch-btn")
            with Horizontal(classes="touch-row"):
                yield TouchButton("viz", "v", classes="touch-btn touch-aux")
                yield TouchButton("dir", "d", classes="touch-btn touch-aux")
            with Horizontal(classes="touch-row"):
                yield TouchButton("down", "▼", classes="touch-btn touch-down")
