"""Convenience entry point: `python run.py [MUSIC_DIRECTORY]`.

The real logic lives in music_player.cli so it can also be exposed as the `tt`
console script (see pyproject.toml). Prefer `pip install .` then `tt`.
"""

from music_player.cli import main

if __name__ == "__main__":
    main()
