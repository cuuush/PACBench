"""Hermes isolated runtime (seam for later stages — not implemented in 0.1).

The primary experiment evaluates **Hermes + one selected memory provider**: every
scored answer comes from a real isolated Hermes instance using the installed
integration, never from direct provider calls and never from a mock.

Isolation requirements for the later runtime stage (binding, stated here so the
stage is built against them):

- One isolated Hermes home/workspace and provider namespace per (life,
  configuration, repetition); never reuse mutable memory across independent runs.
- Never touch the user's real Hermes profile, real personal data, or live source
  accounts; patch only the isolated run copy and record the patch in the run
  manifest.
- A shared, fairness-neutral simulated clock across all providers; advancing
  fictional time must not give one provider more real background-processing time
  than another (the real allowance is declared and recorded).
- Memory-tool boundary: only the selected provider's legitimate memory tools and
  native automatic context are available. Shell, unrestricted file reads, web
  search, other providers, independent Hermes memory/history retrieval, and
  benchmark inputs or answer keys are blocked at dispatch, not by prompt alone.
- Built-in alternate memory is disabled; the selected provider is the only
  long-term memory source. Ordinary current-session conversation is allowed.
- Direct user turns run through real Hermes with normal persistence: wrong
  answers and their consequences remain in the run (assistant-authorship
  contamination is a scored capability, not a harness bug to paper over).

v0.1 ships the policy and the seam only. No Hermes instance is provisioned, no
scored answer is collected, and no comparative claim is made.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class IsolationPolicy:
    """The tool/context boundary every primary run enforces (later stage)."""

    blocked_routes: tuple[str, ...] = (
        "shell",
        "unrestricted-filesystem-reads",
        "web-search",
        "other-memory-providers",
        "builtin-memory-reads",
        "builtin-memory-writes",
        "old-session-search",
        "benchmark-inputs",
        "hidden-answer-keys",
    )
    allowed_routes: tuple[str, ...] = (
        "selected-provider-memory-tools",
        "selected-provider-automatic-context",
        "ordinary-current-session-conversation",
    )


@dataclass(frozen=True)
class IsolatedHermes:
    """Handle to one isolated Hermes instance (later stage)."""

    namespace: str
    provider: str
    policy: IsolationPolicy = field(default_factory=IsolationPolicy)

    def ask(self, prompt: str, at: str) -> str:
        """Send a user turn at simulated time `at`. NOT-IN-0.1."""
        raise NotImplementedError(
            "NOT-IN-0.1: Hermes isolated runtime — no scored answers in 0.1")


def preflight() -> list[str]:
    """Requirements the runtime stage must satisfy before any scored run.

    Returns the list of currently unmet requirements (all of them in 0.1).
    A real runner refuses to score while this list is non-empty.
    """
    return [
        "isolated Hermes provisioning",
        "simulated-clock injection shared across providers",
        "memory-tool boundary enforcement at dispatch",
        "built-in memory disablement verification",
        "provider readiness and namespace isolation",
    ]
