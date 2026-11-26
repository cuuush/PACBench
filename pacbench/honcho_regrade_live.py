"""Compatibility entry point for the honcho regrade live command."""
from .providers.honcho.regrade_live import main


if __name__ == '__main__':
    raise SystemExit(main())
