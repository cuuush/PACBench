"""Natural source-document identity (plan.md P0-1).

Maps each public observation to the *logical source document* it belongs to,
using natural source boundaries — never benchmark-story boundaries.

Authoring context: a ``BeatPlan`` becomes an ``Observation`` with
``source_id = f"{plan.id}/{beat.local_id}"`` (see :mod:`pacbench.authoring`),
so the life/story prefix is baked into every ``source_id``. This module is
where that benchmark-internal prefix is stripped back out: document identity
is derived solely from public source provenance (channel, thread, and — for
singleton documents — the message-local part of ``source_id``).

Only public ``Observation`` fields are read here (``channel``, ``thread``,
``sender``, ``source_id``, plus ``structured`` where noted). Nothing
authoring-side (``ThreadPlan.participants``, ``LifePlan`` lineage, checkpoint
or grading objects) is consulted, because adapters never receive it.

Mapping version: :data:`SOURCE_DOCUMENT_MAPPING_VERSION`. Bumped whenever
the channel table or key format changes; adapter run manifests must record
the version they ingested with (P0-2 digest).
"""

from __future__ import annotations

#: Version of the channel→document table + key format below. Recorded in
#: adapter run manifests so reports can refuse to aggregate runs that
#: ingested under different mappings.
SOURCE_DOCUMENT_MAPPING_VERSION = "1.0"

#: ``agent`` is the historical alias for the ``hermes`` channel
#: (see :mod:`pacbench.observation`); both name the same source.
HERMES_CHANNEL = "hermes"
AGENT_ALIAS = "agent"

#: Channels whose messages form an evolving conversation: one conversation
#: is one logical document. ``text`` (SMS) is not named in the P0-1 table
#: but is conversational by the same reasoning as iMessage; it keys on its
#: own channel label.
CONVERSATIONAL_CHANNELS = frozenset({
    "imessage",
    "text",
    "whatsapp",
    "groupme",
    "slack",
    "hermes",
})

#: Channels whose messages are standalone documents: one message (or one
#: explicitly modelled real thread, once the corpus can express one — it
#: currently cannot) is one logical document.
SINGLETON_CHANNELS = frozenset({
    "email",
})


def normalize_channel(channel: str) -> str:
    """Canonical channel label; folds the ``agent`` alias into ``hermes``."""
    if channel == AGENT_ALIAS:
        return HERMES_CHANNEL
    return channel


def is_conversational(channel: str) -> bool:
    """True when the channel groups messages into evolving conversation documents.

    Raises :exc:`ValueError` on unknown channels: a new channel must get an
    explicit policy row (conversational vs singleton) before it can be
    ingested — fail closed rather than guessing.
    """
    channel = normalize_channel(channel)
    if channel in CONVERSATIONAL_CHANNELS:
        return True
    if channel in SINGLETON_CHANNELS:
        return False
    raise ValueError(f"unknown channel {channel!r}: no source-document policy row")


def _message_local_id(source_id: str) -> str:
    """The message-local part of ``source_id`` with the life prefix removed.

    Authoring formats ``source_id`` as ``{plan.id}/{beat.local_id}``; the
    ``plan.id`` (life/story) prefix is benchmark-internal and must never
    appear in document identity, so only the part after the first ``/`` is
    kept. A ``source_id`` without ``/`` is already local and used as-is.
    """
    head, sep, tail = source_id.partition("/")
    return tail if sep else head


def logical_document_key(obs: dict) -> str:
    """Stable logical-document key for one public observation dict.

    Reads ONLY public ``Observation`` fields:

    * conversational channels (iMessage, SMS/``text``, WhatsApp, GroupMe,
      Slack DM / thread / channel, Hermes): ``{channel}:{thread}``. Sender
      is deliberately NOT part of the key — a conversation with several
      senders is still one document; sender rides inside the document
      content instead. Participant lists are authoring-side
      (``ThreadPlan.participants``) and never reach adapters, so they
      cannot be key inputs either.
    * email (singleton until the corpus can model a real thread with
      message-ID references): ``email:{message-local-id}`` — one document
      per message, with the life prefix stripped. Same-sender mail is NOT
      grouped (see policy doc).

    The key never contains a life ID, checkpoint ID, story/family name,
    grading metadata, or batching/collection timestamps — all of which vary
    across collection cycles, resumes, and provider repetitions while the
    underlying source conversation is unchanged.
    """
    channel = normalize_channel(str(obs["channel"]))
    thread = str(obs["thread"])
    if is_conversational(channel):
        return f"{channel}:{thread}"
    # Singleton path (currently only email).
    local = _message_local_id(str(obs["source_id"]))
    return f"{channel}:{local}"
