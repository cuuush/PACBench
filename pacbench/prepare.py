"""Compatibility entry point for `python -m pacbench.prepare`."""
from .corpus import prepare as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
else:
    import sys

    sys.modules[__name__] = _implementation
