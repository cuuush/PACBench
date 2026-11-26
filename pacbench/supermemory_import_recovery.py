"""Compatibility entry point for the supermemory import recovery command."""
from .providers.supermemory.import_recovery import main


if __name__ == '__main__':
    raise SystemExit(main())
