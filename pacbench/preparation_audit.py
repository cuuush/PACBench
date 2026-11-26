"""Compatibility entry point for `python -m pacbench.preparation_audit`."""
from .corpus import audit as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
else:
    import sys

    sys.modules[__name__] = _implementation
