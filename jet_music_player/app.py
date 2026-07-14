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

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer

from .audio import AudioEngine
from .dirprompt import DirPrompt
from .library import Artist, Track, scan_library
from .widgets import Banner, Browser, BrowserBanner, ProgressBar, UpNext, Visualizer


class JetMusicPlayerApp(App):
    """Jet Music Player — an ASCII terminal music player."""

    CSS_PATH = "app.tcss"

    TITLE = "Jet Music Player"

    BINDINGS = [
        ("b", "browse", "Browse"),
        ("escape", "back", "Back"),
        ("d", "change_dir", "Dir"),
        ("space", "toggle_play", "Play / Pause"),
        ("v", "cycle_viz", "Visual"),
        ("n", "next_track", "Next"),
        ("p", "prev_track", "Prev"),
        ("plus", "vol_up", "Vol +"),
        ("equals_sign", "vol_up", "Vol +"),   # `=` so + works without Shift
        ("minus", "vol_down", "Vol -"),
        ("underscore", "vol_down", "Vol -"),  # `_` alias for symmetry
        ("q", "quit", "Quit"),
    ]

    def __init__(self, music_dir: Path) -> None:
        super().__init__()
        self.music_dir = music_dir
        self.engine = AudioEngine()
        # The library is scanned in the background after mount (see _start_scan)
        # so a large collection doesn't block the UI from appearing. Start empty.
        self.library: dict[str, Artist] = {}

        self.current_track: Track | None = None
        self.current_playlist: list[Track] = []
        self.current_index: int = -1

    # ---- Textual lifecycle ----

    def compose(self) -> ComposeResult:
        with Vertical(id="home"):
            yield BrowserBanner(id="browser-banner")
            yield Browser(self.library, id="browser")
        with Vertical(id="player"):
            yield Banner(id="banner")
            yield Visualizer(id="visualizer")
            yield ProgressBar(id="progress")
            yield UpNext(id="upnext")
        yield Footer()

    def on_mount(self) -> None:
        # Start in the browser; the player is hidden until a track is chosen.
        self._show_browser()
        # Scan the library in the background so the UI is responsive right away.
        self._start_scan(self.music_dir)
        # 30 fps: smooth, light motion for the flowing visualizer.
        self.set_interval(1 / 30, self._tick)

    # ---- background library scan ----

    def _start_scan(self, music_dir: Path) -> None:
        """Show the scanning state and kick off a background scan of `music_dir`.
        `_scan_worker` finishes on the UI thread and installs the result."""
        self.query_one("#browser", Browser).set_scanning(True)
        self._scan_worker(music_dir)

    @work(thread=True, exclusive=True)
    def _scan_worker(self, music_dir: Path) -> None:
        """Scan on a worker thread (mutagen I/O is slow), then hand the result
        back to the UI thread to install."""
        library = scan_library(music_dir)
        self.call_from_thread(self._install_library, music_dir, library)

    # ---- mode switching ----

    def _show_browser(self) -> None:
        self.query_one("#player").display = False
        # #home wraps the Braille header and the browser; toggle it as one view.
        self.query_one("#home").display = True
        self.query_one("#browser", Browser).focus()

    def _show_player(self) -> None:
        self.query_one("#home").display = False
        self.query_one("#player").display = True

    def action_browse(self) -> None:
        self._show_browser()

    def action_back(self) -> None:
        """Escape is the universal back button, looping between the two views:

            Player          → Browser (pick a song)
            Browser/Tracks  → Browser/Albums
            Browser/Albums  → Browser/Artists
            Browser/Artists → Player (back to now-playing / "waiting")

        Inside the browser it first walks back up the drill-down levels; only
        once at the top (Artists) does it cross over to the player.
        """
        if self.query_one("#player").display:
            self._show_browser()
            return
        # In the browser: pop a level; if already at the top, go to the player.
        browser = self.query_one("#browser", Browser)
        if not browser.back_up_level():
            self._show_player()

    def action_change_dir(self) -> None:
        """Prompt for a new music root, then re-scan into the browser."""
        self._show_browser()
        self.push_screen(DirPrompt(str(self.music_dir)), self._on_dir_chosen)

    def _on_dir_chosen(self, path: str | None) -> None:
        if not path:
            return  # cancelled
        new_dir = Path(path).expanduser()
        if not new_dir.is_dir():
            self.notify(f"Not a directory: {new_dir}", severity="error")
            return
        # Scan in the background like startup, then swap in on completion.
        self._start_scan(new_dir)

    def _install_library(self, music_dir: Path, library: dict[str, Artist]) -> None:
        """Apply a freshly scanned library to the browser (runs on UI thread).
        If the scan found nothing, keep the current library rather than swapping
        to an empty one — but always clear the scanning placeholder."""
        if not library and self.library:
            self.query_one("#browser", Browser).set_library(self.library)
            self.notify(f"No audio files found in {music_dir}", severity="warning")
            return
        self.music_dir = music_dir
        self.library = library
        self.query_one("#browser", Browser).set_library(library)

    # ---- browser -> play ----

    def on_browser_track_chosen(self, event: Browser.TrackChosen) -> None:
        self.current_playlist = event.playlist
        try:
            self.current_index = self.current_playlist.index(event.track)
        except ValueError:
            self.current_index = 0
        self._play_current()
        self._show_player()

    def on_browser_shuffle_chosen(self, event: Browser.ShuffleChosen) -> None:
        if not event.tracks:
            return
        self.current_playlist = event.tracks
        self.current_index = 0
        self._play_current()
        self._show_player()

    def _play_current(self) -> None:
        if not (0 <= self.current_index < len(self.current_playlist)):
            return
        track = self.current_playlist[self.current_index]
        self.current_track = track
        self.engine.play(track.path)

        # A shuffle playlist mixes albums, so look up this track's own tags.
        artist, album = self._locate(track)
        banner = self.query_one("#banner", Banner)
        banner.set_track(track.title, album, artist)
        banner.set_status("playing")
        self._update_up_next()

    def _update_up_next(self) -> None:
        """Refresh the "Up Next" footer with the track that follows the current
        one in the playlist, looping back to the first track at the end."""
        upnext = self.query_one("#upnext", UpNext)
        if not self.current_playlist:
            upnext.clear()
            return
        # Loop: after the last track, "up next" is the first again.
        nxt = self.current_playlist[(self.current_index + 1) % len(self.current_playlist)]
        artist, _ = self._locate(nxt)
        upnext.set_next(nxt.title, artist)

    def _locate(self, track: Track) -> tuple[str, str]:
        """Reverse-lookup a track's artist and album names in the library."""
        for artist_name, artist in self.library.items():
            for album_name, album in artist.albums.items():
                if track in album.tracks:
                    return artist_name, album_name
        return "", ""

    # ---- tick loop ----

    def _tick(self) -> None:
        # On the home view, keep the header's wind streaks animating; nothing
        # else there needs per-frame updates.
        if not self.query_one("#player").display:
            self.query_one("#browser-banner", BrowserBanner).tick()
            return

        viz = self.query_one("#visualizer", Visualizer)
        viz.update_frame(self.engine.get_current_bars(), self.engine.get_current_wave())
        self.query_one("#banner", Banner).tick()
        self.query_one("#upnext", UpNext).tick()

        if self.current_track:
            progress = self.query_one("#progress", ProgressBar)
            progress.update_progress(self.engine.get_pos_ms() / 1000.0, self.current_track.duration)

        # Auto-advance when a track finishes.
        if self.engine.is_finished():
            self.action_next_track()

    # ---- actions bound to keys ----

    def action_cycle_viz(self) -> None:
        # Cycle the visualization silently — no on-screen mode indicator.
        self.query_one("#visualizer", Visualizer).cycle_mode()

    def action_toggle_play(self) -> None:
        if self.current_track is None:
            return
        self.engine.toggle_pause()
        banner = self.query_one("#banner", Banner)
        banner.set_status("paused" if self.engine.is_paused() else "playing")

    def action_next_track(self) -> None:
        if not self.current_playlist:
            return
        # Advance, looping back to the first track at the end of the playlist —
        # this matches the "Up Next" footer, which loops to the first track too.
        self.current_index = (self.current_index + 1) % len(self.current_playlist)
        self._play_current()

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
        self._change_volume(+0.05)

    def action_vol_down(self) -> None:
        self._change_volume(-0.05)

    def _change_volume(self, delta: float) -> None:
        """Adjust volume and flash the volume meter over the visualizer for ~1s."""
        self.engine.set_volume(self.engine.get_volume() + delta)
        self.query_one("#visualizer", Visualizer).show_volume(self.engine.get_volume())
