"""Compatibility entry point for the documented offline Pizza Day review command."""
from .campaigns.pizza_day_review import review_main


if __name__ == '__main__':
    raise SystemExit(review_main())
