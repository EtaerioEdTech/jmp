"""Main Textual app.

Layout:
    +---------------------+---------------------------+
    |                     |  Now Playing              |
    |   Library Tree      +---------------------------+
    |   (arrow keys)      |                           |
    |                     |   Visualizer              |
    |                     |                           |
    |                     +---------------------------+
    |                     |  Progress bar             |
    +---------------------+---------------------------+
                          Footer with keybinds
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Tree
from textual.widgets.tree import TreeNode

from .audio import AudioEngine
from .library import Album, Artist, Track, scan_library
from .widgets import NowPlaying, ProgressBar, Visualizer


class MusicPlayerApp(App):
    """ASCII terminal music player."""

    CSS_PATH = "app.tcss"

    TITLE = "ASCII Music Player"

    BINDINGS = [
        ("space", "toggle_play", "Play / Pause"),
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

        # Loaded on mount.
        self.library: dict[str, Artist] = {}
        self.track_by_node: dict[int, Track] = {}
        self.current_track: Track | None = None
        self.current_playlist: list[Track] = []
        self.current_index: int = -1

    # ---- Textual lifecycle ----

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="left-pane"):
                yield Tree("♪ Library", id="library-tree")
            with Vertical(id="right-pane"):
                yield NowPlaying(id="now-playing")
                yield Visualizer(id="visualizer")
                yield ProgressBar(id="progress")
        yield Footer()

    def on_mount(self) -> None:
        self._populate_tree()
        # ~15 fps: fast enough for a smooth visualizer, light on CPU.
        self.set_interval(1 / 15, self._tick)

    # ---- library ----

    def _populate_tree(self) -> None:
        tree = self.query_one("#library-tree", Tree)
        tree.show_root = True
        tree.root.expand()

        self.library = scan_library(self.music_dir)

        if not self.library:
            tree.root.add_leaf("(no audio files found)")
            return

        for artist_name in sorted(self.library, key=str.lower):
            artist = self.library[artist_name]
            artist_node = tree.root.add(f"{artist_name}", data={"kind": "artist", "name": artist_name})
            for album_name in sorted(artist.albums, key=str.lower):
                album = artist.albums[album_name]
                label = f"{album_name}"
                if album.year:
                    label += f"  ({album.year})"
                album_node = artist_node.add(label, data={"kind": "album", "artist": artist_name, "album": album_name})
                for track in album.tracks:
                    num = f"{track.track_num:02d}. " if track.track_num else ""
                    track_node = album_node.add_leaf(
                        f"{num}{track.title}",
                        data={"kind": "track", "artist": artist_name, "album": album_name},
                    )
                    self.track_by_node[track_node.id] = track

    # ---- interactions ----

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """User pressed Enter (or clicked) on a tree node."""
        node = event.node
        data = node.data or {}
        kind = data.get("kind")

        if kind == "track":
            track = self.track_by_node.get(node.id)
            if track:
                self._play_track_in_album(track, data["artist"], data["album"])
        elif kind == "album":
            # Play whole album from the first track.
            album = self.library[data["artist"]].albums[data["album"]]
            if album.tracks:
                self._play_track_in_album(album.tracks[0], data["artist"], data["album"])

    def _play_track_in_album(self, track: Track, artist_name: str, album_name: str) -> None:
        """Start playing `track` and set the album as the current playlist for next/prev."""
        album = self.library[artist_name].albums[album_name]
        self.current_playlist = list(album.tracks)
        try:
            self.current_index = self.current_playlist.index(track)
        except ValueError:
            self.current_index = 0

        self._play_current()

    def _play_current(self) -> None:
        if not (0 <= self.current_index < len(self.current_playlist)):
            return
        track = self.current_playlist[self.current_index]
        self.current_track = track
        self.engine.play(track.path)

        now_playing = self.query_one("#now-playing", NowPlaying)
        # We know the artist/album from the enclosing playlist context, but simplest is to look up.
        artist_name, album_name = self._locate(track)
        now_playing.set_track(track.title, album_name, artist_name)
        now_playing.set_status("playing")

    def _locate(self, track: Track) -> tuple[str, str]:
        """Reverse-lookup an artist and album name for a track. Falls back to blanks."""
        for artist_name, artist in self.library.items():
            for album_name, album in artist.albums.items():
                if track in album.tracks:
                    return artist_name, album_name
        return "", ""

    # ---- tick loop ----

    def _tick(self) -> None:
        # Update visualizer.
        viz = self.query_one("#visualizer", Visualizer)
        viz.update_bars(self.engine.get_current_bars())

        # Update progress bar.
        if self.current_track:
            progress = self.query_one("#progress", ProgressBar)
            progress.update_progress(self.engine.get_pos_ms() / 1000.0, self.current_track.duration)

        # Auto-advance when a track finishes.
        if self.engine.is_finished():
            self.action_next_track()

    # ---- actions bound to keys ----

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
