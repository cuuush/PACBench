"""Compatibility entry point for the supermemory source qualify command."""
from .providers.supermemory.source_qualify import main


if __name__ == '__main__':
    raise SystemExit(main())
