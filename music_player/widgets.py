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
    """A flowing harmonograph curve — a Lissajous figure driven by the music.

    Instead of bars, a single continuous parametric curve is traced across the
    field:

        x(θ) = Σ Aᵢ · sin(fᵢ·θ + φᵢ) · e^(−dᵢθ)
        y(θ) = Σ Bᵢ · sin(gᵢ·θ + ψᵢ) · e^(−dᵢθ)

    This is a *harmonograph* (the curve two coupled pendulums draw). Because the
    component frequencies fᵢ, gᵢ are small integer ratios, the figure is
    periodic — it looks chaotic and hand-drawn but is fully deterministic and
    closes into a loop. A slowly advancing global phase rotates the whole
    figure so it seems random yet never repeats moment-to-moment, and the
    live FFT spectrum modulates the amplitudes and phases so the curve breathes
    with the sound. Where the curve folds over itself the passes pile up into
    bright caustics — the geometric beauty is intrinsic to the math.

    Rendering uses Braille characters (U+2800…): every cell packs a 2×4 dot
    grid, giving 8× the resolution of one glyph per cell, so the curve reads as
    a fine, light line rather than chunky blocks. No color fills — fully
    transparent — with brightness carried by dim/normal styling on the dots.
    """

    DECAY = 0.82  # spectrum smoothing between frames: fast attack, slow release

    # Braille cell geometry: 2 dot-columns × 4 dot-rows per character.
    DOT_W = 2
    DOT_H = 4
    # Bit weight of each (dx, dy) dot within a Braille cell (Unicode layout).
    _DOT_BITS = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))

    # Integer-ratio harmonics for the two axes. Small coprime ratios give
    # closed rosette figures; the slowly advancing phase makes them precess.
    FX = (1.0, 2.0, 3.0)      # x-axis harmonic multipliers
    FY = (1.0, 2.0, 3.0)      # y-axis harmonic multipliers

    THETA_SPAN = 24 * np.pi   # how far along the curve we trace (12 lobes)
    N_SAMPLES = 2600          # points sampled along the curve per frame

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._smoothed: np.ndarray | None = None
        self._active = False
        self._phase = 0.0  # global phase, advances every frame → animation

    def update_bars(self, bars: np.ndarray | None) -> None:
        """Push a new spectrum frame in, advance the animation, and refresh."""
        # The phase always advances so the figure keeps flowing between the
        # spectrogram's coarser (20 fps) updates.
        self._phase += 0.05

        if bars is None:
            self._active = False
            self._smoothed = None
        else:
            self._active = True
            if self._smoothed is None or len(self._smoothed) != len(bars):
                self._smoothed = bars.astype(float).copy()
            else:
                self._smoothed = np.maximum(bars, self._smoothed * self.DECAY)
        self.refresh()

    # ---- spectrum → curve parameters ----

    def _bands(self) -> tuple[float, float, float]:
        """Collapse the spectrum into (bass, mid, treble) energies in ~0..1."""
        if self._smoothed is None or len(self._smoothed) == 0:
            return 0.0, 0.0, 0.0
        s = self._smoothed
        n = len(s)
        bass = float(s[: n // 3].mean())
        mid = float(s[n // 3 : 2 * n // 3].mean())
        treble = float(s[2 * n // 3 :].mean())
        return bass, mid, treble

    def _curve(self, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
        """Compute the harmonograph curve as integer dot coordinates (dx, dy)
        on the high-resolution Braille sub-grid."""
        bass, mid, treble = self._bands() if self._active else (0.28, 0.16, 0.09)

        theta = np.linspace(0.0, self.THETA_SPAN, self.N_SAMPLES)
        p = self._phase

        # Amplitudes and phases per harmonic, modulated by the spectrum. Bass
        # swells the fundamental lobe; treble adds fine high-harmonic filigree.
        ax = np.array([0.75 + 0.7 * bass, 0.45 + 0.6 * mid, 0.22 + 0.7 * treble])
        ay = np.array([0.75 + 0.7 * mid, 0.45 + 0.6 * treble, 0.22 + 0.7 * bass])
        # Golden-angle offsets keep harmonics from lining up; p precesses all.
        phix = p * np.array([1.0, 2.0, 3.0]) + np.array([0.0, 2.399963, 4.799926])
        phiy = p * np.array([1.5, 2.5, 3.5]) + np.array([1.570796, 3.970759, 6.370722])
        # Gentle damping along the trace gives the inward-spiralling envelope.
        damp = np.exp(-0.018 * theta)

        x = (ax[0] * np.sin(self.FX[0] * theta + phix[0])
             + ax[1] * np.sin(self.FX[1] * theta + phix[1])
             + ax[2] * np.sin(self.FX[2] * theta + phix[2])) * damp
        y = (ay[0] * np.sin(self.FY[0] * theta + phiy[0])
             + ay[1] * np.sin(self.FY[1] * theta + phiy[1])
             + ay[2] * np.sin(self.FY[2] * theta + phiy[2])) * damp

        # High-res sub-pixel grid dimensions.
        gw = width * self.DOT_W
        gh = height * self.DOT_H

        # Fit the curve's bounding box into the sub-grid, centered, aspect kept.
        xmid = (x.min() + x.max()) / 2.0
        ymid = (y.min() + y.max()) / 2.0
        xspan = max(x.max() - x.min(), 1e-6)
        yspan = max(y.max() - y.min(), 1e-6)
        scale = min((gw - 1) / xspan, (gh - 1) / yspan) * 0.94
        dx = ((gw - 1) / 2.0 + (x - xmid) * scale)
        dy = ((gh - 1) / 2.0 + (y - ymid) * scale)
        return dx, dy

    def render(self) -> Text:
        width = self.size.width
        height = self.size.height
        if width <= 0 or height <= 0:
            return Text("")

        dx, dy = self._curve(width, height)
        gw = width * self.DOT_W
        gh = height * self.DOT_H

        # Draw the curve as a *connected* line: supersample along it so there
        # are no gaps even at high resolution (a light, continuous stroke rather
        # than scattered dots). A fixed oversample keeps this fully vectorized —
        # no per-segment Python loop — so it stays fast enough for 30 fps.
        OVER = 4
        # Linear interpolation between each pair of samples at OVER sub-steps.
        t = (np.arange(OVER) / OVER)[None, :]  # (1, OVER)
        px = (dx[:-1, None] + np.diff(dx)[:, None] * t).ravel().astype(int)
        py = (dy[:-1, None] + np.diff(dy)[:, None] * t).ravel().astype(int)
        np.clip(px, 0, gw - 1, out=px)
        np.clip(py, 0, gh - 1, out=py)

        # Accumulate hit density on the sub-grid: folds/caustics pile up bright.
        dots = np.zeros((gh, gw), dtype=np.float32)
        np.add.at(dots, (py, px), 1.0)
        hit = dots > 0

        # Vectorized Braille packing. Reshape the (gh, gw) dot grid into
        # (height, DOT_H, width, DOT_W) blocks, weight each dot by its Braille
        # bit, and OR the bits together to get one code per cell — no per-cell
        # Python loop, so this stays fast enough for 30 fps.
        bit_weights = np.array(
            [[self._DOT_BITS[ddx][ddy] for ddx in range(self.DOT_W)]
             for ddy in range(self.DOT_H)],
            dtype=np.int32,
        )  # shape (DOT_H, DOT_W)
        blocks = hit.reshape(height, self.DOT_H, width, self.DOT_W)
        codes = (blocks * bit_weights[None, :, None, :]).sum(axis=(1, 3)).astype(np.int32)
        # Per-cell density total → brightness tier (0 dim, 1 default, 2 bold).
        totals = dots.reshape(height, self.DOT_H, width, self.DOT_W).sum(axis=(1, 3))
        tier = np.where(totals >= 3, 2, np.where(totals >= 2, 1, 0))

        styles = ("dim", "default", "bold")
        lines: list[Text] = []
        for cy in range(height):
            line = Text()
            row_codes = codes[cy]
            row_tier = tier[cy]
            # Group consecutive cells that share a style into single spans.
            for cx in range(width):
                code = int(row_codes[cx])
                if code == 0:
                    line.append(" ")
                else:
                    line.append(chr(0x2800 + code), style=styles[int(row_tier[cx])])
            lines.append(line)

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


def _format_time(seconds: float) -> str:
    """Format seconds as M:SS."""
    if seconds < 0 or seconds != seconds:  # negative or NaN
        seconds = 0
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"
