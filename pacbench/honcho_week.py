"""Compatibility entry point for the honcho week command."""
from .providers.honcho.week import main


if __name__ == '__main__':
    raise SystemExit(main())
