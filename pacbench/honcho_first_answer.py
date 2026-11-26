"""Compatibility entry point for the honcho first answer command."""
from .providers.honcho.first_answer import main


if __name__ == '__main__':
    raise SystemExit(main())
