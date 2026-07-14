# ASCII Music Player

A terminal music player rendered in **Braille glyphs** for a fine, high-res,
futuristic look — a pure-text, SSH-menu-style library browser and a real-time
Braille visualizer with several switchable modes. Everything is monochrome
(brightness only), so it stays fully transparent and the terminal shows through.

Two full-screen modes, shown one at a time. Browse the library with the arrow
keys; pick a track and the browser gives way to the player. Press `b` to go back
and pick another; press `v` to cycle the visualizer. Escape is a universal back
button that loops between the views — from the player it returns to the browser,
and inside the browser it walks back up (Tracks → Albums → Artists) before
crossing back over to the player.

```
BROWSER                            PLAYER

ARTISTS                              ⣿⣿⣿  RADIOHEAD
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿                                everything in its right place
⣿ ⤮  Shuffle All                     ⣀⡠⠔⠒⠉⠉⠑⠢⢄⡀
  Radiohead                    →   ⣀⠔⠊         ⠈⠢⣀   ⡀
  Aphex Twin                     ⠊                 ⠑⠊ ⠈
  Boards of Canada                 1:23 ⣿⣿⣿⣿⣿⠉⠉⠉⠉⠉ 4:12
↑↓ move  → open  s shuffle  d dir  q quit
```

## Requirements

- **Python 3.9+.** Playback uses `pygame`, which ships prebuilt wheels only for
  released Python versions — install into a Python that has a pygame wheel
  (3.9–3.13 at time of writing), or `pip` will try to compile it from source and
  need SDL development headers.
- **ffmpeg** on your `PATH` — a system package, *not* a pip dependency. Without
  it, MP3/OGG/FLAC/WAV still play, but **M4A and Opus won't play** and the
  visualizer can't analyze compressed audio. The app warns at startup if it's
  missing.

Install ffmpeg (and, on Linux, the venv module):

```bash
# Linux (Ubuntu / Debian)
sudo apt install ffmpeg python3-venv
# macOS
brew install ffmpeg
# Windows
winget install ffmpeg
```

## Install

The recommended way — installs a `tt` command on your `PATH`:

```bash
pip install .            # from a clone; or: pip install git+<repo-url>
```

Using [pipx](https://pipx.pypa.io/) keeps it in its own isolated environment:

```bash
pipx install .
```

Or, for development, a virtual environment against the raw sources:

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

Once installed, the `tt` command scans your library:

```bash
tt                        # scans ~/Music
tt /path/to/music         # scans a specific directory
```

Or run straight from a clone without installing:

```bash
python -m music_player [/path/to/music]
python run.py [/path/to/music]      # equivalent
```

## Platform support

Cross-platform — Linux, macOS, and Windows. The code uses `pathlib` throughout
with no OS-specific paths, so the only per-platform differences are how you
install ffmpeg (above) and the terminal you run in. Use a modern
truecolor-capable terminal with mouse support (Windows Terminal, iTerm2, or any
recent Linux emulator); the legacy Windows `cmd.exe` console renders poorly.

## Controls

**Browser** (Artist → Album → Track drill-down):

| Key           | Action                              |
|---------------|-------------------------------------|
| ↑ / ↓         | Move up / down the list             |
| →             | Advance — drill in / play the track |
| ← / Backspace | Retreat — back up a level           |
| Escape        | Universal back — up a level, then over to the player (Tracks → Albums → Artists → Player) |
| `s`           | Shuffle the highlighted folder (all of an artist's songs, or an album). On the **Shuffle All** row at the top of the artist list, shuffles the whole library |
| `d`           | Change the music directory (type a new root path) |

**Player:**

| Key           | Action                                        |
|---------------|-----------------------------------------------|
| `b`           | Back to the browser                           |
| Escape        | Universal back — from the player, returns to the browser |
| `v`           | Cycle visualizer (spectrum bars / mirror) |
| Space         | Pause / resume                                |
| `n`           | Next track                                    |
| `p`           | Previous track                                |
| `+` / `-`     | Volume up / down                              |
| `q`           | Quit                                          |

## How it works

- **Library scan** (`library.py`): walks the music directory, reads ID3 / Vorbis / FLAC tags with `mutagen`, groups tracks into Artist → Album → Track. Per-file tag results are cached under `~/.cache/terminaltunes`, keyed by path + mtime + size, so relaunching only re-reads new or changed files — startup stays fast as the library grows. The scan runs on a background thread after the UI mounts (a "Scanning…" placeholder shows meanwhile), so a large collection never blocks the app from appearing.
- **Browser** (`widgets.py`): a pure-text `Browser` widget renders a single-column list per level with a Braille `⣿` cursor and a Braille rule under the heading — no tree widget, no boxes. The row list is windowed to the widget height and scrolls with the cursor (with `⋯` cues when rows are hidden above/below), so long artist lists page instead of running off-screen. → drills Artists → Albums → Tracks; ← goes back up. Picking a track posts a message to the app.
- **Modes** (`app.py`): the browser and player are two full-screen views toggled by `display`; only the active one is shown. Choosing a track hides the browser and shows the player; `b` returns to the browser. Escape is a universal back button that loops between the views: from the player it goes to the browser, and inside the browser it pops one drill-down level (Tracks → Albums → Artists) before crossing over to the player. When nothing is playing, the player's banner shows a scrolling **WAITING FOR TRACK** until a song is picked. Pressing `s` in the browser shuffles the highlighted folder (an artist's whole catalogue, or a single album) into a playlist; a **Shuffle All** row at the top of the artist list shuffles the whole library. `d` re-scans a new music directory in place. Playback loops the current playlist — the last track advances back to the first.
- **Banner** (`widgets.py`): the player's title is two stacked lines — the artist big, the track name half-size below it — rendered in a Braille bitmap font (`braille.py`). Each line is centered when it fits and scrolls as a marquee when it's wider than the screen.
- **Up Next** (`widgets.py`): a one-line `Up Next: <track> by <artist>` footer under the progress bar, right-aligned to the bottom-right corner, shows what plays when the current track finishes, looping to the first track at the end of the playlist.
- **Braille rendering** (`braille.py`): a small `Canvas` rasterizes points/lines/bars into a 2×4-dots-per-cell sub-pixel grid (8× resolution) and packs each block into one Braille glyph, fully vectorized in NumPy. The visualizer, progress bar, browser cursor and play icons all use it, so the whole UI reads as one fine, futuristic, monochrome (transparent) system.
- **Playback** (`audio.py`): `pygame.mixer.music` streams the file from disk.
- **Visualizer**: on load, `pydub` decodes the file to raw samples and NumPy computes a windowed FFT every 50 ms into 32 log-spaced frequency bands (~40 Hz to 16 kHz). Analysis runs in a background thread in two passes — the opening seconds first, so the visualizer starts in step with playback — and the widget indexes into the spectrogram by `get_pos_ms()`. Press `v` to switch between two modes: **spectrum** (32-band frequency bars) and **mirror** (the spectrum reflected above and below a center line). Both are Braille-rendered and hold 30 fps, scaling their resolution to the terminal size.

## Supported formats

MP3, OGG, FLAC, WAV, M4A, Opus. MP3/OGG/FLAC/WAV play natively; **M4A and Opus
are transcoded to a temporary WAV via ffmpeg on the fly**, so those formats (and
the visualizer for any compressed format) require ffmpeg to be installed.

## Project layout

```
terminaltunes/
├── pyproject.toml            # packaging + `tt` entry point
├── run.py                    # convenience shim (delegates to music_player.cli)
├── requirements.txt          # dev install of raw sources
├── README.md
└── music_player/
    ├── __init__.py
    ├── __main__.py           # enables `python -m music_player`
    ├── cli.py                # entry point (`tt`)
    ├── app.py                # Textual app
    ├── app.tcss              # styling
    ├── audio.py              # pygame + FFT + waveform
    ├── braille.py            # Braille sub-pixel canvas
    ├── library.py            # directory scan + tags
    └── widgets.py            # Browser, Visualizer, ProgressBar, Banner
```

## Notes

- ffmpeg is required to play M4A/Opus and for the visualizer to analyze any
  compressed format. MP3/OGG/FLAC/WAV playback works without it.
- The spectrogram is computed in a background thread, opening seconds first, so the visualizer starts in step with playback rather than catching up after the whole track is analyzed.
