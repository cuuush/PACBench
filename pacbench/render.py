"""Surface rendering for the corpus: a structured beat -> natural channel text.

Ground truth never comes from the model. A `Beat` carries a plain `draft` that already
states the fact and the atomic `must_include` tokens that define its meaning. A generator
(spark) may rewrite the draft into natural, channel-appropriate voice and add mundane
colour, but `entails()` rejects any rendering that drops a required token, and `render`
refuses to ship it — so the model does the tedious surface work while the spec keeps the
key. With no generator, `render` returns the draft verbatim, so the corpus still builds
free, offline, and reproducibly.

Memcal-free like the rest of the package: the Meta API client is a dozen lines of
`urllib`, and the API key is passed in by the caller, never read from a memcal module.
Spark generation and the scored-run driver share one gateway and one credential:
Meta's Model API (``https://api.meta.ai/v1``) with the ``META`` credential from
the PACBench-owned `.env` (see :mod:`pacbench.generation` and `.env.example`).
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

Generate = Callable[[str], str]

DEFAULT_MODEL = "muse-spark-1.3-contributor"

_REGISTER = {
    "casual": "a casual text message — lowercase-leaning, contractions, no greeting or sign-off",
    "formal": "a short, plain formal email body in complete sentences",
    "terse": "a terse one-line message",
}


@dataclass(frozen=True)
class Beat:
    """A message to render. `draft` states the fact plainly; `must_include` are the atomic
    tokens the rendering must preserve verbatim so the ground truth cannot drift."""

    channel: str
    sender: str
    draft: str
    must_include: tuple[str, ...] = ()
    register: str = "casual"


class RenderError(RuntimeError):
    def __init__(self, beat: Beat, output: str, missing: list[str]):
        super().__init__(
            f"rendering for {beat.sender} on {beat.channel} dropped {missing}")
        self.beat, self.output, self.missing = beat, output, missing


def entails(text: str, beat: Beat) -> list[str]:
    """The required tokens the rendering failed to preserve. Empty == the fact survived."""
    hay = text.lower()
    return [t for t in beat.must_include if t.lower() not in hay]


def render(beat: Beat, *, generate: Generate | None = None) -> str:
    """Deterministic (draft verbatim) with no generator; otherwise a validated rewrite."""
    if generate is None:
        return beat.draft
    out = generate(_prompt(beat)).strip()
    missing = entails(out, beat)
    if missing:
        raise RenderError(beat, out, missing)
    return out


def _prompt(beat: Beat) -> str:
    register = _REGISTER.get(beat.register, beat.register)
    keep = "; ".join(beat.must_include) or "(none)"
    return (
        f"You are writing {register} on {beat.channel}, as {beat.sender}. "
        f"Rewrite the message below in that voice. Keep these details exactly and "
        f"verbatim: {keep}. Do not add any appointment, date, time, place, or name "
        f"beyond what is given, and do not change any fact. Return only the message text, "
        f"nothing else.\n\nMessage: {beat.draft}"
    )


def noise_prompt(channel: str, sender: str) -> str:
    """A prompt for mundane filler — the ordinary low-value traffic the corpus needs.
    Nothing to preserve, so it is never validated for facts; it must carry none."""
    return (
        f"Write one short, mundane {channel} message from {sender} about everyday life — "
        f"weather, food, a TV show, a small chore. One line, casual. It must NOT mention "
        f"any appointment, plan, date, time, or place. Return only the message."
    )


# --------------------------------------------------------------------- Meta --

META_GATEWAY_URL = "https://api.meta.ai/v1/chat/completions"


def meta_generator(api_key: str, model: str = DEFAULT_MODEL, *,
                   temperature: float = 0.8, timeout: float = 60.0) -> Generate:
    """A `generate(prompt) -> text` bound to one Meta Spark model. The only network piece.

    `api_key` is the ``META`` credential from the PACBench-owned `.env` (the same
    credential :mod:`pacbench.generation` and :mod:`pacbench.creds` use). It is sent
    only as a Bearer header and never logged. Failures propagate to the caller —
    a failed generation is reported, never silently swapped for another model.
    """

    def generate(prompt: str) -> str:
        body = json.dumps({
            "model": model,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        request = urllib.request.Request(
            META_GATEWAY_URL, data=body,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read())
        return data["choices"][0]["message"]["content"]

    return generate


def openrouter_generator(api_key: str, model: str = DEFAULT_MODEL, *,
                         temperature: float = 0.8, timeout: float = 60.0) -> Generate:
    """Legacy alias for :func:`meta_generator` (kept for existing imports).

    Older tooling pointed this name at OpenRouter with ``meta/``-prefixed model
    ids; it now routes to Meta's Model API like the rest of the generation path.
    Pass a native model id (e.g. ``muse-spark-1.3-contributor``) — an
    OpenRouter-style ``meta/...`` id is NOT remapped and will fail against the
    Meta gateway instead of being silently substituted. New code should call
    :func:`meta_generator` directly.
    """
    return meta_generator(api_key, model,
                          temperature=temperature, timeout=timeout)


def load_key(env_path: str | Path) -> str | None:
    """Read the `META` credential from a .env file (the Meta API credential).

    Accepts a `META=` (or `META_API_KEY=`) line; legacy `OPENROUTER_API_KEY=`
    and bare `sk-or-...` lines are still recognized as a fallback, in that
    order, so old tooling keeps working. Tooling calls this; the library's
    data path never does, so no key touches the corpus."""
    path = Path(env_path).expanduser()
    if not path.exists():
        return None
    legacy = None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line.startswith("META=") or line.startswith("META_API_KEY="):
            return line.split("=", 1)[1].strip()
        if legacy is None:
            if line.startswith("sk-or-"):
                legacy = line
            elif line.startswith("OPENROUTER_API_KEY="):
                legacy = line.split("=", 1)[1].strip()
    return legacy


# ------------------------------------------------------------------- smoke --

def _demo() -> int:
    """`python3 -m pacbench.render --env .env` — render a few beats through
    spark and show the draft, the rewrite, and that every required fact survived."""
    import argparse

    parser = argparse.ArgumentParser(description="Render sample beats through the Meta API.")
    parser.add_argument("--env", default=".env",
                        help="a .env holding MODEL + META lines (default: ./.env)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--temperature", type=float, default=0.8)
    args = parser.parse_args()

    key = load_key(args.env)
    if not key:
        print(f"no META credential in {args.env} (want a META=<credential> line)")
        return 1
    generate = meta_generator(key, args.model, temperature=args.temperature)

    beats = [
        Beat(channel="groupme", sender="Jordan Vance",
             draft="moving poker to Saturday 8pm at my place, 44 Birch Ave, can't do Friday",
             must_include=("Saturday", "8pm", "44 Birch"), register="casual"),
        Beat(channel="email", sender="Bright Smile Dental",
             draft="Reminder: your dental cleaning is Friday at 2:30pm.",
             must_include=("Friday", "2:30"), register="formal"),
    ]
    for beat in beats:
        print(f"\n[{beat.channel} / {beat.sender}]  keep: {beat.must_include}")
        print(f"  draft : {beat.draft}")
        try:
            print(f"  spark : {render(beat, generate=generate)}")
            print("  facts : all preserved ✓")
        except RenderError as error:
            print(f"  spark : {error.output}")
            print(f"  facts : DROPPED {error.missing} ✗")
    print(f"\n[noise / Priya Nair]\n  spark : {generate(noise_prompt('whatsapp', 'Priya Nair')).strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
