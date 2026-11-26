"""Compatibility entry point for the honcho daily gate command."""
from .providers.honcho.daily_gate import main


if __name__ == '__main__':
    raise SystemExit(main())
