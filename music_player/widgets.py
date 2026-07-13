"""Custom Textual widgets: Browser, Visualizer, ProgressBar, NowPlaying.

The whole UI is rendered in one visual language: Braille glyphs for the
visualizer, the progress bar, the browser cursor/rule, and the play icons, all
monochrome (brightness only) so it stays transparent and futuristic.
"""

from __future__ import annotations

import numpy as np
from rich.text import Text
from textual import events
from textual.message import Message
from textual.widget import Widget

from .braille import Canvas
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
        - "scope"    : oscilloscope of the raw waveform
        - "bars"     : 32-band frequency spectrum as vertical bars
        - "mirror"   : spectrum mirrored above/below a center line
        - "inverted" : spectrum as negative space — field filled, bars carved out
        - "envelope" : smooth amplitude-envelope hills (never noisy, even loud)
        - "waterfall": scrolling time × frequency spectrogram history
        - "rings"    : concentric rings that pulse outward with energy
    """

    MODES = ("scope", "bars", "mirror", "inverted", "envelope", "waterfall", "rings")
    MODE_LABELS = {
        "scope": "WAVEFORM",
        "bars": "SPECTRUM",
        "mirror": "MIRROR",
        "inverted": "INVERTED",
        "envelope": "ENVELOPE",
        "waterfall": "WATERFALL",
        "rings": "RINGS",
    }

    HISTORY = 96  # frames of spectrum/energy history kept for scrolling modes

    DECAY = 0.80  # spectrum smoothing between frames: fast attack, slow release

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._mode_idx = 0
        self._smoothed: np.ndarray | None = None   # smoothed spectrum bars
        self._wave: np.ndarray | None = None        # latest raw waveform slice
        self._phase = 0.0
        # Rolling history for the scrolling / pulsing modes.
        self._spec_hist: list[np.ndarray] = []      # recent spectrum frames
        self._energy_hist: list[float] = []         # recent overall loudness

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
        """Feed the current spectrum + waveform, advance animation, refresh."""
        self._phase += 0.05
        if bars is None:
            self._smoothed = None
        else:
            b = np.asarray(bars, dtype=float)
            if self._smoothed is None or len(self._smoothed) != len(b):
                self._smoothed = b.copy()
            else:
                self._smoothed = np.maximum(b, self._smoothed * self.DECAY)
        self._wave = None if wave is None else np.asarray(wave, dtype=float)

        # Push a frame of history for the scrolling / pulsing modes.
        if self._smoothed is not None:
            self._spec_hist.append(self._smoothed.copy())
            self._energy_hist.append(float(self._smoothed.mean()))
            if len(self._spec_hist) > self.HISTORY:
                self._spec_hist.pop(0)
                self._energy_hist.pop(0)
        self.refresh()

    # ---- rendering ----

    def render(self) -> Text:
        width = self.size.width
        height = self.size.height
        if width <= 0 or height <= 0:
            return Text("")

        canvas = Canvas(width, height)
        draw = {
            "scope": self._draw_scope,
            "bars": self._draw_bars,
            "mirror": self._draw_mirror,
            "inverted": self._draw_inverted,
            "envelope": self._draw_envelope,
            "waterfall": self._draw_waterfall,
            "rings": self._draw_rings,
        }[self.mode]
        draw(canvas)

        return self._emit(canvas)

    def _emit(self, canvas: Canvas) -> Text:
        """Turn a Canvas into styled Braille Text (transparent, dim→bold)."""
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

    # ---- individual modes ----

    def _draw_scope(self, canvas: Canvas) -> None:
        """Oscilloscope: the raw waveform as a wiggling line down the middle."""
        wave = self._wave
        if wave is None or len(wave) < 2:
            self._draw_flatline(canvas)
            return
        # Resample the waveform to one point per dot-column.
        n = canvas.gw
        idx = np.linspace(0, len(wave) - 1, n).astype(int)
        w = wave[idx]
        # Map amplitude [-1,1] to dot rows, centered, with a little headroom.
        mid = (canvas.gh - 1) / 2.0
        ys = mid - w * mid * 0.9
        xs = np.arange(n)
        canvas.line(xs, ys, oversample=2)

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

    def _draw_inverted(self, canvas: Canvas) -> None:
        """Negative-space spectrum: fill the whole field, carve the bars out as
        gaps so the spectrum reads as a silhouette."""
        canvas.buf[:] = 1.0  # fill everything
        bars = self._smoothed
        if bars is None:
            return
        n_cols = canvas.gw
        idx = np.linspace(0, len(bars) - 1, n_cols).astype(int)
        vals = np.clip(bars[idx], 0, 1)
        heights = (vals * canvas.gh).astype(int)
        for c in range(n_cols):
            h = heights[c]
            if h > 0:
                # Clear (carve out) the bar column from the bottom up.
                canvas.buf[canvas.gh - h:canvas.gh, c] = 0.0

    def _draw_envelope(self, canvas: Canvas) -> None:
        """Amplitude-envelope waveform: smooth loudness hills, not raw samples,
        so it never dissolves into noise at high volume."""
        wave = self._wave
        if wave is None or len(wave) < 2:
            self._draw_flatline(canvas)
            return
        n = canvas.gw
        # Split the window into `n` chunks; each chunk's envelope is its peak
        # absolute amplitude. This is a smooth outline, not the raw wiggle.
        edges = np.linspace(0, len(wave), n + 1).astype(int)
        env = np.array([
            np.abs(wave[edges[i]:edges[i + 1]]).max() if edges[i + 1] > edges[i] else 0.0
            for i in range(n)
        ])
        # Light smoothing so the hills flow.
        k = np.array([0.25, 0.5, 0.25])
        env = np.convolve(env, k, mode="same")
        env = np.clip(env, 0, 1)
        mid = (canvas.gh - 1) / 2.0
        xs = np.arange(n)
        top = mid - env * mid * 0.95
        bot = mid + env * mid * 0.95
        # Fill the mirrored envelope so it reads as a solid flowing shape.
        for c in range(n):
            canvas.buf[int(top[c]):int(bot[c]) + 1, c] += 1.0

    def _draw_waterfall(self, canvas: Canvas) -> None:
        """Scrolling spectrogram: newest frame on the right, scrolling left;
        frequency runs bottom (low) to top (high), density = energy."""
        hist = self._spec_hist
        if not hist:
            self._draw_flatline(canvas)
            return
        gw, gh = canvas.gw, canvas.gh
        # Take the most recent `gw` frames (one per dot-column), oldest → left.
        frames = hist[-gw:]
        start_col = gw - len(frames)
        for i, frame in enumerate(frames):
            col = start_col + i
            # Resample this frame's bands up the column (low freq at bottom).
            idx = np.linspace(0, len(frame) - 1, gh).astype(int)
            colvals = np.clip(frame[idx], 0, 1)
            # Light a dot where energy clears a small threshold; brighter dots
            # (higher buf value) read bolder via the density tiers in _emit.
            lit = colvals > 0.12
            canvas.buf[gh - 1 - np.arange(gh)[lit], col] = 1.0 + colvals[lit] * 5.0

    def _draw_rings(self, canvas: Canvas) -> None:
        """Concentric rings rippling outward; recent loudness sets their radii
        so beats send pulses out from the center."""
        cx = (canvas.gw - 1) / 2.0
        cy = (canvas.gh - 1) / 2.0
        max_r = min(cx, cy)
        energy = self._energy_hist[-24:] if self._energy_hist else [0.1]
        # A few rings, each expanding with the phase and modulated by a recent
        # energy sample, wrapping around so they ripple outward continuously.
        n_rings = 5
        theta = np.linspace(0, 2 * np.pi, 240)
        ct, st = np.cos(theta), np.sin(theta) * 0.5  # squash y → round rings
        for k in range(n_rings):
            # Expanding radius that wraps 0→1; offset per ring so they trail.
            frac = ((self._phase * 0.15) + k / n_rings) % 1.0
            e = energy[-1 - (k % len(energy))]
            r = frac * max_r * (0.7 + 0.6 * float(np.clip(e, 0, 1)))
            if r < 1:
                continue
            canvas.plot(cx + ct * r, cy + st * r)


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


class NowPlaying(Widget):
    """Shows current track title, album, artist, and the active viz mode.

    Monochrome — brightness only (bold / normal / dim) so it stays transparent
    and on-theme with the Braille rendering. Play/pause icons are Braille dots.
    """

    # Braille "glyph" status icons keep the whole UI in one visual language.
    _ICON = {"playing": "⣸⣷", "paused": "⣇⣸", "stopped": "⣿⣿"}

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._title = ""
        self._album = ""
        self._artist = ""
        self._status = "stopped"
        self._viz_label = ""

    def set_track(self, title: str, album: str, artist: str) -> None:
        self._title = title
        self._album = album
        self._artist = artist
        self.refresh()

    def set_status(self, status: str) -> None:
        """status is one of: playing, paused, stopped."""
        self._status = status
        self.refresh()

    def set_viz_label(self, label: str) -> None:
        self._viz_label = label
        self.refresh()

    def render(self) -> Text:
        if not self._title:
            return Text("⠶  no track loaded", style="dim italic")

        icon = self._ICON.get(self._status, "⣿⣿")
        text = Text()
        text.append(f"{icon}  ", style="bold")
        text.append(self._title, style="bold")
        if self._viz_label:
            text.append(f"     ⟩ {self._viz_label}", style="dim")
        text.append("\n")
        text.append(f"      {self._artist}", style="default")
        if self._album:
            text.append("  ·  ", style="dim")
            text.append(self._album, style="dim italic")
        return text


def _format_time(seconds: float) -> str:
    """Format seconds as M:SS."""
    if seconds < 0 or seconds != seconds:  # negative or NaN
        seconds = 0
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"
