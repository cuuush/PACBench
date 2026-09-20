"""The PACBench corpus: one module per incident family or development life.

Each module exposes ``lives()`` returning the family's `Life` objects — the bet
life and its matched control, or a full development life. Development-life
modules additionally expose ``manifest()`` returning the authoring-time support
manifest for that life (kept out of scored runs; see `pacbench.manifest`).

Ground truth lives in the rubric; message text here is hand-authored for the
first families and, at scale, rendered by a model from the same structured
authoring plans (never the other way round — the model that writes surface text
never authors the key).
"""

from __future__ import annotations

from ..life import Life
from ..manifest import SupportManifest
from . import (life_a_dentist, life_b_collision, life_c_coat, life_d_reunion,
                 life_e_revoke, poker_move, weekend_plans)


def all_lives() -> list[Life]:
    return [*poker_move.lives(), *weekend_plans.lives(),
            *life_a_dentist.lives(), *life_b_collision.lives(),
            *life_c_coat.lives(), *life_d_reunion.lives(),
            *life_e_revoke.lives()]


def development_lives() -> list[Life]:
    """The development lives (contract §§43, 39): update depth (A), entity
    collision and source authority (B), proactive tasks and broad context
    (C), thread-size difficulty (D), and cancellation-reversal, deletion,
    and completion (E). Each covers a 7-day scored horizon plus older
    history."""
    return [*life_a_dentist.lives(), *life_b_collision.lives(),
            *life_c_coat.lives(), *life_d_reunion.lives(),
            *life_e_revoke.lives()]


def get_life(life_id: str) -> Life:
    for life in all_lives():
        if life.id == life_id:
            return life
    raise KeyError(f"unknown life {life_id!r}")


MANIFESTS: dict[str, SupportManifest] = {
    _mod.lives()[0].id: _mod.manifest()
    for _mod in (life_a_dentist, life_b_collision, life_c_coat,
                 life_d_reunion, life_e_revoke)
}
