"""Registered corpus lives. Prose-only pizza scenes remain diagnostic."""

from __future__ import annotations

from ..life import Life
from ..manifest import SupportManifest
from . import poker_move, weekend_plans


def all_lives() -> list[Life]:
    return [*poker_move.lives(), *weekend_plans.lives()]


def development_lives() -> list[Life]:
    return []


def get_life(life_id: str) -> Life:
    for life in all_lives():
        if life.id == life_id:
            return life
    raise KeyError(f"unknown life {life_id!r}")


MANIFESTS: dict[str, SupportManifest] = {}
