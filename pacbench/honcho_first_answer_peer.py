"""Compatibility entry point for the honcho first answer peer command."""
from .providers.honcho.first_answer_peer import main


if __name__ == '__main__':
    raise SystemExit(main())
