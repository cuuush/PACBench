"""Compatibility entry point for the supermemory first question command."""
from .providers.supermemory.first_question import main


if __name__ == '__main__':
    raise SystemExit(main())
