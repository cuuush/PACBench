"""Compatibility entry point for the supermemory week command."""
from .providers.supermemory.week import main


if __name__ == '__main__':
    raise SystemExit(main())
