"""Main Textual app.

Two full-screen modes, toggled — never both at once:

    BROWSER                          PLAYER
    +-------------------------+      +-------------------------+
    | ARTISTS                 |      |  ▶  Kid A               |
    |                         |      |      Radiohead · Kid A  |
    | › Radiohead             |  →   |                         |
    |   Aphex Twin            |      |    █ █   █   (visualizer)|
    |   Boards of Canada      |      |                         |
    |                         |      |  1:23 ███──── 4:12      |
    | ↑↓ move  ↵ open         |      |  b browse  space pause  |
    +-------------------------+      +-------------------------+

Picking a track in the browser hides it and shows only the player.
Pressing `b` returns to the browser to pick another track.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer

from .audio import AudioEngine
from .library import Track, scan_library
from .widgets import Browser, NowPlaying, ProgressBar, Visualizer


class MusicPlayerApp(App):
    """ASCII terminal music player."""

    CSS_PATH = "app.tcss"

    TITLE = "ASCII Music Player"

    BINDINGS = [
        ("b", "browse", "Browse"),
        ("escape", "browse", "Browse"),
        ("space", "toggle_play", "Play / Pause"),
        ("v", "cycle_viz", "Visual"),
        ("n", "next_track", "Next"),
        ("p", "prev_track", "Prev"),
        ("plus", "vol_up", "Vol +"),
        ("minus", "vol_down", "Vol -"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, music_dir: Path) -> None:
        super().__init__()
        self.music_dir = music_dir
        self.engine = AudioEngine()

        self.current_track: Track | None = None
        self.current_playlist: list[Track] = []
        self.current_index: int = -1
        self.current_artist: str = ""
        self.current_album: str = ""

    # ---- Textual lifecycle ----

    def compose(self) -> ComposeResult:
        library = scan_library(self.music_dir)
        yield Browser(library, id="browser")
        with Vertical(id="player"):
            yield NowPlaying(id="now-playing")
            yield Visualizer(id="visualizer")
            yield ProgressBar(id="progress")
        yield Footer()

    def on_mount(self) -> None:
        # Start in the browser; the player is hidden until a track is chosen.
        self._show_browser()
        # 30 fps: smooth, light motion for the flowing visualizer.
        self.set_interval(1 / 30, self._tick)

    # ---- mode switching ----

    def _show_browser(self) -> None:
        self.query_one("#player").display = False
        browser = self.query_one("#browser", Browser)
        browser.display = True
        browser.focus()

    def _show_player(self) -> None:
        self.query_one("#browser").display = False
        self.query_one("#player").display = True

    def action_browse(self) -> None:
        self._show_browser()

    # ---- browser -> play ----

    def on_browser_track_chosen(self, event: Browser.TrackChosen) -> None:
        self.current_playlist = event.playlist
        self.current_artist = event.artist
        self.current_album = event.album
        try:
            self.current_index = self.current_playlist.index(event.track)
        except ValueError:
            self.current_index = 0
        self._play_current()
        self._show_player()

    def _play_current(self) -> None:
        if not (0 <= self.current_index < len(self.current_playlist)):
            return
        track = self.current_playlist[self.current_index]
        self.current_track = track
        self.engine.play(track.path)

        now_playing = self.query_one("#now-playing", NowPlaying)
        now_playing.set_track(track.title, self.current_album, self.current_artist)
        now_playing.set_status("playing")
        now_playing.set_viz_label(self.query_one("#visualizer", Visualizer).mode_label)

    # ---- tick loop ----

    def _tick(self) -> None:
        # Only the player pane needs updating; skip work while browsing.
        if not self.query_one("#player").display:
            return

        viz = self.query_one("#visualizer", Visualizer)
        viz.update_frame(self.engine.get_current_bars(), self.engine.get_current_wave())

        if self.current_track:
            progress = self.query_one("#progress", ProgressBar)
            progress.update_progress(self.engine.get_pos_ms() / 1000.0, self.current_track.duration)

        # Auto-advance when a track finishes.
        if self.engine.is_finished():
            self.action_next_track()

    # ---- actions bound to keys ----

    def action_cycle_viz(self) -> None:
        viz = self.query_one("#visualizer", Visualizer)
        label = viz.cycle_mode()
        self.query_one("#now-playing", NowPlaying).set_viz_label(label)

    def action_toggle_play(self) -> None:
        if self.current_track is None:
            return
        self.engine.toggle_pause()
        now_playing = self.query_one("#now-playing", NowPlaying)
        now_playing.set_status("paused" if self.engine.is_paused() else "playing")

    def action_next_track(self) -> None:
        if not self.current_playlist:
            return
        if self.current_index + 1 < len(self.current_playlist):
            self.current_index += 1
            self._play_current()
        else:
            self.engine.stop()
            self.current_track = None
            self.query_one("#now-playing", NowPlaying).set_status("stopped")

    def action_prev_track(self) -> None:
        if not self.current_playlist:
            return
        # Restart current track if we're > 3 seconds in, else go back one.
        if self.engine.get_pos_ms() > 3000:
            self._play_current()
        elif self.current_index > 0:
            self.current_index -= 1
            self._play_current()

    def action_vol_up(self) -> None:
        self.engine.set_volume(self.engine.get_volume() + 0.05)

    def action_vol_down(self) -> None:
        self.engine.set_volume(self.engine.get_volume() - 0.05)
