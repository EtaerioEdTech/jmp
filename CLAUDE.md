# CLAUDE.md

Guidance for AI agents (and humans) working in this repository.

## Project

**Jet Music Player** (`jmp`) — an ASCII/Braille terminal music player with a
live visualizer, built on Textual. The Python package is `jet_music_player`;
the installed console command is `jmp` (see `pyproject.toml`).

## Naming & rename history

This project has been renamed. Watch for stale references:

- **Local directory:** `~/code/jmp` — renamed from `~/code/terminaltunes` on
  2026-07-15.
- **GitHub repo:** `github.com/EtaerioEdTech/jmp` — renamed from
  `EtaerioEdTech/terminaltunes` on 2026-07-15. GitHub redirects the old URL,
  but the local `origin` remote already points at the new one.
- **Package / command:** `jet_music_player` package, `jmp` command. These were
  set earlier (commit `0ffe444`, "Rename to Jet Music Player") and are the
  current canonical names.

If you find the string `terminaltunes` anywhere in code, config, or docs, it is
a leftover from before the rename and should be updated.

## Running

The package imports by name from the working directory (nothing hardcodes the
folder path), so the rename did not require code changes.

- `python run.py [MUSIC_DIRECTORY]` — convenience entry point.
- `pip install .` then `jmp [MUSIC_DIRECTORY]` — installs the `jmp` console script.
- `jmp [QUERY...]` — trailing words are a search query (`jmp vampire weekend`),
  resolved in `search.py` against the scanned library. A lone leading argument
  is still taken as the music directory when it starts with `/`, `~`, or `.`,
  or names an existing directory; use `-d DIR` to give both a directory and a
  query. See `_looks_like_path` in `cli.py`.

The `.venv` is relocatable — no absolute project path is baked into it.

### Editable `uv` tool install (needs reattaching after a move/rename)

The installed `jmp` command is a `uv` tool installed in **editable** mode. Unlike
the `.venv`, an editable install bakes an **absolute source path** into its finder
script (`.../uv/tools/jet-music-player/.../__editable___*_finder.py`, the `MAPPING`
dict). After the 2026-07-15 `terminaltunes` → `jmp` rename this still pointed at the
old `~/code/terminaltunes/jet_music_player`, so `jmp` failed with
`ModuleNotFoundError: No module named 'jet_music_player'` from every directory except
`~/code/jmp` itself (where the current dir masked it). Fix — run from `~/code/jmp`:

```
uv tool install --editable . --reinstall
```

If you move or rename the project directory again, re-run that command.
