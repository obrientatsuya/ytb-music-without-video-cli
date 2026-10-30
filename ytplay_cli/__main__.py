"""Allow running the player with ``python -m ytplay_cli``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
