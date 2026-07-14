"""Convenience entry point: `python run.py [MUSIC_DIRECTORY]`.

The real logic lives in jet_music_player.cli, exposed as the `jmp` console
script (see pyproject.toml). Prefer `pip install .` then `jmp`.
"""

from jet_music_player.cli import main

if __name__ == "__main__":
    main()
